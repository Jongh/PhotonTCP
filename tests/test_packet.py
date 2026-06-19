"""Unit tests for :mod:`photontcp.packet.header` pack/unpack round-trips."""

from __future__ import annotations

import pytest

from photontcp.packet.header import (
    HEADER_SIZE,
    ChecksumError,
    MalformedPacketError,
    Packet,
)
from photontcp.packet.types import PROTOCOL_VERSION, Flags, PacketType


def _sample_packet() -> Packet:
    return Packet(
        type=PacketType.DATA,
        session_id=4242,
        stream_id=7,
        seq=1234567,
        ack=7654321,
        window=512,
        flags=Flags.ACK | Flags.SYN,
        version=PROTOCOL_VERSION,
        payload=b"hello photontcp",
    )


def test_pack_unpack_roundtrip_preserves_all_fields() -> None:
    pkt = _sample_packet()
    restored = Packet.unpack(pkt.pack())

    assert restored.version == pkt.version
    assert restored.type == pkt.type
    assert restored.flags == pkt.flags
    assert restored.session_id == pkt.session_id
    assert restored.stream_id == pkt.stream_id
    assert restored.seq == pkt.seq
    assert restored.ack == pkt.ack
    assert restored.window == pkt.window
    assert restored.payload == pkt.payload
    # Equality across the whole dataclass for good measure.
    assert restored == pkt


def test_pack_includes_full_header_plus_payload() -> None:
    pkt = _sample_packet()
    raw = pkt.pack()
    assert len(raw) == HEADER_SIZE + len(pkt.payload)


def test_empty_payload_roundtrip() -> None:
    pkt = Packet(
        type=PacketType.ACK,
        session_id=1,
        stream_id=0,
        seq=0,
        ack=99,
        window=0,
        payload=b"",
    )
    raw = pkt.pack()
    assert len(raw) == HEADER_SIZE

    restored = Packet.unpack(raw)
    assert restored.payload == b""
    assert restored == pkt


def test_single_byte_corruption_raises_checksum_error() -> None:
    pkt = _sample_packet()
    raw = bytearray(pkt.pack())

    # Flip one byte in the header region (before the CRC field) so that the
    # recomputed CRC will no longer match the stored CRC.
    raw[5] ^= 0xFF

    with pytest.raises(ChecksumError):
        Packet.unpack(bytes(raw))


def test_corruption_in_payload_raises_checksum_error() -> None:
    pkt = _sample_packet()
    raw = bytearray(pkt.pack())

    # Corrupt a payload byte (last byte of the frame).
    raw[-1] ^= 0x01

    with pytest.raises(ChecksumError):
        Packet.unpack(bytes(raw))


def test_truncated_input_below_header_size_raises() -> None:
    pkt = _sample_packet()
    raw = pkt.pack()

    with pytest.raises(MalformedPacketError):
        Packet.unpack(raw[: HEADER_SIZE - 1])


def test_empty_input_raises() -> None:
    with pytest.raises(MalformedPacketError):
        Packet.unpack(b"")
