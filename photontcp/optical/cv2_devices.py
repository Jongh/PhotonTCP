"""Real hardware device adapters for the optical channel (M8, cv2-backed).

This module provides the *physical* implementations of the device abstractions
defined in :mod:`photontcp.optical.devices`:

* :class:`Cv2Display` — a :class:`~photontcp.optical.devices.DisplaySink` that
  renders each QR frame in an OpenCV window (``cv2.imshow``) on a real screen.
* :class:`Cv2Camera` — a :class:`~photontcp.optical.devices.CameraSource` that
  captures frames from a real webcam via ``cv2.VideoCapture``.

Together with the in-memory fakes (``MemoryDisplay``/``MemoryCamera``) these let
the *same* :class:`~photontcp.optical.channel.OpticalChannel` run either fully in
process memory (deterministic tests) or over an actual screen+webcam light link.

**Import safety.** Importing this module touches **no hardware** — ``import cv2``
at the top merely loads the library; no window is opened and no camera is probed
at import or class-definition time. All device access is deferred to instance
construction (``__init__``) or per-call methods (``show``/``read``/``close``).
This means the module imports cleanly on a machine that has ``opencv-python``
installed but **no camera and no display** (the M8 completion criterion), so the
test suite can ``pytest.importorskip("cv2")`` and import this module without
needing real devices. The actual hardware paths are exercised manually / from
``examples/optical_link.py --real``.
"""

from __future__ import annotations

import cv2
import numpy as np

from .devices import CameraSource, DisplaySink

__all__ = ["Cv2Display", "Cv2Camera"]


class Cv2Display(DisplaySink):
    """A :class:`DisplaySink` that renders QR frames in an OpenCV window.

    The named window is created lazily on the first :meth:`show` call (not at
    construction time) so that merely instantiating the display — or importing
    this module — never touches the GUI subsystem. Each :meth:`show` renders one
    frame and pumps the OpenCV event loop so it actually appears on screen.

    :param window: The OpenCV window name to render into (also the on-screen
        title). Reused for the lifetime of this display.
    :param fullscreen: When ``True``, the window is created in fullscreen mode
        (via ``cv2.WND_PROP_FULLSCREEN``) on first :meth:`show` — useful for a
        clean, distraction-free QR surface that a camera can frame precisely.
    """

    def __init__(self, window: str = "PhotonTCP", *, fullscreen: bool = False) -> None:
        self._window = window
        self._fullscreen = fullscreen
        # Whether the named window has been created yet. Creating it lazily keeps
        # construction (and import) hardware/GUI-free.
        self._created = False
        self._closed = False

    def _ensure_window(self) -> None:
        """Create the named window on first use, applying the fullscreen option.

        Idempotent: ``cv2.namedWindow`` on an existing name is a no-op, but we
        guard with ``_created`` so the fullscreen property is only set once.
        """
        if self._created:
            return
        cv2.namedWindow(self._window, cv2.WINDOW_NORMAL)
        if self._fullscreen:
            cv2.setWindowProperty(
                self._window,
                cv2.WND_PROP_FULLSCREEN,
                cv2.WINDOW_FULLSCREEN,
            )
        self._created = True

    def show(self, image: np.ndarray) -> None:
        """Render ``image`` as the current frame in the window.

        :param image: The QR-code image to display, as produced by
            :func:`photontcp.qr.encode.encode_frame` (a 2D ``uint8`` bitmap).

        After ``cv2.imshow`` we call ``cv2.waitKey(1)``: OpenCV's HighGUI only
        actually paints the window from inside its event loop, and ``waitKey`` is
        what pumps that loop. Without it the ``imshow`` is buffered and the screen
        never updates (so the camera would never see the new QR). A 1 ms wait is
        the minimal pump. Calls after :meth:`close` are silently ignored.
        """
        if self._closed:
            return
        self._ensure_window()
        cv2.imshow(self._window, image)
        # REQUIRED: pump the HighGUI event loop so the frame is actually drawn.
        cv2.waitKey(1)

    def close(self) -> None:
        """Destroy the window and mark the display closed (idempotent).

        Safe to call when the window was never created (``destroyWindow`` on an
        unknown name can raise on some backends) — the destroy is guarded so a
        never-shown display closes cleanly. After closing, :meth:`show` is a
        no-op.
        """
        if self._closed:
            return
        self._closed = True
        try:
            cv2.destroyWindow(self._window)
        except cv2.error:
            # Window was never created (or already gone): nothing to destroy.
            pass


