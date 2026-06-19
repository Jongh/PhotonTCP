"""QR 코덱 라운드트립 테스트 (M5-T04).

``photontcp.qr.encode.encode_frame`` 와 ``photontcp.qr.decode.decode_frame`` 의
라운드트립 무결성을 검증한다. base64 바이너리 무결성(전 바이트값 포함),
실제 :class:`~photontcp.packet.header.Packet` 통과(CRC 포함 필드 복원),
빈 payload 패킷, 결정성, 검출 실패 동작을 다룬다.

``segno``/``cv2`` 미설치 시 모듈 전체를 skip 한다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")

import numpy as np

from photontcp.qr.encode import encode_frame
from photontcp.qr.decode import decode_frame
from photontcp.packet.header import Packet
from photontcp.packet.types import Flags, PacketType


# --------------------------------------------------------------------------- #
# 1. 라운드트립 무결성 (raw bytes)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "data",
    [
        b"PhotonTCP over light" * 5,  # 중간 길이
        b"x" * 800,  # 긴 바이트열 (단일 QR 용량 내)
        bytes(range(256)),  # 전 바이트값 -> base64 바이너리 무결성
    ],
)
def test_roundtrip_bytes(data: bytes) -> None:
    image = encode_frame(data)
    assert isinstance(image, np.ndarray)
    assert image.dtype == np.uint8
    decoded = decode_frame(image)
    assert decoded == data


# --------------------------------------------------------------------------- #
# 2. 실제 Packet 통과 (CRC 포함 전체 필드 복원)
# --------------------------------------------------------------------------- #

def test_roundtrip_packet() -> None:
    pkt = Packet(
        type=PacketType.DATA,
        session_id=4242,
        stream_id=1,
        seq=1234,
        ack=0,
        window=8,
        flags=Flags.ACK,
        payload=b"some interesting payload bytes for the QR frame",
    )
    raw = pkt.pack()

    image = encode_frame(raw)
    decoded_raw = decode_frame(image)
    assert decoded_raw == raw  # 바이트 단위 동일

    # CRC 검증을 통과하며 예외 없이 복원되어야 한다.
    restored = Packet.unpack(decoded_raw)

    assert restored.type == pkt.type
    assert restored.session_id == pkt.session_id
    assert restored.stream_id == pkt.stream_id
    assert restored.seq == pkt.seq
    assert restored.ack == pkt.ack
    assert restored.window == pkt.window
    assert restored.flags == pkt.flags
    assert restored.version == pkt.version
    assert restored.payload == pkt.payload


# --------------------------------------------------------------------------- #
# 3. 빈 payload 패킷 (헤더 22B만) 통과
# --------------------------------------------------------------------------- #

def test_roundtrip_empty_payload_packet() -> None:
    pkt = Packet(
        type=PacketType.ACK,
        session_id=1,
        stream_id=0,
        seq=0,
        ack=99,
        window=16,
        flags=Flags.ACK,
        payload=b"",
    )
    raw = pkt.pack()
    assert len(raw) == 22  # 헤더만

    image = encode_frame(raw)
    decoded_raw = decode_frame(image)
    assert decoded_raw == raw

    restored = Packet.unpack(decoded_raw)
    assert restored.payload == b""
    assert restored.type == pkt.type
    assert restored.ack == pkt.ack


# --------------------------------------------------------------------------- #
# 4. (선택) 결정성: 같은 입력 두 번 인코딩 -> 동일 배열
# --------------------------------------------------------------------------- #

def test_encode_deterministic() -> None:
    data = b"PhotonTCP over light" * 5
    a = encode_frame(data)
    b = encode_frame(data)
    assert np.array_equal(a, b)


# --------------------------------------------------------------------------- #
# 5. (선택) 검출 실패: 흰색/노이즈 이미지 -> None
# --------------------------------------------------------------------------- #

def test_decode_blank_white_returns_none() -> None:
    white = np.full((256, 256), 255, dtype=np.uint8)
    assert decode_frame(white) is None


def test_decode_noise_returns_none() -> None:
    rng = np.random.default_rng(1234)
    noise = rng.integers(0, 256, size=(256, 256), dtype=np.uint8)
    assert decode_frame(noise) is None
