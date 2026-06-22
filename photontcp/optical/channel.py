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
a 1-byte rolling counter (a *nonce*, ``mod 256``) before encoding::

    channel_frame = bytes([nonce]) + packet

The capture thread compares each freshly decoded ``channel_frame`` byte-for-byte
against the **last delivered** one: identical means the same displayed QR was
re-captured (drop it); different means the nonce advanced to a genuinely new frame
(strip the nonce, deliver the packet). Two consecutive *identical* ARQ packets
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

import queue
import threading

from ..channel.base import Channel
from ..qr.decode import decode_frame
from ..qr.encode import encode_frame
from .devices import CameraSource, DisplaySink, memory_device_pair

__all__ = ["OpticalChannel"]


class OpticalChannel(Channel):
    """Full-duplex :class:`~photontcp.channel.base.Channel` over a display+camera.

    Outgoing frames are QR-encoded (with a 1-byte rolling nonce prepended) and
    shown on the injected :class:`~photontcp.optical.devices.DisplaySink`. A
    background thread captures frames from the injected
    :class:`~photontcp.optical.devices.CameraSource`, decodes them, de-duplicates
    re-captures via the nonce, and queues recovered packets for :meth:`recv_frame`.

    The capture thread is started lazily on the first :meth:`recv_frame` call (or
    explicitly via :meth:`start`), so an instance used purely to *send* never spins
    up a thread.
    """

    def __init__(
        self,
        display: DisplaySink,
        camera: CameraSource,
        *,
        scale: int = 8,
        border: int = 4,
        error: str = "m",
        poll_interval: float = 0.01,
        detector=None,
    ) -> None:
        """Wrap a display+camera pair as a frame-oriented channel.

        :param display: Sink that renders each outgoing QR frame.
        :param camera: Source the background thread polls for incoming frames.
        :param scale: QR module pixel size, passed to :func:`encode_frame`.
        :param border: QR quiet-zone width in modules, passed to
            :func:`encode_frame`.
        :param error: QR error-correction level (segno notation), passed to
            :func:`encode_frame`.
        :param poll_interval: Seconds the capture thread waits per
            :meth:`CameraSource.read` poll. Also bounds how promptly the thread
            notices :meth:`close` (it loops at most this often).
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
        self.poll_interval = poll_interval
        self._detector = detector

        # Rolling 1-byte send counter (nonce). Only the send side (caller thread)
        # touches it, so no lock is needed. mod 256 keeps it one byte.
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
        :param error: QR error-correction level, passed to :func:`encode_frame`.
        :param poll_interval: Capture-thread poll interval for both endpoints.
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

        A 1-byte rolling nonce is prepended (``bytes([nonce]) + frame``) so the
        receiver can distinguish a re-captured still frame from a genuinely new one
        even when consecutive packets are identical (see the module docstring). The
        combined ``channel_frame`` is encoded via :func:`encode_frame` and rendered
        with :meth:`DisplaySink.show`; the nonce counter then advances ``mod 256``.

        A closed channel silently discards the send. :class:`~photontcp.qr.encode.
        QRCapacityError` is **not** caught — it propagates so the layer above can
        shrink the packet (consistent with the other channels).
        """
        if self._closed:
            return

        nonce = self._send_nonce
        channel_frame = bytes([nonce]) + frame
        # encode_frame may raise QRCapacityError; let it propagate untouched.
        image = encode_frame(
            channel_frame,
            scale=self.scale,
            border=self.border,
            error=self.error,
        )
        self._display.show(image)
        # Advance only after a successful show so a capacity failure does not burn
        # a nonce (keeps the rolling sequence contiguous across retries).
        self._send_nonce = (nonce + 1) & 0xFF

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
        (3) drops the frame if its ``channel_frame`` bytes equal the last delivered
        ones (same QR re-captured), else strips the nonce and queues the packet.
        """
        last_decoded_array = None  # identity-only; for the cheap re-decode skip
        last_delivered = None  # bytes of the last channel_frame put on the inbox

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
            if len(channel_frame) == 0:
                # Defensive: a zero-length frame carries no nonce/payload.
                continue

            # Byte-exact de-dup: same channel_frame == same displayed QR recaptured.
            if channel_frame == last_delivered:
                continue
            last_delivered = channel_frame

            # Strip the leading nonce byte; deliver the packet payload only.
            payload = channel_frame[1:]
            self._inbox.put(payload)

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
