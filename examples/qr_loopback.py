"""PhotonTCP chat carried over *real QR images* via the optical loopback (M5-T08).

Unlike ``chat_loopback.py`` -- which moves raw packet bytes through an in-memory
queue -- this example exercises the genuine M5 optical codec path. The
:class:`ImageLoopbackChannel` **encodes every frame into a QR-code image with
:func:`photontcp.qr.encode.encode_frame` and decodes it back** with
:func:`photontcp.qr.decode.decode_frame` on the receiving side, exactly as a true
camera link would. The transit medium between the two peers is a ``numpy`` image,
not bytes.

Using **virtual time only** (a deterministic :class:`ManualClock` per peer; never
a real ``sleep``) it drives two :class:`ChatSession` endpoints through

    1. a 3-way handshake over the QR-image link until *both* peers reach
       ``ESTABLISHED`` -- every SYN / SYN_ACK / ACK control frame is rasterized to
       a QR code and read back;
    2. a short interleaved conversation: each side ``send_message``\\ s a couple of
       tiny text lines, every one of which travels as one or more QR images and is
       reassembled by the peer in order;
    3. a consistency check -- each side's received messages are compared, in
       order, against what the other side sent (MATCH) -- followed by a graceful
       close until *both* peers reach ``CLOSED``.

To make the QR round-trip explicit and undeniable, the demo also encodes one
sample payload directly with :func:`encode_frame` and prints the resulting QR
image's pixel shape and module count, proving the data really is being carried as
QR pictures.

QR decoding is comparatively slow, so the payloads and message counts here are
deliberately tiny and every pump loop is hard-capped; the whole run finishes in a
few seconds. The link is run loss-free (the QR codec fidelity, not loss recovery,
is the point of this example) and is seeded for reproducibility.

Run it from the repository root::

    python examples/qr_loopback.py

Output is intentionally English-only so it stays readable on Windows consoles
regardless of code page.
"""

from __future__ import annotations

import os
import sys
import time

# Allow running directly from the repository root without installation.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# --- [0] Hard dependency check before importing anything heavy. ------------- #
# The QR codec needs segno (encode) and OpenCV (decode); fail gracefully if the
# user's environment is missing either, with a clear pip hint.
try:
    import segno  # noqa: F401
    import cv2  # noqa: F401
except ImportError:
    print(
        "This example requires segno and opencv-python.\n"
        "    pip install segno opencv-python"
    )
    raise SystemExit(0)

from photontcp.app import ChatMessage, ChatSession  # noqa: E402
from photontcp.channel import ImageLoopbackChannel  # noqa: E402
from photontcp.qr.encode import encode_frame  # noqa: E402
from photontcp.session import (  # noqa: E402
    ManualClock,
    Session,
    SessionState,
)

# --- Link / timing parameters. -------------------------------------------- #

#: Loss-free QR-image link. The point of this example is QR codec fidelity, not
#: ARQ loss recovery (that is covered by reliable_loopback.py / chat_loopback.py).
LOSS = 0.0
#: Fixed RNG seed so the run replays identically every time.
SEED = 5
#: Smaller QR modules keep decoding fast while staying well above the camera
#: link's resolution floor.
SCALE = 6

#: Virtual seconds advanced per pump round. Must exceed the control RTO so any
#: unacknowledged handshake/close frame would be retransmitted each round.
ROUND_DT = 0.6

#: Heartbeat / idle-timeout window.
HEARTBEAT_INTERVAL = 5.0
IDLE_TIMEOUT = 120.0

# Hard upper bounds so no pump loop can ever run forever.
MAX_HANDSHAKE_ROUNDS = 100
MAX_CHAT_ROUNDS = 100
MAX_CLOSE_ROUNDS = 100

#: Tiny message sets -- QR decode is slow, so keep both bodies and counts small.
MESSAGES_A = ["hi over QR", "frame two"]
MESSAGES_B = ["got your QR", "B done"]


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


def _show_qr_proof() -> None:
    """Encode a sample payload to a QR image and print its dimensions.

    This makes the "carried as a real QR picture" claim concrete: we render a
    representative payload with the same encoder the channel uses and report the
    image's pixel shape plus the inferred QR module grid size.
    """
    sample = b"PhotonTCP/QR sample frame"
    img = encode_frame(sample, scale=SCALE)
    h, w = img.shape
    # Inverse of encode_frame's geometry: H == (modules + 2*border) * scale,
    # with the default quiet-zone border of 4 modules.
    border = 4
    modules = h // SCALE - 2 * border
    print(
        f"    encode_frame({len(sample)} bytes) -> QR image shape={img.shape} "
        f"(uint8), ~{modules}x{modules} modules @ scale={SCALE}px"
    )


