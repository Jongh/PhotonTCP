"""Display / camera device abstractions for the real optical channel (M8).

The optical channel transports QR frames over light: outgoing frames are shown
on a **display** and incoming frames are captured by a **camera**. To keep the
channel logic testable without any hardware, those two roles are expressed as
small abstract interfaces — :class:`DisplaySink` and :class:`CameraSource` — and
the channel depends only on them. Real hardware adapters (``cv2_devices.py``) and
in-memory fakes (below) both satisfy the same contract, so the exact same
``OpticalChannel`` code runs on a webcam+screen link or fully in process memory.

Two fakes are provided for deterministic tests:

* :class:`MemoryDisplay` — its :meth:`show` pushes the image onto a shared queue.
* :class:`MemoryCamera` — its :meth:`read` pops from that queue, and (with
  ``repeat_last=True``, the default) keeps returning the most recently captured
  frame when no newer one is available. That re-capture behaviour mirrors a real
  camera, which reads the *currently displayed* QR many times before the display
  advances to the next frame — exactly the case the channel's frame de-duplication
  must handle.

Use :func:`memory_device_pair` to build a connected ``(display, camera)`` one-way
link; :meth:`photontcp.optical.channel.OpticalChannel.pair` wires two of them
crosswise into a full-duplex in-memory optical link.
"""

from __future__ import annotations

import abc
import queue

import numpy as np

__all__ = [
    "DisplaySink",
    "CameraSource",
    "MemoryDisplay",
    "MemoryCamera",
    "memory_device_pair",
]


class DisplaySink(abc.ABC):
    """A sink that renders one QR-frame image at a time.

    The optical channel calls :meth:`show` for every outgoing frame. An
    implementation displays the image however its medium allows (a window, a
    physical screen, an in-memory queue) and must treat each call as replacing
    whatever was shown before — only the most recently shown frame matters.
    """

    @abc.abstractmethod
    def show(self, image: np.ndarray) -> None:
        """Render ``image`` (a 2D ``uint8`` QR bitmap) as the current frame.

        :param image: The QR-code image to display, as produced by
            :func:`photontcp.qr.encode.encode_frame`.

        The call replaces any previously shown frame. Whether it blocks is
        implementation-defined, but it must not partially render a frame.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """Release the display's resources (idempotent)."""
        raise NotImplementedError


class CameraSource(abc.ABC):
    """A source that captures QR-frame images one at a time.

    The optical channel's background capture loop calls :meth:`read` repeatedly.
    Each call returns the most recent captured frame (or ``None`` if none became
    available within ``timeout``). A real camera returns a fresh image array on
    every call — even when pointed at an unchanged display — so the channel is
    responsible for de-duplicating identical decoded frames.
    """

    @abc.abstractmethod
    def read(self, timeout: float | None = None) -> np.ndarray | None:
        """Return the latest captured frame, or ``None`` on timeout.

        :param timeout: Maximum seconds to wait for a frame. ``None`` blocks
            indefinitely; ``0`` polls without blocking.
        :returns: A captured image (2D ``uint8`` grayscale, or ``HxWx3`` BGR as a
            real camera yields), or ``None`` if nothing was available in time.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """Release the camera's resources (idempotent)."""
        raise NotImplementedError


class MemoryDisplay(DisplaySink):
    """In-memory :class:`DisplaySink` that enqueues shown frames for a camera.

    Each :meth:`show` puts the image on a shared :class:`queue.Queue` that the
    paired :class:`MemoryCamera` drains. The queue buffers frames so that none is
    lost even if the producer outruns the consumer — keeping the in-memory link
    reliable for deterministic tests (a real optical link can drop frames; that
    impairment is already covered by the loopback channels).
    """

    def __init__(self, sink: "queue.Queue[np.ndarray]") -> None:
        self._sink = sink
        self._closed = False

    def show(self, image: np.ndarray) -> None:
        """Enqueue ``image`` for the paired camera. Closed displays discard."""
        if self._closed:
            return
        self._sink.put(image)

    def close(self) -> None:
        """Mark the display closed (idempotent); further shows are discarded."""
        self._closed = True


