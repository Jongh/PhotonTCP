"""File-transfer frame codec, reassembler, and SHA-256 helper.

This module defines the wire format for the single-stream interleaved
file-transfer protocol (milestone M6) and the logic to encode/decode
frames over a reliable, ordered byte stream.

Wire format (per frame)::

    +----------------------+----------------+---------------------------+
    | length prefix        | type byte      | body                      |
    | 4 bytes, big-endian  | 1 byte, 0..255 | ``length - 1`` bytes long |
    | unsigned (struct >I) | FileFrameType  |                           |
    +----------------------+----------------+---------------------------+

The 4-byte length prefix counts ``1 (type byte) + len(body)`` -- that is,
every byte after the prefix. This is the same length-prefixed framing as
the chat codec (``app.codec``), with one extra leading type byte so that
control (JSON) and chunk (binary) frames can be interleaved on one stream.

Shared protocol (all M6 tasks must honor this):

* **Frame format**: ``4-byte big-endian length`` + ``1-byte type`` +
  ``body``; ``length == 1 + len(body)``.
* **Frame types** (:class:`FileFrameType`): ``OFFER``, ``ACCEPT``,
  ``REJECT``, ``CHUNK``, ``DONE``, ``ACK``, ``NACK``.
* **Control bodies** are JSON (UTF-8). ``OFFER`` body =
  ``{"name": str, "size": int, "sha256": hex str}``. ``DONE``/``ACK``/
  ``NACK`` bodies = JSON (empty object, or ``{"reason": ...}`` /
  ``{"ok": bool}``).
* **CHUNK body** = raw file bytes (no chunk index -- the underlying stream
  preserves order, so chunks are simply concatenated on arrival).

Because the underlying stream guarantees reliability and ordering, this
codec deals only with framing -- never retransmission or reordering.

Pure standard library only: ``json``, ``struct``, ``hashlib``, ``enum``,
``dataclasses``.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from enum import IntEnum

__all__ = [
    "FileFrameType",
    "FileFrame",
    "MAX_FRAME_BYTES",
    "encode_frame",
    "encode_control",
    "decode_control",
    "FileFrameReassembler",
    "sha256_hex",
]

#: Length prefix: 4-byte big-endian unsigned integer.
_LENGTH_STRUCT = struct.Struct(">I")
_LENGTH_SIZE = _LENGTH_STRUCT.size  # 4

#: Sanity limit on a single frame's declared length (type byte + body), in
#: bytes. A declared length larger than this is treated as a
#: protocol/corruption error rather than an attempt to buffer an unbounded
#: amount of data.
MAX_FRAME_BYTES = 1 << 24  # 16 MiB


class FileFrameType(IntEnum):
    """Type tag carried in the single type byte of every file frame.

    Values fit in one unsigned byte (0..255). Control frames
    (OFFER/ACCEPT/REJECT/DONE/ACK/NACK) carry JSON bodies; CHUNK frames
    carry raw file bytes.
    """

    OFFER = 0
    ACCEPT = 1
    REJECT = 2
    CHUNK = 3
    DONE = 4
    ACK = 5
    NACK = 6


@dataclass
class FileFrame:
    """A single decoded file-transfer frame.

    Attributes:
        type: The frame's :class:`FileFrameType`.
        body: The raw body bytes (without length prefix or type byte).
            For control frames this is UTF-8 JSON; for CHUNK frames it is
            raw file bytes. Use :func:`decode_control` to parse control
            bodies.
    """

    type: FileFrameType
    body: bytes


def encode_frame(type: FileFrameType, body: bytes = b"") -> bytes:
    """Encode a single frame: length prefix + type byte + body.

    The 4-byte big-endian length prefix counts ``1 + len(body)`` (the type
    byte plus the body).

    Args:
        type: The frame type tag.
        body: Raw body bytes (UTF-8 JSON for control frames, raw file
            bytes for CHUNK). Defaults to empty.

    Returns:
        The framed bytes ready to write to a reliable stream.

    Raises:
        ValueError: If the resulting frame length (``1 + len(body)``)
            exceeds :data:`MAX_FRAME_BYTES`.
    """
    length = 1 + len(body)
    if length > MAX_FRAME_BYTES:
        raise ValueError(
            f"frame length {length} exceeds MAX_FRAME_BYTES ({MAX_FRAME_BYTES})"
        )
    return _LENGTH_STRUCT.pack(length) + bytes([int(type)]) + body


def encode_control(type: FileFrameType, obj: dict) -> bytes:
    """Encode a control frame whose body is the JSON encoding of ``obj``.

    The body is ``json.dumps(obj).encode("utf-8")`` (with
    ``ensure_ascii=False`` so Unicode, e.g. file names, stays readable),
    framed via :func:`encode_frame`.

    Args:
        type: The control frame type (e.g. ``OFFER``, ``ACCEPT``).
        obj: A JSON-serializable mapping (e.g. the OFFER body).

    Returns:
        The framed control bytes.

    Raises:
        ValueError: If the framed body exceeds :data:`MAX_FRAME_BYTES`.
    """
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return encode_frame(type, body)


def decode_control(body: bytes) -> dict:
    """Decode a control frame body (UTF-8 JSON) into a dict.

    Args:
        body: The raw control body bytes (UTF-8 JSON object).

    Returns:
        The decoded mapping.

    Raises:
        ValueError: If the body is not valid UTF-8/JSON, or is not a JSON
            object.
    """
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to parse control body: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError(f"control body is not a JSON object: {type(obj)!r}")

    return obj


class FileFrameReassembler:
    """Reassemble complete file frames from a reliable byte stream.

    Bytes received from a reliable, ordered stream are accumulated in an
    internal buffer. :meth:`feed` extracts every complete frame currently
    available, leaving any partial trailing frame buffered for the next
    call. This correctly handles a single frame split across multiple
    feeds (boundary spanning) as well as multiple frames -- of any mix of
    types -- delivered in one feed, always in stream order.
    """

    def __init__(self) -> None:
        """Initialize an empty reassembler."""
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[FileFrame]:
        """Append ``data`` and return any newly completed frames.

        Args:
            data: Newly received bytes from the stream.

        Returns:
            A list of fully reassembled :class:`FileFrame` objects, in
            stream order. May be empty if no frame completed.

        Raises:
            ValueError: If a frame declares a length greater than
                :data:`MAX_FRAME_BYTES`, a length less than 1 (no room for
                even a type byte), or carries an unknown type byte.
        """
        self._buf.extend(data)
        frames: list[FileFrame] = []

        while True:
            if len(self._buf) < _LENGTH_SIZE:
                break

            (length,) = _LENGTH_STRUCT.unpack_from(self._buf, 0)

            if length < 1:
                raise ValueError(
                    f"declared frame length {length} is too small "
                    "(no room for a type byte)"
                )
            if length > MAX_FRAME_BYTES:
                raise ValueError(
                    f"declared frame length {length} exceeds "
                    f"MAX_FRAME_BYTES ({MAX_FRAME_BYTES})"
                )

            frame_end = _LENGTH_SIZE + length
            if len(self._buf) < frame_end:
                # Frame not fully arrived yet; wait for more data.
                break

            type_byte = self._buf[_LENGTH_SIZE]
            try:
                frame_type = FileFrameType(type_byte)
            except ValueError as exc:
                raise ValueError(
                    f"unknown file frame type byte: {type_byte}"
                ) from exc

            body = bytes(self._buf[_LENGTH_SIZE + 1:frame_end])
            frames.append(FileFrame(type=frame_type, body=body))
            del self._buf[:frame_end]

        return frames


def sha256_hex(data: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of ``data``.

    Args:
        data: The bytes to hash.

    Returns:
        The 64-character lowercase hexadecimal digest.
    """
    return hashlib.sha256(data).hexdigest()
