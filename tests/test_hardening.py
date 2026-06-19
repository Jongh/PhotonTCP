"""M7-T08 — 하드닝 테스트.

M7 에서 도입된 각 하드닝 동작(NACK 억제·데이터 재전송 상한·종료 이벤트
정합성·데이터 활동 idle 갱신·``RtoEstimator.clone()``·스레드 안전성·QR
용량 가드·파일 전용 stream·OFFER 검증·acked 기반 progress)을 **결정적으로**
검증한다(완료 기준 1~10 매핑).

설계 원칙
---------
* 시간은 오직 :class:`~photontcp.session.clock.ManualClock` (또는 테스트가
  직접 증가시키는 float ``now``) 으로만 흐른다 — real sleep 없음.
* 채널 노이즈는 고정 ``seed`` 로 재현 가능하다.
* 모든 진행 루프는 유한 반복 상한을 갖고, 미수렴 시 :func:`pytest.fail`.
* QR/스레드 테스트는 라이브러리 부재 시 해당 테스트만
  :func:`pytest.importorskip` 으로 skip 한다(모듈 전체는 import 가능).
"""

from __future__ import annotations

import threading

import pytest

from photontcp.app import ChatSession
from photontcp.app.file import (
    FILE_STREAM_ID,
    FileReceiver,
    FileSender,
    FileTransferState,
)
from photontcp.app.file_codec import (
    FileFrameType,
    encode_control,
    sha256_hex,
)
from photontcp.channel.loopback import LoopbackChannel
from photontcp.packet.types import PacketType
from photontcp.reliability.arq import ArqEndpoint, ArqEvent
from photontcp.reliability.rto import RtoEstimator
from photontcp.session.clock import ManualClock
from photontcp.session.session import Session
from photontcp.session.state_machine import SessionStateMachine
from photontcp.session.states import SessionEvent, SessionState
from photontcp.stream.mux import DEFAULT_STREAM_ID

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

SESSION_ID = 7


# ===========================================================================
# 공용 헬퍼
# ===========================================================================
def _make_arq_pair(*, window_size: int = 8, max_payload: int = 200,
                   max_retx: int = 8, initial_rto: float = 1.0):
    """동일 세션·서로 맞춘 ISN 을 가진 (A 송신, B 수신) ARQ 쌍을 만든다.

    채널 없이 손으로 펌프한다(``test_arq.py`` 패턴): A 가 보내는 DATA 를
    선택적으로 B 에 전달(손실=전달 생략)하고, B 의 ACK/NACK 를 A 에 전달한다.
    """
    a = ArqEndpoint(
        session_id=SESSION_ID, send_isn=0, recv_isn=0,
        window_size=window_size,
        rto=RtoEstimator(initial_rto=initial_rto, min_rto=0.2, max_rto=60.0),
        max_payload=max_payload, max_retx=max_retx,
    )
    b = ArqEndpoint(
        session_id=SESSION_ID, send_isn=0, recv_isn=0,
        window_size=window_size,
        rto=RtoEstimator(initial_rto=initial_rto, min_rto=0.2, max_rto=60.0),
        max_payload=max_payload, max_retx=max_retx,
    )
    return a, b


def _of_type(out, ptype):
    return [p for p in out.packets if p.type == ptype]


