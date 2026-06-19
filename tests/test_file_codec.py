"""Tests for the file-transfer frame codec and reassembler (M6-T03).

Validates control-frame JSON round-trips (OFFER/ACCEPT/DONE/ACK), binary
CHUNK byte preservation, interleaved multi-frame streams, frame splitting
across arbitrary feed boundaries, the known SHA-256 vector, and the
defensive ValueError paths for oversize length prefixes and unknown type
bytes.

Deterministic: all inputs are fixed byte/JSON literals.
"""

from __future__ import annotations

import struct

import pytest

from photontcp.app.file_codec import (
    MAX_FRAME_BYTES,
    FileFrame,
    FileFrameReassembler,
    FileFrameType,
    decode_control,
    encode_control,
    encode_frame,
    sha256_hex,
)

# Mirror of the codec's wire-format length prefix (4-byte big-endian).
_LENGTH_STRUCT = struct.Struct(">I")


# --------------------------------------------------------------------------
# 1. Control round-trip: OFFER / ACCEPT / DONE / ACK
# --------------------------------------------------------------------------

def test_offer_control_roundtrip() -> None:
    obj = {"name": "a.bin", "size": 2048, "sha256": "deadbeef"}
    data = encode_control(FileFrameType.OFFER, obj)

    frames = FileFrameReassembler().feed(data)

    assert len(frames) == 1
    assert frames[0].type == FileFrameType.OFFER
    assert decode_control(frames[0].body) == obj


@pytest.mark.parametrize(
    "ftype, obj",
    [
        (FileFrameType.ACCEPT, {"ok": True}),
        (FileFrameType.DONE, {}),
        (FileFrameType.ACK, {"ok": True}),
        (FileFrameType.REJECT, {"reason": "no space"}),
        (FileFrameType.NACK, {"reason": "bad sha"}),
    ],
)
def test_control_roundtrip_each_type(ftype: FileFrameType, obj: dict) -> None:
    data = encode_control(ftype, obj)

    frames = FileFrameReassembler().feed(data)

    assert len(frames) == 1
    assert frames[0].type == ftype
    assert decode_control(frames[0].body) == obj


def test_control_roundtrip_preserves_unicode_name() -> None:
    obj = {"name": "사진.bin", "size": 10, "sha256": "abcd"}
    frames = FileFrameReassembler().feed(encode_control(FileFrameType.OFFER, obj))

    assert decode_control(frames[0].body) == obj


# --------------------------------------------------------------------------
# 2. CHUNK binary: every byte value preserved
# --------------------------------------------------------------------------

def test_chunk_binary_all_byte_values() -> None:
    payload = bytes(range(256))
    data = encode_frame(FileFrameType.CHUNK, payload)

    frames = FileFrameReassembler().feed(data)

    assert len(frames) == 1
    assert frames[0].type == FileFrameType.CHUNK
    assert frames[0].body == payload


def test_empty_chunk_body() -> None:
    frames = FileFrameReassembler().feed(encode_frame(FileFrameType.CHUNK))

    assert len(frames) == 1
    assert frames[0].type == FileFrameType.CHUNK
    assert frames[0].body == b""


# --------------------------------------------------------------------------
# 3. Interleaved / multiple frames in one feed
# --------------------------------------------------------------------------

def _build_blob() -> tuple[bytes, list[FileFrame]]:
    """Return a concatenated OFFER+CHUNK+CHUNK+DONE+ACK blob and expected frames."""
    offer = {"name": "x.bin", "size": 5, "sha256": "00"}
    chunk_a = bytes(range(64))
    chunk_b = bytes(range(64, 128))

    blob = b"".join(
        [
            encode_control(FileFrameType.OFFER, offer),
            encode_frame(FileFrameType.CHUNK, chunk_a),
            encode_frame(FileFrameType.CHUNK, chunk_b),
            encode_control(FileFrameType.DONE, {}),
            encode_control(FileFrameType.ACK, {"ok": True}),
        ]
    )

    expected = [
        FileFrame(FileFrameType.OFFER, encode_control(FileFrameType.OFFER, offer)[5:]),
        FileFrame(FileFrameType.CHUNK, chunk_a),
        FileFrame(FileFrameType.CHUNK, chunk_b),
        FileFrame(FileFrameType.DONE, encode_control(FileFrameType.DONE, {})[5:]),
        FileFrame(
            FileFrameType.ACK,
            encode_control(FileFrameType.ACK, {"ok": True})[5:],
        ),
    ]
    return blob, expected


