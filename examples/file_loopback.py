"""Reliable PhotonTCP *file transfer* over a lossy in-memory loopback (M6-T05).

Where ``reliable_loopback.py`` shows a raw byte stream surviving frame loss and
``chat_loopback.py`` drives the message-oriented chat application, this example
exercises the **single-file transfer** application -- :class:`FileSender` /
:class:`FileReceiver` -- end to end over a noisy optical link. Using **virtual
time only** (a deterministic :class:`ManualClock` per peer; never a real
``sleep``) it walks an initiator and a responder through

    1. a 3-way handshake over a lossy channel until *both* peers reach
       ``ESTABLISHED`` (dropped SYN / SYN_ACK frames are retransmitted as each
       pump round advances virtual time past the control RTO);
    2. a reliable, chunked file transfer: the sender ``start()``s an OFFER for an
       in-memory "file", the receiver auto-accepts, and every CHUNK + the final
       DONE are reassembled and SHA-256-verified despite ongoing frame loss --
       progress is reported at the 25 / 50 / 75 / 100 % marks as the bytes land;
    3. the application-level completion handshake: the sender reaches COMPLETE
       only after the receiver's ACK arrives (which proves every byte was
       delivered + verified), the received bytes are compared against the
       original (MATCH) and their SHA-256 digests confirmed equal;
    4. a graceful close (the sender closes only *after* COMPLETE, per the
       file-transfer flush-on-close contract) until *both* peers reach
       ``CLOSED``.

Each peer reads time only through its own injected :class:`ManualClock`, and both
clocks are advanced in lockstep once per pump round, so loss recovery is fully
deterministic and reproducible (the link is seeded). No wall-clock time is ever
consulted.

Run it from the repository root::

    python examples/file_loopback.py

Output is intentionally English-only so it stays readable on Windows consoles
regardless of code page.
"""

from __future__ import annotations

import hashlib
import os
import sys

# Allow running directly from the repository root without installation.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from photontcp.app import FileReceiver, FileSender, FileTransferState  # noqa: E402
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
#: Chosen together with the caps below so the transfer always converges to MATCH
#: while still forcing a healthy amount of CHUNK/ACK retransmission.
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
MAX_TRANSFER_ROUNDS = 600
MAX_CLOSE_ROUNDS = 200

#: The in-memory "file" to transfer. 256-byte ramp repeated 12 times = 3072 B,
#: comfortably larger than the CHUNK size below so it is split into several CHUNK
#: frames, each of which may be lost and retransmitted independently.
FILE_NAME = "demo.bin"
FILE_DATA = bytes(range(256)) * 12

#: Payload bytes per CHUNK frame. Small enough to span multiple chunks (and thus
#: multiple transport DATA packets) so loss recovery is genuinely exercised.
CHUNK_SIZE = 256


def _advance_both(clock_a: ManualClock, clock_b: ManualClock, dt: float) -> None:
    """Advance both peers' virtual clocks in lockstep (no real sleep)."""
    clock_a.advance(dt)
    clock_b.advance(dt)


def _log_states(initiator: Session, responder: Session, note: str = "") -> None:
    """Print both peers' current lifecycle states on one line."""
    suffix = f"   ({note})" if note else ""
    print(
        f"    initiator={initiator.state.name:<12} "
        f"responder={responder.state.name:<12}{suffix}"
    )


def _drive_handshake(
    initiator: Session,
    responder: Session,
    clock_i: ManualClock,
    clock_r: ManualClock,
) -> int:
    """Pump both peers until both are ESTABLISHED. Return the round count.

    Returns ``-1`` if the cap is hit without converging.
    """
    for rnd in range(1, MAX_HANDSHAKE_ROUNDS + 1):
        # Advance virtual time first so any expired control RTO fires this round,
        # retransmitting whichever handshake frame the link just dropped.
        _advance_both(clock_i, clock_r, ROUND_DT)
        initiator.pump()
        responder.pump()
        if initiator.is_established and responder.is_established:
            return rnd
    return -1


def _drive_transfer(
    sender: FileSender,
    receiver: FileReceiver,
    clock_i: ManualClock,
    clock_r: ManualClock,
) -> int:
    """Pump both endpoints until the transfer completes. Return the round count.

    Prints progress as the receiver crosses the 25 / 50 / 75 / 100 % marks.
    Returns ``-1`` if the cap is hit without both endpoints reaching COMPLETE,
    or if either endpoint fails (REJECT / NACK / SHA mismatch).
    """
    milestones = [0.25, 0.50, 0.75, 1.00]
    next_mark = 0  # index of the next progress milestone to report

    for rnd in range(1, MAX_TRANSFER_ROUNDS + 1):
        _advance_both(clock_i, clock_r, ROUND_DT)
        # Pump both directions: OFFER/CHUNK/DONE flow sender->receiver,
        # ACCEPT/ACK back the other way (all over the same reliable stream).
        sender.pump()
        receiver.pump()

        if sender.is_failed or receiver.is_failed:
            print(
                f"    round {rnd:>3}: ERROR transfer FAILED "
                f"(sender={sender.state.name}, receiver={receiver.state.name})"
            )
            return -1

        # Report each progress milestone the receiver has reached this round.
        while next_mark < len(milestones) and receiver.progress >= milestones[next_mark]:
            pct = int(milestones[next_mark] * 100)
            recv_bytes = int(round(receiver.progress * len(FILE_DATA)))
            print(
                f"    round {rnd:>3}: t={clock_i.now():6.1f}s  "
                f"progress {pct:>3}%  (~{recv_bytes:>4}/{len(FILE_DATA)} bytes)"
            )
            next_mark += 1

        if sender.is_complete and receiver.is_complete:
            return rnd
    return -1


