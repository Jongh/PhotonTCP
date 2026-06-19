"""Reliable PhotonTCP transfer over a *lossy* in-memory loopback (M3-T09).

Where ``session_loopback.py`` demonstrates the lifecycle over a perfect link,
this example shows PhotonTCP's reliability machinery working through a noisy
optical link that drops ~30% of every frame. Using **virtual time only** (a
deterministic :class:`ManualClock`; no real ``sleep``) it drives two
:class:`Session` peers through

    1. a 3-way handshake over a 30%-loss channel: dropped SYN / SYN_ACK frames
       force control-path retransmissions (each round advances virtual time past
       the control RTO so the retransmit timers fire) until *both* peers reach
       ``ESTABLISHED``;
    2. a reliable data transfer larger than a single DATA payload: one peer
       ``send()``s a multi-chunk "file", and despite ongoing frame loss the ARQ
       engine retransmits lost DATA/ACK frames until the receiver reassembles a
       byte stream that exactly MATCHes the original;
    3. a graceful close, again surviving loss, until both peers reach ``CLOSED``.

Every pump round advances the :class:`ManualClock`, so loss recovery happens in
virtual time without ever sleeping. The link is seeded for reproducibility.

Run it from the repository root::

    python examples/reliable_loopback.py

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

# --- Link / timing parameters. -------------------------------------------- #

#: Per-frame drop probability of the simulated lossy optical link.
LOSS = 0.3
#: Fixed RNG seed so the loss pattern (and therefore the whole run) replays
#: identically every time. Chosen to exercise plenty of retransmission (several
#: lost DATA frames recovered during the transfer) while still converging well
#: within the round caps below.
SEED = 23

#: Virtual seconds advanced per pump round. Must exceed the control RTO (0.5s)
#: so unacknowledged SYN / SYN_ACK / FIN frames are retransmitted each round,
#: and must let the ARQ retransmission timers fire for the data path too.
ROUND_DT = 0.6

#: Heartbeat / idle-timeout window. The idle timeout is generous so the link is
#: not declared dead while we wait out a burst of consecutive losses.
HEARTBEAT_INTERVAL = 5.0
IDLE_TIMEOUT = 120.0

# Hard upper bounds so no pump loop can ever run forever even under heavy loss.
MAX_HANDSHAKE_ROUNDS = 200
MAX_TRANSFER_ROUNDS = 400
MAX_CLOSE_ROUNDS = 200

#: The "file" to transfer. Deliberately far longer than the ARQ max payload
#: (default 200 bytes) so it is split into several DATA packets, each of which
#: may be lost and retransmitted independently.
PAYLOAD = ("PhotonTCP over a lossy optical link! " * 20).encode("ascii")


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


def _drive_handshake(initiator: Session, responder: Session, clock: ManualClock) -> int:
    """Pump both peers until both are ESTABLISHED. Return the round count.

    Returns ``-1`` if the cap is hit without converging.
    """
    for rnd in range(1, MAX_HANDSHAKE_ROUNDS + 1):
        # Advance virtual time first so any expired control RTO fires this round,
        # retransmitting whichever handshake frame the link just dropped.
        clock.advance(ROUND_DT)
        ev_i = initiator.pump()
        ev_r = responder.pump()
        if ev_i or ev_r:
            print(
                f"    round {rnd:>3}: t={clock.now():6.1f}s  "
                f"initiator=[{_fmt_events(ev_i)}]  "
                f"responder=[{_fmt_events(ev_r)}]"
            )
        if initiator.is_established and responder.is_established:
            return rnd
    return -1


def _drive_transfer(
    sender: Session, receiver: Session, clock: ManualClock, total: int
) -> tuple[bytes, int]:
    """Pump both peers until ``receiver`` has reassembled ``total`` bytes.

    Returns ``(received_bytes, rounds)``; ``rounds`` is the number of pump
    rounds spent (a proxy for how much retransmission the loss forced).
    """
    received = bytearray()
    for rnd in range(1, MAX_TRANSFER_ROUNDS + 1):
        clock.advance(ROUND_DT)
        # Pump both directions: DATA flows sender->receiver, ACK/NACK back.
        sender.pump()
        receiver.pump()
        chunks = receiver.recv()
        if chunks:
            received.extend(b"".join(chunks))
            print(
                f"    round {rnd:>3}: t={clock.now():6.1f}s  "
                f"received {len(received):>4}/{total} bytes"
            )
        if len(received) >= total:
            return bytes(received), rnd
    return bytes(received), -1


def _drive_close(initiator: Session, responder: Session, clock: ManualClock) -> int:
    """Pump both peers until both are CLOSED. Return the round count.

    Returns ``-1`` if the cap is hit without converging.
    """
    for rnd in range(1, MAX_CLOSE_ROUNDS + 1):
        clock.advance(ROUND_DT)
        ev_i = initiator.pump()
        ev_r = responder.pump()
        if ev_i or ev_r:
            print(
                f"    round {rnd:>3}: t={clock.now():6.1f}s  "
                f"initiator=[{_fmt_events(ev_i)}]  "
                f"responder=[{_fmt_events(ev_r)}]"
            )
        if initiator.is_closed and responder.is_closed:
            return rnd
    return -1


def main() -> int:
    """Drive the demo and return a process exit code (0 = success)."""
    print("=" * 68)
    print("PhotonTCP reliable loopback demo (virtual time, lossy link)")
    print(f"link: loss={LOSS:.0%}  seed={SEED}  payload={len(PAYLOAD)} bytes")
    print("=" * 68)

    # --- Setup: lossy loopback pair + a shared manual clock. -------------- #
    clock = ManualClock()
    chan_a, chan_b = LoopbackChannel.pair(seed=SEED, loss=LOSS)

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

    # --- [1] Handshake over the lossy link. ------------------------------- #
    print("\n[1] Handshake over lossy link (SYN/SYN_ACK retransmitted on loss)")
    print("    initiator.connect() -> sending SYN")
    initiator.connect()
    _log_states(initiator, responder, "after connect()")

    hs_rounds = _drive_handshake(initiator, responder, clock)
    _log_states(initiator, responder, "after handshake")
    if hs_rounds < 0:
        print("    ERROR: handshake did not complete within the round cap")
        return 1
    print(f"    => both peers ESTABLISHED after {hs_rounds} pump round(s)")

    # --- [2] Reliable multi-chunk transfer over the lossy link. ----------- #
    print("\n[2] Reliable transfer (DATA/ACK retransmitted until reassembled)")
    print(f"    initiator.send({len(PAYLOAD)} bytes)")
    initiator.send(PAYLOAD)

    received, tx_rounds = _drive_transfer(
        initiator, responder, clock, len(PAYLOAD)
    )
    if tx_rounds < 0:
        print(
            f"    ERROR: only {len(received)}/{len(PAYLOAD)} bytes arrived "
            "within the round cap"
        )
        return 1

    match = received == PAYLOAD
    print(
        f"    reassembled {len(received)} bytes in {tx_rounds} pump round(s)"
    )
    print(f"    integrity: {'MATCH' if match else 'MISMATCH'}")
    if not match:
        print("    ERROR: reassembled bytes differ from the original payload")
        return 1
    print("    => lossless delivery achieved over a 30%-loss link")

    # --- [3] Graceful close over the lossy link. -------------------------- #
    print("\n[3] Graceful close over lossy link (FIN/FIN_ACK retransmitted)")
    print("    initiator.close() -> sending FIN")
    ev = initiator.close()
    if ev:
        print(f"    initiator close() events=[{_fmt_events(ev)}]")
    _log_states(initiator, responder, "after close()")

    close_rounds = _drive_close(initiator, responder, clock)
    _log_states(initiator, responder, "after close handshake")
    if (
        close_rounds < 0
        or initiator.state is not SessionState.CLOSED
        or responder.state is not SessionState.CLOSED
    ):
        print("    ERROR: both peers did not reach CLOSED")
        return 1
    print(f"    => both peers CLOSED after {close_rounds} pump round(s)")

    # --- Summary. --------------------------------------------------------- #
    print("\n" + "-" * 68)
    print(
        f"Summary: loss={LOSS:.0%} link | handshake={hs_rounds} rounds | "
        f"transfer={tx_rounds} rounds | close={close_rounds} rounds"
    )
    print(
        f"         {len(PAYLOAD)} bytes delivered intact (MATCH); "
        "both peers CLOSED"
    )
    print("-" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
