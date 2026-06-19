"""Heartbeat and idle-timeout tests for the synchronous Session driver (M2-T06).

These tests exercise the heartbeat / idle-timeout machinery of two real
:class:`~photontcp.session.Session` peers over a *lossless*
:class:`~photontcp.channel.loopback.LoopbackChannel` pair, driven entirely by a
deterministic :class:`~photontcp.session.ManualClock`. Real sleeping is never
used: virtual time only moves via :meth:`ManualClock.advance` (or
:meth:`ManualClock.set`), so heartbeats and timeouts are fully reproducible.

Covered:

1. Heartbeat emission keeps the peer alive: after both peers are ESTABLISHED,
   advancing virtual time past ``heartbeat_interval`` and pumping the sender
   transmits a HEARTBEAT that the receiver consumes (staying ESTABLISHED with a
   refreshed idle timer) -- exercised both by inspecting the wire frame and by
   confirming periodic exchange prevents an idle timeout.
2. Idle timeout: with one side's pump stopped (so it receives nothing), letting
   only that side's virtual clock cross ``idle_timeout`` and pumping it yields
   ``SessionEvent.TIMED_OUT`` and a transition to CLOSED.
3. Boundary: while heartbeats are exchanged on schedule (both sides pumped with
   modest time steps) no ``TIMED_OUT`` ever surfaces.
4. Light coverage of :class:`ManualClock` itself (start/now/advance/set and the
   negative-input ``ValueError`` guards).

Clock topology note: each :class:`Session` is injected with its *own* clock, so
the timeout scenario can advance a single side's virtual time in isolation
(advancing a shared clock would move both peers' notion of "now" together,
making "one side receives nothing while time passes" awkward to express). The
handshake itself is driven with both clocks held at ``0`` so no spurious
heartbeat interferes before ESTABLISHED.

No source under ``photontcp/`` is modified.
"""

from __future__ import annotations

import pytest

from photontcp.channel.loopback import LoopbackChannel
from photontcp.packet.header import Packet
from photontcp.packet.types import PacketType
from photontcp.session import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_IDLE_TIMEOUT,
    ManualClock,
    Session,
    SessionEvent,
    SessionState,
)

# Finite hard cap on every progression loop: a stuck handshake/exchange fails
# fast via assertion instead of hanging the suite.
MAX_ITERS = 200


def _make_pair(
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    a_isn: int = 1000,
    b_isn: int = 5000,
    session_id: int = 1,
    seed: int = 0,
):
    """Create two cross-wired sessions, each with its *own* ManualClock.

    Returns ``(a, b, ch_a, ch_b, clock_a, clock_b)`` where ``a`` is the
    initiator and ``b`` the passive responder. Separate clocks let a single
    side's virtual time be advanced in isolation (needed for the idle-timeout
    scenario). Both clocks start at ``0.0``.
    """
    ch_a, ch_b = LoopbackChannel.pair(seed=seed)
    clock_a = ManualClock()
    clock_b = ManualClock()
    a = Session(
        ch_a,
        clock_a,
        is_initiator=True,
        session_id=session_id,
        isn=a_isn,
        heartbeat_interval=heartbeat_interval,
        idle_timeout=idle_timeout,
    )
    b = Session(
        ch_b,
        clock_b,
        is_initiator=False,
        session_id=session_id,
        isn=b_isn,
        heartbeat_interval=heartbeat_interval,
        idle_timeout=idle_timeout,
    )
    return a, b, ch_a, ch_b, clock_a, clock_b


def _pump_both_until(a, b, pred, *, max_iters=MAX_ITERS):
    """Alternately pump ``a`` then ``b`` until ``pred()`` or the cap is hit.

    Returns ``(events_a, events_b)``. The predicate is checked up front and
    after each full iteration so an already-satisfied condition costs no pumps.
    Exhausting the cap is a test failure (never an infinite loop).
    """
    events_a: list[SessionEvent] = []
    events_b: list[SessionEvent] = []
    for _ in range(max_iters):
        if pred():
            break
        events_a.extend(a.pump())
        events_b.extend(b.pump())
    else:
        pytest.fail(
            f"progression did not converge within {max_iters} iterations "
            f"(a={a.state}, b={b.state})"
        )
    return events_a, events_b


