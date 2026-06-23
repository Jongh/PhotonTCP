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

import base64

import numpy as np

from photontcp.qr.encode import encode_frame
from photontcp.qr import decode as _decode_mod
from photontcp.qr.decode import decode_frame, _decode_variants
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


# --------------------------------------------------------------------------- #
# 6. (M10) 전처리 변형 캐스케이드 + detector 폴백 단위 테스트
# --------------------------------------------------------------------------- #
#
# 핫패스 단락 / 캐스케이드 회복 / 대체 detector 우아한 미가용·회복을 검증한다.
# cv2 의 콘텐츠 의존 디코드 동작에 의존하지 않도록, 결정적인 fake detector 로
# 캐스케이드/폴백 배선을 직접 검사한다.


class _CountingPrimaryDetector:
    """detectAndDecode 호출을 세는 fake 1차 detector.

    ``fail_first`` 번 호출은 검출 실패((``""``, None, None))를 흉내내고, 그
    이후부터는 주어진 base64 텍스트를 ``(text, points, straight)`` 모양으로
    반환한다(cv2.QRCodeDetector 시그니처와 동일).
    """

    def __init__(self, text: str = "", fail_first: int = 0) -> None:
        self.text = text
        self.fail_first = fail_first
        self.calls = 0

    def detectAndDecode(self, image):  # noqa: N802 - cv2 API 이름 모방
        self.calls += 1
        if self.calls <= self.fail_first:
            return "", None, None
        pts = np.zeros((1, 4, 2), dtype=np.float32)
        return self.text, pts, None


class _AlwaysFailDetector:
    """모든 변형에서 검출 실패하는 fake 1차 detector."""

    def __init__(self) -> None:
        self.calls = 0

    def detectAndDecode(self, image):  # noqa: N802 - cv2 API 이름 모방
        self.calls += 1
        return "", None, None


class _FakeArucoDetector:
    """aruco 모양 대체 detector: detectAndDecode -> (text, points, straight)."""

    def __init__(self, text: str) -> None:
        self.text = text

    def detectAndDecode(self, image):  # noqa: N802 - cv2 API 이름 모방
        pts = np.zeros((1, 4, 2), dtype=np.float32)
        return self.text, pts, None


def test_variant_cascade_first_is_identity() -> None:
    """변형 1은 입력 배열을 그대로(=원본 그레이스케일 핫패스) 내놓고,
    전형적인 QR 이미지에 대해 변형이 여러 개(>= 4) 생성된다."""
    img = encode_frame(b"variant cascade probe")
    # decode_frame 의 정규화와 동일하게 2D uint8 gray 를 넘긴다.
    gray = img if img.ndim == 2 else img[..., 0]
    variants = list(_decode_variants(gray))

    assert np.array_equal(variants[0], gray)  # 변형 1 == 원본(아이덴티티)
    assert variants[0] is gray  # 추가 복사 없이 그대로 yield
    assert len(variants) >= 4  # 이진화/샤프닝/업스케일 등 다수 변형


def test_hot_path_short_circuits_on_variant_1() -> None:
    """깨끗한 QR 은 변형 1에서 즉시 디코드되어 detectAndDecode 가 정확히
    1회만 호출된다(핫패스 단락)."""
    data = b"hot path short circuit"
    text = base64.b64encode(data).decode("ascii")
    fake = _CountingPrimaryDetector(text=text, fail_first=0)

    img = encode_frame(data)
    result = decode_frame(img, detector=fake)

    assert result == data
    assert fake.calls == 1  # 변형 1 에서 단락 — 추가 변형 미평가


def test_cascade_recovers_after_failed_variants() -> None:
    """앞쪽 변형들이 실패해도 캐스케이드가 뒤 변형으로 진행해 회복한다."""
    data = b"cascade recovery payload"
    text = base64.b64encode(data).decode("ascii")
    # 처음 2개 변형은 실패, 3번째 변형에서 성공.
    fake = _CountingPrimaryDetector(text=text, fail_first=2)

    img = encode_frame(data)
    result = decode_frame(img, detector=fake)

    assert result == data
    assert fake.calls == 3  # 변형 3개를 거쳐 회복


def test_alt_detector_absent_returns_none(monkeypatch) -> None:
    """1차 detector 가 항상 실패하고 대체 detector 도 없을 때(우아한 미가용)
    decode_frame 은 raise 없이 None 을 반환한다."""
    monkeypatch.setattr(_decode_mod, "_alt_kind_cached", lambda: None)

    fake = _AlwaysFailDetector()
    img = encode_frame(b"no alternate available")
    assert decode_frame(img, detector=fake) is None


def test_alt_detector_recovers_via_fallback(monkeypatch) -> None:
    """1차 detector 가 항상 실패해도, 대체(aruco 모양) detector 가 회복하면
    decode_frame 은 폴백 경로로 원본 바이트를 복원한다."""
    data = b"alternate detector recovery"
    text = base64.b64encode(data).decode("ascii")
    alt = _FakeArucoDetector(text)

    # 캐시와 싸우지 않도록 헬퍼 함수를 직접 monkeypatch (teardown 자동 복원).
    monkeypatch.setattr(
        _decode_mod, "_thread_alt_detector", lambda: ("aruco", alt)
    )

    fake = _AlwaysFailDetector()
    img = encode_frame(data)
    result = decode_frame(img, detector=fake)

    assert result == data
