"""Channel abstraction layer.

Re-exports the :class:`Channel` abstract interface that every transport
implementation must satisfy, plus the in-memory :class:`LoopbackChannel`.
"""

from .base import Channel
from .loopback import LoopbackChannel

__all__ = ["Channel", "LoopbackChannel"]