def _make_session_pair(
    *,
    loss: float = 0.0,
    seed: int = 0,
    idle_timeout: float = GENEROUS_IDLE_TIMEOUT,
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
    arq_window_size: int = 8,
    arq_max_payload: int = 64,
):
    """손실 채널 위의 (초기자 a, 응답자 b) 세션 쌍 + 공유 ManualClock 생성."""
    ch_a, ch_b = LoopbackChannel.pair(seed=seed, loss=loss)
    clock = ManualClock()
    a = Session(
        ch_a, clock, is_initiator=True, session_id=SESSION_ID, isn=1000,
        heartbeat_interval=heartbeat_interval, idle_timeout=idle_timeout,
        arq_window_size=arq_window_size, arq_max_payload=arq_max_payload,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    b = Session(
        ch_b, clock, is_initiator=False, session_id=SESSION_ID, isn=5000,
        heartbeat_interval=heartbeat_interval, idle_timeout=idle_timeout,
        arq_window_size=arq_window_size, arq_max_payload=arq_max_payload,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    return a, b, clock


def _pump_until(a, b, clock, pred, *, dt=ROUND_DT, max_iters=MAX_ITERS):
    """``pred()`` 가 참이 될 때까지 a/b 를 번갈아 펌프 + 시간 전진."""
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


def _establish(a, b, clock):
    a.connect()
    _pump_until(a, b, clock, lambda: a.is_established and b.is_established)
    assert a.is_established and b.is_established


# ===========================================================================
# 완료 기준 1: NACK 억제
# ===========================================================================
def test_nack_suppressed_to_once_per_hole():
    """한 구멍(누락 seq)에 대해 NACK 가 1회만 생성된다(후속 DATA 에도 추가
    NACK 없음). RTO 백업 재전송으로 결국 인도된다."""
    a, b = _make_arq_pair(window_size=8, max_payload=10)
    now = 0.0
    send_out = a.send(b"0123456789" * 4, now)  # 4 청크: seq 0,1,2,3
    data = _of_type(send_out, PacketType.DATA)
    assert [p.seq for p in data] == [0, 1, 2, 3]

    # seq 0 을 손실(B 에 전달하지 않음)시키고 seq 1,2,3 을 순서대로 전달한다.
    # seq 1 도착 시 구멍(0)에 대해 NACK 1회 발생해야 한다.
    nack_count = 0
    for pkt in data[1:]:
        now += 0.1
        out = b.on_packet(pkt, now)
        nack_count += len(_of_type(out, PacketType.NACK))

    assert nack_count == 1, (
        f"한 구멍(seq 0)에 대해 NACK 가 정확히 1회여야 하는데 {nack_count}회"
    )
    # NACK 가 가리킨 구멍은 seq 0 이다.
    # (첫 후속 DATA = seq 1 에서만 NACK 가 났다.)

    # 후속으로 같은 seq 1 을 중복 전달해도 추가 NACK 가 나면 안 된다.
    now += 0.1
    dup_out = b.on_packet(data[1], now)
    assert _of_type(dup_out, PacketType.NACK) == [], "중복 DATA 가 NACK 재발"

    # 손실 복구: RTO 가 만료되도록 시간을 전진시키면 A 가 seq 0 을 재전송한다.
    now += 5.0
    retx = a.on_tick(now)
    retx_data = _of_type(retx, PacketType.DATA)
    assert any(p.seq == 0 for p in retx_data), "RTO 재전송으로 seq 0 복구 안 됨"

    # 재전송된 seq 0 을 B 에 전달하면 0,1,2,3 이 전부 인도된다.
    now += 0.1
    fill = b.on_packet(retx_data[0], now)
    # 구멍이 메워졌으니 0 과 버퍼된 1,2,3 이 한꺼번에 인도되어야 한다.
    assert len(fill.delivered) == 4


def test_nack_resuppression_after_new_hole():
    """rcv_base 가 구멍을 지나간 뒤, 다른 위치의 새 구멍은 다시 NACK 한다
    (억제 집합이 stale seq 를 정리하므로)."""
    a, b = _make_arq_pair(window_size=16, max_payload=10)
    now = 0.0
    out = a.send(b"x" * 60, now)  # 6 청크: seq 0..5
    data = _of_type(out, PacketType.DATA)
    assert [p.seq for p in data] == [0, 1, 2, 3, 4, 5]

    # 0 손실, 1 도착 -> NACK(0).
    n1 = b.on_packet(data[1], now + 0.1)
    assert len(_of_type(n1, PacketType.NACK)) == 1
    # 0 도착 -> 0,1 인도, rcv_base=2, 억제 집합 정리.
    fill = b.on_packet(data[0], now + 0.2)
    assert len(fill.delivered) == 2

    # 이제 2 를 손실시키고 3 을 도착 -> 새 구멍(2)에 대해 NACK 가 다시 나야 한다.
    n2 = b.on_packet(data[3], now + 0.3)
    nacks = _of_type(n2, PacketType.NACK)
    assert len(nacks) == 1, "새 구멍에 대해 NACK 재발생해야 함"
    assert nacks[0].ack == 2, "새 NACK 는 구멍 seq 2 를 가리켜야 함"


# ===========================================================================
# 완료 기준 2: 데이터 재전송 상한
# ===========================================================================
def test_data_retx_cap_marks_failed_and_stops():
    """상대가 ACK 하지 않고 on_tick 을 RTO 넘게 반복하면 max_retx 초과 후
    is_failed=True + SEND_FAILED 이벤트, 이후 재전송 중단."""
    a, _b = _make_arq_pair(window_size=4, max_payload=10, max_retx=3,
                           initial_rto=1.0)
    now = 0.0
    out = a.send(b"hello", now)
    assert len(_of_type(out, PacketType.DATA)) == 1
    assert not a.is_failed

    saw_failed_event = False
    retx_total = 0
    # 매 tick 마다 RTO 보다 크게 시간 전진. backoff 가 있으므로 넉넉히 전진한다.
    rto_advance = 1000.0
    for _ in range(MAX_ITERS):
        if a.is_failed:
            break
        now += rto_advance
        out = a.on_tick(now)
        retx_total += len(_of_type(out, PacketType.DATA))
        if any(ev is ArqEvent.SEND_FAILED for ev in out.events):
            saw_failed_event = True
    else:
        pytest.fail("max_retx 를 넘겼는데도 엔드포인트가 실패로 표시되지 않음")

    assert a.is_failed and a.failed
    assert saw_failed_event, "SEND_FAILED 이벤트가 surface 되지 않음"
    # max_retx=3 이므로 재전송은 최대 3회(이후 실패로 전환, 더 안 보냄).
    assert retx_total == 3, f"재전송 횟수가 max_retx 와 불일치: {retx_total}"

    # 실패 이후 추가 tick 은 어떤 패킷도 내지 않는다(재전송 중단).
    now += rto_advance
    after = a.on_tick(now)
    assert after.packets == [] and after.events == []


def test_data_retx_cap_normal_path_unaffected():
    """정상 경로(상대가 ACK)에서는 실패가 surface 되지 않는다."""
    a, b = _make_arq_pair(window_size=4, max_payload=10, max_retx=2)
    now = 0.0
    out = a.send(b"hi there", now)
    for pkt in _of_type(out, PacketType.DATA):
        now += 0.1
        ack_out = b.on_packet(pkt, now)
        for ack in _of_type(ack_out, PacketType.ACK):
            now += 0.1
            a.on_packet(ack, now)
    # 시간이 흘러도 outstanding 이 없으니 실패하지 않는다.
    now += 100.0
    a.on_tick(now)
    assert not a.is_failed


def test_session_surfaces_failed_stream():
    """세션 레벨에서 ARQ 재전송 상한 초과가 data_failed_streams() 로 노출된다.

    응답자 b 를 절대 펌프하지 않아 a 의 DATA 가 영영 ACK 되지 않게 한다.
    """
    # max_retx 를 작게 하기 위해 ARQ RTO 추정기 초기값을 작게 잡고, 라운드당
    # 큰 시간 전진으로 빠르게 재전송 budget 을 소진한다. (max_retx 는 ARQ
    # 기본 8 이므로 충분한 라운드를 돈다.)
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)
    a.send_on(DEFAULT_STREAM_ID, b"payload-bytes-here" * 4)

    # 이제 b 를 펌프하지 않는다 -> a 의 DATA 는 ACK 되지 않는다. a 만 펌프하며
    # 큰 시간 전진으로 ARQ on_tick 재전송 budget 을 소진시킨다.
    failed = []
    for _ in range(MAX_ITERS):
        a.pump()
        clock.advance(1.0e6)  # RTO·backoff 를 압도하는 큰 전진.
        failed = a.data_failed_streams()
        if failed:
            break
    else:
        pytest.fail("data 스트림 재전송 상한 초과가 노출되지 않음")

    assert DEFAULT_STREAM_ID in failed


# ===========================================================================
# 완료 기준 3: 종료 이벤트 정합성
# ===========================================================================
def _established_machine_pair():
    """핸드셰이크를 완료한 두 SessionStateMachine (a 초기자, b 응답자)."""
    a = SessionStateMachine(is_initiator=True, session_id=42, isn=1000,
                            idle_timeout=10.0)
    b = SessionStateMachine(is_initiator=False, session_id=42, isn=5000,
                            idle_timeout=10.0)
    syn = a.connect(0.0).packets[0]
    synack = b.on_packet(syn, 0.0).packets[0]
    ack = a.on_packet(synack, 0.0).packets[0]
    b.on_packet(ack, 0.0)
    assert a.is_established and b.is_established
    return a, b


def test_fin_wait_idle_timeout_yields_closed():
    """FIN_WAIT 에서 상대 무응답으로 idle_timeout 전진 시 CLOSED(not
    TIMED_OUT) 이벤트로 종료된다."""
    a, _b = _established_machine_pair()
    out = a.close(1.0)  # ESTABLISHED -> FIN_WAIT, FIN 송신.
    assert a.state is SessionState.FIN_WAIT
    assert [p.type for p in out.packets] == [PacketType.FIN]

    # 상대가 침묵: idle_timeout(10.0) 을 넘기면 CLOSED 로 종료.
    tick = a.on_tick(1.0 + 10.0 + 0.1)
    assert a.is_closed
    assert SessionEvent.CLOSED in tick.events
    assert SessionEvent.TIMED_OUT not in tick.events


def test_close_wait_idle_timeout_yields_closed():
    """CLOSE_WAIT(수동 종료 진행 중)에서 idle_timeout 전진 시 CLOSED."""
    # b2 가 상대(a2)의 FIN 을 받아 CLOSE_WAIT 로 진입하게 만든다.
    a2, b2 = _established_machine_pair()
    a_fin = a2.close(1.0).packets[0]
    out = b2.on_packet(a_fin, 1.0)
    assert b2.state is SessionState.CLOSE_WAIT
    assert SessionEvent.PEER_CLOSED in out.events

    tick = b2.on_tick(1.0 + b2.idle_timeout + 0.1)
    assert b2.is_closed
    assert SessionEvent.CLOSED in tick.events
    assert SessionEvent.TIMED_OUT not in tick.events


def test_established_idle_timeout_yields_timed_out():
    """ESTABLISHED 무수신은 여전히 TIMED_OUT(CLOSED 아님)."""
    a, _b = _established_machine_pair()
    assert a.state is SessionState.ESTABLISHED
    tick = a.on_tick(a.idle_timeout + 0.1)
    assert a.is_closed
    assert SessionEvent.TIMED_OUT in tick.events
    assert SessionEvent.CLOSED not in tick.events


# ===========================================================================
# 완료 기준 4: 데이터 활동 idle 갱신
# ===========================================================================
def test_data_activity_keeps_session_alive():
    """ESTABLISHED 후 (하트비트 비활성·idle_timeout 작게) 데이터만 주고받으며
    시간을 전진해도 idle 로 안 죽고 데이터가 계속 전달된다."""
    # idle_timeout 을 작게, heartbeat 를 사실상 무한대로 둔다 -> 데이터 활동이
    # idle 타이머를 살리는지가 단독으로 검증된다.
    idle = 5.0
    a, b, clock = _make_session_pair(
        loss=0.0, seed=0, idle_timeout=idle, heartbeat_interval=1.0e9,
    )
    _establish(a, b, clock)

    received = bytearray()
    # 라운드당 데이터를 한 메시지씩 주고받으며 idle 보다 작은 dt 로 여러 번
    # 전진한다(누적 전진은 idle 을 한참 넘김). 하트비트가 없으므로 오직 데이터
    # 활동만이 세션을 살린다.
    dt = idle * 0.5  # 한 라운드로는 idle 을 안 넘지만 누적으로는 크게 넘김.
    rounds = 20
    for i in range(rounds):
        a.send_on(DEFAULT_STREAM_ID, f"a-msg-{i}|".encode())
        # 충분히 펌프해 DATA/ACK 가 오가게 한다(각 라운드 양방향 여러 번).
        for _ in range(4):
            a.pump()
            b.pump()
        clock.advance(dt)
        received.extend(b"".join(b.recv_on(DEFAULT_STREAM_ID)))

    # 마지막 잔여분 회수.
    for _ in range(8):
        a.pump()
        b.pump()
    received.extend(b"".join(b.recv_on(DEFAULT_STREAM_ID)))

    # 누적 전진(rounds * dt = 50.0) 이 idle(5.0) 을 크게 넘었음에도 세션이
    # 살아있고 데이터가 전부 도착했다.
    assert clock.now() > idle, "테스트가 idle 한도를 넘기지 못함(무의미)"
    assert a.is_established and b.is_established, "데이터 활동에도 세션이 죽음"
    expected = b"".join(f"a-msg-{i}|".encode() for i in range(rounds))
    assert bytes(received) == expected


# ===========================================================================
# 완료 기준 5: RtoEstimator.clone()
# ===========================================================================
def test_rto_clone_same_config_reset_state():
    """clone() 은 설정 동일·상태 초기화의 새 추정기를 반환하고, 원본
    on_sample 후에도 clone 은 불변이다."""
    orig = RtoEstimator(initial_rto=2.5, min_rto=0.3, max_rto=42.0)
    # 원본에 샘플을 먹여 상태를 바꾼다.
    orig.on_sample(0.5)
    orig.on_sample(0.7)
    assert orig.srtt is not None and orig.rttvar is not None

    clone = orig.clone()
    # 설정은 동일.
    assert clone.initial_rto == orig.initial_rto == 2.5
    assert clone.min_rto == orig.min_rto == 0.3
    assert clone.max_rto == orig.max_rto == 42.0
    # 상태는 초기화(샘플 없음, rto == initial_rto).
    assert clone.srtt is None
    assert clone.rttvar is None
    assert clone.rto() == clone.initial_rto

    # 원본을 더 변형해도 clone 은 영향받지 않는다(독립 인스턴스).
    orig.on_sample(0.9)
    assert clone.srtt is None
    assert clone.rto() == 2.5

    # clone 에 샘플을 줘도 원본은 영향받지 않는다.
    before = orig.rto()
    clone.on_sample(0.1)
    assert orig.rto() == before


# ===========================================================================
# 완료 기준 6: 스레드 안전성 스모크 (결정 검증 아님, 안전성만)
# ===========================================================================
def test_decode_frame_concurrent_threads_no_crash():
    """멀티스레드에서 decode_frame 동시 호출 시 크래시 없음(스레드로컬
    detector)."""
    pytest.importorskip("cv2")
    from photontcp.qr.decode import decode_frame
    from photontcp.qr.encode import encode_frame

    # 디코드 가능한 실제 QR 이미지를 하나 만들어 여러 스레드가 동시에 디코드.
    image = encode_frame(b"thread-safety-smoke-payload")
    errors: list[BaseException] = []
    results: list[bytes | None] = []
    lock = threading.Lock()

    def worker():
        try:
            for _ in range(10):
                r = decode_frame(image)
                with lock:
                    results.append(r)
        except BaseException as exc:  # noqa: BLE001 - 스모크: 어떤 예외든 기록.
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    assert not any(t.is_alive() for t in threads), "decode 스레드가 멈춤"
    assert not errors, f"동시 decode 에서 예외 발생: {errors[:3]}"
    # 결정 검증은 아니지만, 적어도 한 번은 원본을 정확히 복원했어야 한다.
    assert any(r == b"thread-safety-smoke-payload" for r in results)


def test_loopback_send_frame_concurrent_threads_no_crash():
    """멀티스레드에서 loopback 채널 send_frame 동시 호출 시 크래시 없음
    (RNG 락)."""
    a, _b = LoopbackChannel.pair(seed=1, loss=0.3, dup=0.2,
                                 corrupt=0.2, reorder=0.2)
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(tid: int):
        try:
            for i in range(200):
                a.send_frame(f"frame-{tid}-{i}".encode())
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    assert not any(t.is_alive() for t in threads), "send 스레드가 멈춤"
    assert not errors, f"동시 send_frame 에서 예외 발생: {errors[:3]}"


def test_image_loopback_send_frame_concurrent_threads_no_crash():
    """멀티스레드에서 image 채널 send_frame 동시 호출 시 크래시 없음."""
    pytest.importorskip("segno")
    pytest.importorskip("cv2")
    from photontcp.channel.image_loopback import ImageLoopbackChannel

    a, _b = ImageLoopbackChannel.pair(seed=1, loss=0.3, dup=0.2)
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(tid: int):
        try:
            for i in range(20):
                a.send_frame(f"img-{tid}-{i}".encode())
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60.0)

    assert not any(t.is_alive() for t in threads), "image send 스레드가 멈춤"
    assert not errors, f"동시 image send_frame 에서 예외 발생: {errors[:3]}"


