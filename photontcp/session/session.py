"""Synchronous session driver / pump for PhotonTCP (M2-T04, M3-T05).

:class:`Session` binds the pure :class:`SessionStateMachine` (M2-T03) to a
:class:`Channel` (the byte transport) and a :class:`Clock` (the injected time
source). It is a **synchronous driver**: there is no background thread. The
caller advances the session by repeatedly calling :meth:`pump`, which drains
inbound frames, routes them to the right engine, runs timers, transmits any
resulting packets, and returns the lifecycle events that surfaced.

Driving two peers is therefore as simple as alternately calling
``a.pump()`` / ``b.pump()`` over a lossless loopback channel: each pump moves
one side's pending I/O forward. Under the lossless assumption no frame is ever
lost, but :meth:`pump` defensively ignores any frame that fails to
:meth:`Packet.unpack` so a corrupt frame can never crash the pump.

Two engines share the channel (M3-T05):

* The :class:`SessionStateMachine` owns the *control* path -- the 3-way
  handshake, heartbeats, and graceful close (SYN/SYN_ACK/FIN/FIN_ACK/HEARTBEAT,
  plus the handshake-completing ACK).
* An :class:`~photontcp.reliability.arq.ArqEndpoint` owns the *data* path --
  reliable, ordered application bytes (DATA/NACK, plus data ACKs).

The ARQ engine uses a sequence space **independent of the handshake ISN**: both
peers create their endpoint with ``send_isn = recv_isn = 0`` so no peer-ISN
negotiation is required for the data stream. Application data is queued with
:meth:`send`, accumulated on the receiver into an internal buffer as :meth:`pump`
delivers it, and drained by the application via :meth:`recv`. :meth:`pump`'s
return type is unchanged (``list[SessionEvent]``) so the control-path contract
and all M2 tests are preserved.

Only the standard library and the precedent modules are used. Imports use
submodule paths so this module does not depend on package ``__init__``
re-exports.
"""

from __future__ import annotations

from typing import Callable

from photontcp.channel.base import Channel
from photontcp.packet.header import Packet, PacketError
from photontcp.packet.types import PacketType
from photontcp.reliability.arq import ArqEndpoint, ArqOutput
from photontcp.reliability.rto import RtoEstimator

from .clock import Clock
from .state_machine import Output, SessionStateMachine
from .states import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_IDLE_TIMEOUT,
    SessionEvent,
    SessionState,
)

__all__ = ["Session"]

#: Default Selective-Repeat send-window size (max outstanding DATA packets) for
#: the per-session ARQ data path when the caller does not override it.
DEFAULT_ARQ_WINDOW_SIZE = 32

#: Default chunk size (bytes) used by the ARQ engine to split application data.
DEFAULT_ARQ_MAX_PAYLOAD = 200

#: Packet types routed to the control state machine. (The handshake-completing
#: ACK is routed conditionally on state, so ACK is deliberately excluded here.)
_CONTROL_TYPES = frozenset(
    {
        PacketType.SYN,
        PacketType.SYN_ACK,
        PacketType.FIN,
        PacketType.FIN_ACK,
        PacketType.HEARTBEAT,
    }
)

#: Non-blocking poll timeout used when draining inbound frames. A lossless
#: loopback ``recv_frame(timeout=0)`` returns immediately (``queue.get`` with a
#: zero timeout polls), so a single pump never blocks waiting for a peer.
_DRAIN_TIMEOUT = 0.0


