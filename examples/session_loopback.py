"""End-to-end PhotonTCP session demo over a lossless in-memory loopback.

This example exercises the full M2 session lifecycle with **virtual time only**
(no real ``sleep``): a deterministic :class:`ManualClock` plus a lossless
:meth:`LoopbackChannel.pair` link drive two :class:`Session` peers through

    1. the 3-way handshake until both reach ``ESTABLISHED``;
    2. an idle "established" phase where advancing virtual time past the
       heartbeat interval causes HEARTBEAT frames to flow, keeping both peers
       alive (the link survives because heartbeats refresh the idle timeout);
    3. a graceful, symmetric auto-close: one side calls ``close()`` and pumping
       both peers alone is enough to bring *both* to ``CLOSED`` (the passive side
       observes ``PEER_CLOSED`` first, then ``CLOSED``).

Run it from the repository root::

    python examples/session_loopback.py

Output is intentionally English-only so it stays readable on Windows consoles
regardless of code page.
"""

from __future__ import annotations

import os
import sys

# Allow running directly from the repository root without installation.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from photontcp.channel.loopback import LoopbackChannel  # noqa: E402
from photontcp.session import (  # noqa: E402
    ManualClock,
    Session,
    SessionEvent,
    SessionState,
)

# Timing parameters chosen so the demo is short and easy to follow.
HEARTBEAT_INTERVAL = 1.0
IDLE_TIMEOUT = 3.0

# Hard upper bounds so no pump loop can ever run forever.
MAX_HANDSHAKE_ROUNDS = 20
MAX_CLOSE_ROUNDS = 20


def _fmt_events(events: list[SessionEvent]) -> str:
    """Render a list of session events compactly for logging."""
    if not events:
        return "-"
    return ", ".join(e.name for e in events)


def _log_states(initiator: Session, responder: Session, note: str = "") -> None:
    """Print both peers' current lifecycle states on one line."""
    suffix = f"   ({note})" if note else ""
    print(
        f"    initiator={initiator.state.name:<12} "
        f"responder={responder.state.name:<12}{suffix}"
    )


def main() -> int:
    """Drive the demo and return a process exit code (0 = success)."""
    print("=" * 64)
    print("PhotonTCP session loopback demo (virtual time, lossless link)")
    print("=" * 64)

    # --- Setup: lossless loopback pair + a shared manual clock. ----------
    clock = ManualClock()
    chan_a, chan_b = LoopbackChannel.pair(seed=0)  # lossless: all noise == 0

    initiator = Session(
        chan_a,
        clock,
        is_initiator=True,
        session_id=1,
        isn=1000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )
    responder = Session(
        chan_b,
        clock,
        is_initiator=False,
        session_id=0,
        isn=5000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )

    print("\n[1] Handshake")
    print("    initiator.connect() -> sending SYN")
    initiator.connect()
    _log_states(initiator, responder, "after connect()")

    # Alternate pumps until both sides are ESTABLISHED (or we hit the cap).
    established_ok = False
    for rnd in range(MAX_HANDSHAKE_ROUNDS):
        ev_i = initiator.pump()
        ev_r = responder.pump()
        if ev_i or ev_r:
            print(
                f"    round {rnd}: initiator events=[{_fmt_events(ev_i)}] "
                f"responder events=[{_fmt_events(ev_r)}]"
            )
        if initiator.is_established and responder.is_established:
            established_ok = True
            break

    _log_states(initiator, responder, "after handshake")
    if not established_ok:
        print("    ERROR: handshake did not complete")
        return 1
    print("    => both peers ESTABLISHED")

    # --- Heartbeat phase: advance virtual time, watch the link survive. --
    print("\n[2] Heartbeat (virtual time only, no real sleep)")
    for beat in range(1, 3):
        # Move past the heartbeat interval so each side decides to emit a
        # HEARTBEAT, then let both peers pump to send and receive it.
        clock.advance(HEARTBEAT_INTERVAL + 0.01)
        initiator.pump()  # sender emits HEARTBEAT to channel
        responder.pump()  # responder receives it AND emits its own HEARTBEAT
        initiator.pump()  # initiator receives responder's HEARTBEAT
        alive = initiator.is_established and responder.is_established
        print(
            f"    beat {beat}: clock={clock.now():.2f}s  heartbeat exchanged  "
            f"both_established={alive}"
        )
        if not alive:
            print("    ERROR: link dropped during heartbeat phase")
            return 1
    print("    => heartbeats kept both peers alive past idle timeout window")

    # --- Graceful symmetric close. ---------------------------------------
    print("\n[3] Graceful close (symmetric auto-close)")
    print("    initiator.close() -> sending FIN")
    ev = initiator.close()
    if ev:
        print(f"    initiator close() events=[{_fmt_events(ev)}]")
    _log_states(initiator, responder, "after close()")

    closed_ok = False
    for rnd in range(MAX_CLOSE_ROUNDS):
        ev_i = initiator.pump()
        ev_r = responder.pump()
        if ev_i or ev_r:
            print(
                f"    round {rnd}: initiator events=[{_fmt_events(ev_i)}] "
                f"responder events=[{_fmt_events(ev_r)}]"
            )
        if initiator.is_closed and responder.is_closed:
            closed_ok = True
            break

    _log_states(initiator, responder, "after close handshake")
    if not (
        closed_ok
        and initiator.state is SessionState.CLOSED
        and responder.state is SessionState.CLOSED
    ):
        print("    ERROR: both peers did not reach CLOSED")
        return 1
    print("    => both peers CLOSED")

    # --- Summary. --------------------------------------------------------
    print("\n" + "-" * 64)
    print("Summary: session established -> heartbeat -> graceful close "
          "(both CLOSED)")
    print("-" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
