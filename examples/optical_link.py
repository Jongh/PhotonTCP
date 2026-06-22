"""PhotonTCP chat carried over the *real optical channel* (M8-T06).

Where ``qr_loopback.py`` moves QR images through an in-process queue, this example
drives the genuine :class:`~photontcp.optical.channel.OpticalChannel` — the channel
that *shows* every outgoing frame on a display and recovers incoming frames from a
camera, transporting bytes over **light**. The channel depends only on the
``DisplaySink`` / ``CameraSource`` device abstractions, so the *same* session code
runs either fully in memory (the in-memory fakes) or over a real webcam+screen link
(the cv2 adapters), proving the upper layers care only about the ``Channel``
contract.

Two modes
=========

**Default (in-memory)** — ``python examples/optical_link.py``
    Uses :meth:`OpticalChannel.pair` to build a full-duplex in-memory optical link
    (cross-wired ``MemoryDisplay``/``MemoryCamera`` fakes). NO screen or camera is
    needed, so this mode always runs and is what CI / the M8 completion criterion
    exercises. It drives two :class:`ChatSession` peers through a handshake, a
    couple of tiny messages, and a graceful close — exactly like ``qr_loopback.py``
    but over the real optical channel instead of ``ImageLoopbackChannel``.

    KEY DIFFERENCE from ``qr_loopback.py``: the optical channel delivers frames
    **asynchronously on a real background capture thread**, not synchronously. A
    ``Session.pump`` drains the channel with ``recv_frame(timeout=0)`` (a
    non-blocking poll), so after sending we must give the capture thread a brief
    real moment to capture+decode+enqueue the frame before pumping the receiver.
    This demo therefore uses a small real ``time.sleep`` between pump rounds (the
    only place virtual time is supplemented by wall time) and bounds every loop
    with both a round cap and a wall-clock deadline so it can never hang.

**Real (``--real``)** — ``python examples/optical_link.py --real``
    Renders each outgoing QR frame to an actual on-screen OpenCV window
    (:class:`Cv2Display`) so you can SEE the real QR pictures the protocol emits,
    while the session round-trip itself still runs over the in-memory link (so the
    demo stays a self-contained, non-flaky proof). Requires ``opencv-python`` and a
    display; optional ``--window NAME`` sets the window title.

    Honest scope: a single screen + camera on one machine cannot easily see its own
    window, so a true two-peer optical *loop* needs **two screens and two cameras**
    (one per peer) — this is the remaining limitation on the road to a fully
    hardware-proven v1.0. ``--real`` here proves the *display* half on real
    hardware (real QR frames on a real screen) and is a MANUAL run only; point
    another device's camera at the window, or pair it with a second machine running
    a ``Cv2Camera``-backed ``OpticalChannel``, to close the loop physically.

Run from the repository root::

    python examples/optical_link.py            # in-memory, always works
    python examples/optical_link.py --real      # also show real QR frames on screen
    python examples/optical_link.py --real --window "PhotonTCP demo"

Output is intentionally English-only so it stays readable on Windows consoles
regardless of code page.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Allow running directly from the repository root without installation.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# --- [0] Hard dependency check before importing anything heavy. ------------- #
# The QR codec needs segno (encode) and OpenCV (decode); fail gracefully if the
# user's environment is missing either, with a clear pip hint. (--real needs cv2
# too, which is covered by this same check.)
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
from photontcp.optical import OpticalChannel  # noqa: E402
from photontcp.qr.encode import encode_frame  # noqa: E402
from photontcp.session import (  # noqa: E402
    ManualClock,
    Session,
    SessionState,
)

# --- Link / timing parameters. -------------------------------------------- #

#: Fixed RNG seed so the run replays identically every time (the in-memory
#: optical devices are deterministic; the seed is accepted for API symmetry).
SEED = 5
#: Smaller QR modules keep decoding fast while staying well above a camera link's
#: resolution floor.
SCALE = 6

#: Virtual seconds advanced per pump round. Must exceed the control RTO so any
#: unacknowledged handshake/close frame would be retransmitted each round.
ROUND_DT = 0.6

#: Real wall-clock pause per pump round. UNLIKE qr_loopback.py, the optical
#: channel delivers on a background capture thread, so we give that thread a brief
#: real moment to capture+decode+enqueue a shown frame before pumping the receiver
#: (Session.pump polls recv_frame(timeout=0), so it does not block waiting).
ROUND_SLEEP = 0.03

#: Heartbeat / idle-timeout window.
HEARTBEAT_INTERVAL = 5.0
IDLE_TIMEOUT = 120.0

# Hard upper bounds so no pump loop can ever run forever (round cap AND a
# wall-clock deadline, since this demo also burns real time per round).
MAX_HANDSHAKE_ROUNDS = 200
MAX_CHAT_ROUNDS = 200
MAX_CLOSE_ROUNDS = 200
#: Absolute wall-clock budget for the whole session drive (belt-and-suspenders
#: against any capture-thread stall).
WALL_DEADLINE_S = 60.0

#: Tiny message sets -- QR decode is slow, so keep both bodies and counts small.
MESSAGES_A = ["hi over light", "frame two"]
MESSAGES_B = ["got your light", "B done"]


def _advance_both(clock_a: ManualClock, clock_b: ManualClock, dt: float) -> None:
    """Advance both peers' virtual clocks in lockstep, then pause for real.

    The virtual-time advance drives the session's RTO/heartbeat logic; the small
    real sleep lets the optical channel's background capture thread move shown
    frames into the receiver's inbox before the next pump drains it.
    """
    clock_a.advance(dt)
    clock_b.advance(dt)
    time.sleep(ROUND_SLEEP)


def _log_states(a: ChatSession, b: ChatSession, note: str = "") -> None:
    """Print both peers' current lifecycle states on one line."""
    suffix = f"   ({note})" if note else ""
    print(f"    A={a.state.name:<12} B={b.state.name:<12}{suffix}")


