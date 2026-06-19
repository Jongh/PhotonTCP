"""PhotonTCP 패킷 타입·플래그 정의.

이 모듈은 PhotonTCP 와이어 포맷의 두 헤더 필드와 1:1로 매핑되는 열거형을
제공한다:

* :class:`PacketType` — 헤더의 ``type`` 필드(1바이트). 패킷의 의미(핸드셰이크,
  데이터, 종료 등)를 나타낸다.
* :class:`Flags` — 헤더의 ``flags`` 필드(1바이트). 보조 비트 플래그의 OR 조합.

또한 와이어 포맷 호환성을 식별하기 위한 :data:`PROTOCOL_VERSION` 상수를 둔다.

두 열거형의 값은 모두 0~255 범위 안에 있어야 하며(각 필드가 1바이트),
값은 명시적으로 고정해 둔다 — 한 번 배포된 와이어 포맷의 숫자 값은
호환성을 위해 변경되어서는 안 된다.
"""

from enum import IntEnum, IntFlag

__all__ = ["PROTOCOL_VERSION", "PacketType", "Flags"]

#: PhotonTCP 와이어 포맷 버전. 헤더 ``version`` 필드(1바이트)에 들어간다.
PROTOCOL_VERSION = 1


class PacketType(IntEnum):
    """패킷의 종류. 헤더 ``type`` 필드(1바이트, 0~255)와 매핑된다."""

    SYN = 0
    """연결 개시 요청(핸드셰이크 1단계)."""

    SYN_ACK = 1
    """연결 개시 수락(핸드셰이크 2단계)."""

    ACK = 2
    """수신 확인."""

    DATA = 3
    """애플리케이션 페이로드 전송."""

    NACK = 4
    """부정 확인(재전송 요청 등)."""

    FIN = 5
    """연결 종료 요청."""

    FIN_ACK = 6
    """연결 종료 확인."""

    HEARTBEAT = 7
    """연결 유지(keep-alive) 신호."""


class Flags(IntFlag):
    """보조 비트 플래그. 헤더 ``flags`` 필드(1바이트)와 매핑되며 OR로 조합한다."""

    NONE = 0
    """플래그 없음."""

    SYN = 1
    """SYN 비트."""

    ACK = 2
    """ACK 비트."""

    FIN = 4
    """FIN 비트."""

    NACK = 8
    """NACK 비트."""
