"""Pure session state machine for PhotonTCP (M2-T03).

This module implements :class:`SessionStateMachine`, a *pure* connection
lifecycle engine. It has **no I/O, channel, or clock dependencies**: time is
always supplied by the caller as a ``now: float`` argument, and the
``session_id`` and initial sequence number (``isn``) are injected at
construction time. No randomness or wall-clock access is performed, so the
machine is fully deterministic -- the same input sequence always yields the
same outputs. This makes it trivially testable on a virtual clock and a
lossless loopback channel (M2 lossless assumption).

Each input method (:meth:`connect`, :meth:`on_packet`, :meth:`on_tick`,
:meth:`close`) returns an :class:`Output` carrying the packets to send and the
events to surface to the upper layer.

State table (lossless M2 assumption)::

    current state   input                         -> next state    sent          event
    -----------------------------------------------------------------------------------------
    CLOSED          connect() [initiator]         -> SYN_SENT      SYN           -
    CLOSED          recv SYN [responder]          -> SYN_RCVD      SYN_ACK       -
    SYN_SENT        recv SYN_ACK                  -> ESTABLISHED   ACK           ESTABLISHED
    SYN_RCVD        recv ACK                      -> ESTABLISHED   -             ESTABLISHED
    ESTABLISHED     close()                       -> FIN_WAIT      FIN           -
    ESTABLISHED     recv FIN [auto-close]         -> CLOSE_WAIT    FIN_ACK,FIN   PEER_CLOSED
    ESTABLISHED     tick: heartbeat due           -> (same)        HEARTBEAT     -
    FIN_WAIT        recv FIN_ACK (peer not FIN'd) -> (same)        -             -
    FIN_WAIT        recv FIN_ACK (peer FIN'd)     -> CLOSED        -             CLOSED
    FIN_WAIT        recv FIN (our FIN unacked)    -> (same)        FIN_ACK       -
    FIN_WAIT        recv FIN (our FIN acked)      -> CLOSED        FIN_ACK       CLOSED
    CLOSE_WAIT      recv FIN_ACK                  -> CLOSED        -             CLOSED
    ESTABLISHED     tick: idle timeout            -> CLOSED        -             TIMED_OUT
    FIN_WAIT        tick: idle timeout            -> CLOSED        -             CLOSED
    CLOSE_WAIT      tick: idle timeout            -> CLOSED        -             CLOSED

A data-plane activity hook, :meth:`note_data_activity`, lets the session driver
refresh ``last_recv`` from DATA/ACK/NACK traffic so an actively-transferring but
control-plane-quiet session does not trip the idle timeout. It is accepted in
the active states (ESTABLISHED / FIN_WAIT / CLOSE_WAIT), performs no transition,
and emits nothing. The idle timeout itself is termination-aware: tripping it
while a close is already in progress (FIN_WAIT / CLOSE_WAIT) is reported as
``CLOSED`` (graceful close completed), only genuinely active states report
``TIMED_OUT``.

Control-packet retransmission (M3-T04)
--------------------------------------

The handshake (SYN/SYN_ACK/ACK) and graceful close (FIN/FIN_ACK) use control
packets that, on a lossy link, may be dropped. To tolerate this, the machine
remembers the last *outstanding* control packet awaiting a reply -- the SYN
(SYN_SENT), the SYN_ACK (SYN_RCVD), or our FIN (FIN_WAIT / CLOSE_WAIT) -- along
with the time it was sent and how many times it has been retransmitted. Each
:meth:`on_tick` retransmits that packet once ``control_rto`` seconds have
elapsed since the last (re)send::

    pending           tick: now - sent >= control_rto, retries <= max -> resend (same)  -
    pending           retries > max, establishment phase              -> CLOSED  CONNECT_FAILED
    pending           retries > max, close phase                      -> CLOSED  TIMED_OUT

The pending packet is cleared as soon as its reply arrives (SYN_ACK clears the
SYN, the handshake-completing ACK clears the SYN_ACK, FIN_ACK clears our FIN)
and whenever the session reaches ESTABLISHED or CLOSED. On a lossless link the
reply is immediate, so the pending packet is cleared before any tick fires and
no retransmission ever happens -- the M2 behaviour is unchanged.

Retransmissions refresh ``last_send`` (so heartbeat timing stays consistent) but
deliberately do **not** touch ``last_recv``: a peer that has gone silent must
still eventually trip either the control-retry limit or the idle timeout.

Graceful close is symmetric: a session reaches CLOSED only once *both* its own
FIN is acked and it has acked the peer's FIN. Receiving a FIN while ESTABLISHED
auto-sends our own FIN (alongside the FIN_ACK), so a single active close() tears
down both peers without the passive side calling close() itself. close() is
idempotent and is a no-op outside ESTABLISHED.

Sequence/ack arithmetic uses plain integer increments; the modulo-2^32 wrap
policy is owned by M3 (ARQ) and intentionally not applied here.

Only the standard library and the precedent modules
(:mod:`photontcp.packet.header`, :mod:`photontcp.packet.types`,
:mod:`photontcp.session.states`) are used. Imports use submodule paths to avoid
depending on package ``__init__`` re-exports (which are authored in M2-T04).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from photontcp.packet.header import Packet
from photontcp.packet.types import Flags, PacketType

from .states import (
    DEFAULT_CONTROL_RTO,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_MAX_CONTROL_RETRIES,
    SessionEvent,
    SessionState,
)

__all__ = ["Output", "SessionStateMachine"]


@dataclass
class Output:
    """Result of a single state-machine step.

    Attributes:
        packets: Packets the caller should transmit (in order).
        events: Lifecycle events the caller should surface to the upper layer.
    """

    packets: list[Packet] = field(default_factory=list)
    events: list[SessionEvent] = field(default_factory=list)


class SessionStateMachine:
    """Pure, deterministic PhotonTCP session lifecycle engine.

    The machine drives the 3-way handshake (SYN/SYN_ACK/ACK), the graceful
    close handshake (FIN/FIN_ACK), and heartbeat/idle-timeout bookkeeping. It
    never performs I/O or reads a clock; ``now`` is injected on every call.

    Args:
        is_initiator: ``True`` for the active opener (uses :meth:`connect`),
            ``False`` for the passive responder.
        session_id: Injected session identifier. For the initiator this is the
            proposed id; the responder adopts the id carried by the incoming
            SYN (handshake agreement).
        isn: Injected initial sequence number (the first ``seq`` value).
        heartbeat_interval: Seconds of send-idleness after which a HEARTBEAT is
            emitted while ESTABLISHED.
        idle_timeout: Seconds without any received frame after which the session
            is declared dead (``TIMED_OUT`` + transition to ``CLOSED``).
        control_rto: Fixed retransmission timeout (seconds) for an outstanding
            control packet (SYN/SYN_ACK/FIN). A simple fixed RTO is used rather
            than an adaptive estimator for the handshake/close path.
        max_control_retries: Maximum number of control-packet retransmissions
            before the session is aborted (``CONNECT_FAILED`` during
            establishment, ``TIMED_OUT`` during close).
    """

    def __init__(
        self,
        *,
        is_initiator: bool,
        session_id: int,
        isn: int,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        control_rto: float = DEFAULT_CONTROL_RTO,
        max_control_retries: int = DEFAULT_MAX_CONTROL_RETRIES,
    ) -> None:
        self.is_initiator = is_initiator
        self.session_id = session_id
        self.isn = isn
        self.seq = isn
        self.heartbeat_interval = heartbeat_interval
        self.idle_timeout = idle_timeout
        self.control_rto = control_rto
        self.max_control_retries = max_control_retries

        self._state = SessionState.CLOSED
        #: Last time a packet was sent / a frame was received. ``None`` until
        #: the first send/receive so timers don't fire spuriously from CLOSED.
        self.last_send: float | None = None
        self.last_recv: float | None = None

        #: Graceful-close bookkeeping. A session reaches CLOSED only once *both*
        #: directions are torn down: our own FIN has been acknowledged
        #: (``_fin_acked``) and we have acknowledged the peer's FIN
        #: (``_peer_fin_acked``). This makes close symmetric so a single active
        #: close() drives both peers to CLOSED.
        self._fin_acked = False
        self._peer_fin_acked = False

        #: Control-packet retransmission bookkeeping (M3-T04). ``_pending_ctrl``
        #: is the last control packet (SYN/SYN_ACK/FIN) still awaiting a reply,
        #: ``_pending_ctrl_since`` the time it was last (re)sent, and
        #: ``_ctrl_retries`` how many times it has been retransmitted. ``None``
        #: means nothing is outstanding, so :meth:`on_tick` never retransmits.
        self._pending_ctrl: Packet | None = None
        self._pending_ctrl_since: float = 0.0
        self._ctrl_retries: int = 0

    # ------------------------------------------------------------------ #
    # Read-only state introspection
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> SessionState:
        """Current lifecycle state (read-only)."""
        return self._state

    @property
    def is_established(self) -> bool:
        """``True`` while the session is in the ESTABLISHED state."""
        return self._state is SessionState.ESTABLISHED

    @property
    def is_closed(self) -> bool:
        """``True`` while the session is in the CLOSED state."""
        return self._state is SessionState.CLOSED

    # ------------------------------------------------------------------ #
    # Internal packet factory
    # ------------------------------------------------------------------ #

    def _make(
        self,
        ptype: PacketType,
        *,
        seq: int = 0,
        ack: int = 0,
        flags: Flags = Flags.NONE,
    ) -> Packet:
        """Build a control packet for this session (stream_id/window = 0)."""
        return Packet(
            type=ptype,
            session_id=self.session_id,
            stream_id=0,
            seq=seq,
            ack=ack,
            window=0,
            flags=flags,
        )

    # ------------------------------------------------------------------ #
    # Control-packet retransmission bookkeeping (M3-T04)
    # ------------------------------------------------------------------ #

    def _arm_pending(self, pkt: Packet, now: float) -> None:
        """Record ``pkt`` as the outstanding control packet awaiting a reply."""
        self._pending_ctrl = pkt
        self._pending_ctrl_since = now
        self._ctrl_retries = 0

    def _clear_pending(self) -> None:
        """Drop the outstanding control packet (its reply arrived / done)."""
        self._pending_ctrl = None
        self._pending_ctrl_since = 0.0
        self._ctrl_retries = 0

    # ------------------------------------------------------------------ #
    # Inputs
    # ------------------------------------------------------------------ #

    def connect(self, now: float) -> Output:
        """Initiator-only: begin the handshake (CLOSED -> SYN_SENT).

        Emits a SYN carrying ``seq=isn`` and the proposed ``session_id``.
        No-op (empty Output) if not an initiator or not in CLOSED.
        """
        if not self.is_initiator or self._state is not SessionState.CLOSED:
            return Output()

        self._state = SessionState.SYN_SENT
        pkt = self._make(PacketType.SYN, seq=self.isn, ack=0, flags=Flags.SYN)
        self.last_send = now
        self._arm_pending(pkt, now)
        return Output(packets=[pkt])

    def close(self, now: float) -> Output:
        """Local graceful close initiation (active close).

        * ESTABLISHED -> FIN_WAIT, emitting our FIN.
        * Any other state (including CLOSE_WAIT, where our FIN was already sent
          automatically on receiving the peer's FIN) is a no-op -- close is
          idempotent and never emits a duplicate FIN.
        """
        if self._state is not SessionState.ESTABLISHED:
            return Output()

        self._state = SessionState.FIN_WAIT
        pkt = self._make(PacketType.FIN, seq=self.seq, ack=0, flags=Flags.FIN)
        self.last_send = now
        self._arm_pending(pkt, now)
        return Output(packets=[pkt])

    def note_data_activity(self, now: float) -> None:
        """Refresh the idle timer from data-plane traffic (M7-T03).

        The session driver calls this whenever it observes data-plane activity
        (e.g. a DATA/ACK/NACK frame handed to the ARQ layer) so that an
        otherwise-quiet-on-the-control-plane but actively-transferring session
        does not trip the idle timeout. Updates :attr:`last_recv` while the
        session is in an active state -- ESTABLISHED or a close already in
        progress (FIN_WAIT/CLOSE_WAIT), where data may still drain. Performs no
        state transition and produces no packets (no return value); states where
        no traffic is expected (CLOSED and the handshake states) are ignored.
        """
        if self._state in (
            SessionState.ESTABLISHED,
            SessionState.FIN_WAIT,
            SessionState.CLOSE_WAIT,
        ):
            self.last_recv = now

    def on_packet(self, pkt: Packet, now: float) -> Output:
        """Process an arriving packet: transition + reply + events.

        Packets whose ``session_id`` does not match the agreed session are
        dropped (empty Output) -- except a SYN arriving while still CLOSED on a
        responder, which establishes the agreed id. Packets that are
        meaningless in the current state are safely ignored.
        """
        responder_initial = (
            self._state is SessionState.CLOSED
            and not self.is_initiator
            and pkt.type is PacketType.SYN
        )
        if not responder_initial and pkt.session_id != self.session_id:
            return Output()  # dropped: wrong session

        # A valid (accepted) frame refreshes the idle timer.
        self.last_recv = now

        state = self._state
        ptype = pkt.type

        # --- Handshake ------------------------------------------------ #
        if state is SessionState.CLOSED and responder_initial:
            # Responder adopts the proposed session id and replies SYN_ACK.
            self.session_id = pkt.session_id
            self._state = SessionState.SYN_RCVD
            reply = self._make(
                PacketType.SYN_ACK,
                seq=self.isn,
                ack=pkt.seq + 1,
                flags=Flags.SYN | Flags.ACK,
            )
            self.last_send = now
            self._arm_pending(reply, now)
            return Output(packets=[reply])

        if state is SessionState.SYN_SENT and ptype is PacketType.SYN_ACK:
            # Initiator completes the handshake: ACK + ESTABLISHED. The SYN_ACK
            # is the reply to our SYN -> clear the outstanding control packet.
            self._clear_pending()
            self._state = SessionState.ESTABLISHED
            self.seq = self.isn + 1
            reply = self._make(
                PacketType.ACK,
                seq=self.seq,
                ack=pkt.seq + 1,
                flags=Flags.ACK,
            )
            self.last_send = now
            return Output(
                packets=[reply], events=[SessionEvent.ESTABLISHED]
            )

        if state is SessionState.SYN_RCVD and ptype is PacketType.ACK:
            # Responder completes the handshake. The ACK is the reply to our
            # SYN_ACK -> clear the outstanding control packet.
            self._clear_pending()
            self._state = SessionState.ESTABLISHED
            self.seq = self.isn + 1
            return Output(events=[SessionEvent.ESTABLISHED])

        if state is SessionState.ESTABLISHED and ptype is PacketType.SYN_ACK:
            # Our handshake-completing ACK was lost: the responder, still in
            # SYN_RCVD, retransmits its SYN_ACK. The ACK is the one control
            # packet we never arm for retransmission (we move straight to
            # ESTABLISHED), so we recover it here by re-sending the ACK in
            # response to each duplicate SYN_ACK -- standard TCP behaviour. No
            # state change, no duplicate ESTABLISHED event.
            reply = self._make(
                PacketType.ACK,
                seq=self.seq,
                ack=pkt.seq + 1,
                flags=Flags.ACK,
            )
            self.last_send = now
            return Output(packets=[reply])

        # --- Graceful close ------------------------------------------- #
        if state is SessionState.ESTABLISHED and ptype is PacketType.FIN:
            # Passive side: the peer initiated close. Auto-tear-down both
            # directions -- ACK the peer's FIN *and* send our own FIN at once --
            # then enter CLOSE_WAIT to await the FIN_ACK for our FIN. This makes
            # a single active close() drive both peers to CLOSED without the
            # passive application having to call close() itself.
            self._state = SessionState.CLOSE_WAIT
            self._peer_fin_acked = True
            fin_ack = self._make(
                PacketType.FIN_ACK, seq=self.seq, ack=pkt.seq + 1,
                flags=Flags.FIN | Flags.ACK,
            )
            own_fin = self._make(
                PacketType.FIN, seq=self.seq, ack=0, flags=Flags.FIN
            )
            self.last_send = now
            # Our auto-sent FIN awaits a FIN_ACK -> track it for retransmission.
            self._arm_pending(own_fin, now)
            return Output(
                packets=[fin_ack, own_fin],
                events=[SessionEvent.PEER_CLOSED],
            )

        if state is SessionState.FIN_WAIT and ptype is PacketType.FIN_ACK:
            # Our FIN was acknowledged. Close only once we've also acked the
            # peer's FIN (handled below); otherwise keep waiting for it.
            self._clear_pending()
            self._fin_acked = True
            if self._peer_fin_acked:
                self._state = SessionState.CLOSED
                return Output(events=[SessionEvent.CLOSED])
            return Output()

        if state is SessionState.FIN_WAIT and ptype is PacketType.FIN:
            # The peer is also closing (auto-close or simultaneous close): ack
            # its FIN. Close once our own FIN has been acked too.
            self._peer_fin_acked = True
            reply = self._make(
                PacketType.FIN_ACK, seq=self.seq, ack=pkt.seq + 1,
                flags=Flags.FIN | Flags.ACK,
            )
            self.last_send = now
            out = Output(packets=[reply])
            if self._fin_acked:
                self._state = SessionState.CLOSED
                out.events.append(SessionEvent.CLOSED)
            return out

        if state is SessionState.CLOSE_WAIT and ptype is PacketType.FIN_ACK:
            # Passive side: the FIN_ACK for our auto-sent FIN arrived. Both
            # directions are now torn down.
            self._clear_pending()
            self._fin_acked = True
            self._state = SessionState.CLOSED
            return Output(events=[SessionEvent.CLOSED])

        # Anything else (e.g. HEARTBEAT, duplicate/out-of-state control
        # packets) is accepted for the idle timer but produces no action.
        return Output()

    def on_tick(self, now: float) -> Output:
        """Evaluate timers: heartbeats, idle timeout, and control retransmits.

        * Idle timeout (active states only): if ``now - last_recv >=
          idle_timeout`` the session transitions to CLOSED. Checked first so a
          dead link never heartbeats. The event is termination-aware: a close
          already in progress (FIN_WAIT / CLOSE_WAIT) yields ``CLOSED`` (the
          graceful close is treated as completed once the peer goes silent),
          while genuinely active states (ESTABLISHED) yield ``TIMED_OUT``.
        * Control-packet retransmission (M3-T04): if a control packet is still
          outstanding and ``now - _pending_ctrl_since >= control_rto``, the
          retry limit is enforced first -- if ``_ctrl_retries`` already exceeds
          ``max_control_retries`` the session is aborted (CONNECT_FAILED while
          establishing in SYN_SENT/SYN_RCVD, otherwise TIMED_OUT) and the
          pending packet cleared. Otherwise the packet is retransmitted,
          ``_ctrl_retries`` is incremented, ``_pending_ctrl_since`` reset to
          ``now``, and ``last_send`` refreshed (``last_recv`` is left untouched).
        * Heartbeat (ESTABLISHED only): if ``now - last_send >=
          heartbeat_interval`` emit a HEARTBEAT and refresh ``last_send``.
        """
        out = Output()

        # Active (non-CLOSED) sessions can time out.
        if self._state is not SessionState.CLOSED:
            if (
                self.last_recv is not None
                and now - self.last_recv >= self.idle_timeout
            ):
                # A timeout while a close is already in progress (FIN_WAIT /
                # CLOSE_WAIT) is treated as the graceful close having completed
                # -- the peer simply went silent after we started tearing down
                # -- so we surface CLOSED, not TIMED_OUT. Only genuinely active
                # states (ESTABLISHED, handshake) report TIMED_OUT.
                closing = self._state in (
                    SessionState.FIN_WAIT,
                    SessionState.CLOSE_WAIT,
                )
                self._state = SessionState.CLOSED
                self._clear_pending()
                out.events.append(
                    SessionEvent.CLOSED if closing else SessionEvent.TIMED_OUT
                )
                return out

        # Control-packet retransmission / give-up.
        if (
            self._pending_ctrl is not None
            and now - self._pending_ctrl_since >= self.control_rto
        ):
            if self._ctrl_retries > self.max_control_retries:
                # Give up: the retry budget is exhausted. An unanswered SYN /
                # SYN_ACK means the connection never came up (CONNECT_FAILED);
                # an unanswered FIN means the close never completed (TIMED_OUT).
                establishing = self._state in (
                    SessionState.SYN_SENT,
                    SessionState.SYN_RCVD,
                )
                self._state = SessionState.CLOSED
                self._clear_pending()
                out.events.append(
                    SessionEvent.CONNECT_FAILED
                    if establishing
                    else SessionEvent.TIMED_OUT
                )
                return out

            # Retransmit the outstanding control packet.
            self._ctrl_retries += 1
            self._pending_ctrl_since = now
            self.last_send = now
            out.packets.append(self._pending_ctrl)
            return out

        if self._state is SessionState.ESTABLISHED:
            if (
                self.last_send is None
                or now - self.last_send >= self.heartbeat_interval
            ):
                hb = self._make(PacketType.HEARTBEAT, seq=self.seq)
                self.last_send = now
                out.packets.append(hb)

        return out