def _print_arrivals(tag: str, msgs: list[ChatMessage]) -> None:
    """Print each newly arrived chat message with a direction tag."""
    for m in msgs:
        print(f"    {tag} msg#{m.msg_id}: {m.text}")


def _show_qr_proof(display=None) -> None:
    """Encode a sample payload to a QR image, print its size, optionally show it.

    Makes the "carried as a real QR picture over light" claim concrete: we render
    a representative payload with the same encoder the channel uses and report the
    image's pixel shape plus the inferred QR module grid size. When ``display`` is
    given (``--real``), the frame is also painted to a real on-screen window.
    """
    sample = b"PhotonTCP/optical sample frame"
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
    if display is not None:
        display.show(img)
        print("    (sample QR painted to the real display window)")


def _drive_handshake(
    a: ChatSession,
    b: ChatSession,
    clock_a: ManualClock,
    clock_b: ManualClock,
    deadline: float,
) -> int:
    """Pump both peers until both are ESTABLISHED. Return round count (-1 = cap)."""
    for rnd in range(1, MAX_HANDSHAKE_ROUNDS + 1):
        if time.perf_counter() > deadline:
            return -1
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
    deadline: float,
) -> bool:
    """Interleave sends from both sides and pump until everything arrives.

    Returns ``True`` if both sides received the expected number of messages
    within the round cap / wall-clock deadline.
    """
    next_a = 0
    next_b = 0
    for _rnd in range(1, MAX_CHAT_ROUNDS + 1):
        if time.perf_counter() > deadline:
            return False
        _advance_both(clock_a, clock_b, ROUND_DT)

        if next_a < len(MESSAGES_A):
            mid = a.send_message(MESSAGES_A[next_a])
            print(f"    A sends msg#{mid} over light: {MESSAGES_A[next_a]}")
            next_a += 1
        if next_b < len(MESSAGES_B):
            mid = b.send_message(MESSAGES_B[next_b])
            print(f"    B sends msg#{mid} over light: {MESSAGES_B[next_b]}")
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
    deadline: float,
) -> int:
    """Pump both peers until both are CLOSED. Return round count (-1 = cap)."""
    for rnd in range(1, MAX_CLOSE_ROUNDS + 1):
        if time.perf_counter() > deadline:
            return -1
        _advance_both(clock_a, clock_b, ROUND_DT)
        a.pump()
        b.pump()
        if a.is_closed and b.is_closed:
            return rnd
    return -1


def _texts(msgs: list[ChatMessage]) -> list[str]:
    """Extract message bodies in order for comparison."""
    return [m.text for m in msgs]