class MemoryCamera(CameraSource):
    """In-memory :class:`CameraSource` that reads frames from a shared queue.

    :meth:`read` pops the next frame a :class:`MemoryDisplay` enqueued. When the
    queue is empty it either re-returns the last captured frame (``repeat_last``,
    the default — modelling a camera that keeps seeing the still-displayed QR) or
    waits up to ``timeout`` and returns ``None``.

    When a repeat is returned it is the **same array object** as before, so the
    channel can cheaply skip re-decoding it via an identity check; a real camera,
    by contrast, returns a distinct array each capture.
    """

    def __init__(
        self,
        source: "queue.Queue[np.ndarray]",
        *,
        repeat_last: bool = True,
    ) -> None:
        self._source = source
        self._repeat_last = repeat_last
        self._last: np.ndarray | None = None
        self._closed = False

    #: Lower bound (seconds) for the queue wait when ``repeat_last`` is active and
    #: the caller passes a falsy timeout. A pathological direct caller looping
    #: ``while True: read(0)`` would otherwise poll with a 0.0 timeout and peg a
    #: core; flooring the wait here yields the CPU between idle ticks while still
    #: returning a freshly enqueued frame promptly. ``OpticalChannel`` always
    #: passes ``poll_interval`` (>= 1e-3 after its own clamp), so this floor never
    #: changes the timeout the channel asked for and the existing optical tests are
    #: unaffected — it only protects misuse by direct, tight-loop callers.
    _MIN_REPEAT_WAIT = 1e-3

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        """Return the next enqueued frame, or repeat/``None`` when idle.

        A newly enqueued frame is returned as soon as it arrives (updating the
        remembered "last" frame). If none arrives within ``timeout``, the last
        captured frame is returned again when ``repeat_last`` is set, otherwise
        ``None``. A closed camera always returns ``None``.

        When ``repeat_last`` is set and ``timeout`` is falsy (``0`` or ``None``),
        the queue wait is floored to :attr:`_MIN_REPEAT_WAIT` so a direct caller
        polling in a tight loop cannot busy-spin on a 0.0-second wait; a newly
        enqueued frame is still returned as soon as it arrives. Semantics are
        otherwise unchanged.
        """
        if self._closed:
            return None
        try:
            if timeout is None and not self._repeat_last:
                image = self._source.get(block=True)
            else:
                # Bounded wait so an idle camera can fall through to a repeat
                # (or None) instead of blocking forever on the empty queue. When
                # repeating with a falsy timeout, floor the wait to a small
                # positive value so a tight direct-poll loop does not spin.
                if self._repeat_last and not timeout:
                    wait = self._MIN_REPEAT_WAIT
                else:
                    wait = timeout or 0.0
                image = self._source.get(block=True, timeout=wait)
        except queue.Empty:
            # No new frame this tick: model re-capturing the still-displayed QR.
            return self._last if self._repeat_last else None
        self._last = image
        return image

    def close(self) -> None:
        """Mark the camera closed (idempotent); further reads return ``None``."""
        self._closed = True


def memory_device_pair(
    *,
    repeat_last: bool = True,
) -> tuple[MemoryDisplay, MemoryCamera]:
    """Build a connected one-way ``(display, camera)`` in-memory optical link.

    Frames shown on the returned :class:`MemoryDisplay` are captured by the
    returned :class:`MemoryCamera`. Two such pairs, cross-wired, form a
    full-duplex link (see
    :meth:`photontcp.optical.channel.OpticalChannel.pair`).

    :param repeat_last: Passed to the camera — when set, an idle camera re-returns
        the last frame (re-capture simulation).
    :returns: ``(display, camera)`` sharing one frame queue.
    """
    q: "queue.Queue[np.ndarray]" = queue.Queue()
    return MemoryDisplay(q), MemoryCamera(q, repeat_last=repeat_last)