def _establish(a, b):
    """Drive the 3-way handshake to completion; return ``(events_a, events_b)``.

    Both clocks are at ``0`` here, so ``now - last_send`` stays ``0`` and no
    heartbeat fires during the handshake.
    """
    a.connect()
    events = _pump_both_until(
        a, b, lambda: a.is_established and b.is_established
    )
    assert a.is_established and b.is_established
    return events


def _drain_frames(channel, *, max_frames: int = 100) -> list[Packet]:
    """Pop and unpack every frame currently waiting in ``channel``'s inbox.

    Bounded by ``max_frames`` so a runaway producer cannot loop forever.
    """
    packets: list[Packet] = []
    for _ in range(max_frames):
        frame = channel.recv_frame(timeout=0)
        if frame is None:
            break
        packets.append(Packet.unpack(frame))
    return packets


# --------------------------------------------------------------------------- #
# 1. Heartbeat emission
# --------------------------------------------------------------------------- #


def test_heartbeat_frame_emitted_after_interval():
    """Advancing past ``heartbeat_interval`` and pumping emits a HEARTBEAT."""
    a, b, ch_a, ch_b, clock_a, clock_b = _make_pair()
    _establish(a, b)

    # Drain any handshake leftovers so we observe only new wire traffic.
    _drain_frames(ch_a)
    _drain_frames(ch_b)

    # Advance only the initiator's clock past the heartbeat interval and pump
    # it: on_tick must emit exactly one HEARTBEAT onto the wire toward b.
    clock_a.advance(DEFAULT_HEARTBEAT_INTERVAL)
    events_a = a.pump()

    assert events_a == []  # heartbeat emission surfaces no lifecycle event
    assert a.state is SessionState.ESTABLISHED

    # The frame b will receive (b's inbox == a's outbox) is a HEARTBEAT.
    delivered = _drain_frames(ch_b)
    assert len(delivered) == 1
    assert delivered[0].type is PacketType.HEARTBEAT


def test_heartbeat_received_keeps_peer_established_and_resets_idle():
    """A received HEARTBEAT refreshes the peer's idle timer (no timeout)."""
    a, b, ch_a, ch_b, clock_a, clock_b = _make_pair()
    _establish(a, b)

    # Capture b's idle baseline established during the handshake.
    last_recv_before = b._machine.last_recv
    assert last_recv_before is not None

    # a sends a heartbeat at virtual time = heartbeat_interval.
    clock_a.advance(DEFAULT_HEARTBEAT_INTERVAL)
    a.pump()

    # b's clock advances to *just under* its idle timeout, then it pumps and
    # consumes the heartbeat -- which must refresh last_recv to b's "now".
    hb_arrival = DEFAULT_IDLE_TIMEOUT - 0.5
    clock_b.set(hb_arrival)
    events_b = b.pump()

    assert events_b == []  # a plain heartbeat surfaces no event
    assert b.is_established
    assert b.state is SessionState.ESTABLISHED
    # Idle timer was reset to the moment the heartbeat was accepted.
    assert b._machine.last_recv == hb_arrival
    assert b._machine.last_recv > last_recv_before


# --------------------------------------------------------------------------- #
# 2. Idle timeout
# --------------------------------------------------------------------------- #


def test_idle_timeout_when_no_heartbeats_received():
    """Crossing ``idle_timeout`` with no inbound frame -> TIMED_OUT + CLOSED."""
    a, b, ch_a, ch_b, clock_a, clock_b = _make_pair()
    _establish(a, b)

    # b's heartbeats are stopped (b is simply never pumped to send) and, more
    # importantly, b receives nothing. Advance ONLY b's clock past the idle
    # timeout; a's clock is frozen so a never produces traffic either.
    clock_b.advance(DEFAULT_IDLE_TIMEOUT)
    events_b = b.pump()

    assert SessionEvent.TIMED_OUT in events_b
    assert b.is_closed
    assert b.state is SessionState.CLOSED