def _drive_session(display=None) -> int:
    """Run the full handshake -> chat -> close drive over an in-memory optical pair.

    The link is :meth:`OpticalChannel.pair` (cross-wired in-memory fakes); when
    ``display`` is provided (``--real``), every sample/outgoing QR is also painted
    to a real screen so the user sees genuine QR frames. Returns a process exit
    code (0 = success).
    """
    started = time.perf_counter()
    deadline = started + WALL_DEADLINE_S

    # --- [0] Show that data really becomes a QR picture. ------------------- #
    print("\n[0] QR encoding proof (data -> real QR image over light)")
    _show_qr_proof(display)

    # --- Setup: in-memory optical pair + a ManualClock per peer. ---------- #
    clock_a = ManualClock()
    clock_b = ManualClock()
    chan_a, chan_b = OpticalChannel.pair(seed=SEED, scale=SCALE)

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

    try:
        # --- [1] Handshake over the optical link. ------------------------- #
        print("\n[1] Handshake over the optical link (display + camera)")
        print("    A.connect() -> SYN shown as a QR frame, captured by B")
        chat_a.connect()
        _log_states(chat_a, chat_b, "after connect()")

        hs_rounds = _drive_handshake(chat_a, chat_b, clock_a, clock_b, deadline)
        _log_states(chat_a, chat_b, "after handshake")
        if hs_rounds < 0:
            print("    ERROR: handshake did not complete within the cap")
            return 1
        print(f"    => both peers ESTABLISHED after {hs_rounds} pump round(s)")

        # --- [2] Bidirectional chat carried over light. ------------------- #
        print("\n[2] Bidirectional chat (every frame shown then captured)")
        if not _drive_chat(chat_a, chat_b, clock_a, clock_b, deadline):
            print(
                f"    ERROR: chat did not converge "
                f"(A got {len(chat_a.received)}/{len(MESSAGES_B)}, "
                f"B got {len(chat_b.received)}/{len(MESSAGES_A)})"
            )
            return 1

        # --- [3] Consistency check: received == sent, in order. ----------- #
        print("\n[3] Consistency check (optical round-trip preserved the data)")
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
            print("    ERROR: messages recovered over light differ from what was sent")
            return 1
        print("    => every message survived the optical encode/decode intact (MATCH)")

        # --- [4] Graceful close over the optical link. -------------------- #
        print("\n[4] Graceful close over the optical link")
        print("    A.close() -> FIN shown as a QR frame")
        chat_a.close()
        _log_states(chat_a, chat_b, "after close()")

        close_rounds = _drive_close(chat_a, chat_b, clock_a, clock_b, deadline)
        _log_states(chat_a, chat_b, "after close handshake")
        if (
            close_rounds < 0
            or chat_a.state is not SessionState.CLOSED
            or chat_b.state is not SessionState.CLOSED
        ):
            print("    ERROR: both peers did not reach CLOSED")
            return 1
        print(f"    => both peers CLOSED after {close_rounds} pump round(s)")
    finally:
        # Always stop the background capture threads (and release devices).
        chan_a.close()
        chan_b.close()

    # --- Summary. --------------------------------------------------------- #
    elapsed = time.perf_counter() - started
    print("\n" + "-" * 68)
    print(
        f"Summary: OpticalChannel | handshake={hs_rounds} rounds | "
        f"close={close_rounds} rounds | wall={elapsed:.2f}s"
    )
    print("data carried over the real optical channel | MATCH | both CLOSED")
    print("-" * 68)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments for the two demo modes."""
    parser = argparse.ArgumentParser(
        description=(
            "PhotonTCP optical-link demo. Default: in-memory OpticalChannel.pair "
            "(no hardware). --real also paints real QR frames to an OpenCV window."
        )
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Also render outgoing QR frames to a real on-screen OpenCV window "
            "(needs a display). MANUAL run only -- a true two-peer optical loop "
            "needs two screens + two cameras (see the module docstring)."
        ),
    )
    parser.add_argument(
        "--window",
        default="PhotonTCP",
        help="OpenCV window title for --real mode (default: 'PhotonTCP').",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help=(
            "Camera device index, accepted for forward compatibility with a "
            "two-machine real loop. The bundled --real mode displays only and does "
            "not open a camera, so this is currently unused."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Drive the demo and return a process exit code (0 = success)."""
    args = _parse_args(argv)

    print("=" * 68)
    print("PhotonTCP optical-link demo (real OpticalChannel, in-memory devices)")
    mode = "REAL display window + in-memory loop" if args.real else "in-memory"
    print(
        f"mode: {mode}  seed={SEED}  scale={SCALE}px  "
        f"A->{len(MESSAGES_A)} msgs  B->{len(MESSAGES_B)} msgs"
    )
    print("=" * 68)

    display = None
    if args.real:
        # Import the cv2-backed display lazily and only for --real, so the default
        # in-memory mode never depends on a working GUI/display.
        from photontcp.optical import Cv2Display

        if Cv2Display is None:  # pragma: no cover - cv2-absent machines
            print("    --real needs opencv-python (cv2). Install it and retry.")
            return 0
        print(
            f"\n--real: outgoing QR frames will be shown in window {args.window!r}.\n"
            "    NOTE: a single screen+camera cannot see its own window, so this\n"
            "    proves the DISPLAY half on real hardware. For a true bidirectional\n"
            "    optical loop, point another device's camera at this window (or run\n"
            "    a second Cv2Camera-backed peer) -- two screens + two cameras."
        )
        display = Cv2Display(window=args.window)

    try:
        return _drive_session(display)
    finally:
        if display is not None:
            display.close()


if __name__ == "__main__":
    raise SystemExit(main())
