"""Bidirectional PhotonTCP *chat* demo over a lossy in-memory loopback (M4-T08).

Where ``reliable_loopback.py`` shows a raw byte transfer surviving frame loss,
this example drives the **application layer** -- :class:`ChatSession` -- end to
end over a noisy optical link. Using **virtual time only** (a deterministic
:class:`ManualClock` per peer; never a real ``sleep``) it walks two chat
endpoints through

    1. a 3-way handshake over a lossy channel until *both* peers reach
       ``ESTABLISHED`` (dropped SYN / SYN_ACK frames are retransmitted as each
       pump round advances virtual time past the control RTO);
    2. an interleaved conversation: side A and side B each ``send_message`` a
       series of text lines, and despite ongoing frame loss every message is
       reassembled by the peer **in order** and printed as it arrives
       (e.g. ``[A->B] msg#1: hello from A``);
    3. a final consistency check -- each side's received messages are compared,
       in order, against what the other side sent (MATCH) -- followed by a
       graceful close until *both* peers reach ``CLOSED``.

Each peer reads time only through its own injected :class:`ManualClock`, and
both clocks are advanced in lockstep once per pump round, so loss recovery and
message timestamps are fully deterministic and reproducible (the link is
seeded). No wall-clock time is ever consulted.

Run it from the repository root::

    python examples/chat_loopback.py

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

from photontcp.app import ChatMessage, ChatSession  # noqa: E402
from photontcp.channel.loopback import LoopbackChannel  # noqa: E402
from photontcp.session import (  # noqa: E402
    ManualClock,
    Session,
    SessionState,
)

# --- Link / timing parameters. -------------------------------------------- #

#: Per-frame drop probability of the simulated lossy optical link.
LOSS = 0.2
#: Fixed RNG seed so the loss pattern (and the whole run) replays identically.
#: Chosen together with the caps below so the conversation always converges to
#: MATCH while still forcing a healthy amount of retransmission.
SEED = 7

#: Virtual seconds advanced per pump round. Must exceed the control RTO so
#: unacknowledged handshake/close frames are retransmitted each round, and lets
#: the ARQ retransmission timers fire for the data path too.
ROUND_DT = 0.6

#: Heartbeat / idle-timeout window. The idle timeout is generous so the link is
#: not declared dead while we wait out a burst of consecutive losses.
HEARTBEAT_INTERVAL = 5.0
IDLE_TIMEOUT = 240.0

# Hard upper bounds so no pump loop can ever run forever even under heavy loss.
MAX_HANDSHAKE_ROUNDS = 200
MAX_CHAT_ROUNDS = 400
MAX_CLOSE_ROUNDS = 200

#: Messages each side sends, interleaved A, B, A, B, ... over the conversation.
MESSAGES_A = [
    "hello from A",
    "how is the link holding up?",
    "sending a third line",
    "A signing off soon",
]
MESSAGES_B = [
    "hi A, B here",
    "link is lossy but reliable",
    "got all of yours so far",
    "B acknowledges, ready to close",
]


def _advance_both(clock_a: ManualClock, clock_b: ManualClock, dt: float) -> None:
    """Advance both peers' virtual clocks in lockstep (no real sleep)."""
    clock_a.advance(dt)
    clock_b.advance(dt)


def _log_states(a: ChatSession, b: ChatSession, note: str = "") -> None:
    """Print both peers' current lifecycle states on one line."""
    suffix = f"   ({note})" if note else ""
    print(f"    A={a.state.name:<12} B={b.state.name:<12}{suffix}")


def _print_arrivals(tag: str, msgs: list[ChatMessage]) -> None:
    """Print each newly arrived chat message with a direction tag."""
    for m in msgs:
        print(f"    {tag} msg#{m.msg_id}: {m.text}")


def _drive_handshake(
    a: ChatSession,
    b: ChatSession,
    clock_a: ManualClock,
    clock_b: ManualClock,
) -> int:
    """Pump both peers until both are ESTABLISHED. Return the round count.

    Returns ``-1`` if the cap is hit without converging.
    """
    for rnd in range(1, MAX_HANDSHAKE_ROUNDS + 1):
        # Advance virtual time first so any expired control RTO fires this
        # round, retransmitting whichever handshake frame the link dropped.
        _advance_both(clock_a, clock_b, ROUND_DT)
        a.pump()
        b.pump()
        if a.is_established and b.is_established:
            return rnd
    return -1


def _drive_chat(
    a: ChatSession,
    b: ChatSession,
    clock_a: ManualClock,
    clock_b: ManualClock,
) -> bool:
    """Interleave sends from both sides and pump until everything arrives.

    Each side enqueues its messages one per "turn"; every pump round advances
    virtual time (so lost DATA/ACK frames are retransmitted) and prints any
    messages that completed this round. Returns ``True`` if both sides received
    the expected number of messages within the round cap.
    """
    next_a = 0  # index of A's next message to send
    next_b = 0  # index of B's next message to send

    for rnd in range(1, MAX_CHAT_ROUNDS + 1):
        _advance_both(clock_a, clock_b, ROUND_DT)

        # Interleave one new send from each side per round (until exhausted).
        if next_a < len(MESSAGES_A):
            mid = a.send_message(MESSAGES_A[next_a])
            print(f"    A sends msg#{mid}: {MESSAGES_A[next_a]}")
            next_a += 1
        if next_b < len(MESSAGES_B):
            mid = b.send_message(MESSAGES_B[next_b])
            print(f"    B sends msg#{mid}: {MESSAGES_B[next_b]}")
            next_b += 1

        # Pump both directions; print whatever arrived in order.
        got_b = b.pump()  # messages A->B land here
        got_a = a.pump()  # messages B->A land here
        _print_arrivals("[A->B]", got_b)
        _print_arrivals("[B->A]", got_a)

        a_done = len(a.received) >= len(MESSAGES_B)
        b_done = len(b.received) >= len(MESSAGES_A)
        if a_done and b_done:
            return True
    return False


