"""Channel abstraction layer.

Re-exports the :class:`Channel` abstract interface that every transport
implementation must satisfy, plus the in-memory :class:`LoopbackChannel` and the
QR-image round-tripping :class:`ImageLoopbackChannel`.
"""

from .base import Channel
from .image_loopback import ImageLoopbackChannel
from .loopback import LoopbackChannel

__all__ = ["Channel", "LoopbackChannel", "ImageLoopbackChannel"]
