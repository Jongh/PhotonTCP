"""Real optical :class:`Channel` — display QR frames, capture them with a camera.

:class:`OpticalChannel` is the hardware-path counterpart to
:class:`~photontcp.channel.image_loopback.ImageLoopbackChannel`. Instead of moving
QR images through an in-process queue, it *shows* every outgoing frame on a
:class:`~photontcp.optical.devices.DisplaySink` and recovers incoming frames from a
:class:`~photontcp.optical.devices.CameraSource` — i.e. it transports bytes over
**light**. The channel depends only on those two device abstractions and on the
pure QR codec (:func:`photontcp.qr.encode.encode_frame` /
:func:`photontcp.qr.decode.decode_frame`); it never imports ``cv2`` itself, so the
exact same logic drives a webcam+screen link or a fully in-memory fake pair.

Sending is synchronous (encode + ``display.show``). Receiving is asynchronous: a
single background **capture thread** polls the camera, decodes frames, and hands
recovered packets to an ``inbox`` :class:`queue.Queue` that :meth:`recv_frame`
drains. This mirrors physical reality — the camera sees frames on its own clock,
independent of when the application calls :meth:`recv_frame`.

Re-capture de-duplication (channel framing nonce)
-------------------------------------------------
A camera runs faster than the display advances, so it captures the *same* QR many
times; meanwhile the ARQ layer legitimately retransmits identical packets. To tell
those apart **without depending on timing**, every outgoing frame is prefixed with
a fixed-width rolling counter (a *nonce*) before encoding::

    channel_frame = nonce.to_bytes(_NONCE_BYTES, "big") + packet

The nonce is :attr:`OpticalChannel._NONCE_BYTES` byte(s) wide (M10: **2** → a
``mod 65536`` period). M8-review 권장 3 worried that a 1-byte nonce could wrap back to a
value still being compared and be falsely dropped. The windowed de-dup below (depth
:attr:`OpticalChannel._DEDUP_DEPTH`) already made that impossible in practice — a
wrapped nonce can only collide with a frame still inside a window orders of magnitude
shorter than the wrap period, which never happens — but the de-dup argument only made
the corner *practically* unreachable, not arithmetically absent. M9 had to keep the
width at 1 because widening shifts every QR's bytes and one shifted frame fell into
cv2's content-dependent detector blind spot, breaking decode. **M10 hardened the
decoder** (preprocessing cascade + alternate-detector fallback, see
:func:`photontcp.qr.decode.decode_frame`), so widening no longer regresses decoding
(re-validated: a 1B-vs-2B decode-rate sweep is 100% on both, and the full suite is
green at 2 bytes). The width is therefore raised to 2, which pushes the wrap period
(65536) so far beyond both the de-dup window and any realistic in-flight frame count
that the false-dedup corner is gone *arithmetically* as well as practically. The
framing is parameterized by this constant, so the width can still be changed in one
place if ever needed.

The capture thread keeps a small bounded window of the most recently delivered
``channel_frame`` byte strings (a deque of depth :attr:`OpticalChannel._DEDUP_DEPTH`
mirrored by a set for O(1) membership). For each freshly decoded ``channel_frame``:
if it is already in the window the *same* displayed QR was re-captured (drop it);
otherwise the nonce advanced to a genuinely new frame, so strip the nonce, deliver
the packet, and record the ``channel_frame`` in the window. This is safe because the
nonce is monotonic within each wrap cycle (65536 frames) and the de-dup window
(:attr:`_DEDUP_DEPTH`) is far shorter than that cycle: every *legitimately new* frame
therefore carries a fresh, never-recently-seen ``channel_frame``, so any
``channel_frame`` matching one in the window is necessarily a re-capture (camera
jitter / a still display) and must be dropped. A larger window thus never wrongly
drops a real frame within it — it only makes re-capture suppression robust to
*out-of-order* captures (e.g. the camera briefly re-seeing an earlier frame), which a
single last-delivered slot would mishandle. Two consecutive *identical* ARQ packets
carry different nonces, so each is delivered exactly once — retransmissions are not
swallowed. This scheme is deterministic, so it can be exercised with the in-memory
fakes (a blank-gap-between-frames alternative would be timing-dependent and was
rejected).

The :class:`~photontcp.optical.devices.MemoryCamera` re-returns the *same array
object* when idle, so the capture loop additionally skips re-decoding a frame whose
array ``is`` the one it last decoded — a cheap identity check that avoids redundant
QR decodes on a still display. A real camera yields a distinct array each capture,
so that fast path never triggers there and every frame is decoded.

Thread-safety
-------------
The only state shared across the capture thread and caller threads is the
thread-safe ``inbox`` queue and a plain ``_closed`` boolean (assignment is atomic
in CPython). :func:`decode_frame` with ``detector=None`` uses a *thread-local* cv2
detector, so calling it from the capture thread is safe and shares nothing with the
caller — no cv2 detector is created across threads. No other unguarded shared
mutable state is introduced.

Build a full-duplex in-memory pair (no hardware) with :meth:`OpticalChannel.pair`,
mirroring :meth:`ImageLoopbackChannel.pair`'s usage feel. Real links inject
``Cv2Display`` / ``Cv2Camera`` directly into the constructor.
"""

