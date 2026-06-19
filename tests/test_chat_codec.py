"""Tests for the chat message codec and stream reassembler (M4-T06).

Validates encode/decode round-trips, multi-message batches, frame splitting
across feed boundaries, mixed/chunked streams, Unicode preservation, and the
defensive ValueError paths for oversize length prefixes and corrupted bodies.

Deterministic: all timestamps are fixed floats.
"""

from __future__ import annotations

import json
import struct

import pytest

from photontcp.app.codec import (
    MAX_MESSAGE_BYTES,
    ChatMessage,
    StreamReassembler,
    encode_message,
)

# Fixed length-prefix struct mirroring the codec's wire format.
_LENGTH_STRUCT = struct.Struct(">I")


def _assert_same(decoded: ChatMessage, expected: ChatMessage) -> None:
    assert decoded.msg_id == expected.msg_id
    assert decoded.timestamp == expected.timestamp
    assert decoded.text == expected.text


def test_single_message_round_trip() -> None:
    msg = ChatMessage(msg_id=1, timestamp=1000.5, text="hello world")
    reassembler = StreamReassembler()

    out = reassembler.feed(encode_message(msg))

    assert len(out) == 1
    _assert_same(out[0], msg)


def test_multiple_messages_single_feed() -> None:
    msgs = [
        ChatMessage(msg_id=i, timestamp=float(i) + 0.25, text=f"msg-{i}")
        for i in range(5)
    ]
    stream = b"".join(encode_message(m) for m in msgs)

    out = StreamReassembler().feed(stream)

    assert len(out) == len(msgs)
    for decoded, expected in zip(out, msgs):
        _assert_same(decoded, expected)


def test_frame_split_byte_by_byte() -> None:
    msg = ChatMessage(msg_id=42, timestamp=12345.0, text="split me up")
    encoded = encode_message(msg)
    reassembler = StreamReassembler()

    completed: list[ChatMessage] = []
    # Feed every byte except the last individually; nothing should complete.
    for i in range(len(encoded) - 1):
        result = reassembler.feed(encoded[i : i + 1])
        assert result == [], f"unexpected completion at byte {i}"
        completed.extend(result)

    # The final byte completes the single frame.
    final = reassembler.feed(encoded[-1:])
    completed.extend(final)

    assert len(completed) == 1
    _assert_same(completed[0], msg)


def test_mixed_stream_arbitrary_boundaries() -> None:
    msgs = [
        ChatMessage(msg_id=10, timestamp=1.0, text="first"),
        ChatMessage(msg_id=20, timestamp=2.0, text="second message"),
        ChatMessage(msg_id=30, timestamp=3.5, text="third"),
        ChatMessage(msg_id=40, timestamp=4.75, text="fourth and final"),
    ]
    stream = b"".join(encode_message(m) for m in msgs)

    reassembler = StreamReassembler()
    collected: list[ChatMessage] = []
    # Arbitrary, irregular chunk sizes that don't align to frame boundaries.
    chunk_sizes = [1, 3, 7, 2, 11, 5, 13, 4]
    pos = 0
    si = 0
    while pos < len(stream):
        size = chunk_sizes[si % len(chunk_sizes)]
        si += 1
        chunk = stream[pos : pos + size]
        pos += size
        collected.extend(reassembler.feed(chunk))

    assert len(collected) == len(msgs)
    for decoded, expected in zip(collected, msgs):
        _assert_same(decoded, expected)


def test_unicode_preserved() -> None:
    msg = ChatMessage(
        msg_id=7,
        timestamp=999.0,
        text="안녕하세요 세계 🌍🚀 한글과 이모지 😀",
    )
    reassembler = StreamReassembler()

    out = reassembler.feed(encode_message(msg))

    assert len(out) == 1
    _assert_same(out[0], msg)
    assert out[0].text == msg.text


def test_oversize_length_prefix_raises() -> None:
    # Declared length exceeds MAX_MESSAGE_BYTES; reassembler must reject it
    # rather than buffering unbounded data.
    bogus = _LENGTH_STRUCT.pack(MAX_MESSAGE_BYTES + 1)
    reassembler = StreamReassembler()

    with pytest.raises(ValueError):
        reassembler.feed(bogus)


def test_encode_oversize_body_raises() -> None:
    too_big = "x" * (MAX_MESSAGE_BYTES + 1)
    msg = ChatMessage(msg_id=1, timestamp=0.0, text=too_big)

    with pytest.raises(ValueError):
        encode_message(msg)


def test_corrupted_json_body_raises() -> None:
    # A well-formed length prefix pointing at a body that is not valid JSON.
    body = b"this is not json{{{"
    frame = _LENGTH_STRUCT.pack(len(body)) + body
    reassembler = StreamReassembler()

    with pytest.raises(ValueError):
        reassembler.feed(frame)


def test_missing_field_body_raises() -> None:
    # Valid JSON object but missing the required 'text' field.
    body = json.dumps({"msg_id": 1, "timestamp": 0.0}).encode("utf-8")
    frame = _LENGTH_STRUCT.pack(len(body)) + body
    reassembler = StreamReassembler()

    with pytest.raises(ValueError):
        reassembler.feed(frame)
