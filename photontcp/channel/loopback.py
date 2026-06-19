"""In-memory loopback :class:`Channel` with optional noise simulation.

:class:`LoopbackChannel` connects two peers entirely in process memory using a
pair of thread-safe :class:`queue.Queue` instances. It is the workhorse for unit
and integration tests of the upper PhotonTCP layers: it needs no sockets or
hardware, runs deterministically, and can faithfully reproduce the kinds of
impairments a real optical link exhibits — frame loss, duplication, bit
corruption, and reordering.

Two channels are created together with :meth:`LoopbackChannel.pair`, which wires
their inbound/outbound queues crosswise so that whatever one peer sends the other
receives (full duplex). All noise is applied at *send* time so that the receiving
side simply drains whatever survived transmission.

Reproducibility is a hard requirement: every random decision is drawn from a
single injected :class:`random.Random` instance seeded via ``pair(seed=...)``.
The same seed (and the same sequence of sends) always yields the same pattern of
loss/dup/corrupt/reorder. The global :mod:`random` module is never touched, and
no time-based seeding is performed.
"""

from __future__ import annotations

import queue
import random

from .base import Channel

__all__ = ["LoopbackChannel"]


class LoopbackChannel(Channel):
    """A bidirectional in-memory channel between two peers.

    An instance owns an ``inbox`` (frames destined for *this* peer) and an
    ``outbox`` (frames *this* peer sends, i.e. the partner's inbox). Construct
    instances in pairs via :meth:`pair` rather than directly; the constructor is
    primarily an implementation detail.

    Noise parameters are applied in :meth:`send_frame`:

    * ``loss`` — probability in ``[0, 1]`` that a frame is silently dropped.
    * ``dup`` — probability that a delivered frame is enqueued a second time.
    * ``corrupt`` — probability that a single byte of the frame is flipped
      (XOR-ed with a random non-zero mask) before delivery.
    * ``reorder`` — probability that a frame is held back one slot, swapping its
      delivery order with the following frame (a deterministic one-slot delay
      buffer).
    * ``latency`` / ``jitter`` — accepted for forward compatibility; see the
      module/class notes. They are currently *not* applied to delivery timing.

    All randomness comes from the shared :class:`random.Random` ``rng`` passed in
    by :meth:`pair`, guaranteeing deterministic replay for a given seed.
    """

    def __init__(
        self,
        inbox: "queue.Queue[bytes]",
        outbox: "queue.Queue[bytes]",
        rng: random.Random,
        *,
        loss: float = 0.0,
        dup: float = 0.0,
        corrupt: float = 0.0,
        reorder: float = 0.0,
        latency: float = 0.0,
        jitter: float = 0.0,
    ) -> None:
        self._inbox = inbox
        self._outbox = outbox
        self._rng = rng

        self.loss = loss
        self.dup = dup
        self.corrupt = corrupt
        self.reorder = reorder
        self.latency = latency
        self.jitter = jitter

        # One-slot delay buffer used to implement deterministic reordering.
        self._reorder_held: bytes | None = None

        self._closed = False

    @classmethod
    def pair(
        cls,
        *,
        seed: int | None = None,
        loss: float = 0.0,
        dup: float = 0.0,
        corrupt: float = 0.0,
        reorder: float = 0.0,
        latency: float = 0.0,
        jitter: float = 0.0,
    ) -> tuple["LoopbackChannel", "LoopbackChannel"]:
        """Create two cross-wired channels sharing the same noise profile.

        :param seed: Seed for the shared :class:`random.Random`. Equal seeds (and
            equal send sequences) reproduce the same loss/dup/corrupt/reorder
            pattern exactly. ``None`` leaves the RNG unseeded (non-deterministic).
        :param loss: Per-frame drop probability.
        :param dup: Per-frame duplication probability.
        :param corrupt: Per-frame single-byte corruption probability.
        :param reorder: Per-frame one-slot reorder probability.
        :param latency: Reserved; accepted but not applied to timing.
        :param jitter: Reserved; accepted but not applied to timing.
        :returns: A tuple ``(a, b)`` of connected channels. Frames sent by ``a``
            are received by ``b`` and vice versa.

        Both endpoints share a *single* RNG instance so the simulated link has
        one deterministic noise stream regardless of direction.
        """
        q_ab: "queue.Queue[bytes]" = queue.Queue()
        q_ba: "queue.Queue[bytes]" = queue.Queue()
        rng = random.Random(seed)

        noise = dict(
            loss=loss,
            dup=dup,
            corrupt=corrupt,
            reorder=reorder,
            latency=latency,
            jitter=jitter,
        )

        # A sends to q_ab and receives from q_ba; B is the mirror image.
        a = cls(inbox=q_ba, outbox=q_ab, rng=rng, **noise)
        b = cls(inbox=q_ab, outbox=q_ba, rng=rng, **noise)
        return a, b

    def _corrupt(self, frame: bytes) -> bytes:
        """Return ``frame`` with one random byte XOR-flipped (or unchanged)."""
        if not frame:
            return frame
        idx = self._rng.randrange(len(frame))
        mask = self._rng.randint(1, 255)
        buf = bytearray(frame)
        buf[idx] ^= mask
        return bytes(buf)

    def send_frame(self, frame: bytes) -> None:
        """Apply the noise profile and enqueue ``frame`` to the partner's inbox.

        Order of operations: loss is decided first (a dropped frame is gone),
        then corruption is applied, then duplication, then reordering through the
        one-slot delay buffer. A closed channel silently discards sends.
        """
        if self._closed:
            return

        # Reordering uses a one-slot hold buffer regardless of loss, so that a
        # dropped frame can still release a previously held frame.
        emit: list[bytes] = []

        if self._rng.random() < self.loss:
            survivors: list[bytes] = []
        else:
            f = frame
            if self._rng.random() < self.corrupt:
                f = self._corrupt(f)
            survivors = [f]
            if self._rng.random() < self.dup:
                survivors.append(f)

        for f in survivors:
            if self._rng.random() < self.reorder:
                # Hold this frame back one slot. If something was already held,
                # release the older one first (then this becomes the new held).
                if self._reorder_held is not None:
                    emit.append(self._reorder_held)
                self._reorder_held = f
            else:
                # Non-reordered frame: anything held is older, so it goes first,
                # then this frame, restoring an interleaved-but-swapped order.
                if self._reorder_held is not None:
                    emit.append(self._reorder_held)
                    self._reorder_held = None
                emit.append(f)

        for f in emit:
            self._outbox.put(f)

    def recv_frame(self, timeout: float | None = None) -> bytes | None:
        """Pop the next frame from this peer's inbox.

        :param timeout: Seconds to wait. ``None`` blocks indefinitely; ``0``
            polls. Returns ``None`` if no frame is available within ``timeout``.
        """
        if self._closed:
            return None
        try:
            if timeout is None:
                return self._inbox.get(block=True)
            return self._inbox.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        """Mark the channel closed (idempotent).

        After closing, :meth:`send_frame` discards frames and
        :meth:`recv_frame` returns ``None`` immediately. Any frame currently held
        in the reorder buffer is dropped.
        """
        self._closed = True
        self._reorder_held = None