def test_interleaved_multiple_frames_single_feed() -> None:
    blob, expected = _build_blob()

    frames = FileFrameReassembler().feed(blob)

    assert frames == expected
    # Spot-check decoded semantics for the control frames.
    assert decode_control(frames[0].body) == {"name": "x.bin", "size": 5, "sha256": "00"}
    assert decode_control(frames[3].body) == {}
    assert decode_control(frames[4].body) == {"ok": True}


# --------------------------------------------------------------------------
# 4. Boundary spanning: split the blob byte-by-byte
# --------------------------------------------------------------------------

def test_boundary_byte_by_byte_accumulates_identically() -> None:
    blob, expected = _build_blob()
    reasm = FileFrameReassembler()

    collected: list[FileFrame] = []
    partial_residue_seen = False
    for i, byte in enumerate(blob):
        out = reasm.feed(bytes([byte]))
        collected.extend(out)
        # Before the final byte, at least one feed must yield nothing
        # (frames remaining partially buffered).
        if i < len(blob) - 1 and not out:
            partial_residue_seen = True

    assert collected == expected
    assert partial_residue_seen


def test_boundary_arbitrary_split_two_chunks() -> None:
    blob, expected = _build_blob()
    reasm = FileFrameReassembler()

    mid = len(blob) // 3  # likely lands mid-frame
    first = reasm.feed(blob[:mid])
    second = reasm.feed(blob[mid:])

    assert first + second == expected
    # The split point should not have completed all frames at once.
    assert len(first) < len(expected)


# --------------------------------------------------------------------------
# 5. sha256_hex known vector
# --------------------------------------------------------------------------

def test_sha256_hex_known_vector() -> None:
    expected = (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )
    assert sha256_hex(b"abc") == expected


def test_sha256_hex_empty() -> None:
    expected = (
        "e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855"
    )
    assert sha256_hex(b"") == expected


# --------------------------------------------------------------------------
# 6. Defensive: oversize length prefix and unknown type byte
# --------------------------------------------------------------------------

def test_oversize_length_prefix_raises() -> None:
    # Declare a length larger than MAX_FRAME_BYTES; no body needed since the
    # length check fires before waiting for body bytes.
    bad = _LENGTH_STRUCT.pack(MAX_FRAME_BYTES + 1)

    with pytest.raises(ValueError):
        FileFrameReassembler().feed(bad)


def test_zero_length_prefix_raises() -> None:
    # length == 0 leaves no room for the type byte.
    with pytest.raises(ValueError):
        FileFrameReassembler().feed(_LENGTH_STRUCT.pack(0))


def test_unknown_type_byte_raises() -> None:
    # length = 1 (type byte only), type byte = 99 (not a FileFrameType).
    bad = _LENGTH_STRUCT.pack(1) + bytes([99])

    with pytest.raises(ValueError):
        FileFrameReassembler().feed(bad)


def test_encode_frame_oversize_body_raises() -> None:
    with pytest.raises(ValueError):
        encode_frame(FileFrameType.CHUNK, b"\x00" * MAX_FRAME_BYTES)


def test_decode_control_rejects_non_object() -> None:
    # A JSON array is valid JSON but not a control object.
    frames = FileFrameReassembler().feed(encode_frame(FileFrameType.OFFER, b"[1, 2]"))

    with pytest.raises(ValueError):
        decode_control(frames[0].body)
