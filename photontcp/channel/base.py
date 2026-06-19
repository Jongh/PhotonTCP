"""Abstract :class:`Channel` interface.

A ``Channel`` is the lowest layer of PhotonTCP: a bidirectional, frame-oriented
byte transport. One *frame* corresponds to exactly one serialized packet. Higher
layers (session, reliability) depend only on this interface, so any concrete
transport — an in-memory loopback, a socket, or real optical hardware — can be
swapped in without changes upstream.
"""

from __future__ import annotations

import abc

__all__ = ["Channel"]


class Channel(abc.ABC):
    """Bidirectional, frame-oriented byte transport.

    Implementations move opaque byte frames between two endpoints. Each frame is
    delivered as a whole (or not at all); the channel does not interpret packet
    contents and provides no reliability guarantees of its own.
    """

    @abc.abstractmethod
    def send_frame(self, frame: bytes) -> None:
        """Send a single frame.

        :param frame: One serialized packet (the output of a packet ``pack()``)
            to transmit to the peer endpoint.

        The frame is treated as an indivisible unit. Whether the call blocks
        until the frame is handed off depends on the implementation, but it must
        not partially send a frame. No delivery guarantee is implied: a lossy or
        noisy transport may drop, duplicate, or corrupt the frame.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def recv_frame(self, timeout: float | None = None) -> bytes | None:
        """Receive a single frame.

        :param timeout: Maximum number of seconds to wait for a frame. ``None``
            (the default) blocks indefinitely until a frame arrives or the
            channel is closed. ``0`` polls without blocking.
        :returns: The next received frame, or ``None`` if no frame became
            available within ``timeout``.

        Each call returns at most one whole frame.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """Release the channel's resources.

        After ``close()`` the channel must not be used to send or receive
        frames. Implementations should make this idempotent: calling ``close()``
        more than once is harmless.
        """
        raise NotImplementedError