def _drive_close(
    initiator: Session,
    responder: Session,
    clock_i: ManualClock,
    clock_r: ManualClock,
) -> int:
    """Pump both peers until both are CLOSED. Return the round count.

    Returns ``-1`` if the cap is hit without converging.
    """
    for rnd in range(1, MAX_CLOSE_ROUNDS + 1):
        _advance_both(clock_i, clock_r, ROUND_DT)
        initiator.pump()
        responder.pump()
        if initiator.is_closed and responder.is_closed:
            return rnd
    return -1


def main() -> int:
    """Drive the demo and return a process exit code (0 = success)."""
    digest = hashlib.sha256(FILE_DATA).hexdigest()
    print("=" * 68)
    print("PhotonTCP file-transfer loopback demo (virtual time, lossy link)")
    print(
        f"link: loss={LOSS:.0%}  seed={SEED}  "
        f"file={FILE_NAME!r} {len(FILE_DATA)} bytes  chunk={CHUNK_SIZE} bytes"
    )
    print(f"source SHA-256: {digest}")
    print("=" * 68)

    # --- Setup: lossy loopback pair + a ManualClock per peer. ------------- #
    clock_i = ManualClock()
    clock_r = ManualClock()
    chan_a, chan_b = LoopbackChannel.pair(seed=SEED, loss=LOSS)

    initiator = Session(
        chan_a,
        clock_i,
        is_initiator=True,
        session_id=1,
        isn=1000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )
    responder = Session(
        chan_b,
        clock_r,
        is_initiator=False,
        session_id=0,
        isn=5000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )

    sender = FileSender(
        initiator,
        clock_i,
        name=FILE_NAME,
        data=FILE_DATA,
        chunk_size=CHUNK_SIZE,
    )
    receiver = FileReceiver(responder, clock_r, auto_accept=True)

    # --- [1] Handshake over the lossy link. ------------------------------- #
    print("\n[1] Handshake over lossy link (SYN/SYN_ACK retransmitted on loss)")
    print("    initiator.connect() -> sending SYN")
    initiator.connect()
    _log_states(initiator, responder, "after connect()")

    hs_rounds = _drive_handshake(initiator, responder, clock_i, clock_r)
    _log_states(initiator, responder, "after handshake")
    if hs_rounds < 0:
        print("    ERROR: handshake did not complete within the round cap")
        return 1
    print(f"    => both peers ESTABLISHED after {hs_rounds} pump round(s)")

    # --- [2] Reliable chunked file transfer over the lossy link. ---------- #
    print("\n[2] File transfer (OFFER/CHUNK/DONE + ACCEPT/ACK over reliable stream)")
    print(f"    sender.start() -> OFFER {FILE_NAME!r} ({len(FILE_DATA)} bytes)")
    sender.start()

    tx_rounds = _drive_transfer(sender, receiver, clock_i, clock_r)
    if tx_rounds < 0:
        print(
            f"    ERROR: transfer did not complete within the round cap "
            f"(sender={sender.state.name}, receiver={receiver.state.name})"
        )
        return 1
    print(f"    => transfer COMPLETE after {tx_rounds} pump round(s)")

    # --- [3] Integrity + completion-handshake verification. --------------- #
    print("\n[3] Integrity check (completion handshake guarantees full delivery)")
    received = receiver.file_bytes
    if received is None:
        print("    ERROR: receiver reports COMPLETE but file_bytes is None")
        return 1

    bytes_match = received == FILE_DATA
    recv_digest = hashlib.sha256(received).hexdigest()
    sha_match = recv_digest == digest
    print(f"    received name : {receiver.name!r}")
    print(f"    received size : {len(received)} bytes")
    print(f"    received SHA  : {recv_digest}")
    print(f"    byte compare  : {'MATCH' if bytes_match else 'MISMATCH'}")
    print(f"    SHA-256       : {'MATCH' if sha_match else 'MISMATCH'}")
    print(f"    verified flag : {receiver.verified}")
    if not (bytes_match and sha_match and receiver.verified):
        print("    ERROR: received file differs from the original")
        return 1
    if not (sender.state is FileTransferState.COMPLETE):
        print("    ERROR: sender did not reach COMPLETE (ACK not received)")
        return 1
    print("    => file delivered intact and verified over the lossy link")

    # --- [4] Graceful close (sender closes only after COMPLETE). ---------- #
    print("\n[4] Graceful close over lossy link (FIN/FIN_ACK retransmitted)")
    print("    initiator.close() -> sending FIN")
    initiator.close()
    _log_states(initiator, responder, "after close()")

    close_rounds = _drive_close(initiator, responder, clock_i, clock_r)
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
        f"file transferred over lossy link | {len(received)} bytes | "
        "SHA-256 MATCH | both CLOSED"
    )
    print("-" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
