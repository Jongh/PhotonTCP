"""In-memory loopback :class:`Channel` that round-trips frames through QR images.

:class:`ImageLoopbackChannel` mirrors the full-duplex, queue-based design of
:class:`~photontcp.channel.loopback.LoopbackChannel`, but instead of moving raw
packet bytes between the two peers it actually **encodes every frame into a QR
code image and decodes it back on the receiving side**. The transit medium is
therefore a ``numpy`` image, not bytes: on :meth:`send_frame` the payload is run
through :func:`photontcp.qr.encode.encode_frame` and the resulting 2D ``uint8``
QR array is what gets enqueued; on :meth:`recv_frame` that image is popped and
fed to :func:`photontcp.qr.decode.decode_frame` to recover the original bytes.

This makes the channel a faithful exercise of the real M5 optical codec path —
the same QR encode/decode used by a true camera link — while staying entirely in
process memory and deterministic for tests. Two cross-wired endpoints are built
with :meth:`ImageLoopbackChannel.pair`.

Noise is applied at *send* time, in **image (frame) units**:

* ``loss`` — probability the QR image is silently dropped.
* ``dup`` — probability the QR image is enqueued a second time.
* ``degrade`` — optional ``callable(np.ndarray) -> np.ndarray`` applied to the QR
  image before it is enqueued (e.g. Gaussian noise / blur), to stress the QR
  error-correction's ability to survive a degraded picture.

All randomness is drawn from a single injected :class:`random.Random` seeded via
``pair(seed=...)`` so a given seed (and send sequence) reproduces the same
loss/dup pattern. The global :mod:`random` module is never touched.
"""

from __future__ import annotations

import queue
import random

import numpy as np

from ..qr.decode import decode_frame
from ..qr.encode import encode_frame
from .base import Channel

__all__ = ["ImageLoopbackChannel"]


class ImageLoopbackChannel(Channel):
    """A full-duplex in-memory channel that transports frames as QR images.

    Each instance owns an ``inbox`` (QR images destined for *this* peer) and an
    ``outbox`` (QR images *this* peer sends, i.e. the partner's inbox). The
    queues carry ``numpy`` arrays (QR-code bitmaps), not bytes. Build instances
    in pairs via :meth:`pair`; the constructor is an implementation detail.

    Encoding parameters (``scale``, ``border``, ``error``) are applied to every
    outgoing frame, and noise (``loss``, ``dup``, ``degrade``) is applied at send
    time. All randomness comes from the shared injected :class:`random.Random`.
    """

    def __init__(
        self,
        inbox: "queue.Queue[np.ndarray]",
        outbox: "queue.Queue[np.ndarray]",
        rng: random.Random,
        *,
        loss: float = 0.0,
        dup: float = 0.0,
        scale: int = 8,
        border: int = 4,
        error: str = "m",
        degrade=None,
    ) -> None:
        self._inbox = inbox
        self._outbox = outbox
        self._rng = rng

        self.loss = loss
        self.dup = dup
        self.scale = scale
        self.border = border
        self.error = error
        self.degrade = degrade

        self._closed = False

    @classmethod
    def pair(
        cls,
        *,
        seed: int | None = None,
        loss: float = 0.0,
        dup: float = 0.0,
        scale: int = 8,
        border: int = 4,
        error: str = "m",
        degrade=None,
    ) -> tuple["ImageLoopbackChannel", "ImageLoopbackChannel"]:
        """Create two cross-wired QR-image channels sharing one noise profile.

        :param seed: Seed for the shared :class:`random.Random`. Equal seeds
            (and equal send sequences) reproduce the same loss/dup pattern
            exactly. ``None`` leaves the RNG unseeded (non-deterministic).
        :param loss: Per-frame (per-image) drop probability in ``[0, 1]``.
        :param dup: Per-frame (per-image) duplication probability in ``[0, 1]``.
        :param scale: QR module pixel size, passed to :func:`encode_frame`.
        :param border: QR quiet-zone width in modules, passed to
            :func:`encode_frame`.
        :param error: QR error-correction level (segno notation), passed to
            :func:`encode_frame`.
        :param degrade: Optional ``callable(np.ndarray) -> np.ndarray`` applied
            to each outgoing QR image (e.g. blur / Gaussian noise). ``None``
            leaves the image unmodified.
        :returns: A tuple ``(a, b)`` of connected channels. Frames sent by ``a``
            are received (after QR round-trip) by ``b`` and vice versa.

        Both endpoints share a *single* RNG instance so the simulated link has
        one deterministic noise stream regardless of direction.
        """
        q_ab: "queue.Queue[np.ndarray]" = queue.Queue()
        q_ba: "queue.Queue[np.ndarray]" = queue.Queue()
        rng = random.Random(seed)

        opts = dict(
            loss=loss,
            dup=dup,
            scale=scale,
            border=border,
            error=error,
            degrade=degrade,
        )

        # A sends to q_ab and receives from q_ba; B is the mirror image.
        a = cls(inbox=q_ba, outbox=q_ab, rng=rng, **opts)
        b = cls(inbox=q_ab, outbox=q_ba, rng=rng, **opts)
        return a, b

    def send_frame(self, frame: bytes) -> None:
        """Encode ``frame`` to a QR image and enqueue it to the partner's inbox.

        The bytes are encoded via :func:`encode_frame` (using this channel's
        ``scale``/``border``/``error``). The ``loss`` decision is made first (a
        dropped frame is gone); surviving frames then have the optional
        ``degrade`` callable applied to the image, and ``dup`` may enqueue a
        second copy. A closed channel silently discards sends.
        """
        if self._closed:
            return

        # Decide loss before doing any (relatively expensive) encoding work.
        if self._rng.random() < self.loss:
            return

        image = encode_frame(
            frame,
            scale=self.scale,
            border=self.border,
            error=self.error,
        )
        if self.degrade is not None:
            image = self.degrade(image)

        self._outbox.put(image)
        if self._rng.random() < self.dup:
            self._outbox.put(image)

    def recv_frame(self, timeout: float | None = None) -> bytes | None:
        """Pop the next QR image from this peer's inbox and decode it.

        :param timeout: Seconds to wait. ``None`` blocks indefinitely; ``0``
            polls. Returns ``None`` if no image is available within ``timeout``.

        The popped image is decoded with :func:`decode_frame`. **A decode
        failure (``None``) is treated as a lost frame**: the offending image is
        discarded and this method returns ``None`` immediately (it does not
        retry within the remaining timeout). This is safe because the reliability
        (ARQ) layer above will retransmit the frame; from the channel's point of
        view an undecodable QR is indistinguishable from a dropped frame.
        """
        if self._closed:
            return None
        try:
            if timeout is None:
                image = self._inbox.get(block=True)
            else:
                image = self._inbox.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

        # Decode failure -> frame is considered lost (ARQ will retransmit).
        return decode_frame(image)

    def close(self) -> None:
        """Mark the channel closed (idempotent).

        After closing, :meth:`send_frame` discards frames and
        :meth:`recv_frame` returns ``None`` immediately.
        """
        self._closed = True
