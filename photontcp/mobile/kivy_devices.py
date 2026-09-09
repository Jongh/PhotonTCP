"""Kivy/Android device adapters for the optical channel (M11).

This module provides the *mobile* implementations of the device abstractions
defined in :mod:`photontcp.optical.devices`, so a phone can act as one peer of
an optical link:

* :class:`KivyDisplay` — a :class:`~photontcp.optical.devices.DisplaySink` that
  uploads each QR bitmap into a Kivy ``Texture`` and hands it to a widget.
* :class:`KivyCamera` — a :class:`~photontcp.optical.devices.CameraSource` fed
  by ``camera4kivy``'s ``Preview`` (CameraX) pixel callback, keeping only the
  most recent frame.

They are the mobile counterpart of ``optical/cv2_devices.py`` and follow the
same discipline:

**Import safety.** Importing this module touches no hardware and creates no GL
context or window: only ``kivy.clock`` / ``kivy.graphics.texture`` are imported
(neither instantiates a ``Window``). Every graphics call happens inside a
method, on the Kivy main thread. Note that this module still *requires* Kivy to
be installed — :mod:`photontcp.mobile` guards the import so a desktop without
Kivy can still ``import photontcp.mobile`` (the cv2 guard pattern).

**Threading.** The optical channel shows frames from its calling thread and
reads frames from its own capture thread, while Kivy owns the UI/GL thread.
Therefore:

* :meth:`KivyDisplay.show` never renders inline — it stores the frame and
  marshals a render onto the main thread with ``Clock.schedule_once``, then
  returns immediately. It does **not** sleep: ``hold`` pacing is
  :class:`~photontcp.optical.channel.OpticalChannel`'s job.
* :meth:`KivyCamera.read` picks up the single latest frame stored by the
  capture callback. No queue is kept, so latency cannot accumulate — the same
  goal as ``Cv2Camera``'s bounded drain.

**Colour space.** The camera hands out RGBA (or another packed format); the
decoder (:func:`photontcp.qr.decode.decode_frame`) accepts a 2D ``uint8`` gray
image or an ``HxWx3`` BGR image. The conversion lives in exactly one place
(:func:`_frame_to_array`) and runs in :meth:`KivyCamera.read` — i.e. only on
the one frame that is actually consumed, never inside the capture callback.
"""

from __future__ import annotations

import threading

import numpy as np
from kivy.clock import Clock
from kivy.graphics.texture import Texture

from ..optical.devices import CameraSource, DisplaySink

__all__ = ["KivyDisplay", "KivyCamera"]


#: Bytes per pixel for the packed buffer formats a Kivy/CameraX preview can
#: hand out. ``camera4kivy``'s pixel callback documents RGBA; the others are
#: accepted for robustness (and for desktop ``kivy.core.camera`` providers).
_CHANNELS = {
    "rgba": 4,
    "bgra": 4,
    "rgb": 3,
    "bgr": 3,
    "luminance": 1,
    "gray": 1,
}


