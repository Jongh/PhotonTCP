"""CRC32 integrity helpers for PhotonTCP packets.

This module provides a thin wrapper around the standard library
:func:`zlib.crc32` so the rest of the codebase has a single, well-typed
entry point for computing and verifying packet checksums. The wrapper
normalises the result to an unsigned 32-bit integer, which is what the
packet header stores in its ``crc32`` field.

Only the Python standard library (:mod:`zlib`) is used.
"""

from __future__ import annotations

import zlib

__all__ = ["crc32", "verify"]


def crc32(data: bytes) -> int:
    """Compute the CRC32 checksum of ``data`` as an unsigned 32-bit integer.

    Wraps :func:`zlib.crc32` and masks the result with ``0xFFFFFFFF`` so the
    return value is always a non-negative integer in the range
    ``0 .. 0xFFFFFFFF``, regardless of platform-specific signedness.

    Args:
        data: The bytes to checksum.

    Returns:
        The CRC32 checksum as an unsigned 32-bit integer.
    """
    return zlib.crc32(data) & 0xFFFFFFFF


def verify(data: bytes, expected: int) -> bool:
    """Check whether ``data`` matches an ``expected`` CRC32 checksum.

    Args:
        data: The bytes whose checksum is to be verified.
        expected: The previously computed unsigned 32-bit CRC32 value to
            compare against.

    Returns:
        ``True`` if ``crc32(data)`` equals ``expected``, otherwise ``False``.
    """
    return crc32(data) == expected
