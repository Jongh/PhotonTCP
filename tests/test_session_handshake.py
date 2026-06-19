"""Handshake and graceful-teardown tests for the synchronous Session driver.

These tests (M2-T05) exercise two real :class:`~photontcp.session.Session`
peers over a *lossless* :class:`~photontcp.channel.loopback.LoopbackChannel`
pair driven by a deterministic :class:`~photontcp.session.ManualClock`. The
peers are advanced by alternately calling ``pump()`` on each side; every
progression loop is bounded by a finite iteration cap so a missing transition
can never hang the suite.

Covered:

1. 3-way handshake -> both peers ESTABLISHED, both surface ``ESTABLISHED``.
2. Graceful active close -> a single ``a.close()`` drives *both* peers to
   CLOSED (symmetric auto-close): the passive side ``b`` sees ``PEER_CLOSED``
   then ``CLOSED``; the active side ``a`` sees ``CLOSED``.
3. Simultaneous close -> both sides ``close()``, both reach CLOSED.
4. session_id agreement -> the responder adopts the initiator's proposed id.

No source under ``photontcp/`` is modified; the tests adapt to the established
teardown semantics (passive side need not call ``close()`` itself).
"""

from __future__ import annotations

import pytest

from photontcp.channel.loopback import LoopbackChannel
from photontcp.session import (
    ManualClock,
    Session,
    SessionEvent,
    SessionState,
)

# A finite hard cap on every progression loop so a stuck handshake/teardown
# fails fast (via assertion) instead of looping forever.
MAX_ITERS = 200


def _make_pair(
    *,
    a_session_id: int = 1,
    b_session_id: int = 1,
    a_isn: int = 1000,
    b_isn: int = 5000,
    seed: int = 0,
):
    """Create two cross-wired sessions sharing one ManualClock.

    Returns ``(a, b, ch_a, ch_b, clock)`` where ``a`` is the initiator and
    ``b`` is the passive responder. ``heartbeat_interval`` is left at its
    default; because the ManualClock never advances during these scenarios,
    ``now - last_send`` stays at ``0`` once the first packet is sent, so no
    spurious heartbeats interfere with the handshake or teardown.
    """
    ch_a, ch_b = LoopbackChannel.pair(seed=seed)
    clock = ManualClock()
    a = Session(
        ch_a,
        clock,
        is_initiator=True,
        session_id=a_session_id,
        isn=a_isn,
    )
    b = Session(
        ch_b,
        clock,
        is_initiator=False,
        session_id=b_session_id,
        isn=b_isn,
    )
    return a, b, ch_a, ch_b, clock


def _pump_both_until(a, b, pred, *, max_iters=MAX_ITERS):
    """Alternately pump ``a`` and ``b`` until ``pred()`` or the cap is hit.

    Returns ``(events_a, events_b)``: the events surfaced by each side across
    all iterations, in order. A single iteration pumps ``a`` then ``b`` so each
    side gets a chance to process whatever its peer just transmitted. The
    predicate is checked after each full iteration (and once up front) so an
    already-satisfied condition costs no pumps.
    """
    events_a: list[SessionEvent] = []
    events_b: list[SessionEvent] = []
    for _ in range(max_iters):
        if pred():
            break
        events_a.extend(a.pump())
        events_b.extend(b.pump())
    else:
        # Loop exhausted without the predicate ever becoming true.
        pytest.fail(
            f"progression did not converge within {max_iters} iterations "
            f"(a={a.state}, b={b.state})"
        )
    return events_a, events_b


def _establish(a, b):
    """Drive the handshake to completion; return ``(events_a, events_b)``."""
    a.connect()
    return _pump_both_until(
        a, b, lambda: a.is_established and b.is_established
    )


# --------------------------------------------------------------------------- #
# 1. Handshake
# --------------------------------------------------------------------------- #


def test_handshake_establishes_both_peers():
    a, b, *_ = _make_pair()

    events_a, events_b = _establish(a, b)

    assert a.is_established
    assert b.is_established
    assert a.state is SessionState.ESTABLISHED
    assert b.state is SessionState.ESTABLISHED
    assert SessionEvent.ESTABLISHED in events_a
    assert SessionEvent.ESTABLISHED in events_b


# --------------------------------------------------------------------------- #
# 2. Graceful active close (symmetric auto-close)
# --------------------------------------------------------------------------- #


def test_active_close_tears_down_both_peers():
    a, b, *_ = _make_pair()
    _establish(a, b)

    # Only the active side closes; auto-close must drive both to CLOSED.
    close_events_a = a.close()

    events_a, events_b = _pump_both_until(
        a, b, lambda: a.is_closed and b.is_closed
    )
    events_a = close_events_a + events_a

    assert a.is_closed
    assert b.is_closed
    assert a.state is SessionState.CLOSED
    assert b.state is SessionState.CLOSED

    # Passive side observes the peer-initiated close, then full close.
    assert SessionEvent.PEER_CLOSED in events_b
    assert SessionEvent.CLOSED in events_b
    assert events_b.index(SessionEvent.PEER_CLOSED) < events_b.index(
        SessionEvent.CLOSED
    )

    # Active side observes full close.
    assert SessionEvent.CLOSED in events_a


# --------------------------------------------------------------------------- #
# 3. Simultaneous close
# --------------------------------------------------------------------------- #


def test_simultaneous_close_tears_down_both_peers():
    a, b, *_ = _make_pair()
    _establish(a, b)

    events_a = a.close()
    events_b = b.close()

    pumped_a, pumped_b = _pump_both_until(
        a, b, lambda: a.is_closed and b.is_closed
    )
    events_a += pumped_a
    events_b += pumped_b

    assert a.is_closed
    assert b.is_closed
    assert a.state is SessionState.CLOSED
    assert b.state is SessionState.CLOSED
    assert SessionEvent.CLOSED in events_a
    assert SessionEvent.CLOSED in events_b


# --------------------------------------------------------------------------- #
# 4. session_id agreement
# --------------------------------------------------------------------------- #


def test_responder_adopts_initiator_session_id():
    # Give the two sides *different* initial session ids: the responder must
    # adopt the initiator's proposed id during the handshake for it to succeed.
    proposed_id = 4242
    a, b, *_ = _make_pair(a_session_id=proposed_id, b_session_id=9999)

    # Sanity: ids genuinely differ before the handshake.
    assert a._machine.session_id != b._machine.session_id

    _establish(a, b)

    assert a.is_established
    assert b.is_established
    # The agreed session id is the initiator's proposal on both peers.
    assert a._machine.session_id == proposed_id
    assert b._machine.session_id == proposed_id