def _frame_to_array(
    raw: object,
    size: tuple[int, int] | None,
    colorfmt: str,
    *,
    flip_vertical: bool = False,
    flip_horizontal: bool = False,
) -> np.ndarray | None:
    """Normalize one captured frame into something ``decode_frame`` accepts.

    This is the **single** colour-space conversion point of the mobile camera
    path (see the module docstring). It is deliberately a module-level pure
    function so it can be unit-tested without any Kivy object.

    :param raw: The captured pixels — either a packed buffer (``bytes`` /
        ``bytearray`` / ``memoryview``, as ``camera4kivy`` hands out) or an
        ``np.ndarray`` that is already shaped (a fake source, or a desktop
        provider that yields arrays).
    :param size: ``(width, height)`` of the packed buffer. Required when ``raw``
        is a flat buffer or a 1-D array; ignored for already-shaped arrays.
    :param colorfmt: The buffer's pixel format, one of :data:`_CHANNELS`.
    :param flip_vertical: Flip rows. Needed when the source hands out pixels in
        GL (bottom-up) order. **This is NOT required for decoding** (M13
        measurement): ``cv2.QRCodeDetector`` reads a QR under 90/180/270°
        rotation *and* under horizontal/vertical mirroring — all seven variants
        decoded, the base detector alone sufficing. Earlier revisions of this
        docstring claimed a mirrored QR cannot be decoded; that was wrong. The
        flags remain a normalisation knob rather than a correctness requirement
        **for the shipped cv2 backend**. That scope matters: the decoder is a
        registerable seam (:func:`~photontcp.qr.decode.register_decoder_backend`),
        and a third-party backend need not share cv2's tolerance — so keep the
        orientation sane rather than relying on the detector to forgive it.
    :param flip_horizontal: Flip columns — for a front ("selfie") camera whose
        preview is mirrored. Same caveat.
    :returns: A 2D ``uint8`` gray image or an ``HxWx3`` ``uint8`` BGR image
        (both accepted by :func:`photontcp.qr.decode.decode_frame`), or ``None``
        if the buffer is unusable (unknown format, short buffer).
    """
    if raw is None:
        return None

    if isinstance(raw, np.ndarray):
        arr = raw
    else:
        try:
            arr = np.frombuffer(raw, dtype=np.uint8)
        except (TypeError, ValueError):
            return None

    if arr.ndim == 1:
        # Packed buffer: reshape using the declared size and pixel format.
        if size is None:
            return None
        channels = _CHANNELS.get(colorfmt.lower())
        if channels is None:
            return None
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        expected = width * height * channels
        if arr.size < expected:
            # Truncated buffer: treat as a failed capture rather than guessing.
            return None
        arr = arr[:expected].reshape(height, width, channels)
    elif arr.ndim not in (2, 3):
        return None

    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8, copy=False)

    if arr.ndim == 3:
        depth = arr.shape[2]
        if depth == 1:
            arr = arr[:, :, 0]
        elif depth in (3, 4):
            arr = arr[:, :, :3]
            if colorfmt.lower() in ("rgba", "rgb"):
                # RGB -> BGR: decode_frame runs cv2.cvtColor(..., BGR2GRAY).
                arr = arr[:, :, ::-1]
        else:
            return None

    if flip_vertical:
        arr = arr[::-1, ...]
    if flip_horizontal:
        arr = arr[:, ::-1, ...]

    # np.frombuffer yields a read-only array and the slices above are
    # negative-stride views; OpenCV wants a contiguous buffer, so materialize
    # once here — on the single consumed frame only.
    return np.ascontiguousarray(arr)


