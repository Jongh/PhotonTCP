"""Synchronous session driver / pump for PhotonTCP (M2-T04).

:class:`Session` binds the pure :class:`SessionStateMachine` (M2-T03) to a
:class:`Channel` (the byte transport) and a :class:`Clock` (the injected time
source). It is a **synchronous driver**: there is no background thread. The
caller advances the session by repeatedly calling :meth:`pump`, which drains
inbound frames, feeds them to the state machine, runs timers, transmits any
resulting packets, and returns the lifecycle events that surfaced.

Driving two peers is therefore as simple as alternately calling
``a.pump()`` / ``b.pump()`` over a lossless loopback channel: each pump moves
one side's pending I/O forward. Under the M2 lossless assumption no frame is
ever lost, but :meth:`pump` defensively ignores any frame that fails to
:meth:`Packet.unpack` so a corrupt frame can never crash the pump.

Only the standard library and the precedent modules are used. Imports use
submodule paths so this module does not depend on package ``__init__``
re-exports.
"""

from __future__ import annotations

from typing import Callable

from photontcp.channel.base import Channel
from photontcp.packet.header import Packet, PacketError

from .clock import Clock
from .state_machine import Output, SessionStateMachine
from .states import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_IDLE_TIMEOUT,
    SessionEvent,
    SessionState,
)

__all__ = ["Session"]

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

    # ------------------------------------------------------------------ #
    # Read-only state introspection (delegated to the state machine)
    # ------------------------------------------------------------------ #

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

    def pump(self, max_frames: int | None = None) -> list[SessionEvent]:
        """Run one synchronous I/O + timer cycle and return surfaced events.

        Cycle:

        1. Drain currently-available inbound frames (non-blocking). Each frame
           is :meth:`Packet.unpack`-ed; a frame that fails to parse
           (:class:`PacketError`) is silently dropped (defensive -- under the
           M2 lossless assumption this should not happen). Each parsed packet is
           fed to ``on_packet``; outgoing packets are transmitted and events
           collected.
        2. Run ``on_tick`` once; transmit packets and collect events.
        3. Return the accumulated events.

        Args:
            max_frames: Maximum number of inbound frames to process this cycle.
                ``None`` processes exactly the frames currently available (the
                drain stops as soon as ``recv_frame`` returns ``None``), which
                cannot loop forever on a finite queue.

        Returns:
            The lifecycle events surfaced during this cycle, in order.
        """
        events: list[SessionEvent] = []

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

            events.extend(
                self._emit(self._machine.on_packet(pkt, self.clock.now()))
            )

        events.extend(self._emit(self._machine.on_tick(self.clock.now())))
        return events

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