def _drive_handshake(
    a: ChatSession,
    b: ChatSession,
    clock_a: ManualClock,
    clock_b: ManualClock,
) -> int:
    """Pump both peers until both are ESTABLISHED. Return the round count (-1 = cap)."""
    for rnd in range(1, MAX_HANDSHAKE_ROUNDS + 1):
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

    Returns ``True`` if both sides received the expected number of messages
    within the round cap.
    """
    next_a = 0
    next_b = 0
    for _rnd in range(1, MAX_CHAT_ROUNDS + 1):
        _advance_both(clock_a, clock_b, ROUND_DT)

        if next_a < len(MESSAGES_A):
            mid = a.send_message(MESSAGES_A[next_a])
            print(f"    A sends msg#{mid} as QR: {MESSAGES_A[next_a]}")
            next_a += 1
        if next_b < len(MESSAGES_B):
            mid = b.send_message(MESSAGES_B[next_b])
            print(f"    B sends msg#{mid} as QR: {MESSAGES_B[next_b]}")
            next_b += 1

        got_b = b.pump()  # messages A->B land here
        got_a = a.pump()  # messages B->A land here
        _print_arrivals("[A->B]", got_b)
        _print_arrivals("[B->A]", got_a)

        if len(a.received) >= len(MESSAGES_B) and len(b.received) >= len(MESSAGES_A):
            return True
    return False


def _drive_close(
    a: ChatSession,
    b: ChatSession,
    clock_a: ManualClock,
    clock_b: ManualClock,
) -> int:
    """Pump both peers until both are CLOSED. Return the round count (-1 = cap)."""
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
    started = time.perf_counter()

    print("=" * 68)
    print("PhotonTCP QR loopback demo (virtual time, real QR encode/decode)")
    print(
        f"link: ImageLoopbackChannel  loss={LOSS:.0%}  seed={SEED}  scale={SCALE}px  "
        f"A->{len(MESSAGES_A)} msgs  B->{len(MESSAGES_B)} msgs"
    )
    print("=" * 68)

    # --- [0] Show that data really becomes a QR picture. ------------------- #
    print("\n[0] QR encoding proof (data -> real QR image)")
    _show_qr_proof()

    # --- Setup: QR-image loopback pair + a ManualClock per peer. ---------- #
    clock_a = ManualClock()
    clock_b = ManualClock()
    chan_a, chan_b = ImageLoopbackChannel.pair(seed=SEED, loss=LOSS, scale=SCALE)

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

    # --- [1] Handshake over the QR-image link. ---------------------------- #
    print("\n[1] Handshake over the QR-image link")
    print("    A.connect() -> SYN rendered as a QR image")
    chat_a.connect()
    _log_states(chat_a, chat_b, "after connect()")

    hs_rounds = _drive_handshake(chat_a, chat_b, clock_a, clock_b)
    _log_states(chat_a, chat_b, "after handshake")
    if hs_rounds < 0:
        print("    ERROR: handshake did not complete within the round cap")
        return 1
    print(f"    => both peers ESTABLISHED after {hs_rounds} pump round(s)")

    # --- [2] Bidirectional chat carried over QR images. ------------------- #
    print("\n[2] Bidirectional chat (every frame is QR-encoded then decoded)")
    if not _drive_chat(chat_a, chat_b, clock_a, clock_b):
        print(
            f"    ERROR: chat did not converge "
            f"(A got {len(chat_a.received)}/{len(MESSAGES_B)}, "
            f"B got {len(chat_b.received)}/{len(MESSAGES_A)})"
        )
        return 1

    # --- [3] Consistency check: received == sent, in order. --------------- #
    print("\n[3] Consistency check (QR round-trip preserved the data)")
    b_recv = _texts(chat_b.received)
    a_recv = _texts(chat_a.received)
    a_to_b_match = b_recv == MESSAGES_A
    b_to_a_match = a_recv == MESSAGES_B
    print(
        f"    A->B: B received {len(b_recv)}/{len(MESSAGES_A)} "
        f"-> {'MATCH' if a_to_b_match else 'MISMATCH'}"
    )
    print(
        f"    B->A: A received {len(a_recv)}/{len(MESSAGES_B)} "
        f"-> {'MATCH' if b_to_a_match else 'MISMATCH'}"
    )
    if not (a_to_b_match and b_to_a_match):
        print("    ERROR: messages recovered from QR differ from what was sent")
        return 1
    print("    => every message survived the QR encode/decode intact (MATCH)")

    # --- [4] Graceful close over the QR-image link. ----------------------- #
    print("\n[4] Graceful close over the QR-image link")
    print("    A.close() -> FIN rendered as a QR image")
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
    elapsed = time.perf_counter() - started
    print("\n" + "-" * 68)
    print(
        f"Summary: QR-image link | handshake={hs_rounds} rounds | "
        f"close={close_rounds} rounds | wall={elapsed:.2f}s"
    )
    print("data carried over real QR images | MATCH | both CLOSED")
    print("-" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