class Session:
    """Synchronous driver wrapping a :class:`SessionStateMachine`.

    The state machine owns all protocol logic and decides *what* to send and
    *which* events to surface; this class owns the *I/O and timing*: it reads
    the clock, serializes outgoing packets, writes them to the channel, and
    drains incoming frames into the machine.

    Args:
        channel: The byte transport carrying serialized packets.
        clock: The injectable monotonic time source.
        is_initiator: ``True`` for the active opener (calls :meth:`connect`),
            ``False`` for the passive responder.
        session_id: Proposed session id (initiator) / placeholder adopted from
            the incoming SYN (responder).
        isn: Initial sequence number injected into the state machine.
        heartbeat_interval: Seconds of send-idleness before a HEARTBEAT.
        idle_timeout: Seconds without a received frame before declaring death.
        arq_window_size: Selective-Repeat send-window size for the data path.
        arq_max_payload: Chunk size used to split application data into DATA
            packets.
        rto: Adaptive RTO estimator for the ARQ data path. A fresh
            :class:`RtoEstimator` is created when omitted.
    """

    def __init__(
        self,
        channel: Channel,
        clock: Clock,
        *,
        is_initiator: bool,
        session_id: int,
        isn: int,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        arq_window_size: int = DEFAULT_ARQ_WINDOW_SIZE,
        arq_max_payload: int = DEFAULT_ARQ_MAX_PAYLOAD,
        rto: RtoEstimator | None = None,
    ) -> None:
        self.channel = channel
        self.clock = clock
        self._machine = SessionStateMachine(
            is_initiator=is_initiator,
            session_id=session_id,
            isn=isn,
            heartbeat_interval=heartbeat_interval,
            idle_timeout=idle_timeout,
        )

        # The ARQ data path uses a sequence space independent of the handshake
        # ISN: both peers start at 0 so no peer-ISN negotiation is needed for
        # the data stream. The session id is shared so emitted DATA/ACK/NACK
        # packets carry the same session identifier as the control path.
        self._arq = ArqEndpoint(
            session_id=session_id,
            send_isn=0,
            recv_isn=0,
            window_size=arq_window_size,
            rto=rto if rto is not None else RtoEstimator(),
            max_payload=arq_max_payload,
        )

        # Bytes delivered in-order by the ARQ receiver, awaiting recv().
        self._recv_buffer: list[bytes] = []

    # ------------------------------------------------------------------ #
    # Read-only state introspection (delegated to the state machine)
    # ------------------------------------------------------------------ #

    @property
    def session_id(self) -> int:
        """Session identifier (delegates to the state machine)."""
        return self._machine.session_id

    @property
    def state(self) -> SessionState:
        """Current lifecycle state (delegates to the state machine)."""
        return self._machine.state

    @property
    def is_established(self) -> bool:
        """``True`` while the session is ESTABLISHED."""
        return self._machine.is_established

    @property
    def is_closed(self) -> bool:
        """``True`` while the session is CLOSED."""
        return self._machine.is_closed

    # ------------------------------------------------------------------ #
    # Lifecycle drivers
    # ------------------------------------------------------------------ #

    def connect(self) -> list[SessionEvent]:
        """Initiator-only: begin the handshake and transmit the SYN.

        Returns any events surfaced by the machine (normally none, since
        ESTABLISHED arrives later via :meth:`pump`).
        """
        return self._emit(self._machine.connect(self.clock.now()))

    def close(self) -> list[SessionEvent]:
        """Begin a graceful close: transmit the FIN.

        Returns any events surfaced by the machine.
        """
        return self._emit(self._machine.close(self.clock.now()))

    # ------------------------------------------------------------------ #
    # Reliable data path (M3-T05)
    # ------------------------------------------------------------------ #

    def send(self, data: bytes) -> None:
        """Queue application *data* for reliable, ordered delivery to the peer.

        The bytes are handed to the per-session ARQ engine, which splits them
        into DATA packets and transmits as many as the send window allows; the
        rest are flushed by :meth:`pump` as ACKs open the window. Sending never
        delivers data locally.

        Args:
            data: Application payload bytes to send. An empty ``data`` is a
                no-op.

        Raises:
            RuntimeError: If the session is not ESTABLISHED. Data may only be
                sent on an established connection.
        """
        if not self.is_established:
            raise RuntimeError(
                f"cannot send data: session is {self.state.value}, "
                "not ESTABLISHED"
            )
        self._sync_arq_session_id()
        self._emit_arq(self._arq.send(data, self.clock.now()))

    def recv(self) -> list[bytes]:
        """Return and clear the bytes delivered in-order by the ARQ receiver.

        :meth:`pump` accumulates each in-order chunk the ARQ engine delivers
        into an internal buffer; this method drains that buffer and returns the
        chunks in delivery order. Concatenating the returned chunks reproduces
        the peer's sent byte stream.

        Returns:
            The buffered delivered chunks in order, or an empty list if none.
        """
        delivered = self._recv_buffer
        self._recv_buffer = []
        return delivered

    # ------------------------------------------------------------------ #
    # I/O + timer cycle
    # ------------------------------------------------------------------ #

    def pump(self, max_frames: int | None = None) -> list[SessionEvent]:
        """Run one synchronous I/O + timer cycle and return surfaced events.

        Cycle:

        1. Drain currently-available inbound frames (non-blocking). Each frame
           is :meth:`Packet.unpack`-ed; a frame that fails to parse
           (:class:`PacketError`) is silently dropped (defensive -- under the
           lossless assumption this should not happen). Each parsed packet is
           routed by type:

           * ``SYN`` / ``SYN_ACK`` / ``FIN`` / ``FIN_ACK`` / ``HEARTBEAT`` ->
             the control state machine (``on_packet``).
           * ``DATA`` / ``NACK`` -> the ARQ data engine; any delivered bytes are
             appended to the internal receive buffer (drained via :meth:`recv`).
           * ``ACK`` -> the control machine while in ``SYN_RCVD`` (the
             handshake-completing ACK), otherwise the ARQ engine (a data ACK).

           In every case the resulting packets are transmitted and any
           control events collected.
        2. Run both engines' ``on_tick`` once (control timers and ARQ
           retransmission timers); transmit packets and collect events.
        3. Return the accumulated control events.

        The return type is ``list[SessionEvent]`` regardless of data activity;
        delivered application bytes are exposed only through :meth:`recv`.

        Args:
            max_frames: Maximum number of inbound frames to process this cycle.
                ``None`` processes exactly the frames currently available (the
                drain stops as soon as ``recv_frame`` returns ``None``), which
                cannot loop forever on a finite queue.

        Returns:
            The lifecycle events surfaced during this cycle, in order.
        """
        events: list[SessionEvent] = []

        # Keep the ARQ engine's session id aligned with the negotiated id. The
        # responder adopts the initiator's session id from the incoming SYN, so
        # its ARQ endpoint (constructed with the placeholder id) must be synced
        # before it can accept the peer's DATA packets.
        self._sync_arq_session_id()

        processed = 0
        while max_frames is None or processed < max_frames:
            frame = self.channel.recv_frame(timeout=_DRAIN_TIMEOUT)
            if frame is None:
                break  # No frame available right now: drain complete.
            processed += 1

            try:
                pkt = Packet.unpack(frame)
            except PacketError:
                continue  # Corrupt/malformed frame: defensively ignore.

            events.extend(self._route_packet(pkt))

        # Run both engines' timers each cycle.
        events.extend(self._emit(self._machine.on_tick(self.clock.now())))
        self._emit_arq(self._arq.on_tick(self.clock.now()))
        return events

    def _route_packet(self, pkt: Packet) -> list[SessionEvent]:
        """Route one parsed packet to the control machine or the ARQ engine.

        Returns the control events produced (empty for data-path packets).
        """
        now = self.clock.now()

        if pkt.type in _CONTROL_TYPES:
            return self._emit(self._machine.on_packet(pkt, now))

        if pkt.type in (PacketType.DATA, PacketType.NACK):
            self._emit_arq(self._arq.on_packet(pkt, now))
            return []

        if pkt.type == PacketType.ACK:
            # An ACK in SYN_RCVD completes the handshake (control path);
            # any other ACK acknowledges data (ARQ path).
            if self.state == SessionState.SYN_RCVD:
                return self._emit(self._machine.on_packet(pkt, now))
            self._emit_arq(self._arq.on_packet(pkt, now))
            return []

        # Unknown/unhandled type: defensively ignore.
        return []

    def run_until(
        self,
        predicate: Callable[["Session"], bool],
        max_iters: int = 1000,
    ) -> list[SessionEvent]:
        """Pump repeatedly until ``predicate(self)`` is true or the cap is hit.

        Convenience for driving a *single* session; cross-peer scenarios are
        normally driven by alternating ``a.pump()`` / ``b.pump()`` calls.

        Args:
            predicate: Called with this session after each pump; iteration stops
                when it returns ``True``.
            max_iters: Hard upper bound on pumps to guarantee termination.

        Returns:
            All events surfaced across the iterations, in order.
        """
        events: list[SessionEvent] = []
        for _ in range(max_iters):
            if predicate(self):
                break
            events.extend(self.pump())
        return events

    # ------------------------------------------------------------------ #
    # Internal helper
    # ------------------------------------------------------------------ #

    def _emit(self, output: Output) -> list[SessionEvent]:
        """Transmit ``output.packets`` and return ``output.events``.

        Each packet is :meth:`Packet.pack`-ed and written to the channel in
        order; the machine's events are returned unchanged for the caller to
        accumulate.
        """
        for pkt in output.packets:
            self.channel.send_frame(pkt.pack())
        return output.events

    def _sync_arq_session_id(self) -> None:
        """Align the ARQ engine's session id with the negotiated session id.

        The responder is constructed with a placeholder session id and adopts
        the initiator's id during the handshake; the ARQ endpoint must use the
        same id or it would reject the peer's DATA/ACK/NACK as a foreign
        session. Idempotent and cheap, so it is safe to call every cycle.
        """
        self._arq.session_id = self._machine.session_id

    def _emit_arq(self, output: ArqOutput) -> None:
        """Transmit ``output.packets`` and buffer ``output.delivered`` bytes.

        ARQ produces no :class:`SessionEvent`s; its emitted packets are written
        to the channel in order and any in-order delivered chunks are appended
        to the internal receive buffer for :meth:`recv` to drain.
        """
        for pkt in output.packets:
            self.channel.send_frame(pkt.pack())
        if output.delivered:
            self._recv_buffer.extend(output.delivered)
