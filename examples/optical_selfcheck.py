"""Real half-duplex optical self-check on a single machine (M9-T04).

What this proves
================
Every other optical example in this repo carries QR frames over an **in-memory**
medium (``ImageLoopbackChannel`` / ``MemoryDisplay`` + ``MemoryCamera``): the
genuine ``Cv2Camera`` *capture* path is never actually run. This harness is the
first real exercise of that path. It validates the **real DISPLAY -> CAMERA**
optical link end to end on one box:

    1. it renders a sequence of unique, packet-sized payloads to QR images with
       :func:`photontcp.qr.encode.encode_frame`;
    2. it shows each QR on screen with a real :class:`~photontcp.optical.Cv2Display`
       window;
    3. a real :class:`~photontcp.optical.Cv2Camera` -- which **you physically aim
       at that on-screen window** -- captures the displayed light;
    4. each capture is decoded with :func:`photontcp.qr.decode.decode_frame` and
       compared against the payload that was on screen at the time;
    5. it prints a receive-rate / accuracy report and a PASS/FAIL verdict.

This is a deliberately minimal raw ``display -> decode`` loop. It does NOT use
:class:`~photontcp.optical.OpticalChannel`, the session layer, or ARQ -- the
point is to measure the *physics* of the optical link (how reliably the camera
can read what the screen shows), not protocol behaviour.

How to run (MANUAL only -- never in CI)
=======================================
You need a webcam and a screen. From the repository root::

    python examples/optical_selfcheck.py

Then **point the webcam at the PhotonTCP window on your screen** (a phone/USB
cam clipped facing the monitor works; a built-in laptop cam pointed at a second
monitor also works). Keep the QR fully in frame, reasonably filling it, in even
lighting, and hold still. Tune ``--scale`` / ``--hold`` if the receive rate is
low. Press nothing -- the run is automatic and self-terminating.

Useful flags::

    --camera N     camera device index (default 0)
    --window NAME  display window title (default "PhotonTCP")
    --scale PX     QR module pixel size; bigger == easier to decode (default 6)
    --hold SECS    seconds each frame is shown before capture (default 0.3)
    --count N      number of distinct frames to send (default 20)
    --fullscreen   show the window fullscreen

Honest limitations
==================
Receive rate depends heavily on lighting, camera focus/resolution, alignment,
and ``--scale`` -- a low rate usually means optics, not a code bug. This checks
**one direction only** (half-duplex: this machine's screen -> this machine's
camera). A full bidirectional round trip needs two machines (or two
screens/cameras facing each other); use ``examples/optical_link.py --real``
for that. ``--mismatch`` (a non-None decode that doesn't match the *current*
on-screen payload) is most often a *stale* capture of the previous QR rather
than a corrupt decode; with the camera's latest-frame drain it should be ~0.

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
# The QR codec needs segno (encode) and OpenCV (decode + the cv2 devices); fail
# gracefully if either is missing, with a clear pip hint. Mirrors qr_loopback.py.
try:
    import segno  # noqa: F401
    import cv2  # noqa: F401
except ImportError:
    print(
        "This example requires segno and opencv-python.\n"
        "    pip install segno opencv-python"
    )
    raise SystemExit(0)

from photontcp.optical import Cv2Camera, Cv2Display  # noqa: E402
from photontcp.qr.decode import decode_frame  # noqa: E402
from photontcp.qr.encode import encode_frame  # noqa: E402

# --- Defaults / parameters. ------------------------------------------------- #

#: Fraction of frames that must be captured *and* decoded correctly for PASS.
#: 0.8 is deliberately lenient: real optics drop frames to lighting/alignment,
#: and this is a physics check, not a protocol guarantee (ARQ would recover the
#: rest on a live link). Tune up once your rig is dialed in.
PASS_THRESHOLD = 0.8

#: cv2's QR detector cannot localize a QR rendered from a *tiny* payload, so we
#: pad every payload to at least this many bytes (the tests use packet-sized
#: payloads for the same reason).
MIN_PAYLOAD_BYTES = 60


def _build_payloads(count: int) -> list[bytes]:
    """Build ``count`` unique, packet-sized payloads.

    Each payload embeds its own index so a decode can be matched back to the
    frame that was on screen, and is padded to at least :data:`MIN_PAYLOAD_BYTES`
    so cv2's detector can actually localize the resulting QR code.
    """
    payloads: list[bytes] = []
    for i in range(count):
        head = f"PhotonTCP/selfcheck frame {i:04d} ".encode("ascii")
        # Pad with a repeating, index-flavoured filler up to the minimum size.
        if len(head) < MIN_PAYLOAD_BYTES:
            filler = (f"#{i:04d}." * MIN_PAYLOAD_BYTES).encode("ascii")
            head = head + filler[: MIN_PAYLOAD_BYTES - len(head)]
        payloads.append(head)
    return payloads


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line flags (no hardware is touched by --help)."""
    parser = argparse.ArgumentParser(
        description=(
            "Manual half-duplex optical self-check: display QR frames and "
            "capture them with a camera aimed at the screen, then report the "
            "receive rate. Point your webcam at the on-screen window."
        ),
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="camera device index (default: 0)",
    )
    parser.add_argument(
        "--window",
        type=str,
        default="PhotonTCP",
        help='display window title (default: "PhotonTCP")',
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=6,
        help="QR module size in pixels; bigger is easier to decode (default: 6)",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=0.3,
        help="seconds each frame is displayed before capture (default: 0.3)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="number of distinct frames to send (default: 20)",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="show the display window fullscreen",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Drive the self-check and return a process exit code.

    Returns ``0`` on PASS (or on any *graceful* no-hardware bail-out: cv2
    devices unavailable, or no camera at the given index -- CI / headless boxes
    must never see a non-zero exit from those), and ``1`` only when the link ran
    but the receive rate fell below :data:`PASS_THRESHOLD`.
    """
    args = _parse_args(argv)

    # Graceful: the cv2-backed devices are bound to None when opencv-python is
    # missing (see photontcp/optical/__init__.py). We already checked for cv2
    # above, but guard anyway so the intent is explicit and the harness never
    # crashes on an odd partial install.
    if Cv2Display is None or Cv2Camera is None:
        print("Cv2Display / Cv2Camera unavailable (opencv-python not importable).")
        print("    pip install opencv-python")
        return 0

    if args.count <= 0:
        print("Nothing to do: --count must be >= 1.")
        return 0

    print("=" * 68)
    print("PhotonTCP optical self-check (REAL display -> camera, half-duplex)")
    print(
        f"camera={args.camera}  window={args.window!r}  scale={args.scale}px  "
        f"hold={args.hold}s  count={args.count}  fullscreen={args.fullscreen}"
    )
    print("Point your webcam at the on-screen window. This is MANUAL only.")
    print("=" * 68)

    # --- Pre-encode every payload to a QR image up front. ------------------ #
    # Encoding is pure/in-memory, so doing it before we touch any hardware keeps
    # the display loop tight and means an encode error can't leave a device open.
    payloads = _build_payloads(args.count)
    images = [encode_frame(p, scale=args.scale) for p in payloads]
    # Map payload bytes -> index so a decode can be recognized even when it is a
    # *stale* capture of a different (earlier/later) known frame.
    known: dict[bytes, int] = {p: i for i, p in enumerate(payloads)}
    sample_shape = images[0].shape
    print(
        f"\nEncoded {len(images)} unique payloads "
        f"({len(payloads[0])} bytes each) -> QR images shape={sample_shape}"
    )

    # --- Open the real display + camera. ----------------------------------- #
    # Opening the camera is the one step that can fail on a box with no device;
    # catch RuntimeError and bail out *gracefully* (return 0) so headless/CI
    # runs are a no-op rather than an error.
    display: Cv2Display | None = None
    cam: Cv2Camera | None = None
    try:
        display = Cv2Display(window=args.window, fullscreen=args.fullscreen)
        try:
            cam = Cv2Camera(index=args.camera, drain_to_latest=True)
        except RuntimeError as exc:
            print(f"\nNo camera at index {args.camera}: {exc}")
            print("Nothing to capture -- skipping (this is not a failure).")
            return 0

        # --- Tally counters. ----------------------------------------------- #
        hits = 0        # decoded == the payload currently on screen
        misses = 0      # decode returned None (or no frame captured) -> a loss
        mismatches = 0  # decoded to some OTHER value (likely a stale frame)

        print("\nRunning... (each frame: show, settle, capture, decode)\n")
        for i, (payload, img) in enumerate(zip(payloads, images)):
            display.show(img)
            # Let the screen render and the camera's latest-frame drain catch up
            # to the freshly shown QR. We split the hold into a settle wait + a
            # couple of capture attempts so a single dropped grab isn't fatal.
            settle = max(0.0, args.hold * 0.6)
            time.sleep(settle)

            decoded: bytes | None = None
            attempts = 2
            per_attempt = max(0.0, (args.hold - settle) / attempts)
            for _ in range(attempts):
                frame = cam.read()
                if frame is not None:
                    decoded = decode_frame(frame)
                    if decoded == payload:
                        break  # confirmed hit -- no need to retry
                if per_attempt:
                    time.sleep(per_attempt)

            if decoded is None:
                misses += 1
                status = "MISS"
            elif decoded == payload:
                hits += 1
                status = "HIT "
            else:
                mismatches += 1
                other = known.get(decoded)
                if other is not None:
                    status = f"MISMATCH (got known frame {other:04d}, stale?)"
                else:
                    status = "MISMATCH (unknown/garbled decode)"

            print(f"    frame {i:04d}: {status}")

        # --- Report. ------------------------------------------------------- #
        total = len(payloads)
        rate = hits / total if total else 0.0
        passed = rate >= PASS_THRESHOLD

        print("\n" + "-" * 68)
        print(
            f"Total {total} | hits {hits} | misses {misses} | "
            f"mismatches {mismatches}"
        )
        print(
            f"Receive rate (correct decodes): {rate:.1%} "
            f"(PASS threshold {PASS_THRESHOLD:.0%})"
        )
        if mismatches:
            print(
                "Note: mismatches are usually STALE captures of a neighbouring "
                "frame, not corruption. Increase --hold if they persist."
            )
        verdict = "PASS" if passed else "FAIL"
        print(verdict)
        print("-" * 68)
        return 0 if passed else 1
    finally:
        # Always release the hardware, in reverse order of acquisition, even if
        # an exception unwound us out of the loop above.
        if cam is not None:
            cam.close()
        if display is not None:
            display.close()


if __name__ == "__main__":
    raise SystemExit(main())