# ===========================================================================
# 완료 기준 7: QR 용량 가드
# ===========================================================================
def test_qr_capacity_guard_raises():
    """단일 QR 용량을 초과하는 데이터 -> QRCapacityError."""
    pytest.importorskip("segno")
    from photontcp.qr import QRCapacityError
    from photontcp.qr.encode import encode_frame

    with pytest.raises(QRCapacityError):
        encode_frame(b"x" * 5000)


def test_qr_small_payload_ok():
    """작은 페이로드는 정상 인코딩(가드가 정상 경로를 막지 않음)."""
    pytest.importorskip("segno")
    import numpy as np

    from photontcp.qr.encode import encode_frame

    img = encode_frame(b"ok")
    assert isinstance(img, np.ndarray)
    assert img.dtype == np.uint8


# ===========================================================================
# 완료 기준 8: 파일 전용 stream (채팅 + 파일 병행, 기본값만으로 무간섭)
# ===========================================================================
def test_chat_and_file_concurrent_default_streams():
    """기본 stream_id 만으로 ChatSession(stream 1) + 파일(stream 2)을 같은
    세션 위에서 병행해도 서로 간섭 없이 각각 도착한다(무손실)."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    # 채팅(기본 stream 1).
    chat_a = ChatSession(a, clock)
    chat_b = ChatSession(b, clock)
    chat_texts = ["hello", "world", "from-a"]

    # 파일(기본 stream 2, FILE_STREAM_ID). a 가 보내고 b 가 받는다.
    file_data = bytes(range(256)) * 6  # 1536 바이트.
    sender = FileSender(a, clock, name="payload.bin", data=file_data,
                        chunk_size=128)
    receiver = FileReceiver(b, clock)

    # 채팅 default 와 파일 default 가 서로 다른 stream 임을 명시 확인.
    assert chat_a.stream_id == DEFAULT_STREAM_ID == 1
    assert sender.stream_id == FILE_STREAM_ID == 2
    assert chat_a.stream_id != sender.stream_id

    for t in chat_texts:
        chat_a.send_message(t)
    sender.start()

    def _done():
        # 각 endpoint 의 pump 가 자기 세션을 펌프하므로, 양측 pump 를 모두 돈다.
        chat_a.pump()
        chat_b.pump()
        sender.pump()
        receiver.pump()
        clock.advance(ROUND_DT)
        return (
            len(chat_b.received) >= len(chat_texts)
            and sender.is_complete
            and receiver.is_complete
        )

    converged = False
    for _ in range(MAX_ITERS):
        if _done():
            converged = True
            break
    if not converged:
        pytest.fail(
            f"채팅+파일 병행이 수렴하지 않음 "
            f"(chat_b={len(chat_b.received)}, sender={sender.state}, "
            f"receiver={receiver.state})"
        )

    # 채팅 메시지가 stream 1 로 순서대로 도착.
    assert [m.text for m in chat_b.received] == chat_texts
    # 파일이 stream 2 로 검증 완료(서로 간섭 없음).
    assert sender.is_complete
    assert receiver.is_complete
    assert receiver.verified
    assert receiver.file_bytes == file_data
    # 파일 데이터가 채팅 메시지로 새지 않았음(채팅 수신은 정확히 보낸 것만).
    assert len(chat_b.received) == len(chat_texts)


# ===========================================================================
# 완료 기준 9: OFFER 검증
# ===========================================================================
class _CorruptOfferSender(FileSender):
    """OFFER 프레임을 의도적으로 손상시켜 주입하는 테스트용 송신기.

    소스(`photontcp/**`) 미수정 제약을 지키기 위해 FileSender 를 서브클래싱해
    ``start()`` 의 OFFER 송신만 손상 OFFER 로 대체한다(기본 진행 로직은 그대로).
    """

    def __init__(self, *args, bad_offer: bytes, **kwargs):
        super().__init__(*args, **kwargs)
        self._bad_offer = bad_offer

    def start(self) -> None:
        # 정상 OFFER 대신 손상 OFFER 를 직접 stream 에 주입한다.
        self.session.send_on(self.stream_id, self._bad_offer)
        self._state = FileTransferState.OFFERED


@pytest.mark.parametrize(
    "bad_body",
    [
        {"size": 10, "sha256": "ab" * 32},  # name 누락.
        {"name": "f", "sha256": "ab" * 32},  # size 누락.
        {"name": "f", "size": 10},  # sha256 누락.
        {"name": 123, "size": 10, "sha256": "ab" * 32},  # name 타입오류.
        {"name": "f", "size": "big", "sha256": "ab" * 32},  # size 타입오류.
        {"name": "f", "size": -1, "sha256": "ab" * 32},  # size 음수.
        {"name": "f", "size": True, "sha256": "ab" * 32},  # bool size 거부.
        {"name": "f", "size": 10, "sha256": 999},  # sha256 타입오류.
    ],
)
def test_corrupt_offer_rejected(bad_body):
    """손상/누락 필드 OFFER 를 받은 FileReceiver 가 FAILED(REJECT)로 안전
    처리된다."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    bad_offer = encode_control(FileFrameType.OFFER, bad_body)
    sender = _CorruptOfferSender(
        a, clock, name="f", data=b"unused", bad_offer=bad_offer,
    )
    receiver = FileReceiver(b, clock)

    sender.start()

    def _done():
        sender.pump()
        receiver.pump()
        clock.advance(ROUND_DT)
        return receiver.is_failed

    for _ in range(MAX_ITERS):
        if _done():
            break
    else:
        pytest.fail(f"손상 OFFER 가 거부되지 않음 (receiver={receiver.state})")

    assert receiver.is_failed
    assert receiver.state is FileTransferState.FAILED
    assert not receiver.verified