class KivyDisplay(DisplaySink):
    """A :class:`DisplaySink` that renders QR frames into a Kivy widget.

    :meth:`show` is called from the optical channel's sending thread, but Kivy
    textures may only be created and uploaded on the main (UI/GL) thread. So
    ``show`` merely stores the frame and asks ``Clock.schedule_once`` to run the
    upload on the main thread, returning immediately — it never blocks the
    caller for a frame period and never sleeps (``hold`` pacing belongs to
    :class:`~photontcp.optical.channel.OpticalChannel`).

    Frame-replacement semantics are preserved by construction: only one pending
    frame is kept, so a frame superseded before its render is simply dropped and
    the widget always ends up showing the most recently shown image.

    :param widget: The target widget. It must expose a writable ``texture``
        attribute (``kivy.uix.image.Image`` does, as does any widget with a
        ``texture`` property). Injected rather than created here so tests can
        pass a stand-in object and so the app owns the widget tree.
    :param colorfmt: Texture colour format for the uploaded QR bitmap.
        ``"luminance"`` (default) uploads the 2D ``uint8`` bitmap as-is — one
        byte per pixel, no expansion. Pass ``"rgb"`` if a GL backend rejects
        luminance textures. **확인 필요**: luminance texture support on the
        Android GLES backend is not verified here (no device available).
    :param flip_vertical: When ``True`` (default) the texture's vertical
        coordinates are flipped after upload, because a Kivy ``Texture``'s
        origin is bottom-left while the QR bitmap's first row is its top row.
        Set ``False`` if a backend already matches.
    :param scheduler: Callable used to marshal work onto the main thread,
        defaulting to ``kivy.clock.Clock.schedule_once``. It is called as
        ``scheduler(callback, 0)`` and the callback takes one ``dt`` argument.
        Injectable so tests can run the render synchronously.
    :param texture_factory: Callable ``(size=…, colorfmt=…) -> texture`` used to
        build the texture, defaulting to ``Texture.create``. Injectable so tests
        can exercise the render path without a GL context.
    """

    def __init__(
        self,
        widget: object,
        *,
        colorfmt: str = "luminance",
        flip_vertical: bool = True,
        scheduler=None,
        texture_factory=None,
    ) -> None:
        self._widget = widget
        self._colorfmt = colorfmt
        self._flip_vertical = flip_vertical
        self._schedule = scheduler if scheduler is not None else Clock.schedule_once
        self._texture_factory = (
            texture_factory if texture_factory is not None else Texture.create
        )
        self._lock = threading.Lock()
        # The single pending frame: (buffer, (width, height)) or None. Only the
        # latest matters, so a new show overwrites an unrendered one.
        self._pending: tuple[bytes, tuple[int, int]] | None = None
        # True while a render callback is scheduled but has not yet consumed the
        # pending frame; prevents piling up one callback per shown frame.
        self._scheduled = False
        self._closed = False

    def show(self, image: np.ndarray) -> None:
        """Store ``image`` as the current frame and schedule its render.

        :param image: The QR bitmap to display, as produced by
            :func:`photontcp.qr.encode.encode_frame` (2D ``uint8``); an
            ``HxWx1`` array is accepted and squeezed.

        Returns as soon as the pixels are packed (a single contiguous copy),
        without touching the GPU — the actual texture upload runs later on the
        main thread. Calls after :meth:`close`, and unusable images, are
        silently ignored (the channel must not die on a display glitch).
        """
        if self._closed or image is None:
            return
        prepared = self._pack(image)
        if prepared is None:
            return
        with self._lock:
            self._pending = prepared
            needs_schedule = not self._scheduled
            self._scheduled = True
        if needs_schedule:
            # Marshal onto the Kivy main thread; 0 = "next frame".
            self._schedule(self._render, 0)

    def _pack(self, image: np.ndarray) -> tuple[bytes, tuple[int, int]] | None:
        """Convert ``image`` to a ``(buffer, (width, height))`` pair for upload.

        Done on the calling thread — it is a cheap contiguous copy — so the main
        thread only does the GL upload. Returns ``None`` for shapes that cannot
        be uploaded in the configured ``colorfmt``.
        """
        arr = np.asarray(image)
        if arr.ndim == 3 and arr.shape[2] == 1:
            arr = arr[:, :, 0]
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        channels = _CHANNELS.get(self._colorfmt.lower())
        if channels is None:
            return None
        if arr.ndim == 2:
            if channels == 3:
                # Expand gray to RGB only when the backend needs it.
                arr = np.repeat(arr[:, :, None], 3, axis=2)
            elif channels != 1:
                return None
        elif arr.ndim == 3:
            if arr.shape[2] != channels:
                return None
        else:
            return None
        height, width = arr.shape[0], arr.shape[1]
        if height <= 0 or width <= 0:
            return None
        return np.ascontiguousarray(arr).tobytes(), (width, height)

    def _render(self, _dt: float = 0.0) -> None:
        """Upload the pending frame to a texture and attach it to the widget.

        Runs on the Kivy main thread (scheduled by :meth:`show`). Takes the
        pending frame under the lock and clears the scheduled flag in the same
        critical section, so a ``show`` racing this call either replaces the
        frame this callback is about to take (and is rendered by it) or
        schedules a fresh callback — no frame is stranded unrendered.
        """
        with self._lock:
            pending = self._pending
            self._pending = None
            self._scheduled = False
        if pending is None or self._closed:
            return
        buffer, size = pending
        texture = self._texture_factory(size=size, colorfmt=self._colorfmt)
        texture.blit_buffer(buffer, colorfmt=self._colorfmt, bufferfmt="ubyte")
        if self._flip_vertical:
            # Texture origin is bottom-left; the bitmap's first row is its top.
            texture.flip_vertical()
        self._widget.texture = texture
        canvas = getattr(self._widget, "canvas", None)
        if canvas is not None:
            # Repaint even if the widget's texture property is not observed by a
            # canvas instruction binding.
            canvas.ask_update()

    def close(self) -> None:
        """Mark the display closed (idempotent); further shows are discarded.

        The widget itself is not destroyed — it belongs to the app's widget tree
        (and releasing a texture off the main thread is not this object's call).
        """
        self._closed = True
        with self._lock:
            self._pending = None


