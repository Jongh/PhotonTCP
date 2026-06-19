"""M4-T07 — 채팅 통합 테스트.

손실/무손실 :class:`~photontcp.channel.loopback.LoopbackChannel` + 가상
:class:`~photontcp.session.clock.ManualClock` + 두
:class:`~photontcp.session.session.Session` 위에 두
:class:`~photontcp.app.ChatSession` 을 올려, 세션 수립 후 **양방향 텍스트
채팅**이 손실에도 순서대로 무손실 교환됨을 결정적으로 검증한다.

드라이브 패턴
-------------
두 :class:`ChatSession` 을 ``a.pump()`` / ``b.pump()`` 로 번갈아 펌프하고,
**매 라운드마다** 공유 :class:`ManualClock` 을 ``advance(dt)`` 로 전진시킨다.
가상시간이 흐르면 제어 RTO 와 ARQ 데이터 RTO 가 만료되어 손실된
SYN/SYN_ACK/DATA 가 재전송된다. 모든 진행 루프는 유한 반복 상한을 갖고,
미수렴 시 :func:`pytest.fail` 로 즉시 실패한다.

결정성
------
* 채널 노이즈는 고정 ``seed`` 로 재현 가능하다.
* 시간은 오직 :class:`ManualClock` 으로만 흐른다 (real sleep 없음).
* 메시지 ``timestamp`` 도 주입 clock 기반이라 결정적이다.

타이밍/손실률/seed 선택 근거
----------------------------
손실 복구에는 RTO 가 여러 번 만료되도록 충분한 가상시간 전진이 필요한 한편,
핸드셰이크/전송이 끝나기 전에 ``idle_timeout`` 으로 세션이 죽으면 안 된다.
따라서 ``idle_timeout`` 을 넉넉히 크게 주고, 라운드당 ``dt`` 는 제어 RTO
(기본 0.5s) 와 ARQ 초기 RTO(1.0s) 보다 크게(1.5s) 잡아 매 라운드 양쪽
타이머가 만료되게 한다. 손실 시나리오의 ``loss``/``seed`` 는
``tests/test_session_reliable.py`` 에서 동일 트랜스포트(loss=0.25)에 대해
양방향 핸드셰이크+데이터 수렴이 사전 검증된 seed 집합을 재사용한다.
"""

from __future__ import annotations

import pytest

from photontcp.app import ChatMessage, ChatSession
from photontcp.channel.loopback import LoopbackChannel
from photontcp.reliability.rto import RtoEstimator
from photontcp.session.clock import ManualClock
from photontcp.session.session import Session

# 모든 진행 루프의 하드 상한. 멈춘 핸드셰이크/전송이 무한 루프 대신 즉시
# 실패하도록 보장한다.
MAX_ITERS = 2000

# 제어 RTO(0.5s)·ARQ 초기 RTO(1.0s) 가 모두 매 라운드 만료되도록, 라운드당
# 가상시간 전진폭은 둘보다 크게 잡는다.
ROUND_DT = 1.5

# 손실 복구 시나리오에서 세션이 idle 로 죽지 않도록 사실상 무한대 idle 한도.
GENEROUS_IDLE_TIMEOUT = 1.0e9

# idle 을 무한대로 두므로 하트비트가 핸드셰이크/데이터를 방해하지 않도록
# 기본보다 크게 둔다.
HEARTBEAT_INTERVAL = 1.0e6


