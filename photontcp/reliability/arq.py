"""Selective Repeat ARQ engine (M3-T03).

This module provides a pure, I/O- and clock-independent Selective Repeat (SR)
automatic-repeat-request engine. A single :class:`ArqEndpoint` combines both a
sender (sliding send window, per-packet retransmission, RTT/RTO driven timeouts)
and a receiver (reordering + de-duplication buffer producing cumulative ACKs and
selective NACKs).

Design principles (shared with the rest of PhotonTCP):

* **No I/O, no channel, no real clock.** Every method that needs the current
  time takes an explicit ``now: float`` argument. The engine never reads the
  wall clock and never uses randomness, so identical call sequences always
  produce identical state and outputs.
* **Wraparound-safe sequence math.** All sequence comparisons and window-edge
  checks go through :mod:`photontcp.reliability.serial` so behaviour is correct
  across the 32-bit sequence wrap.

The engine is driven by a host (typically a ``Session``) that performs the
actual packet transmission. Each public method returns an :class:`ArqOutput`
describing the packets to send and any data delivered up to the application.

Sender / receiver behaviour summary
-----------------------------------

``send(data, now)``
    Split *data* into ``max_payload``-sized chunks. For each chunk, if the send
    window has room (outstanding count < ``min(window_size, peer_window)``) emit
    a DATA packet and record it as outstanding; otherwise queue the chunk in the
    internal pending buffer to be flushed when the window opens.

``on_packet(pkt, now)`` — DATA
    * ``seq == rcv_base``  -> deliver, advance ``rcv_base``, drain contiguous
      buffered chunks. Always emit a cumulative ACK.
    * future seq in window -> store in reorder buffer, emit cumulative ACK plus a
      NACK for the lowest missing seq (selective repeat hint).
    * duplicate / past seq  -> discard, but still emit a cumulative ACK.

``on_packet(pkt, now)`` — ACK
    Remove every outstanding packet with ``seq < pkt.ack`` (cumulative).
    Karn's algorithm: take an RTT sample only for packets that were never
    retransmitted. Update ``peer_window`` from ``pkt.window`` and flush pending
    chunks the newly-opened window now permits.

``on_packet(pkt, now)`` — NACK
    Immediately retransmit the requested seq (if still outstanding), bumping its
    retransmission count. No RTT sample is taken (Karn).

``on_tick(now)``
    Retransmit any outstanding packet whose age (``now - send_time``) has reached
    the current RTO, bump its retransmission count, and apply one RTO backoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..packet.header import Packet
from ..packet.types import Flags, PacketType
from .rto import RtoEstimator
from .serial import seq_add, seq_diff, seq_lt

__all__ = ["ArqOutput", "ArqEndpoint", "ArqEvent"]


class ArqEvent(Enum):
    """Diagnostic events surfaced through :attr:`ArqOutput.events`.

    Attributes:
        SEND_FAILED: Emitted once when an outstanding DATA packet has been
            retransmitted more than ``max_retx`` times. The endpoint is marked
            failed (:attr:`ArqEndpoint.is_failed`) and stops retransmitting.
    """

    SEND_FAILED = "send_failed"


@dataclass
class ArqOutput:
    """Result of an ARQ operation.

    Attributes:
        packets: Packets the host should transmit, in order.
        delivered: Payload chunks delivered to the application, in order.
        events: Optional list of diagnostic events (see :class:`ArqEvent`).
    """

    packets: list[Packet] = field(default_factory=list)
    delivered: list[bytes] = field(default_factory=list)
    events: list = field(default_factory=list)


@dataclass
class _Outstanding:
    """Book-keeping for a single unacknowledged DATA packet."""

    packet: Packet
    send_time: float
    retx_count: int = 0


class ArqEndpoint:
    """Combined Selective Repeat sender + receiver for one session.

    The endpoint is symmetric: it maintains a send window for outbound DATA and
    a receive/reorder buffer for inbound DATA. It is purely deterministic and
    takes the current time as an explicit ``now`` argument on every method.
    """

    def __init__(
        self,
        *,
        session_id: int,
        send_isn: int,
        window_size: int,
        rto: RtoEstimator,
        max_payload: int = 200,
        recv_isn: int = 0,
        stream_id: int = 0,
        max_retx: int = 8,
    ) -> None:
        """Initialise the endpoint.

        Args:
            session_id: Session identifier stamped on every emitted packet.
            send_isn: Initial send sequence number (first DATA ``seq``).
            window_size: Local send-window limit (max outstanding packets).
            rto: Injected adaptive RTO estimator.
            max_payload: Chunk size used to split application data.
            recv_isn: Initial expected receive sequence number (peer's ISN). The
                handshake may not know the peer ISN, so it is injected here; the
                receiver expects the first inbound DATA to carry ``seq ==
                recv_isn``.
            stream_id: Stream identifier stamped on emitted packets.
            max_retx: Maximum number of retransmissions allowed for any single
                outstanding DATA packet. When ``on_tick`` would push a packet's
                ``retx_count`` past this bound the endpoint is marked failed
                (see :attr:`is_failed`) and stops retransmitting.
        """
        if window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {window_size!r}")
        if max_payload < 1:
            raise ValueError(f"max_payload must be >= 1, got {max_payload!r}")
        if max_retx < 0:
            raise ValueError(f"max_retx must be >= 0, got {max_retx!r}")

        self.session_id = int(session_id)
        self.stream_id = int(stream_id)
        self.window_size = int(window_size)
        self.max_payload = int(max_payload)
        self.max_retx = int(max_retx)
        self._rto = rto

        # --- sender state ---
        self.next_seq = int(send_isn)
        # seq -> _Outstanding
        self._outstanding: dict[int, _Outstanding] = {}
        # Peer's advertised receive window; starts optimistic at our own window.
        self.peer_window = int(window_size)
        # Chunks queued because the window was full when send() was called.
        self._pending: list[bytes] = []
        # Cumulative payload bytes removed from the send window by ACKs.
        self._acked_bytes = 0
        # Set once a packet exceeds max_retx; no further retransmissions.
        self._failed = False

        # --- receiver state ---
        self.rcv_base = int(recv_isn)
        # seq -> payload, for out-of-order chunks past rcv_base.
        self._reorder: dict[int, bytes] = {}
        # Missing seqs we have already emitted a NACK for (suppress duplicates).
        self._nacked: set[int] = set()

    # ------------------------------------------------------------------
    # Read-only helpers (testing / introspection)
    # ------------------------------------------------------------------
    @property
    def unacked_count(self) -> int:
        """Number of outstanding (unacknowledged) DATA packets."""
        return len(self._outstanding)

    @property
    def bytes_in_flight(self) -> int:
        """Total payload bytes currently outstanding."""
        return sum(len(o.packet.payload) for o in self._outstanding.values())

    @property
    def pending_count(self) -> int:
        """Number of chunks queued because the send window was full."""
        return len(self._pending)

    @property
    def acked_bytes(self) -> int:
        """Cumulative payload bytes acknowledged (removed from the window)."""
        return self._acked_bytes

    @property
    def is_failed(self) -> bool:
        """True once a DATA packet exceeded ``max_retx`` (delivery failed)."""
        return self._failed

    @property
    def failed(self) -> bool:
        """Alias of :attr:`is_failed`."""
        return self._failed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _recv_advertised_window(self) -> int:
        """Window size we advertise to the peer (free receive-buffer slots).

        We accept up to ``window_size`` out-of-order chunks; the advertised
        window is the remaining room in that reorder buffer.
        """
        free = self.window_size - len(self._reorder)
        return free if free > 0 else 0

    def _send_limit(self) -> int:
        """Effective send-window limit honouring local and peer windows."""
        return min(self.window_size, self.peer_window)

    def _can_send(self) -> bool:
        return len(self._outstanding) < self._send_limit()

    def _emit_data(self, chunk: bytes, now: float) -> Packet:
        """Build, record, and return a DATA packet for *chunk* at *now*."""
        pkt = Packet(
            type=PacketType.DATA,
            session_id=self.session_id,
            stream_id=self.stream_id,
            seq=self.next_seq,
            ack=self.rcv_base,
            window=self._recv_advertised_window(),
            payload=chunk,
        )
        self._outstanding[self.next_seq] = _Outstanding(
            packet=pkt, send_time=float(now), retx_count=0
        )
        self.next_seq = seq_add(self.next_seq, 1)
        return pkt

    def _flush_pending(self, now: float) -> list[Packet]:
        """Emit as many queued chunks as the open window now allows."""
        out: list[Packet] = []
        while self._pending and self._can_send():
            chunk = self._pending.pop(0)
            out.append(self._emit_data(chunk, now))
        return out

    def _ack_packet(self) -> Packet:
        """Build a cumulative ACK reflecting current receive state."""
        return Packet(
            type=PacketType.ACK,
            session_id=self.session_id,
            stream_id=self.stream_id,
            seq=self.next_seq,
            ack=self.rcv_base,
            window=self._recv_advertised_window(),
        )

    def _lowest_missing_seq(self) -> int | None:
        """Lowest gap seq between ``rcv_base`` and the buffered chunks, if any.

        Returns ``None`` when there is no hole (empty reorder buffer).
        """
        if not self._reorder:
            return None
        # The highest buffered seq bounds the search window.
        highest = max(self._reorder, key=lambda s: seq_diff(s, self.rcv_base))
        seq = self.rcv_base
        # Walk forward from rcv_base up to the highest buffered seq.
        while seq_lt(seq, highest) or seq == highest:
            if seq not in self._reorder:
                return seq
            seq = seq_add(seq, 1)
        return None

    def _nack_packet(self, missing_seq: int) -> Packet:
        """Build a NACK requesting retransmission of *missing_seq*."""
        return Packet(
            type=PacketType.NACK,
            session_id=self.session_id,
            stream_id=self.stream_id,
            seq=self.next_seq,
            ack=missing_seq,
            window=self._recv_advertised_window(),
            flags=Flags.NACK,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send(self, data: bytes, now: float) -> ArqOutput:
        """Queue *data* for reliable, ordered delivery to the peer.

        *data* is split into ``max_payload``-sized chunks. Chunks that fit in the
        current send window are emitted as DATA packets immediately; the rest are
        queued and flushed later (on ACK-driven window opening).
        """
        out = ArqOutput()
        if not data:
            return out

        chunks = [
            data[i : i + self.max_payload]
            for i in range(0, len(data), self.max_payload)
        ]
        for chunk in chunks:
            if self._can_send():
                out.packets.append(self._emit_data(chunk, now))
            else:
                self._pending.append(chunk)
        return out

    def on_packet(self, pkt: Packet, now: float) -> ArqOutput:
        """Process one received packet and return resulting output."""
        # Defensive: ignore packets for a different session.
        if pkt.session_id != self.session_id:
            return ArqOutput()

        if pkt.type == PacketType.DATA:
            return self._on_data(pkt, now)
        if pkt.type == PacketType.ACK:
            return self._on_ack(pkt, now)
        if pkt.type == PacketType.NACK:
            return self._on_nack(pkt, now)
        # Other packet types are not the ARQ engine's concern.
        return ArqOutput()

    def _on_data(self, pkt: Packet, now: float) -> ArqOutput:
        out = ArqOutput()
        seq = pkt.seq

        if seq == self.rcv_base:
            # In-order: deliver and drain any contiguous buffered chunks.
            out.delivered.append(pkt.payload)
            self.rcv_base = seq_add(self.rcv_base, 1)
            while self.rcv_base in self._reorder:
                out.delivered.append(self._reorder.pop(self.rcv_base))
                self.rcv_base = seq_add(self.rcv_base, 1)
            # rcv_base advanced past previously-missing seqs; drop their stale
            # NACK records so a future genuine gap at the same seq re-NACKs.
            self._prune_nacked()
        elif seq_lt(self.rcv_base, seq):
            # Future seq. Accept into the reorder buffer if within our window
            # and not already buffered; otherwise drop.
            within_window = seq_diff(seq, self.rcv_base) < self.window_size
            if within_window and seq not in self._reorder:
                self._reorder[seq] = pkt.payload
            # else: duplicate buffered chunk or beyond-window -> discard.
        else:
            # seq < rcv_base: already delivered duplicate -> discard.
            pass

        # Always send a cumulative ACK.
        out.packets.append(self._ack_packet())

        # If a hole exists, append a selective NACK for the lowest missing seq,
        # but only the first time we observe that particular hole. Suppressing
        # duplicate NACKs avoids driving repeated retransmissions of the same
        # packet; the RTO-driven on_tick retransmit remains as a backup.
        missing = self._lowest_missing_seq()
        if missing is not None and missing not in self._nacked:
            out.packets.append(self._nack_packet(missing))
            self._nacked.add(missing)

        return out

    def _prune_nacked(self) -> None:
        """Drop NACK records for seqs the receiver has now passed.

        A seq is stale once it is no longer strictly ahead of ``rcv_base``
        (i.e. it has been delivered or skipped). Removing it lets a genuine
        future gap at the same seq value emit a fresh NACK.
        """
        if not self._nacked:
            return
        self._nacked = {s for s in self._nacked if seq_lt(self.rcv_base, s)}

    def _on_ack(self, pkt: Packet, now: float) -> ArqOutput:
        out = ArqOutput()

        # Reflect peer's advertised receive window for flow control.
        self.peer_window = int(pkt.window)

        # Cumulative ACK: remove every outstanding packet with seq < pkt.ack.
        acked = [s for s in self._outstanding if seq_lt(s, pkt.ack)]
        for s in acked:
            o = self._outstanding.pop(s)
            # Accumulate acknowledged payload bytes for send-progress reporting.
            self._acked_bytes += len(o.packet.payload)
            # Karn: only sample RTT for packets never retransmitted.
            if o.retx_count == 0:
                self._rto.on_sample(now - o.send_time)

        # Window may have opened; flush any pending chunks.
        out.packets.extend(self._flush_pending(now))
        return out

    def _on_nack(self, pkt: Packet, now: float) -> ArqOutput:
        out = ArqOutput()
        # Reflect advertised window if present (NACKs also carry one).
        self.peer_window = int(pkt.window)

        target = pkt.ack
        o = self._outstanding.get(target)
        if o is not None:
            o.send_time = float(now)
            o.retx_count += 1
            out.packets.append(o.packet)  # No RTT sample (Karn).
        return out

    def on_tick(self, now: float) -> ArqOutput:
        """Retransmit outstanding packets whose RTO has elapsed.

        At most one RTO backoff is applied per tick, regardless of how many
        packets time out together. If retransmitting any timed-out packet would
        push its ``retx_count`` past ``max_retx`` the endpoint is marked failed
        (:attr:`is_failed`), an :data:`ArqEvent.SEND_FAILED` event is appended,
        and no further retransmissions are emitted.
        """
        out = ArqOutput()
        # Once failed, stop all retransmission activity.
        if self._failed:
            return out

        rto = self._rto.rto()
        timed_out = [
            o
            for o in self._outstanding.values()
            if (now - o.send_time) >= rto
        ]
        if not timed_out:
            return out

        for o in timed_out:
            if o.retx_count >= self.max_retx:
                # This packet has exhausted its retransmission budget: fail the
                # endpoint instead of retransmitting again (or forever).
                self._failed = True
                out.events.append(ArqEvent.SEND_FAILED)
                return out
            o.send_time = float(now)
            o.retx_count += 1
            out.packets.append(o.packet)

        # One exponential backoff for the timeout event.
        self._rto.on_timeout()
        return out