def _drive_close(
    a: ChatSession,
    b: ChatSession,
    clock_a: ManualClock,
    clock_b: ManualClock,
) -> int:
    """Pump both peers until both are CLOSED. Return the round count.

    Returns ``-1`` if the cap is hit without converging.
    """
    for rnd in range(1, MAX_CLOSE_ROUNDS + 1):
        _advance_both(clock_a, clock_b, ROUND_DT)
        a.pump()
        b.pump()
        if a.is_closed and b.is_closed:
            return rnd
    return -1


def _texts(msgs: list[ChatMessage]) -> list[str]:
    """Extract message bodies in order for comparison."""
    return [m.text for m in msgs]


def main() -> int:
    """Drive the demo and return a process exit code (0 = success)."""
    print("=" * 68)
    print("PhotonTCP chat loopback demo (virtual time, lossy link)")
    print(
        f"link: loss={LOSS:.0%}  seed={SEED}  "
        f"A->{len(MESSAGES_A)} msgs  B->{len(MESSAGES_B)} msgs"
    )
    print("=" * 68)

    # --- Setup: lossy loopback pair + a ManualClock per peer. ------------- #
    clock_a = ManualClock()
    clock_b = ManualClock()
    chan_a, chan_b = LoopbackChannel.pair(seed=SEED, loss=LOSS)

    session_a = Session(
        chan_a,
        clock_a,
        is_initiator=True,
        session_id=1,
        isn=1000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )
    session_b = Session(
        chan_b,
        clock_b,
        is_initiator=False,
        session_id=0,
        isn=5000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )

    chat_a = ChatSession(session_a, clock_a)
    chat_b = ChatSession(session_b, clock_b)

    # --- [1] Handshake over the lossy link. ------------------------------- #
    print("\n[1] Handshake over lossy link")
    print("    A.connect() -> sending SYN")
    chat_a.connect()
    _log_states(chat_a, chat_b, "after connect()")

    hs_rounds = _drive_handshake(chat_a, chat_b, clock_a, clock_b)
    _log_states(chat_a, chat_b, "after handshake")
    if hs_rounds < 0:
        print("    ERROR: handshake did not complete within the round cap")
        return 1
    print(f"    => both peers ESTABLISHED after {hs_rounds} pump round(s)")

    # --- [2] Interleaved bidirectional chat over the lossy link. ---------- #
    print("\n[2] Bidirectional chat (messages retransmitted until delivered)")
    chat_ok = _drive_chat(chat_a, chat_b, clock_a, clock_b)
    if not chat_ok:
        print(
            f"    ERROR: chat did not converge "
            f"(A got {len(chat_a.received)}/{len(MESSAGES_B)}, "
            f"B got {len(chat_b.received)}/{len(MESSAGES_A)})"
        )
        return 1

    # --- [3] Consistency check: received == sent, in order. --------------- #
    print("\n[3] Consistency check")
    b_recv_texts = _texts(chat_b.received)
    a_recv_texts = _texts(chat_a.received)
    a_to_b_match = b_recv_texts == MESSAGES_A
    b_to_a_match = a_recv_texts == MESSAGES_B
    print(
        f"    A->B: B received {len(b_recv_texts)}/{len(MESSAGES_A)} "
        f"-> {'MATCH' if a_to_b_match else 'MISMATCH'}"
    )
    print(
        f"    B->A: A received {len(a_recv_texts)}/{len(MESSAGES_B)} "
        f"-> {'MATCH' if b_to_a_match else 'MISMATCH'}"
    )
    if not (a_to_b_match and b_to_a_match):
        print("    ERROR: received messages differ from what was sent")
        return 1
    print("    => every message delivered intact and in order (MATCH)")

    # --- [4] Graceful close over the lossy link. -------------------------- #
    print("\n[4] Graceful close over lossy link")
    print("    A.close() -> sending FIN")
    chat_a.close()
    _log_states(chat_a, chat_b, "after close()")

    close_rounds = _drive_close(chat_a, chat_b, clock_a, clock_b)
    _log_states(chat_a, chat_b, "after close handshake")
    if (
        close_rounds < 0
        or chat_a.state is not SessionState.CLOSED
        or chat_b.state is not SessionState.CLOSED
    ):
        print("    ERROR: both peers did not reach CLOSED")
        return 1
    print(f"    => both peers CLOSED after {close_rounds} pump round(s)")

    # --- Summary. --------------------------------------------------------- #
    print("\n" + "-" * 68)
    print(
        f"Summary: loss={LOSS:.0%} link | handshake={hs_rounds} rounds | "
        f"close={close_rounds} rounds"
    )
    print(
        f"         A->B {len(MESSAGES_A)} msgs MATCH, "
        f"B->A {len(MESSAGES_B)} msgs MATCH; both peers CLOSED"
    )
    print("-" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
