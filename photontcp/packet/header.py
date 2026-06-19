"""PhotonTCP 패킷 헤더 직렬화/역직렬화.

이 모듈은 PhotonTCP 와이어 포맷의 고정 길이(22바이트) 헤더와 그 뒤에 이어지는
가변 길이 페이로드를 다룬다. 헤더는 big-endian 으로 인코딩되며, 다음과 같은
필드 순서를 가진다(괄호 안은 바이트 크기):

    version(1), type(1), flags(1), session_id(2), stream_id(1),
    seq(4), ack(4), window(2), payload_len(2), crc32(4)

:class:`Packet` 데이터클래스는 위 헤더 필드 전부와 ``payload`` 바이트열을
담는다. :meth:`Packet.pack` 은 헤더+페이로드를 바이트열로 직렬화하면서
무결성 검사를 위한 CRC32 값을 자동 계산해 채우고, :meth:`Packet.unpack` 은
수신한 바이트열을 파싱하면서 길이·CRC 를 검증한다.

CRC32 는 "crc 필드를 0으로 둔 헤더 + 페이로드" 전체 바이트열에 대해 계산한다.
즉 송신/수신 양쪽 모두 crc 필드 위치를 0으로 만든 동일한 바이트열에 대해
:func:`crc32` 를 적용하므로 결과가 일치해야 한다.

표준 라이브러리(:mod:`struct`)와 선행 모듈(:mod:`.crc`, :mod:`.types`)만
사용한다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .crc import crc32
from .types import PROTOCOL_VERSION, Flags, PacketType

__all__ = [
    "HEADER_FORMAT",
    "HEADER_SIZE",
    "Packet",
    "PacketError",
    "ChecksumError",
    "MalformedPacketError",
]

#: 헤더 ``struct`` 포맷(big-endian, 고정 22바이트).
HEADER_FORMAT = ">BBBHBIIHHI"

#: 헤더 길이(바이트). :data:`HEADER_FORMAT` 의 크기와 일치한다.
HEADER_SIZE = 22

# crc32 필드는 헤더의 마지막 4바이트(오프셋 18~21)에 위치한다.
_CRC_OFFSET = HEADER_SIZE - 4

# 모듈 로드 시점에 포맷/상수 일관성을 보장한다.
assert struct.calcsize(HEADER_FORMAT) == HEADER_SIZE


class PacketError(Exception):
    """PhotonTCP 패킷 처리 중 발생하는 모든 오류의 기반 예외."""


class ChecksumError(PacketError):
    """헤더에 기록된 CRC32 값과 실제 계산값이 일치하지 않을 때 발생."""


class MalformedPacketError(PacketError):
    """패킷 길이/포맷이 올바르지 않을 때 발생(너무 짧거나 길이 필드 불일치 등)."""


@dataclass
class Packet:
    """PhotonTCP 패킷(고정 헤더 + 가변 페이로드).

    Attributes:
        type: 패킷 종류(:class:`PacketType`).
        session_id: 세션 식별자(2바이트, 0~65535).
        stream_id: 스트림 식별자(1바이트, 0~255).
        seq: 송신 시퀀스 번호(4바이트).
        ack: 누적 확인 응답 번호(4바이트).
        window: 수신 윈도 크기(2바이트).
        flags: 보조 비트 플래그(:class:`Flags`). 기본 :data:`Flags.NONE`.
        version: 와이어 포맷 버전. 기본 :data:`PROTOCOL_VERSION`.
        payload: 애플리케이션 페이로드 바이트열. 기본 빈 바이트열.
    """

    type: PacketType
    session_id: int
    stream_id: int
    seq: int
    ack: int
    window: int
    flags: Flags = Flags.NONE
    version: int = PROTOCOL_VERSION
    payload: bytes = field(default=b"")

    def pack(self) -> bytes:
        """패킷을 와이어 포맷 바이트열로 직렬화한다.

        ``payload_len`` 은 ``len(self.payload)`` 로 자동 설정된다. CRC32 는
        crc 필드를 0으로 둔 헤더 + 페이로드 전체에 대해 계산해 헤더의 crc
        필드에 채운다.

        Returns:
            22바이트 헤더 뒤에 페이로드가 이어지는 바이트열.
        """
        payload_len = len(self.payload)

        # 1) crc=0 으로 둔 헤더 + 페이로드 바이트열을 만든다.
        header_zero_crc = struct.pack(
            HEADER_FORMAT,
            int(self.version),
            int(self.type),
            int(self.flags),
            self.session_id,
            self.stream_id,
            self.seq,
            self.ack,
            self.window,
            payload_len,
            0,
        )
        checksum = crc32(header_zero_crc + self.payload)

        # 2) 계산한 crc 를 채운 최종 헤더를 만든다.
        header = struct.pack(
            HEADER_FORMAT,
            int(self.version),
            int(self.type),
            int(self.flags),
            self.session_id,
            self.stream_id,
            self.seq,
            self.ack,
            self.window,
            payload_len,
            checksum,
        )
        return header + self.payload

    @classmethod
    def unpack(cls, raw: bytes) -> "Packet":
        """와이어 포맷 바이트열을 파싱해 :class:`Packet` 으로 복원한다.

        Args:
            raw: 22바이트 이상의 헤더 + 페이로드 바이트열.

        Returns:
            파싱·검증된 :class:`Packet` 인스턴스.

        Raises:
            MalformedPacketError: ``raw`` 가 :data:`HEADER_SIZE` 보다 짧거나,
                ``payload_len`` 필드가 실제 페이로드 길이와 다를 때.
            ChecksumError: 헤더의 CRC32 값이 실제 계산값과 다를 때.
        """
        if len(raw) < HEADER_SIZE:
            raise MalformedPacketError(
                f"패킷이 너무 짧습니다: {len(raw)}바이트(최소 {HEADER_SIZE}바이트 필요)"
            )

        (
            version,
            type_,
            flags,
            session_id,
            stream_id,
            seq,
            ack,
            window,
            payload_len,
            checksum,
        ) = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])

        payload = raw[HEADER_SIZE:]
        if payload_len != len(payload):
            raise MalformedPacketError(
                f"payload_len 불일치: 헤더={payload_len}, 실제={len(payload)}"
            )

        # crc 필드 위치(오프셋 18~21)를 0으로 만든 바이트열로 CRC 를 재계산한다.
        zeroed = (
            raw[:_CRC_OFFSET]
            + b"\x00\x00\x00\x00"
            + raw[_CRC_OFFSET + 4:]
        )
        if crc32(zeroed) != checksum:
            raise ChecksumError(
                f"CRC32 불일치: 헤더={checksum:#010x}, 계산={crc32(zeroed):#010x}"
            )

        return cls(
            type=PacketType(type_),
            session_id=session_id,
            stream_id=stream_id,
            seq=seq,
            ack=ack,
            window=window,
            flags=Flags(flags),
            version=version,
            payload=payload,
        )