class Cv2Camera(CameraSource):
    """A :class:`CameraSource` that captures frames from a real webcam.

    Opens a ``cv2.VideoCapture`` on construction. Each :meth:`read` grabs the
    next available frame as a BGR ``HxWx3 uint8`` ``ndarray`` and returns it
    unchanged — :func:`photontcp.qr.decode.decode_frame` already accepts a
    3-channel BGR image and converts it to grayscale internally, so no color
    conversion is needed here (and doing it lazily there avoids an extra copy
    on dropped/undecodable frames).

    **Frame freshness (buffer staleness fix).** Many capture backends keep a
    small internal FIFO of frames. When the consumer (a QR decode loop) is
    slower than the camera's frame rate, a plain ``cap.read()`` returns the
    *oldest* buffered frame, so latency accumulates and the QR being decoded
    lags the QR actually on screen. Two best-effort mitigations are applied:

    * On construction we request a 1-frame driver buffer
      (``CAP_PROP_BUFFERSIZE = 1``) so the backend keeps as little history as
      possible. Many backends silently ignore this, hence "best-effort".
    * :meth:`read` can *drain to the latest frame* (``drain_to_latest=True``,
      the default): after the initial read it performs a small **fixed** number
      of extra ``grab()``/``retrieve()`` cycles to flush stale buffered frames
      and return the freshest one. The bound (:data:`_MAX_DRAIN`) guarantees the
      drain is finite — no unbounded loop, no excessive added latency.

    :param index: The camera device index passed to ``cv2.VideoCapture``
        (``0`` is the default/first webcam).
    :param api_preference: Optional OpenCV capture backend constant (e.g.
        ``cv2.CAP_DSHOW`` on Windows, ``cv2.CAP_V4L2`` on Linux). When given it
        is passed as the second ``VideoCapture`` argument; otherwise OpenCV
        picks a backend automatically.
    :param width: Optional capture frame width hint
        (``CAP_PROP_FRAME_WIDTH``). Best-effort — the backend may clamp it to a
        supported mode or ignore it. A larger frame helps the QR modules resolve
        finder patterns when the displayed code is small in the camera's view.
    :param height: Optional capture frame height hint
        (``CAP_PROP_FRAME_HEIGHT``). Best-effort, same caveats as ``width``.
    :param drain_to_latest: When ``True`` (default), :meth:`read` discards stale
        buffered frames (up to :data:`_MAX_DRAIN` extra grabs) and returns the
        freshest available frame, reducing capture latency. When ``False`` it
        falls back to a single ``cap.read()`` (the oldest buffered frame), which
        preserves the original M8 behaviour for callers that prefer it.
    :raises RuntimeError: If the capture device fails to open (e.g. no camera at
        ``index``), with the offending index named in the message.
    """

    #: Upper bound on the number of *extra* ``grab()`` calls :meth:`read`
    #: performs to flush stale buffered frames before retrieving the latest one.
    #: It MUST be a small fixed constant: ``grab()`` returns ``True`` whenever a
    #: frame is available, so looping on its truthiness would spin (and add
    #: latency) for as long as the camera keeps producing frames. A fixed bound
    #: makes the drain provably finite (no unbounded loop / runaway latency)
    #: while still flushing a typical short driver FIFO.
    _MAX_DRAIN = 4

    def __init__(
        self,
        index: int = 0,
        *,
        api_preference: int | None = None,
        width: int | None = None,
        height: int | None = None,
        drain_to_latest: bool = True,
    ) -> None:
        self._index = index
        self._drain_to_latest = drain_to_latest
        if api_preference is None:
            cap = cv2.VideoCapture(index)
        else:
            cap = cv2.VideoCapture(index, api_preference)
        if not cap.isOpened():
            # Release the (failed) handle before raising so we don't leak it.
            cap.release()
            raise RuntimeError(f"failed to open camera at index {index!r}")
        # Best-effort: ask the backend for a 1-frame buffer to reduce stale-frame
        # latency. Many backends ignore CAP_PROP_BUFFERSIZE (set() returns False)
        # or even raise, so we guard it and never fail construction over it — the
        # drain in read() is the real safety net.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except cv2.error:
            pass
        # Best-effort resolution hints (guarded for the same reason): the backend
        # may snap to the nearest supported mode or ignore these entirely.
        if width is not None:
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            except cv2.error:
                pass
        if height is not None:
            try:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            except cv2.error:
                pass
        self._cap = cap
        self._closed = False

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        """Capture and return the freshest frame, or ``None`` on failure/close.

        :param timeout: Accepted only for interface compatibility with
            :meth:`CameraSource.read`. A real ``cv2.VideoCapture.read()`` blocks
            for roughly one frame period and exposes **no native timeout**, so we
            do not implement a timer here: this method returns as soon as the
            camera yields (or fails to yield) a frame. The value is otherwise
            unused.
        :returns: The captured frame as a BGR ``HxWx3 uint8`` ``ndarray``, or
            ``None`` if the grab failed or the camera has been closed. The frame
            is returned **as-is** (BGR, not converted to grayscale) because
            :func:`photontcp.qr.decode.decode_frame` handles the conversion.

        When ``drain_to_latest`` is set (the constructor default), this does one
        ``read()`` and then up to :data:`_MAX_DRAIN` extra ``grab()``/
        ``retrieve()`` cycles, keeping the last successfully retrieved frame.
        ``grab()`` only fetches (without decoding) so the flush is cheap, and the
        fixed :data:`_MAX_DRAIN` bound guarantees the loop terminates — it never
        spins on ``grab()`` truthiness (which stays ``True`` while the camera
        keeps producing frames). The net effect is the *freshest* buffered frame
        with bounded added latency. With ``drain_to_latest=False`` it does a
        single ``read()`` and returns that (the original M8 behaviour).
        """
        if self._closed:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            # Grab failed (transient glitch / end of stream): treat as no frame.
            # The capture loop above will simply read again next tick.
            return None
        if self._drain_to_latest:
            # Flush stale buffered frames with a FIXED number of grab attempts so
            # the drain is provably finite (see _MAX_DRAIN). Each successful grab
            # is decoded via retrieve() and supersedes the previous frame; we stop
            # early as soon as a grab/retrieve fails (no newer frame buffered).
            for _ in range(self._MAX_DRAIN):
                if not self._cap.grab():
                    break
                ok2, f2 = self._cap.retrieve()
                if ok2 and f2 is not None:
                    frame = f2
                else:
                    break
        return frame

    def close(self) -> None:
        """Release the capture device and mark the camera closed (idempotent).

        After closing, :meth:`read` returns ``None``. The release is guarded so a
        double close (or a release error) never propagates.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._cap.release()
        except cv2.error:
            # Already released / backend hiccup: nothing more to do.
            pass
