"""PhotonTCP — a TCP-like reliable transport over a pluggable Channel abstraction.

All higher layers depend only on the swappable :class:`~photontcp.channel.Channel`
interface and a fixed-format packet, so transports (loopback, optical, etc.) can be
substituted without changing session or reliability logic.
"""

__version__ = "0.1.0"
