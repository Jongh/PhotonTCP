"""Synchronous session driver / pump for PhotonTCP (M2-T04, M3-T05, M4-T03).

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

Two engines share the channel:

* The :class:`SessionStateMachine` owns the *control* path -- the 3-way
  handshake, heartbeats, and graceful close (SYN/SYN_ACK/FIN/FIN_ACK/HEARTBEAT,
  plus the handshake-completing ACK). These packets travel on
  :data:`~photontcp.stream.mux.CONTROL_STREAM_ID` (stream ``0``).
* A :class:`~photontcp.stream.mux.StreamMux` owns the *data* path -- one
  :class:`~photontcp.reliability.arq.ArqEndpoint` per application stream
  (``stream_id >= 1``), giving each stream independent reliability, ordering and
  retransmission (no head-of-line blocking across streams).

The data path uses a sequence space **independent of the handshake ISN**: every
per-stream endpoint starts at ``send_isn = recv_isn = 0`` so no peer-ISN
negotiation is required for application data. The legacy ``send()``/``recv()``
API keeps working unchanged: it maps onto the shared default stream
(:data:`~photontcp.stream.mux.DEFAULT_STREAM_ID`, stream ``1``). Stream-aware
callers can :meth:`open_stream`, :meth:`send_on` and :meth:`recv_on` for
independent logical streams. :meth:`pump`'s return type is unchanged
(``list[SessionEvent]``) so the control-path contract and all M2/M3 tests are
preserved.

Only the standard library and the precedent modules are used. Imports use
submodule paths so this module does not depend on package ``__init__``
re-exports.
"""

from __future__ import annotations

from typing import Callable

from photontcp.channel.base import Channel
from photontcp.packet.header import Packet, PacketError
from photontcp.packet.types import PacketType
from photontcp.reliability.rto import RtoEstimator
from photontcp.stream.mux import (
    CONTROL_STREAM_ID,
    DEFAULT_STREAM_ID,
    MuxOutput,
    StreamMux,
)

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
#: each per-stream ARQ data path when the caller does not override it.
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


def _rto_factory_from(rto: RtoEstimator | None) -> Callable[[], RtoEstimator]:
    """Build a zero-arg factory producing fresh estimators for each stream.

    The multiplexer needs an *independent* :class:`RtoEstimator` per stream
    (sharing one instance across streams would mix unrelated RTT samples). For
    backward compatibility the caller still passes a single ``rto`` instance;
    we read its configuration and reproduce equivalent fresh estimators. When
    ``rto`` is ``None`` a default-configured estimator is produced per stream.
    """
    if rto is None:
        return lambda: RtoEstimator()

    # Reproduce the supplied estimator's configuration for every new stream.
    # The bounds and initial RTO are the only construction-time settings;
    # they are read defensively so an unexpected estimator shape still yields
    # a usable factory.
    initial_rto = getattr(rto, "_rto", 1.0)
    min_rto = getattr(rto, "_min_rto", 0.2)
    max_rto = getattr(rto, "_max_rto", 60.0)
    return lambda: RtoEstimator(
        initial_rto=initial_rto, min_rto=min_rto, max_rto=max_rto
    )


