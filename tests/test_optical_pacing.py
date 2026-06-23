"""Deterministic in-memory tests for M9 OpticalChannel hardening (M9-T06).

These pin the M9 additions to :class:`~photontcp.optical.channel.OpticalChannel`
that are verifiable without hardware:

* **Display pacing (``hold``)** — successive ``send_frame`` calls are spaced by at
  least ``hold`` seconds (so on a real link the camera can capture each QR before
  it is overwritten); ``hold=0`` adds no delay.
* **Parameter lower-bound guards** — a non-positive ``poll_interval`` is clamped to
  a small positive floor (no capture-loop busy-spin) and a negative ``hold`` is
  clamped to ``0``.
* **Windowed re-capture de-dup** — the recent-N window drops an *out-of-order*
  re-capture of an earlier frame that a single last-delivered slot would have
  mis-delivered, while never dropping a genuinely new frame and never swallowing a
  legitimate retransmission (consecutive identical packets carry distinct nonces).

The pacing test uses a real monotonic clock (pacing is real wall-clock by design);
everything else is deterministic. ``segno`` / ``cv2`` / ``numpy`` missing -> skip.
"""

from __future__ import annotations

import queue
import time

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from photontcp.optical.channel import OpticalChannel  # noqa: E402
from photontcp.optical.devices import (  # noqa: E402
    CameraSource,
    MemoryDisplay,
    memory_device_pair,
)
from photontcp.qr.decode import decode_frame  # noqa: E402

# Packet-sized payloads (cv2 cannot localize a QR from a tiny payload).
PAYLOAD = b"photon-pacing-frame-" + bytes(range(64))
P_A = b"photon-pacing-A-" + bytes(range(64))
P_B = b"photon-pacing-B-" + bytes(range(64, 128))
P_C = b"photon-pacing-C-" + bytes(range(128, 192))


# --------------------------------------------------------------------------- #
# 1. Display pacing (hold) — completion criterion 2.
# --------------------------------------------------------------------------- #


def test_hold_paces_sends_and_zero_holds_nothing() -> None:
    """``hold`` enforces a minimum gap between displays; ``hold=0`` adds none.

    Uses two independent *one-sided* bounds against the pacing floor
    ``(n-1) * hold`` (the total sleep a paced run must perform):

    * the paced run cannot finish faster than that floor — :func:`time.sleep` is a
      hard lower bound (it never returns early), so this holds regardless of
      machine speed or suite load;
    * the unpaced run pays only per-frame encode cost (tens of ms here), which is
      far below the floor (200 ms), so it must finish under it.

    Both bounds compare to the same ``floor * 0.9`` value (0.9 absorbs monotonic
    granularity), with the paced run provably above and the unpaced run provably
    below it by a wide margin. This avoids the earlier *differential* check
    (``paced - unpaced``), which was flaky because per-frame encode cost varies
    run-to-run and could swamp a small pacing budget.
    """
    n = 5
    hold = 0.05
    floor = (n - 1) * hold  # total sleep the paced run must perform (0.20 s)

    a0, b0 = OpticalChannel.pair(hold=0.0)
    try:
        t0 = time.monotonic()
        for _ in range(n):
            a0.send_frame(PAYLOAD)
        unpaced = time.monotonic() - t0
    finally:
        a0.close()
        b0.close()

    a1, b1 = OpticalChannel.pair(hold=hold)
    try:
        t0 = time.monotonic()
        for _ in range(n):
            a1.send_frame(PAYLOAD)
        paced = time.monotonic() - t0
    finally:
        a1.close()
        b1.close()

    # hold=0 must NOT pace: encode-only cost is far below the pacing floor.
    assert unpaced < floor * 0.9
    # hold>0 enforces the floor: the (n-1) sleeps cannot return early, so the
    # paced run cannot finish faster than the floor (minus monotonic slack).
    assert paced >= floor * 0.9


# --------------------------------------------------------------------------- #
# 2. Parameter lower-bound guards — completion criterion 3.
# --------------------------------------------------------------------------- #


def test_poll_interval_and_hold_are_clamped() -> None:
    """Non-positive ``poll_interval`` clamps to a positive floor; negative ``hold`` -> 0.

    A ``poll_interval`` of ``0`` (or negative) would otherwise make the capture
    loop's camera-read timeout zero/negative and busy-spin a core; it must be
    clamped up. A negative ``hold`` must become ``0`` (no pacing), not a negative
    sleep.
    """
    disp, cam = memory_device_pair()
    ch = OpticalChannel(disp, cam, poll_interval=0.0, hold=-2.0)
    try:
        assert ch.poll_interval >= OpticalChannel._MIN_POLL_INTERVAL
        assert ch._hold == 0.0
    finally:
        ch.close()

    disp2, cam2 = memory_device_pair()
    ch2 = OpticalChannel(disp2, cam2, poll_interval=-5.0)
    try:
        assert ch2.poll_interval >= OpticalChannel._MIN_POLL_INTERVAL
    finally:
        ch2.close()


