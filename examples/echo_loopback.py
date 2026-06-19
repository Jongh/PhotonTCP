"""에코 예제: 무손실 LoopbackChannel 위에서 PhotonTCP 패킷 왕복 시연.

이 스크립트는 PhotonTCP 의 가장 기초적인 흐름을 보여준다:

1. :meth:`LoopbackChannel.pair` 로 무손실(loss/dup/corrupt/reorder=0) 양방향
   인메모리 채널 한 쌍(``ch_a``, ``ch_b``)을 만든다.
2. 송신측(``ch_a``)은 문자열을 UTF-8 바이트로 인코딩해 ``payload`` 로 담은
   :class:`Packet` (type=:data:`PacketType.DATA`)을 만들고, :meth:`Packet.pack`
   으로 와이어 포맷 바이트열로 직렬화한 뒤 :meth:`send_frame` 으로 보낸다.
3. 수신측(``ch_b``)은 :meth:`recv_frame` 으로 프레임을 받아
   :meth:`Packet.unpack` 으로 복원하고, ``payload`` 를 UTF-8 로 디코드한다.
4. 입력 문자열과 복원 문자열을 출력하고, 일치 여부("MATCH"/"MISMATCH")를 찍는다.

여러 메시지를 순차로 보내 각각 그대로 에코 복원되는 것을 보인다.

레포 루트에서 다음과 같이 실행한다::

    python examples/echo_loopback.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 레포 루트(이 파일의 부모의 부모)를 sys.path 에 넣어 `python examples/echo_loopback.py`
# 형태의 직접 실행에서도 `photontcp` 패키지를 import 할 수 있게 한다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from photontcp.channel.loopback import LoopbackChannel
from photontcp.packet.header import Packet
from photontcp.packet.types import PacketType

#: 시연용으로 순차 전송할 메시지들.
MESSAGES = [
    "Hello, PhotonTCP over light!",
    "두 번째 메시지입니다 (UTF-8 한글).",
    "Photons carry packets at c.",
    "",  # 빈 페이로드도 정상 왕복되는지 확인.
]

#: 데모용 고정 세션 식별자.
SESSION_ID = 0x1234

#: 데모용 스트림 식별자.
STREAM_ID = 0


def echo_once(
    ch_send: LoopbackChannel,
    ch_recv: LoopbackChannel,
    text: str,
    seq: int,
    *,
    timeout: float = 1.0,
) -> bool:
    """``text`` 를 한 번 보내고 받아 복원한 뒤 일치 여부를 출력한다.

    Args:
        ch_send: 송신 끝점.
        ch_recv: 수신 끝점(``ch_send`` 의 파트너).
        text: 전송할 문자열.
        seq: 패킷 시퀀스 번호.
        timeout: 수신 대기 시간(초).

    Returns:
        입력과 복원 문자열이 일치하면 ``True``.
    """
    # --- 송신측: 문자열 -> Packet -> bytes -> 채널 ---
    payload = text.encode("utf-8")
    packet = Packet(
        type=PacketType.DATA,
        session_id=SESSION_ID,
        stream_id=STREAM_ID,
        seq=seq,
        ack=0,
        window=0,
        payload=payload,
    )
    ch_send.send_frame(packet.pack())

    # --- 수신측: 채널 -> bytes -> Packet -> 문자열 ---
    raw = ch_recv.recv_frame(timeout=timeout)
    if raw is None:
        print(f"[seq={seq}] 수신 실패(timeout): 보낸 값={text!r}")
        print("  -> MISMATCH")
        return False

    received = Packet.unpack(raw)
    decoded = received.payload.decode("utf-8")

    match = decoded == text
    print(f"[seq={seq}] 송신: {text!r}")
    print(f"[seq={seq}] 수신: {decoded!r}")
    print(f"  -> {'MATCH' if match else 'MISMATCH'}")
    return match


def main() -> int:
    """무손실 루프백 채널로 여러 메시지의 에코 왕복을 시연한다.

    Returns:
        모든 메시지가 일치하면 ``0``, 하나라도 어긋나면 ``1``.
    """
    # 무손실 채널 한 쌍을 만든다(노이즈 파라미터 모두 기본값 0).
    ch_a, ch_b = LoopbackChannel.pair()

    print("=== PhotonTCP 에코 예제 (무손실 LoopbackChannel) ===")
    all_ok = True
    for seq, message in enumerate(MESSAGES, start=1):
        ok = echo_once(ch_a, ch_b, message, seq=seq)
        all_ok = all_ok and ok
        print()

    print(f"=== 결과: {'전체 MATCH' if all_ok else '일부 MISMATCH'} ===")

    ch_a.close()
    ch_b.close()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