def test_no_timeout_just_below_idle_threshold():
    """Just under ``idle_timeout`` the session stays alive (boundary, low)."""
    a, b, ch_a, ch_b, clock_a, clock_b = _make_pair()
    _establish(a, b)

    clock_b.advance(DEFAULT_IDLE_TIMEOUT - 0.001)
    events_b = b.pump()

    assert SessionEvent.TIMED_OUT not in events_b
    assert b.is_established
    assert b.state is SessionState.ESTABLISHED


def test_timeout_exactly_at_idle_threshold():
    """At exactly ``idle_timeout`` the inclusive ``>=`` check fires (boundary)."""
    a, b, ch_a, ch_b, clock_a, clock_b = _make_pair()
    _establish(a, b)

    # last_recv was set at virtual time 0 during the handshake; advancing to
    # exactly idle_timeout makes now - last_recv == idle_timeout (>= fires).
    last_recv = b._machine.last_recv
    assert last_recv == 0.0
    clock_b.set(DEFAULT_IDLE_TIMEOUT)
    events_b = b.pump()

    assert SessionEvent.TIMED_OUT in events_b
    assert b.state is SessionState.CLOSED


# --------------------------------------------------------------------------- #
# 3. Boundary: healthy heartbeat exchange never times out
# --------------------------------------------------------------------------- #


def test_periodic_heartbeats_prevent_timeout():
    """While both sides exchange heartbeats on schedule, no TIMED_OUT occurs."""
    a, b, ch_a, ch_b, clock_a, clock_b = _make_pair()
    _establish(a, b)

    # Step both clocks in lock-step by the heartbeat interval and pump both,
    # for many intervals spanning well beyond several idle timeouts. Because
    # each pump cycle sends a heartbeat that the peer then consumes (resetting
    # its idle timer), neither side may ever time out.
    steps = 20  # 20 * interval == far more than idle_timeout (finite cap)
    assert steps * DEFAULT_HEARTBEAT_INTERVAL > DEFAULT_IDLE_TIMEOUT
    all_events: list[SessionEvent] = []
    for _ in range(steps):
        clock_a.advance(DEFAULT_HEARTBEAT_INTERVAL)
        clock_b.advance(DEFAULT_HEARTBEAT_INTERVAL)
        # Pump a then b, then a again so each side both emits its heartbeat and
        # consumes the one the peer just produced within the same time step.
        all_events += a.pump()
        all_events += b.pump()
        all_events += a.pump()

    assert SessionEvent.TIMED_OUT not in all_events
    assert a.is_established and a.state is SessionState.ESTABLISHED
    assert b.is_established and b.state is SessionState.ESTABLISHED


# --------------------------------------------------------------------------- #
# 4. ManualClock unit coverage
# --------------------------------------------------------------------------- #


def test_manual_clock_starts_at_zero_by_default():
    clock = ManualClock()
    assert clock.now() == 0.0


def test_manual_clock_honors_start_value():
    clock = ManualClock(start=2.5)
    assert clock.now() == 2.5


def test_manual_clock_advance_accumulates():
    clock = ManualClock()
    clock.advance(1.0)
    clock.advance(0.5)
    assert clock.now() == 1.5


def test_manual_clock_advance_zero_is_allowed():
    clock = ManualClock(start=3.0)
    clock.advance(0.0)
    assert clock.now() == 3.0


def test_manual_clock_set_absolute():
    clock = ManualClock(start=10.0)
    clock.set(4.0)
    assert clock.now() == 4.0


def test_manual_clock_advance_negative_raises():
    clock = ManualClock()
    with pytest.raises(ValueError):
        clock.advance(-1.0)


def test_manual_clock_set_negative_raises():
    clock = ManualClock()
    with pytest.raises(ValueError):
        clock.set(-0.5)