# --------------------------------------------------------------------------- #
# 3. Windowed de-dup robustness to out-of-order re-capture — criterion 4.
# --------------------------------------------------------------------------- #


class _ScriptedCamera(CameraSource):
    """A camera that returns a fixed scripted list of frames, then ``None``.

    Lets a test drive the capture loop with an exact, reproducible capture order
    (including an out-of-order re-capture of an earlier frame) that the queue-based
    :class:`MemoryCamera` cannot express.
    """

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = list(frames)
        self._i = 0
        self._closed = False

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        if self._closed:
            return None
        if self._i < len(self._frames):
            frame = self._frames[self._i]
            self._i += 1
            return frame
        return None  # exhausted: the capture loop just keeps polling None

    def close(self) -> None:
        self._closed = True


def _encode_channel_frames(payloads: list[bytes]) -> list[np.ndarray]:
    """Produce the real QR images OpticalChannel would display for ``payloads``.

    Uses a send-only channel writing into a MemoryDisplay queue, so the images
    carry the exact nonce framing the receiver expects (no test coupling to the
    private framing layout beyond what send_frame itself produces).
    """
    disp_q: "queue.Queue[np.ndarray]" = queue.Queue()
    tx = OpticalChannel(MemoryDisplay(disp_q), _ScriptedCamera([]))
    try:
        for p in payloads:
            tx.send_frame(p)
    finally:
        tx.close()
    return [disp_q.get_nowait() for _ in payloads]


def test_out_of_order_recapture_is_deduped_without_dropping_new_frames() -> None:
    """An earlier frame re-captured out of order is dropped; new frames are not.

    Capture order is A, B, A, C: the second A is an out-of-order re-capture of an
    already-delivered frame (camera jitter / a brief re-glimpse). A single
    last-delivered slot would mis-deliver it (last delivered was B, so A != B);
    the recent-N window recognises A as a recent delivery and drops it. The genuine
    frames A, B, C are each delivered exactly once, in order.
    """
    img_a, img_b, img_c = _encode_channel_frames([P_A, P_B, P_C])

    scripted = _ScriptedCamera([img_a, img_b, img_a, img_c])
    rx = OpticalChannel(MemoryDisplay(queue.Queue()), scripted, poll_interval=0.005)
    got: list[bytes] = []
    try:
        deadline = time.monotonic() + 3.0
        while len(got) < 3 and time.monotonic() < deadline:
            frame = rx.recv_frame(timeout=0.2)
            if frame is not None:
                got.append(frame)
        # The three genuine frames arrive once each, in order...
        assert got == [P_A, P_B, P_C]
        # ...and the out-of-order duplicate A produced no fourth delivery.
        assert rx.recv_frame(timeout=0.3) is None
    finally:
        rx.close()


# --------------------------------------------------------------------------- #
# 4. Two-byte nonce framing (M10 widening re-validation) — criterion 7.
# --------------------------------------------------------------------------- #


def test_nonce_framing_is_two_bytes_and_increments() -> None:
    """M10 raised the channel-framing nonce from 1 to 2 bytes; pin that here.

    M9 kept the nonce at 1 byte because widening shifted QR content into cv2's
    content-dependent decoder blind spot. M10 hardened the decoder
    (:func:`~photontcp.qr.decode.decode_frame` preprocessing cascade + alternate
    detector), decoupling the nonce width from QR content, so the width was raised
    to 2 (mod-65536 period) — removing the theoretical wrap-collision corner
    *arithmetically*, not just practically. Each displayed ``channel_frame`` must
    now be ``nonce.to_bytes(2, "big") + payload`` with the nonce advancing
    0, 1, 2, ... per successful send.
    """
    assert OpticalChannel._NONCE_BYTES == 2

    payloads = [P_A, P_B, P_C]
    images = _encode_channel_frames(payloads)
    for i, (img, payload) in enumerate(zip(images, payloads)):
        channel_frame = decode_frame(img)
        assert channel_frame is not None  # hardened decoder recovers every frame
        assert channel_frame[:2] == i.to_bytes(2, "big")  # 2-byte big-endian nonce
        assert channel_frame[2:] == payload  # payload intact after the nonce