from __future__ import annotations

import collections
import queue
import threading
import time

from ..channel.base import Channel
from ..qr.decode import decode_frame
from ..qr.encode import encode_frame
from .devices import CameraSource, DisplaySink, memory_device_pair

__all__ = ["OpticalChannel"]


class OpticalChannel(Channel):
    """Full-duplex :class:`~photontcp.channel.base.Channel` over a display+camera.

    Outgoing frames are QR-encoded (with a fixed-width rolling nonce prepended) and
    shown on the injected :class:`~photontcp.optical.devices.DisplaySink`. A
    background thread captures frames from the injected
    :class:`~photontcp.optical.devices.CameraSource`, decodes them, de-duplicates
    re-captures via the nonce, and queues recovered packets for :meth:`recv_frame`.

    The capture thread is started lazily on the first :meth:`recv_frame` call (or
    explicitly via :meth:`start`), so an instance used purely to *send* never spins
    up a thread.

    Display pacing (``hold``)
    -------------------------
    On a real link the camera needs the displayed QR to stay put long enough to be
    captured at least once. If consecutive :meth:`send_frame` calls outrun the
    camera, a frame can be overwritten before it is ever seen (today only ARQ
    retransmission recovers it). The optional ``hold`` parameter enforces a minimum
    wall-clock interval between successive displays — set it to at least one camera
    frame period for real links. ``hold=0`` (the default) disables pacing entirely
    so the in-memory path and existing tests are byte-for-byte unchanged.
    """

    # Lower bound for ``poll_interval`` (seconds). A non-positive poll interval
    # would turn the capture loop's camera read into a zero/negative timeout and
    # busy-spin a core; we clamp to this small positive floor instead of raising so
    # callers stay robust (M9 minor 4 / completion criterion 3).
    _MIN_POLL_INTERVAL = 1e-3

    # Fixed width (bytes, big-endian) of the per-frame rolling nonce prefixed to
    # every channel_frame. 2 bytes (mod-65536 period) since M10. The windowed
    # de-dup below (_DEDUP_DEPTH=8) already made M8-review 권장 3's wrap-collision
    # corner practically unreachable, but only a wider nonce removes it
    # arithmetically. M9 had to stay at 1 byte because widening shifted QR content
    # into cv2's content-dependent detector blind spot (broke one session frame);
    # M10's hardened decoder (qr.decode preprocessing cascade + alternate-detector
    # fallback) removes that constraint — re-validated at 2 bytes with a 100%
    # 1B-vs-2B decode-rate sweep and a green full suite. The framing is
    # parameterized by this constant, so the width can change in one place.
    _NONCE_BYTES = 2

    # Number of recently delivered channel_frames retained for re-capture de-dup.
    # A small bounded window (not just the single last-delivered frame) makes
    # suppression robust to out-of-order captures (camera briefly re-seeing an
    # earlier frame). Kept far smaller than the nonce wrap period so a wrapped
    # nonce can never collide within the window; every genuinely new frame is thus
    # unique within the window (see module docstring).
    _DEDUP_DEPTH = 8

    def __init__(
        self,
        display: DisplaySink,
        camera: CameraSource,
        *,
        scale: int = 8,
        border: int = 4,
        error: str = "m",
        poll_interval: float = 0.01,
        hold: float = 0.0,
        detector=None,
    ) -> None:
        """Wrap a display+camera pair as a frame-oriented channel.

        :param display: Sink that renders each outgoing QR frame.
        :param camera: Source the background thread polls for incoming frames.
        :param scale: QR module pixel size, passed to :func:`encode_frame`.
        :param border: QR quiet-zone width in modules, passed to
            :func:`encode_frame`.
        :param error: QR error-correction level in segno notation
            (``"l"``, ``"m"``, ``"q"``, ``"h"``), passed straight through to
            :func:`encode_frame`. This is the channel's main *optical robustness*
            knob and trades capacity for camera-capture resilience: a higher level
            adds more redundancy so the decoder tolerates more damaged/occluded
            modules (``"q"`` ≈ ~25% recovery vs the default ``"m"`` ≈ ~15%), which
            measurably helps a real screen→camera link where glare, blur, and
            partial occlusion corrupt modules. The cost is that the same payload
            needs more codewords, so segno may pick a *larger* QR version (more
            modules) and the single-symbol capacity drops — i.e.
            :class:`~photontcp.qr.encode.QRCapacityError` triggers at a smaller
            payload than at ``"m"``. The default stays ``"m"`` for backward
            compatibility (existing callers and the in-memory pair are byte- and
            behaviour-identical); real hardware links may raise it to ``"q"`` to
            buy capture robustness, sizing packets to stay within the lower
            ``"q"`` capacity.
        :param poll_interval: Seconds the capture thread waits per
            :meth:`CameraSource.read` poll. Also bounds how promptly the thread
            notices :meth:`close` (it loops at most this often). A value ``<= 0``
            is **clamped** up to :attr:`_MIN_POLL_INTERVAL` (not rejected) so a
            caller passing ``0`` cannot make the capture loop busy-spin.
        :param hold: Minimum seconds between successive :meth:`send_frame`
            displays (display pacing). ``send_frame`` measures elapsed time on a
            monotonic clock since the previous show and sleeps for any shortfall
            before showing the next frame, so a fast sender cannot overwrite a QR
            the camera has not yet captured. The default ``0`` (and any negative,
            which is clamped to ``0``) disables pacing — no sleep, no behavioural
            change from the un-paced path. The wait is a real wall-clock
            :func:`time.sleep` on the caller thread, which is acceptable because
            the channel is already a real-time/threaded boundary (the capture side
            runs independently).
        :param detector: Optional pre-built detector forwarded to
            :func:`decode_frame`. Leave ``None`` (the default) to use the
            per-thread detector — required for thread-safe decoding from the
            capture thread; do **not** share one detector across threads.
        """
        self._display = display
        self._camera = camera
        self.scale = scale
        self.border = border
        self.error = error
        # Clamp non-positive poll intervals to a small positive floor so the
        # capture loop's camera-read timeout can never become 0/negative and spin.
        self.poll_interval = max(poll_interval, self._MIN_POLL_INTERVAL)
        # Clamp negative holds to 0 (no pacing); 0 short-circuits the pacing path.
        self._hold = max(hold, 0.0)
        self._detector = detector

        # Monotonic timestamp of the last display, or None if nothing shown yet.
        # Only the send side (caller thread) touches it, so no lock is needed.
        self._last_show_monotonic: float | None = None

        # Rolling fixed-width send counter (nonce). Only the send side (caller
        # thread) touches it, so no lock is needed. It wraps mod 256 ** _NONCE_BYTES
        # to stay within _NONCE_BYTES big-endian bytes.
        self._send_nonce = 0

        # The single piece of cross-thread state besides ``_closed``: thread-safe.
        self._inbox: "queue.Queue[bytes]" = queue.Queue()

        # Plain bool: assignment is atomic in CPython, read by the capture thread.
        self._closed = False

        self._capture_thread: threading.Thread | None = None
        # Guards lazy thread creation so concurrent recv_frame calls start one
        # thread, not several. Never held across blocking work.
        self._start_lock = threading.Lock()

    @classmethod
    def pair(
        cls,
        *,
        seed: int | None = None,
        repeat_last: bool = True,
        scale: int = 8,
        border: int = 4,
        error: str = "m",
        poll_interval: float = 0.01,
        hold: float = 0.0,
    ) -> tuple["OpticalChannel", "OpticalChannel"]:
        """Create two cross-wired in-memory optical channels (no hardware).

        Builds two one-way :func:`~photontcp.optical.devices.memory_device_pair`
        links and crosses them: ``a``'s display feeds ``b``'s camera and vice
        versa, giving a full-duplex in-memory optical link. Frames sent by ``a``
        are received (after a real QR encode/decode round-trip) by ``b`` and vice
        versa — the same usage feel as
        :meth:`~photontcp.channel.image_loopback.ImageLoopbackChannel.pair`.

        :param seed: Accepted for API symmetry with the loopback channels. The
            in-memory devices use no RNG, so it has no effect here (it is ignored
            rather than rejected, so callers can pass it uniformly).
        :param repeat_last: Passed to the memory cameras — when set, an idle camera
            re-returns the last captured frame, exercising the nonce de-dup path.
        :param scale: QR module pixel size, passed to :func:`encode_frame`.
        :param border: QR quiet-zone width in modules, passed to
            :func:`encode_frame`.
        :param error: QR error-correction level (segno notation:
            ``"l"``/``"m"``/``"q"``/``"h"``) forwarded to both endpoints'
            :func:`encode_frame`. Higher levels (``"q"`` ≈ ~25% recovery vs the
            default ``"m"`` ≈ ~15%) make the optical capture more robust at the
            cost of capacity — a larger QR version for the same payload, so
            :class:`~photontcp.qr.encode.QRCapacityError` triggers sooner. The
            default stays ``"m"`` for backward compatibility; real links raise it
            to ``"q"`` (see :meth:`__init__` for the full tradeoff).
        :param poll_interval: Capture-thread poll interval for both endpoints.
        :param hold: Display-pacing minimum interval (seconds) forwarded to both
            endpoints' :meth:`send_frame`. Defaults to ``0`` (no pacing), so the
            in-memory pair behaves exactly as before; real harnesses raise it to
            at least one camera frame period.
        :returns: A tuple ``(a, b)`` of connected full-duplex channels.
        """
        # seed is intentionally unused: memory devices are deterministic without
        # any randomness. Accepting it keeps the factory signature uniform with
        # the other channels' pair() so callers need not special-case this one.
        del seed

        # Link 1: a shows -> b sees.  Link 2: b shows -> a sees.
        display_ab, camera_ab = memory_device_pair(repeat_last=repeat_last)
        display_ba, camera_ba = memory_device_pair(repeat_last=repeat_last)

        opts = dict(
            scale=scale,
            border=border,
            error=error,
            poll_interval=poll_interval,
            hold=hold,
        )
        a = cls(display=display_ab, camera=camera_ba, **opts)
        b = cls(display=display_ba, camera=camera_ab, **opts)
        return a, b

    def start(self) -> None:
        """Start the background capture thread if it is not already running.

        Idempotent and safe to call from multiple threads; :meth:`recv_frame`
        calls it lazily, so explicit invocation is only needed to begin capturing
        before the first receive. A closed channel does not start a thread.
        """
        if self._closed:
            return
        # Double-checked: cheap unlocked check, then confirm under the lock so two
        # racing callers create exactly one thread.
        if self._capture_thread is not None:
            return
        with self._start_lock:
            if self._capture_thread is not None or self._closed:
                return
            thread = threading.Thread(
                target=self._capture_loop,
                name="OpticalChannel-capture",
                daemon=True,
            )
            self._capture_thread = thread
            thread.start()

    def send_frame(self, frame: bytes) -> None:
        """QR-encode ``frame`` (with a nonce prefix) and show it on the display.

        :param frame: One serialized packet to transmit.

        A fixed-width rolling nonce is prepended
        (``nonce.to_bytes(_NONCE_BYTES, "big") + frame``) so the receiver can
        distinguish a re-captured still frame from a genuinely new one even when
        consecutive packets are identical (see the module docstring). The combined
        ``channel_frame`` is encoded via :func:`encode_frame` and rendered with
        :meth:`DisplaySink.show`; the nonce counter then advances mod
        ``256 ** _NONCE_BYTES``.

        When ``hold > 0`` (display pacing), this method sleeps before showing so
        that at least ``hold`` seconds elapse since the previous display — giving a
        real camera time to capture the prior QR before it is replaced. With
        ``hold == 0`` (the default) the pacing branch is skipped entirely: no
        monotonic read, no sleep, behaviour identical to the un-paced path.

        A closed channel silently discards the send. :class:`~photontcp.qr.encode.
        QRCapacityError` is **not** caught — it propagates so the layer above can
        shrink the packet (consistent with the other channels).
        """
        if self._closed:
            return

        nonce = self._send_nonce
        channel_frame = nonce.to_bytes(self._NONCE_BYTES, "big") + frame
        # encode_frame may raise QRCapacityError; let it propagate untouched.
        image = encode_frame(
            channel_frame,
            scale=self.scale,
            border=self.border,
            error=self.error,
        )

        # Display pacing: ensure >= hold seconds between successive shows, using a
        # monotonic clock (immune to wall-clock jumps). hold == 0 short-circuits so
        # the default path takes no timestamp and never sleeps. The wait is a real
        # time.sleep on the caller thread — acceptable here because the channel is
        # already a real-time/threaded boundary (capture runs on its own thread).
        if self._hold > 0 and self._last_show_monotonic is not None:
            elapsed = time.monotonic() - self._last_show_monotonic
            if elapsed < self._hold:
                time.sleep(self._hold - elapsed)

        self._display.show(image)
        if self._hold > 0:
            self._last_show_monotonic = time.monotonic()
        # Advance only after a successful show so a capacity failure does not burn
        # a nonce (keeps the rolling sequence contiguous across retries). Wrap mod
        # 256 ** _NONCE_BYTES to stay within the fixed nonce width.
        self._send_nonce = (nonce + 1) % (256 ** self._NONCE_BYTES)

    def recv_frame(self, timeout: float | None = None) -> bytes | None:
        """Return the next recovered packet, or ``None`` on timeout.

        :param timeout: Seconds to wait. ``None`` blocks indefinitely; ``0`` polls
            without blocking. Returns ``None`` if no packet became available within
            ``timeout``.
        :returns: One whole packet (the nonce already stripped by the capture
            thread), preserving the :class:`Channel` whole-frame contract.

        Lazily starts the background capture thread on first call. The capture
        thread does all decoding/de-dup work; this method merely drains the
        thread-safe ``inbox`` queue.
        """
        if self._closed:
            return None
        self.start()
        try:
            if timeout is None:
                return self._inbox.get(block=True)
            return self._inbox.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

    def _capture_loop(self) -> None:
        """Background loop: poll camera, decode, de-dup, deliver packets.

        Runs until :attr:`_closed`. For each captured image it (1) skips re-decode
        if the array *is* the one last decoded (a memory re-capture of an unchanged
        display — a real camera yields distinct arrays so this never triggers), (2)
        decodes the QR (a decode failure is a lost frame — ignored, ARQ retransmits),
        (3) drops the frame if its ``channel_frame`` is already in the recent-N
        de-dup window (same QR re-captured, possibly out of order), else strips the
        fixed-width nonce, queues the packet, and records the ``channel_frame`` in
        the window.
        """
        last_decoded_array = None  # identity-only; for the cheap re-decode skip
        # Bounded window of recently delivered channel_frames. The deque caps the
        # history at _DEDUP_DEPTH; recent_set mirrors its contents for O(1)
        # membership. Both are touched only by this thread, so no lock is needed.
        recent = collections.deque(maxlen=self._DEDUP_DEPTH)
        recent_set = set()

        while not self._closed:
            img = self._camera.read(timeout=self.poll_interval)
            if img is None:
                continue

            # Identity fast path: the memory camera re-returns the *same* object
            # when the display has not advanced, so we can skip re-decoding it.
            if img is last_decoded_array:
                continue
            last_decoded_array = img

            channel_frame = decode_frame(img, detector=self._detector)
            if channel_frame is None:
                # Undecodable -> treated as a lost frame; ARQ will retransmit.
                continue
            if len(channel_frame) < self._NONCE_BYTES:
                # Defensive: too short to carry a full fixed-width nonce.
                continue

            # Recent-N de-dup: a channel_frame already in the window is a re-capture
            # (camera jitter / still display, possibly out of order). The monotonic
            # nonce guarantees every genuinely new frame is unique within the
            # window, so this can never drop a real frame (see module docstring).
            if channel_frame in recent_set:
                continue

            # Strip the fixed-width nonce; deliver the packet payload only.
            payload = channel_frame[self._NONCE_BYTES:]
            self._inbox.put(payload)

            # Record as delivered. Drop the about-to-be-evicted item from the set
            # mirror before the deque overwrites it, keeping the two views in sync.
            if len(recent) == recent.maxlen:
                recent_set.discard(recent[0])
            recent.append(channel_frame)
            recent_set.add(channel_frame)

    def close(self) -> None:
        """Mark the channel closed, stop capturing, and release the devices.

        Idempotent. Sets the closed flag (so the capture loop exits within one
        ``poll_interval``), joins the capture thread with a bounded timeout so
        close never hangs, then closes the display and camera. After closing,
        :meth:`send_frame` discards and :meth:`recv_frame` returns ``None``.
        """
        self._closed = True

        thread = self._capture_thread
        if thread is not None and thread.is_alive():
            # The loop polls every ``poll_interval``; give it a generous multiple
            # so close terminates promptly without risking an indefinite hang.
            thread.join(timeout=max(1.0, self.poll_interval * 10))

        self._display.close()
        self._camera.close()