class KivyCamera(CameraSource):
    """A :class:`CameraSource` fed by a Kivy/``camera4kivy`` preview callback.

    Unlike ``Cv2Camera`` this adapter does not pull from a device: the camera
    pushes. ``camera4kivy``'s ``Preview`` runs a CameraX analysis callback on
    its own thread; that callback calls :meth:`submit_frame`, which stores the
    frame and returns. Only the **latest** frame is kept — there is no queue, so
    a decode loop slower than the camera can never accumulate latency (the same
    goal as ``Cv2Camera``'s bounded drain, reached by replacement instead).

    Wiring it to ``camera4kivy`` looks like this (**확인 필요** — this callback
    signature is ``camera4kivy``'s documented ``Preview`` pixel-analysis API and
    has not been verified on a device here)::

        class QRPreview(Preview):
            def analyze_pixels_callback(
                self, pixels, image_size, image_pos, scale, mirror
            ):
                camera.submit_frame(pixels, image_size, colorfmt="rgba")

        preview.connect_camera(enable_analyze_pixels=True)

    Conversion to a decoder-friendly array happens in :meth:`read`, not in
    :meth:`submit_frame`, so the capture callback stays cheap and the cost is
    paid only for the frame actually consumed.

    :param source: Optional frame source to bind. If it exposes ``connect``, it
        is called as ``source.connect(self.submit_frame)`` at construction and
        ``source.disconnect()`` at :meth:`close`. This is the injection point
        for a real ``Preview`` wrapper *and* for a fake source in tests; a test
        may equally construct with no source and call :meth:`submit_frame`
        directly.
    :param colorfmt: Default pixel format of submitted buffers (``"rgba"`` —
        what ``camera4kivy``'s pixel callback documents). Overridable per call.
    :param flip_vertical: Flip captured rows (see :func:`_frame_to_array`).
    :param flip_horizontal: Flip captured columns — for a mirrored front camera.
    :param copy_on_submit: Copy the submitted buffer inside :meth:`submit_frame`
        (default ``True``). **Buffer ownership** is the reason: conversion is
        deferred to :meth:`read`, so between submit and read the adapter holds
        only a *reference* to pixels it does not own. CameraX/``camera4kivy``
        are free to recycle an analysis buffer as soon as the callback returns,
        and a recycled buffer would be overwritten mid-conversion — surfacing as
        a silent decode failure that looks like an optics/alignment problem
        (``Cv2Camera`` has no such exposure: it materializes an array at grab
        time). The copy is a plain ``memcpy`` on the callback thread — no colour
        conversion, which stays in ``read`` — and only ever one frame's worth,
        since submissions replace rather than queue. Set ``False`` only if the
        source is known to hand out buffers it never touches again.
        **확인 필요**: whether ``camera4kivy`` actually recycles is unverified
        here (no device available), so the safe default is to copy.
    """

    def __init__(
        self,
        source: object | None = None,
        *,
        colorfmt: str = "rgba",
        flip_vertical: bool = False,
        flip_horizontal: bool = False,
        copy_on_submit: bool = True,
    ) -> None:
        self._colorfmt = colorfmt
        self._copy_on_submit = copy_on_submit
        self._flip_vertical = flip_vertical
        self._flip_horizontal = flip_horizontal
        self._lock = threading.Lock()
        # The single latest *unconsumed* capture: (raw, size, colorfmt) or None.
        self._pending: tuple[object, tuple[int, int] | None, str] | None = None
        # Set whenever a new capture lands, so a waiting read() wakes at once.
        self._arrived = threading.Event()
        # The last array read() produced, re-returned while no newer frame is
        # available. Returning the *same object* lets the channel skip a
        # redundant decode via its identity check.
        self._last: np.ndarray | None = None
        self._closed = False
        self._source = source
        if source is not None and hasattr(source, "connect"):
            source.connect(self.submit_frame)

    def submit_frame(
        self,
        pixels: object,
        size: tuple[int, int] | None = None,
        colorfmt: str | None = None,
    ) -> None:
        """Store one captured frame as the latest one (called by the camera).

        :param pixels: The captured pixels — a packed buffer (``bytes``-like) or
            an ``np.ndarray``.
        :param size: ``(width, height)``; required for a packed buffer.
        :param colorfmt: Pixel format, defaulting to the constructor's.

        Runs on the camera's callback thread and does **no colour conversion**
        (that stays in :meth:`read`): it takes ownership of the pixels — see
        ``copy_on_submit`` — overwrites the single pending slot, and returns.
        Overwriting (rather than queueing) is what keeps the link fresh — an
        unconsumed frame is stale by definition. Submissions after :meth:`close`
        are ignored.
        """
        if self._closed or pixels is None:
            return
        entry = (self._own(pixels), size, colorfmt or self._colorfmt)
        with self._lock:
            self._pending = entry
            # Set INSIDE the lock, together with the slot it announces. Signalling
            # outside it allows a stale wake-up: read() could take this frame and
            # clear the event before a late set() lands, leaving the event set with
            # no pending frame, so the next read(timeout=…) would return at once
            # instead of waiting for a genuinely new capture.
            self._arrived.set()

    def _own(self, pixels: object) -> object:
        """Return pixels this adapter owns (a copy unless copying is disabled).

        The camera may recycle its buffer the moment the callback returns; see
        ``copy_on_submit`` in the class docstring. Anything we cannot copy
        cheaply is passed through unchanged rather than dropped.
        """
        if not self._copy_on_submit:
            return pixels
        if isinstance(pixels, np.ndarray):
            return pixels.copy()
        if isinstance(pixels, (bytes, bytearray, memoryview)):
            # bytes is already immutable, but a memoryview/bytearray over a
            # recycled buffer is not — normalize all three to an owned bytes.
            return bytes(pixels)
        try:
            # Any other buffer-protocol object (array.array, a ctypes array, a
            # JNI wrapper): copy through the buffer interface rather than keeping
            # a reference, so the ownership guarantee is not silently limited to
            # the types listed above. ``read`` reshapes flat buffers anyway.
            return bytes(memoryview(pixels))
        except TypeError:
            # Not a buffer at all (a fake source handing out something exotic):
            # nothing to copy, so pass it through unchanged.
            return pixels

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        """Return the freshest captured frame, or ``None`` when there is none.

        :param timeout: Seconds to wait for a *new* frame. ``0`` polls without
            blocking; ``None`` waits until one arrives (or the camera closes).
        :returns: A 2D ``uint8`` gray image or an ``HxWx3`` ``uint8`` BGR image
            — both accepted by :func:`photontcp.qr.decode.decode_frame` — or
            ``None`` if the camera is closed, nothing has ever been captured, or
            the only buffer seen was unusable.

        Behaviour matches the other adapters at both ends: a fresh capture is
        converted and returned (a new array each time, as ``Cv2Camera`` yields),
        and when no *new* frame arrived within ``timeout`` the previous array is
        returned again — modelling a camera that keeps seeing the still-displayed
        QR. Since it is the identical object, the channel's identity check skips
        re-decoding it, and its window de-dup absorbs the re-capture anyway.
        ``None`` is reserved for "no frame at all", which is what ``Cv2Camera``
        returns for a failed grab.
        """
        if self._closed:
            return None
        if not self._arrived.is_set():
            # Wait for a fresh capture; close() also sets the event so a
            # blocking read cannot hang past shutdown.
            self._arrived.wait(timeout)
        if self._closed:
            return None
        with self._lock:
            pending = self._pending
            self._pending = None
            self._arrived.clear()
        if pending is None:
            # No new capture this tick: re-return the last one (same object).
            return self._last
        raw, size, colorfmt = pending
        frame = _frame_to_array(
            raw,
            size,
            colorfmt,
            flip_vertical=self._flip_vertical,
            flip_horizontal=self._flip_horizontal,
        )
        if frame is None:
            # Unusable buffer: treat like a failed grab, but keep the previously
            # good frame available for the next read.
            return self._last
        self._last = frame
        return frame

    def close(self) -> None:
        """Release the frame source and mark the camera closed (idempotent).

        Wakes any blocked :meth:`read` (which then returns ``None``) and drops
        the retained frames so a long-lived app does not hold a capture buffer.
        """
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._pending = None
            self._last = None
        # Unblock a waiting read(); it re-checks _closed and returns None.
        self._arrived.set()
        source = self._source
        self._source = None
        if source is not None and hasattr(source, "disconnect"):
            try:
                source.disconnect()
            except Exception:  # pragma: no cover - defensive teardown
                # Never let a camera-teardown glitch propagate out of close().
                pass
