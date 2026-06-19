"""Packet layer — fixed-format packet types, flags, CRC, and (de)serialization.

Re-exports the public packet API so callers can use ``photontcp.packet.Packet``
instead of reaching into submodules.
"""

from .crc import crc32, verify
from .header import (
    HEADER_FORMAT,
    HEADER_SIZE,
    ChecksumError,
    MalformedPacketError,
    Packet,
    PacketError,
)
from .types import PROTOCOL_VERSION, Flags, PacketType

__all__ = [
    "PROTOCOL_VERSION",
    "PacketType",
    "Flags",
    "crc32",
    "verify",
    "Packet",
    "PacketError",
    "ChecksumError",
    "MalformedPacketError",
    "HEADER_FORMAT",
    "HEADER_SIZE",
]