def test_valid_offer_not_rejected():
    """정상 OFFER 는 거부되지 않는다(검증이 정상 경로를 막지 않음)."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    data = b"valid file contents"
    valid_offer = encode_control(
        FileFrameType.OFFER,
        {"name": "f.txt", "size": len(data), "sha256": sha256_hex(data)},
    )
    sender = _CorruptOfferSender(
        a, clock, name="f.txt", data=data, bad_offer=valid_offer,
    )
    receiver = FileReceiver(b, clock)
    sender.start()

    def _done():
        sender.pump()
        receiver.pump()
        clock.advance(ROUND_DT)
        # 정상 OFFER 면 RECEIVING 으로 진입(REJECT/FAILED 가 아님).
        return receiver.state is FileTransferState.RECEIVING

    for _ in range(200):
        if _done():
            break
    else:
        pytest.fail(f"정상 OFFER 가 수락되지 않음 (receiver={receiver.state})")

    assert not receiver.is_failed
    assert receiver.state is FileTransferState.RECEIVING


# ===========================================================================
# 완료 기준 10: acked 기반 progress
# ===========================================================================
def test_file_sender_progress_tracks_acks():
    """FileSender.progress 가 ACK 에 따라 증가한다: 큐잉 즉시 1.0 아님,
    전송 도중 0<p<1 관측, 완료 시 1.0(또는 ~1.0)."""
    a, b, clock = _make_session_pair(
        loss=0.0, seed=0, arq_window_size=4, arq_max_payload=64,
    )
    _establish(a, b, clock)

    # 윈도가 작고 청크가 많도록 큰 파일 + 작은 chunk_size.
    file_data = bytes(range(256)) * 16  # 4096 바이트.
    sender = FileSender(a, clock, name="big.bin", data=file_data,
                        chunk_size=64)
    receiver = FileReceiver(b, clock)

    # 시작 직후(OFFER 만 큐잉) progress 는 0.0 이어야 한다(아직 ACK 없음).
    sender.start()
    assert sender.progress == 0.0, "start 직후 progress 가 0 이 아님"

    # 모든 청크를 큐잉하기 직전/직후에도 즉시 1.0 이 되지 않아야 한다.
    saw_partial = False
    progresses: list[float] = []
    for _ in range(MAX_ITERS):
        sender.pump()
        receiver.pump()
        clock.advance(ROUND_DT)
        p = sender.progress
        progresses.append(p)
        if 0.0 < p < 1.0:
            saw_partial = True
        if sender.is_complete and receiver.is_complete:
            break
    else:
        pytest.fail(
            f"파일 전송이 수렴하지 않음 (sender={sender.state}, "
            f"receiver={receiver.state}, p={sender.progress})"
        )

    assert receiver.verified
    # 큐잉 즉시 1.0 이 아니라 ACK 따라 증가하는 중간값을 관측했어야 한다.
    assert saw_partial, (
        f"전송 도중 0<progress<1 을 관측하지 못함: {progresses[:10]}"
    )
    # progress 는 단조 비감소(누적 acked 기반).
    assert all(b >= a for a, b in zip(progresses, progresses[1:])), (
        "progress 가 감소함(누적 acked 기반이 아님)"
    )
    # 완료 시 ~1.0 (acked 가 파일 크기 이상이면 1.0 으로 clamp).
    assert sender.progress == 1.0


def test_file_progress_not_one_at_queue_time_lossless():
    """무손실이라도 OFFER 큐잉 직후 progress 가 1.0 이 아니다(ACK 대기)."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)
    data = b"some bytes to send across" * 10
    sender = FileSender(a, clock, name="x", data=data, chunk_size=32)
    sender.start()
    # 어떤 pump 도 하기 전: ACK 가 도착할 수 없으므로 progress == 0.0.
    assert sender.progress == 0.0