class Session:
    """Synchronous driver wrapping a :class:`SessionStateMachine`.

    The state machine owns all control-plane logic and decides *what* to send
    and *which* events to surface; a :class:`StreamMux` owns the multiplexed
    data plane; this class owns the *I/O and timing*: it reads the clock,
    serializes outgoing packets, writes them to the channel, and drains
    incoming frames into the right engine.

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
        arq_window_size: Selective-Repeat send-window size for each data stream.
        arq_max_payload: Chunk size used to split application data into DATA
            packets.
        rto: Adaptive RTO estimator template for the data path. Each stream
            gets its own estimator reproduced from this template (a single
            instance cannot be shared across independent streams). A
            default-configured estimator is used per stream when omitted.
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

        # The data path is a per-stream ARQ multiplexer. Each stream uses a
        # sequence space independent of the handshake ISN (every endpoint
        # starts at 0), so no peer-ISN negotiation is needed for application
        # data. The mux shares the session id so emitted DATA/ACK/NACK packets
        # carry the same identifier as the control path. The legacy ``rto``
        # template is converted to a per-stream factory.
        self._mux = StreamMux(
            session_id=session_id,
            is_initiator=is_initiator,
            window_size=arq_window_size,
            max_payload=arq_max_payload,
            rto_factory=_rto_factory_from(rto),
        )

        # Per-stream receive buffers: stream_id -> in-order delivered chunks
        # awaiting drain by recv()/recv_on()/recv_all().
        self._recv_buffers: dict[int, list[bytes]] = {}

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
    # Reliable data path -- stream-aware API (M4-T03)
    # ------------------------------------------------------------------ #

    def open_stream(self) -> int:
        """Allocate and open a new application stream, returning its id.

        Ids follow the multiplexer's parity convention (initiator: odd
        ``3, 5, ...``; responder: even ``2, 4, ...``) so the two peers never
        collide. The shared default stream (:data:`DEFAULT_STREAM_ID`) is never
        returned.

        Returns:
            The id of the newly opened stream.
        """
        return self._mux.open_stream()

    def send_on(self, stream_id: int, data: bytes) -> None:
        """Queue application *data* for reliable, ordered delivery on a stream.

        The bytes are handed to the stream's ARQ endpoint (created on first
        use), which splits them into DATA packets and transmits as many as the
        send window allows; the rest are flushed by :meth:`pump` as ACKs open
        the window. Sending never delivers data locally.

        Args:
            stream_id: Target application stream (``>= 1``).
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
        self._sync_session_id()
        self._emit_mux(self._mux.send(stream_id, data, self.clock.now()))

    def recv_on(self, stream_id: int) -> list[bytes]:
        """Return and clear the bytes delivered in-order on *stream_id*.

        :meth:`pump` accumulates each in-order chunk delivered on a stream into
        that stream's receive buffer; this drains it and returns the chunks in
        delivery order. Concatenating the returned chunks reproduces the peer's
        sent byte stream on that stream.

        Args:
            stream_id: The stream whose buffered chunks to drain.

        Returns:
            The buffered delivered chunks in order, or an empty list if none.
        """
        return self._recv_buffers.pop(stream_id, [])

    def recv_all(self) -> dict[int, list[bytes]]:
        """Return and clear delivered bytes for every non-empty stream.

        Returns:
            A mapping of ``stream_id`` to its buffered in-order chunks. Only
            streams that delivered at least one chunk are present. All returned
            buffers are cleared.
        """
        delivered = self._recv_buffers
        self._recv_buffers = {}
        return delivered

    # ------------------------------------------------------------------ #
    # Reliable data path -- legacy default-stream API
    # ------------------------------------------------------------------ #

    def send(self, data: bytes) -> None:
        """Queue application *data* on the shared default stream.

        Backward-compatible convenience for :meth:`send_on` targeting
        :data:`DEFAULT_STREAM_ID`.

        Args:
            data: Application payload bytes to send. An empty ``data`` is a
                no-op.

        Raises:
            RuntimeError: If the session is not ESTABLISHED.
        """
        self.send_on(DEFAULT_STREAM_ID, data)

    def recv(self) -> list[bytes]:
        """Return and clear bytes delivered on the shared default stream.

        Backward-compatible convenience for :meth:`recv_on` targeting
        :data:`DEFAULT_STREAM_ID`.

        Returns:
            The buffered delivered chunks in order, or an empty list if none.
        """
        return self.recv_on(DEFAULT_STREAM_ID)

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
           routed by its ``stream_id``:

           * Stream ``0`` (:data:`CONTROL_STREAM_ID`): control packets
             (``SYN`` / ``SYN_ACK`` / ``FIN`` / ``FIN_ACK`` / ``HEARTBEAT``)
             go to the control state machine; an ``ACK`` goes to the machine
             while in ``SYN_RCVD`` (the handshake-completing ACK) and is
             otherwise ignored (the control stream carries no data ACKs).
           * Stream ``>= 1``: routed to the :class:`StreamMux` (DATA / ACK /
             NACK); any delivered bytes are accumulated into the per-stream
             receive buffers (drained via :meth:`recv` / :meth:`recv_on`).

           In every case the resulting packets are transmitted and any control
           events collected.
        2. Run the control machine's ``on_tick`` and the mux's ``on_tick``
           once (control timers and per-stream retransmission timers); transmit
           packets and collect events.
        3. Return the accumulated control events.

        The return type is ``list[SessionEvent]`` regardless of data activity;
        delivered application bytes are exposed only through the recv methods.

        Args:
            max_frames: Maximum number of inbound frames to process this cycle.
                ``None`` processes exactly the frames currently available (the
                drain stops as soon as ``recv_frame`` returns ``None``), which
                cannot loop forever on a finite queue.

        Returns:
            The lifecycle events surfaced during this cycle, in order.
        """
        events: list[SessionEvent] = []

        # Keep the data plane's session id aligned with the negotiated id. The
        # responder adopts the initiator's session id from the incoming SYN, so
        # its mux (constructed with the placeholder id) must be synced before it
        # can accept the peer's DATA packets.
        self._sync_session_id()

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
        self._emit_mux(self._mux.on_tick(self.clock.now()))
        return events

    def _route_packet(self, pkt: Packet) -> list[SessionEvent]:
        """Route one parsed packet to the control machine or the data mux.

        Routing is by ``stream_id``: control packets ride stream
        :data:`CONTROL_STREAM_ID`, application traffic rides streams ``>= 1``.
        Returns the control events produced (empty for data-path packets).
        """
        now = self.clock.now()

        if pkt.stream_id == CONTROL_STREAM_ID:
            if pkt.type in _CONTROL_TYPES:
                return self._emit(self._machine.on_packet(pkt, now))
            if pkt.type == PacketType.ACK:
                # An ACK on the control stream completes the handshake only in
                # SYN_RCVD; any other control-stream ACK is ignored (there are
                # no data ACKs on the control stream).
                if self.state == SessionState.SYN_RCVD:
                    return self._emit(self._machine.on_packet(pkt, now))
                return []
            # Unknown control-stream type: defensively ignore.
            return []

        # Application stream (stream_id >= 1): DATA / ACK / NACK -> data mux.
        self._emit_mux(self._mux.on_packet(pkt, now))
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
    # Internal helpers
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

    def _sync_session_id(self) -> None:
        """Align the data plane's session id with the negotiated session id.

        The responder is constructed with a placeholder session id and adopts
        the initiator's id during the handshake; the mux (and every per-stream
        endpoint) must use the same id or it would reject the peer's
        DATA/ACK/NACK as a foreign session. Idempotent and cheap, so it is safe
        to call every cycle.
        """
        self._mux.set_session_id(self._machine.session_id)

    def _emit_mux(self, output: MuxOutput) -> None:
        """Transmit ``output.packets`` and buffer ``output.delivered`` bytes.

        The mux produces no :class:`SessionEvent`s; its emitted packets are
        written to the channel in order and any in-order delivered chunks are
        appended to the matching per-stream receive buffer for the recv methods
        to drain.
        """
        for pkt in output.packets:
            self.channel.send_frame(pkt.pack())
        for stream_id, chunks in output.delivered.items():
            if chunks:
                self._recv_buffers.setdefault(stream_id, []).extend(chunks)
