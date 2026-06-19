"""Chat message codec and reliable-stream reassembler.

This module defines the wire format for chat messages and the logic to
encode/decode them over a reliable byte stream.

Wire format (per message)::

    +----------------------+-------------------------------+
    | length prefix        | JSON body                     |
    | 4 bytes, big-endian  | UTF-8 encoded, ``length`` long|
    | unsigned (struct >I) |                               |
    +----------------------+-------------------------------+

The JSON body is ``{"msg_id": int, "timestamp": float, "text": str}``.
Unicode (e.g. Hangul) text is preserved because the body is dumped with
``ensure_ascii=False`` and then encoded as UTF-8.

Pure standard library only: ``json``, ``struct``, ``dataclasses``.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

__all__ = [
    "ChatMessage",
    "MAX_MESSAGE_BYTES",
    "encode_message",
    "StreamReassembler",
]

#: Length prefix: 4-byte big-endian unsigned integer.
_LENGTH_STRUCT = struct.Struct(">I")
_LENGTH_SIZE = _LENGTH_STRUCT.size  # 4

#: Sanity limit on a single message body, in bytes. A declared length
#: larger than this is treated as a protocol/corruption error rather than
#: an attempt to buffer an unbounded amount of data.
MAX_MESSAGE_BYTES = 1 << 20  # 1 MiB


@dataclass
class ChatMessage:
    """A single chat message.

    Attributes:
        msg_id: Monotonic/identifying integer for the message.
        timestamp: Unix epoch seconds (float) when the message was created.
        text: The message body text (arbitrary Unicode).
    """

    msg_id: int
    timestamp: float
    text: str


def encode_message(msg: ChatMessage) -> bytes:
    """Encode a :class:`ChatMessage` into a length-prefixed frame.

    The body is the UTF-8 encoding of
    ``json.dumps({"msg_id": .., "timestamp": .., "text": ..})`` (with
    ``ensure_ascii=False`` so Unicode text stays readable in the JSON).
    The returned bytes are a 4-byte big-endian unsigned length prefix
    followed by the body.

    Args:
        msg: The message to encode.

    Returns:
        The framed bytes ready to write to a reliable stream.

    Raises:
        ValueError: If the encoded body exceeds :data:`MAX_MESSAGE_BYTES`.
    """
    body = json.dumps(
        {
            "msg_id": msg.msg_id,
            "timestamp": msg.timestamp,
            "text": msg.text,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError(
            f"encoded message body of {len(body)} bytes exceeds "
            f"MAX_MESSAGE_BYTES ({MAX_MESSAGE_BYTES})"
        )

    return _LENGTH_STRUCT.pack(len(body)) + body


def _decode_body(body: bytes) -> ChatMessage:
    """Decode a single JSON body into a :class:`ChatMessage`.

    Args:
        body: The UTF-8 JSON body bytes (without length prefix).

    Returns:
        The decoded message.

    Raises:
        ValueError: If the body is not valid UTF-8/JSON or is missing
            required fields.
    """
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to parse message body: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError(f"message body is not a JSON object: {type(obj)!r}")

    try:
        return ChatMessage(
            msg_id=obj["msg_id"],
            timestamp=obj["timestamp"],
            text=obj["text"],
        )
    except KeyError as exc:
        raise ValueError(f"message body missing field: {exc}") from exc


class StreamReassembler:
    """Reassemble complete chat frames from a reliable byte stream.

    Bytes received from a reliable, ordered stream are accumulated in an
    internal buffer. :meth:`feed` extracts every complete frame currently
    available, leaving any partial trailing frame buffered for the next
    call. This correctly handles a single message split across multiple
    feeds as well as multiple messages delivered in one feed.
    """

    def __init__(self) -> None:
        """Initialize an empty reassembler."""
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[ChatMessage]:
        """Append ``data`` and return any newly completed messages.

        Args:
            data: Newly received bytes from the stream.

        Returns:
            A list of fully reassembled :class:`ChatMessage` objects, in
            stream order. May be empty if no frame completed.

        Raises:
            ValueError: If a frame declares a length greater than
                :data:`MAX_MESSAGE_BYTES`, or if a complete body fails to
                parse as a valid message.
        """
        self._buf.extend(data)
        messages: list[ChatMessage] = []

        while True:
            if len(self._buf) < _LENGTH_SIZE:
                break

            (length,) = _LENGTH_STRUCT.unpack_from(self._buf, 0)

            if length > MAX_MESSAGE_BYTES:
                raise ValueError(
                    f"declared message length {length} exceeds "
                    f"MAX_MESSAGE_BYTES ({MAX_MESSAGE_BYTES})"
                )

            frame_end = _LENGTH_SIZE + length
            if len(self._buf) < frame_end:
                # Body not fully arrived yet; wait for more data.
                break

            body = bytes(self._buf[_LENGTH_SIZE:frame_end])
            messages.append(_decode_body(body))
            del self._buf[:frame_end]

        return messages