def _make_chat_pair(
    *,
    loss: float = 0.0,
    seed: int = 0,
    session_id: int = 1,
    a_isn: int = 1000,
    b_isn: int = 5000,
    arq_window_size: int = 8,
    arq_max_payload: int = 64,
    idle_timeout: float = GENEROUS_IDLE_TIMEOUT,
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
):
    """손실 채널 위의 (초기자, 응답자) 채팅 쌍 + 공유 ManualClock 생성.

    Returns ``(chat_a, chat_b, clock)``. 두 세션은 하나의
    :class:`ManualClock` 을 공유하므로 라운드마다 ``clock.advance(dt)`` 한
    번으로 양쪽 가상시간이 함께 전진한다. ARQ RTO 추정기는 결정성을 위해
    각 세션에 명시적으로 주입한다(초기 RTO 1.0s).
    """
    ch_a, ch_b = LoopbackChannel.pair(seed=seed, loss=loss)
    clock = ManualClock()
    a = Session(
        ch_a,
        clock,
        is_initiator=True,
        session_id=session_id,
        isn=a_isn,
        heartbeat_interval=heartbeat_interval,
        idle_timeout=idle_timeout,
        arq_window_size=arq_window_size,
        arq_max_payload=arq_max_payload,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    b = Session(
        ch_b,
        clock,
        is_initiator=False,
        session_id=session_id,
        isn=b_isn,
        heartbeat_interval=heartbeat_interval,
        idle_timeout=idle_timeout,
        arq_window_size=arq_window_size,
        arq_max_payload=arq_max_payload,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    chat_a = ChatSession(a, clock)
    chat_b = ChatSession(b, clock)
    return chat_a, chat_b, clock


def _pump_both_advancing(
    a: ChatSession,
    b: ChatSession,
    clock: ManualClock,
    pred,
    *,
    dt: float = ROUND_DT,
    max_iters: int = MAX_ITERS,
) -> None:
    """``pred()`` 가 참이 될 때까지 a/b 를 번갈아 펌프 + 시간 전진.

    각 라운드: pred 검사 -> a.pump() -> b.pump() -> clock.advance(dt).
    시간을 매 라운드 전진시키므로 제어 RTO·ARQ RTO 가 주기적으로 만료되어
    손실분이 재전송된다. 미수렴 시 :func:`pytest.fail`.
    """
    for _ in range(max_iters):
        if pred():
            return
        a.pump()
        b.pump()
        clock.advance(dt)
    pytest.fail(
        f"progression did not converge within {max_iters} iterations "
        f"(a={a.state}, b={b.state}, t={clock.now()})"
    )


def _establish(a: ChatSession, b: ChatSession, clock: ManualClock) -> None:
    """손실 채널에서 핸드셰이크를 완료시킨다(미수렴 시 실패)."""
    a.connect()
    _pump_both_advancing(
        a, b, clock, lambda: a.is_established and b.is_established
    )
    assert a.is_established and b.is_established


def _exchange_until_received(
    a: ChatSession,
    b: ChatSession,
    clock: ManualClock,
    *,
    expect_at_b: int,
    expect_at_a: int,
) -> None:
    """양측이 기대 개수만큼 메시지를 받을 때까지 펌프 + 시간 전진.

    :meth:`ChatSession.pump` 가 받은 메시지를 ``received`` 에 누적하므로,
    누적 개수로 수렴을 판정한다.
    """

    def _done() -> bool:
        return (
            len(b.received) >= expect_at_b and len(a.received) >= expect_at_a
        )

    _pump_both_advancing(a, b, clock, _done)
    # 루프 종료 직전 라운드의 마지막 인도분까지 한 번 더 회수(경계 안전).
    a.pump()
    b.pump()


# --------------------------------------------------------------------------- #
# 1. 무손실 양방향 채팅: 각 측이 상대 메시지를 순서대로 전부 수신
# --------------------------------------------------------------------------- #


def test_lossless_bidirectional_chat() -> None:
    """무손실 채널: a→b, b→a 메시지가 순서대로 전부 도착(text·msg_id 보존)."""
    a, b, clock = _make_chat_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    a_texts = ["hello from a", "second a", "third a"]
    b_texts = ["hi from b", "second b", "third b", "fourth b"]

    a_ids = [a.send_message(t) for t in a_texts]
    b_ids = [b.send_message(t) for t in b_texts]

    # msg_id 는 1 부터 단조 증가.
    assert a_ids == [1, 2, 3]
    assert b_ids == [1, 2, 3, 4]

    _exchange_until_received(
        a, b, clock, expect_at_b=len(a_texts), expect_at_a=len(b_texts)
    )

    # b 가 a 의 메시지를 순서대로 전부 수신.
    assert [m.text for m in b.received] == a_texts
    assert [m.msg_id for m in b.received] == a_ids
    # a 가 b 의 메시지를 순서대로 전부 수신.
    assert [m.text for m in a.received] == b_texts
    assert [m.msg_id for m in a.received] == b_ids
    # 모두 ChatMessage 인스턴스.
    assert all(isinstance(m, ChatMessage) for m in b.received + a.received)


# --------------------------------------------------------------------------- #
# 2. 손실 채널 양방향 채팅: ARQ 재전송으로 전부 순서대로 도착
# --------------------------------------------------------------------------- #


# loss=0.25 에서 양방향 핸드셰이크+데이터 수렴이 (test_session_reliable
# 에서) 사전 검증된 seed 집합.
_BIDIR_LOSS_SEEDS = [2, 3, 12]


@pytest.mark.parametrize("seed", _BIDIR_LOSS_SEEDS)
def test_lossy_bidirectional_chat(seed: int) -> None:
    """loss=0.25 채널: 손실에도 ARQ 재전송으로 양방향 메시지가 순서대로
    무손실 도착(text 일치)."""
    a, b, clock = _make_chat_pair(loss=0.25, seed=seed)
    _establish(a, b, clock)

    a_texts = [f"a-message-{i}" for i in range(5)]
    b_texts = [f"b-message-{i}" for i in range(5)]

    for t in a_texts:
        a.send_message(t)
    for t in b_texts:
        b.send_message(t)

    _exchange_until_received(
        a, b, clock, expect_at_b=len(a_texts), expect_at_a=len(b_texts)
    )

    assert [m.text for m in b.received] == a_texts
    assert [m.text for m in a.received] == b_texts
    # 손실 채널에서 실제로 시간이 흘러 재전송 기회가 있었음을 확인.
    assert clock.now() > 0.0


# --------------------------------------------------------------------------- #
# 3. 유니코드 메시지: 한글/이모지가 손상 없이 전달
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("loss,seed", [(0.0, 0), (0.25, 2)])
def test_unicode_messages(loss: float, seed: int) -> None:
    """한글/이모지/혼합 유니코드 text 가 손상 없이 양방향 전달된다."""
    a, b, clock = _make_chat_pair(loss=loss, seed=seed)
    _establish(a, b, clock)

    a_texts = ["안녕하세요", "이모지 👍🚀✨", "혼합 mixed 텍스트 123"]
    b_texts = ["반갑습니다 🙂", "한글과 emoji 🎉 섞기", "끝 🌟"]

    for t in a_texts:
        a.send_message(t)
    for t in b_texts:
        b.send_message(t)

    _exchange_until_received(
        a, b, clock, expect_at_b=len(a_texts), expect_at_a=len(b_texts)
    )

    assert [m.text for m in b.received] == a_texts
    assert [m.text for m in a.received] == b_texts


# --------------------------------------------------------------------------- #
# 4. 다중 메시지 순서·개수·필드 보존 (무손실, 더 많은 메시지)
# --------------------------------------------------------------------------- #


def test_many_messages_order_and_fields_preserved() -> None:
    """한쪽이 다수의 메시지를 연속 전송해도 순서·개수·msg_id·text 가 모두
    보존된다(무손실)."""
    a, b, clock = _make_chat_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    count = 20
    texts = [f"msg #{i} body" for i in range(count)]
    sent_ids = [a.send_message(t) for t in texts]

    _exchange_until_received(a, b, clock, expect_at_b=count, expect_at_a=0)

    assert len(b.received) == count
    assert [m.msg_id for m in b.received] == sent_ids
    assert [m.text for m in b.received] == texts
    # b 는 아무것도 보내지 않았으므로 a 는 메시지를 받지 않는다.
    assert a.received == []


# --------------------------------------------------------------------------- #
# 5. 결정성: 동일 seed → 동일 수신 결과
# --------------------------------------------------------------------------- #


def test_deterministic_replay_same_seed() -> None:
    """동일 seed 로 두 번 돌리면 수신 메시지(text·msg_id·timestamp)가
    완전히 동일하다."""

    def _run():
        a, b, clock = _make_chat_pair(loss=0.25, seed=3)
        _establish(a, b, clock)
        for i in range(4):
            a.send_message(f"a{i}")
            b.send_message(f"b{i}")
        _exchange_until_received(a, b, clock, expect_at_b=4, expect_at_a=4)
        snap = lambda msgs: [
            (m.msg_id, m.timestamp, m.text) for m in msgs
        ]
        return snap(b.received), snap(a.received)

    b1, a1 = _run()
    b2, a2 = _run()
    assert b1 == b2
    assert a1 == a2
    assert [t for _, _, t in b1] == ["a0", "a1", "a2", "a3"]
    assert [t for _, _, t in a1] == ["b0", "b1", "b2", "b3"]
