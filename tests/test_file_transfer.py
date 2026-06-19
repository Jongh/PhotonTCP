"""M6-T04 — 파일 전송 통합 테스트.

손실/무손실 :class:`~photontcp.channel.loopback.LoopbackChannel` + 가상
:class:`~photontcp.session.clock.ManualClock` + 두
:class:`~photontcp.session.session.Session` 위에
:class:`~photontcp.app.FileSender` / :class:`~photontcp.app.FileReceiver` 를
올려, 단방향 파일 전송이 (손실에도) 무손실·무결하게 전달되고 앱 레벨 완료
핸드셰이크(OFFER/ACCEPT/CHUNK/DONE/ACK)가 동작함을 결정적으로 검증한다.

드라이브 패턴
-------------
``tests/test_chat.py`` / ``tests/test_session_reliable.py`` 의 패턴을 그대로
따른다. 송·수신 두 엔드포인트를 ``sender.pump()`` / ``receiver.pump()`` 로
번갈아 펌프하고, **매 라운드마다** 공유 :class:`ManualClock` 을
``advance(dt)`` 로 전진시킨다. 가상시간이 흐르면 제어 RTO·ARQ 데이터 RTO 가
만료되어 손실된 SYN/SYN_ACK/DATA 가 재전송된다. 모든 진행 루프는 유한
반복 상한을 갖고 미수렴 시 :func:`pytest.fail` 로 즉시 실패한다.

결정성
------
* 채널 노이즈는 고정 ``seed`` 로 재현 가능하다.
* 시간은 오직 :class:`ManualClock` 으로만 흐른다(real sleep 없음).
* 파일 코덱은 타임스탬프를 싣지 않으므로 동일 입력 → 동일 결과.

타이밍/손실률/seed 선택 근거
----------------------------
손실 복구에는 RTO 가 여러 번 만료되도록 충분한 가상시간 전진이 필요한 한편,
핸드셰이크/전송이 끝나기 전에 ``idle_timeout`` 으로 세션이 죽으면 안 된다.
따라서 ``idle_timeout`` 을 넉넉히 크게 주고, 라운드당 ``dt`` 는 제어 RTO
(기본 0.5s)·ARQ 초기 RTO(1.0s)보다 크게(1.5s) 잡아 매 라운드 양쪽 타이머가
만료되게 한다. 손실 시나리오의 ``loss``/``seed`` 는
``tests/test_session_reliable.py`` / ``tests/test_chat.py`` 에서 동일
트랜스포트(loss=0.2~0.3) 단방향 대용량 전송 수렴이 사전 검증된 seed 집합을
재사용한다.
"""

from __future__ import annotations

import pytest

from photontcp.app import FileReceiver, FileSender, FileTransferState
from photontcp.app.file import FILE_STREAM_ID
from photontcp.app.file_codec import FileFrameType, encode_control, sha256_hex
from photontcp.channel.loopback import LoopbackChannel
from photontcp.reliability.rto import RtoEstimator
from photontcp.session.clock import ManualClock
from photontcp.session.session import Session

# 모든 진행 루프의 하드 상한. 멈춘 핸드셰이크/전송이 무한 루프 대신 즉시
# 실패하도록 보장한다.
MAX_ITERS = 4000

# 제어 RTO(0.5s)·ARQ 초기 RTO(1.0s)가 모두 매 라운드 만료되도록, 라운드당
# 가상시간 전진폭은 둘보다 크게 잡는다.
ROUND_DT = 1.5

# 손실 복구 시나리오에서 세션이 idle 로 죽지 않도록 사실상 무한대 idle 한도.
GENEROUS_IDLE_TIMEOUT = 1.0e9

# idle 을 무한대로 두므로 하트비트가 핸드셰이크/데이터를 방해하지 않도록
# 기본보다 크게 둔다.
HEARTBEAT_INTERVAL = 1.0e6


# --------------------------------------------------------------------------- #
# 픽스처 헬퍼
# --------------------------------------------------------------------------- #


def _make_session_pair(
    *,
    loss: float = 0.0,
    seed: int = 0,
    session_id: int = 1,
    a_isn: int = 1000,
    b_isn: int = 5000,
    arq_window_size: int = 8,
    arq_max_payload: int = 64,
):
    """손실 채널 위의 (초기자 a, 응답자 b) 세션 쌍 + 공유 ManualClock 생성.

    Returns ``(a, b, clock)``. 두 세션은 하나의 :class:`ManualClock` 을
    공유하므로 라운드마다 ``clock.advance(dt)`` 한 번으로 양쪽 가상시간이
    함께 전진한다. ARQ RTO 추정기는 결정성을 위해 명시적으로 주입한다.
    """
    ch_a, ch_b = LoopbackChannel.pair(seed=seed, loss=loss)
    clock = ManualClock()
    a = Session(
        ch_a,
        clock,
        is_initiator=True,
        session_id=session_id,
        isn=a_isn,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=GENEROUS_IDLE_TIMEOUT,
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
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=GENEROUS_IDLE_TIMEOUT,
        arq_window_size=arq_window_size,
        arq_max_payload=arq_max_payload,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    return a, b, clock


def _establish(a: Session, b: Session, clock: ManualClock) -> None:
    """손실 채널에서 핸드셰이크를 완료시킨다(미수렴 시 실패)."""
    a.connect()
    for _ in range(MAX_ITERS):
        if a.is_established and b.is_established:
            break
        a.pump()
        b.pump()
        clock.advance(ROUND_DT)
    else:
        pytest.fail(
            f"handshake did not converge within {MAX_ITERS} iterations "
            f"(a={a.state}, b={b.state}, t={clock.now()})"
        )
    assert a.is_established and b.is_established


def _drive_transfer(
    sender,
    receiver,
    clock: ManualClock,
    pred,
    *,
    dt: float = ROUND_DT,
    max_iters: int = MAX_ITERS,
    progress_track=None,
) -> None:
    """``pred()`` 가 참이 될 때까지 sender/receiver 를 번갈아 펌프 + 시간 전진.

    각 라운드: pred 검사 -> sender.pump() -> receiver.pump() -> advance(dt).
    ``progress_track`` 가 주어지면 매 라운드 (sender.progress, receiver.progress)
    스냅샷을 append 한다(진행률 단조성 검증용). 미수렴 시 :func:`pytest.fail`.
    """
    for _ in range(max_iters):
        if pred():
            return
        sender.pump()
        receiver.pump()
        if progress_track is not None:
            progress_track.append((sender.progress, receiver.progress))
        clock.advance(dt)
    pytest.fail(
        f"file transfer did not converge within {max_iters} iterations "
        f"(sender={sender.state}, receiver={receiver.state}, "
        f"t={clock.now()})"
    )


def _close_session_pair(a: Session, b: Session, clock: ManualClock) -> None:
    """sender 측 세션을 active close 하고 양쪽이 CLOSED 될 때까지 펌프."""
    a.close()
    for _ in range(MAX_ITERS):
        if a.is_closed and b.is_closed:
            return
        a.pump()
        b.pump()
        clock.advance(ROUND_DT)
    pytest.fail(
        f"close did not converge within {MAX_ITERS} iterations "
        f"(a={a.state}, b={b.state}, t={clock.now()})"
    )


# --------------------------------------------------------------------------- #
# 1. 무손실 전체 전송: file_bytes == 원본, verified True, name 보존
# --------------------------------------------------------------------------- #


def test_lossless_full_transfer() -> None:
    """무손실 채널: 여러 청크로 쪼개지는 파일이 전부 전달되고 SHA 검증 통과."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    data = bytes(range(256)) * 16  # 4096 바이트 -> chunk_size 512 면 8 청크.
    name = "payload.bin"
    sender = FileSender(a, clock, name=name, data=data, chunk_size=512)
    receiver = FileReceiver(b, clock)

    sender.start()
    _drive_transfer(
        sender,
        receiver,
        clock,
        lambda: sender.is_complete and receiver.is_complete,
    )

    assert sender.is_complete
    assert sender.state is FileTransferState.COMPLETE
    assert receiver.is_complete
    assert receiver.state is FileTransferState.COMPLETE
    assert receiver.file_bytes == data
    assert receiver.verified is True
    assert receiver.name == name
    # 수신 파일의 SHA-256 이 원본과 일치.
    assert sha256_hex(receiver.file_bytes) == sha256_hex(data)
    # 진행률 완료치.
    assert sender.progress == pytest.approx(1.0)
    assert receiver.progress == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 2. 손실 채널 전송: ARQ 재전송으로 전부 전달 + SHA 일치
# --------------------------------------------------------------------------- #


# loss=0.2~0.3 단방향 대용량 전송 수렴이 사전 검증된(test_session_reliable
# / test_chat) seed 집합.
_LOSS_SEEDS = [2, 3, 12, 13]


@pytest.mark.parametrize("seed", _LOSS_SEEDS)
def test_lossy_full_transfer(seed: int) -> None:
    """loss=0.2 채널: 손실에도 ARQ 재전송으로 파일이 전부 전달·SHA 일치."""
    a, b, clock = _make_session_pair(loss=0.2, seed=seed)
    _establish(a, b, clock)

    data = bytes(range(256)) * 8  # 2048 바이트.
    name = "lossy.dat"
    sender = FileSender(a, clock, name=name, data=data, chunk_size=128)
    receiver = FileReceiver(b, clock)

    sender.start()
    _drive_transfer(
        sender,
        receiver,
        clock,
        lambda: sender.is_complete and receiver.is_complete,
    )

    assert sender.is_complete and receiver.is_complete
    assert receiver.file_bytes == data
    assert receiver.verified is True
    assert receiver.name == name
    assert sha256_hex(receiver.file_bytes) == sha256_hex(data)
    # 손실 채널에서 실제로 시간이 흘러 재전송 기회가 있었음을 확인.
    assert clock.now() > 0.0


# --------------------------------------------------------------------------- #
# 3. 완료 핸드셰이크 / flush-on-close
# --------------------------------------------------------------------------- #


def test_completion_handshake_then_close() -> None:
    """sender 가 COMPLETE(FILE_ACK 수신) 된 시점에 receiver 도 이미 전체 파일을
    보유·검증한 상태이며, 그 뒤 sender 세션 close → 양쪽 CLOSED.

    sender 가 COMPLETE 전에는 close 하지 않음을 흐름으로 보인다(close 는 오직
    is_complete 이후에만 호출).
    """
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    data = bytes(range(200)) * 10  # 2000 바이트.
    sender = FileSender(a, clock, name="handshake.bin", data=data, chunk_size=256)
    receiver = FileReceiver(b, clock)

    sender.start()
    # sender 가 COMPLETE(FILE_ACK 수신) 될 때까지만 구동.
    _drive_transfer(sender, receiver, clock, lambda: sender.is_complete)

    # sender COMPLETE 시점: ACK 는 모든 청크+DONE 뒤에 신뢰성 스트림으로 도착하므로
    # receiver 는 이미 전체 파일을 보유·검증한 상태여야 한다(flush-on-close 보장).
    assert sender.is_complete
    assert receiver.is_complete
    assert receiver.file_bytes == data
    assert receiver.verified is True

    # 세션은 아직 살아 있어야 한다(앱이 스스로 close 하지 않음).
    assert not a.is_closed
    assert not b.is_closed

    # COMPLETE 이후에야 sender 세션을 닫는다 -> 양쪽 CLOSED.
    _close_session_pair(a, b, clock)
    assert a.is_closed
    assert b.is_closed


# --------------------------------------------------------------------------- #
# 4. 무결성 실패 경로: 잘못된 sha 를 OFFER 하면 FILE_NACK -> FAILED
# --------------------------------------------------------------------------- #


class _BadShaSender(FileSender):
    """OFFER 에 의도적으로 잘못된 sha256 을 실어 무결성 실패를 유발하는 sender.

    소스(``photontcp/**``) 미수정 제약을 지키기 위해, 공개 코덱 헬퍼만 사용해
    ``start()`` 만 오버라이드한다. 실제 데이터는 정상 전송되지만 OFFER 의
    sha 가 틀리므로 receiver 의 DONE 검증이 불일치 -> FILE_NACK -> FAILED.
    """

    def start(self) -> None:  # noqa: D401 - 부모 시그니처 유지
        offer = encode_control(
            FileFrameType.OFFER,
            {
                "name": self.name,
                "size": len(self.data),
                # 원본과 다른(틀린) 해시: 모두 0 으로 채운 64 hex.
                "sha256": "0" * 64,
            },
        )
        self.session.send_on(self.stream_id, offer)
        self._state = FileTransferState.OFFERED


def test_integrity_failure_nack() -> None:
    """OFFER 의 sha256 이 실제 데이터와 다르면 receiver 가 FILE_NACK 를 보내고
    양쪽이 무결성 실패 처리된다(receiver FAILED·verified False, sender FAILED)."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    data = bytes(range(256)) * 4  # 1024 바이트.
    sender = _BadShaSender(a, clock, name="corrupt.bin", data=data, chunk_size=256)
    receiver = FileReceiver(b, clock)

    sender.start()
    _drive_transfer(
        sender,
        receiver,
        clock,
        lambda: sender.is_failed and receiver.is_failed,
    )

    assert receiver.is_failed
    assert receiver.state is FileTransferState.FAILED
    assert receiver.verified is False
    assert receiver.file_bytes is None  # 완료되지 않았으므로 노출 안 됨.
    assert sender.is_failed
    assert sender.state is FileTransferState.FAILED


# --------------------------------------------------------------------------- #
# 5. 진행률: 전송 중 단조 비감소, 완료 시 전체 도달
# --------------------------------------------------------------------------- #


def test_progress_monotonic_and_complete() -> None:
    """전송 동안 sender/receiver progress 가 단조 비감소이고 완료 시 1.0 에 도달."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    _establish(a, b, clock)

    data = bytes(range(256)) * 12  # 3072 바이트.
    sender = FileSender(a, clock, name="progress.bin", data=data, chunk_size=128)
    receiver = FileReceiver(b, clock)

    track: list[tuple[float, float]] = []
    sender.start()
    _drive_transfer(
        sender,
        receiver,
        clock,
        lambda: sender.is_complete and receiver.is_complete,
        progress_track=track,
    )

    # 모든 progress 값은 [0,1] 범위.
    for sp, rp in track:
        assert 0.0 <= sp <= 1.0
        assert 0.0 <= rp <= 1.0

    # 단조 비감소.
    sender_seq = [sp for sp, _ in track]
    receiver_seq = [rp for _, rp in track]
    assert all(
        b_ >= a_ for a_, b_ in zip(sender_seq, sender_seq[1:])
    ), f"sender progress not monotonic: {sender_seq}"
    assert all(
        b_ >= a_ for a_, b_ in zip(receiver_seq, receiver_seq[1:])
    ), f"receiver progress not monotonic: {receiver_seq}"

    # 완료 시 전체 도달.
    assert sender.progress == pytest.approx(1.0)
    assert receiver.progress == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 6. 결정성: 동일 seed → 동일 수신 결과
# --------------------------------------------------------------------------- #


def test_deterministic_replay_same_seed() -> None:
    """동일 seed(손실 채널)로 두 번 돌리면 수신 파일·해시·완료 시각이 동일하다."""

    def _run():
        a, b, clock = _make_session_pair(loss=0.2, seed=3)
        _establish(a, b, clock)
        data = bytes(range(256)) * 6
        sender = FileSender(a, clock, name="det.bin", data=data, chunk_size=96)
        receiver = FileReceiver(b, clock)
        sender.start()
        _drive_transfer(
            sender,
            receiver,
            clock,
            lambda: sender.is_complete and receiver.is_complete,
        )
        return receiver.file_bytes, sha256_hex(receiver.file_bytes), clock.now()

    bytes1, sha1, t1 = _run()
    bytes2, sha2, t2 = _run()
    assert bytes1 == bytes2
    assert sha1 == sha2
    assert t1 == t2


# --------------------------------------------------------------------------- #
# 7. (선택) ImageLoopbackChannel(QR) 위 작은 파일 1건
# --------------------------------------------------------------------------- #


def test_qr_image_channel_small_file() -> None:
    """QR 이미지 채널(M5 광학 코덱 경로) 위에서도 작은 파일이 무손실·무결 전달."""
    pytest.importorskip("segno")
    pytest.importorskip("cv2")
    pytest.importorskip("numpy")

    from photontcp.channel.image_loopback import ImageLoopbackChannel

    ch_a, ch_b = ImageLoopbackChannel.pair(seed=0, loss=0.0)
    clock = ManualClock()
    a = Session(
        ch_a,
        clock,
        is_initiator=True,
        session_id=1,
        isn=1000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=GENEROUS_IDLE_TIMEOUT,
        arq_window_size=8,
        arq_max_payload=64,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    b = Session(
        ch_b,
        clock,
        is_initiator=False,
        session_id=1,
        isn=5000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=GENEROUS_IDLE_TIMEOUT,
        arq_window_size=8,
        arq_max_payload=64,
        rto=RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0),
    )
    _establish(a, b, clock)

    data = b"hello-over-qr-" + bytes(range(64))  # 작은 데이터.
    sender = FileSender(a, clock, name="qr.bin", data=data, chunk_size=32)
    receiver = FileReceiver(b, clock)

    sender.start()
    _drive_transfer(
        sender,
        receiver,
        clock,
        lambda: sender.is_complete and receiver.is_complete,
    )

    assert sender.is_complete and receiver.is_complete
    assert receiver.file_bytes == data
    assert receiver.verified is True
    assert sha256_hex(receiver.file_bytes) == sha256_hex(data)


# --------------------------------------------------------------------------- #
# 부가: 기본 stream_id 가 FILE_STREAM_ID 임을 가벼이 확인(양측 합의 전제).
# --------------------------------------------------------------------------- #


def test_default_stream_id_agreement() -> None:
    """FileSender/FileReceiver 의 기본 stream_id 가 FILE_STREAM_ID 로 일치한다."""
    a, b, clock = _make_session_pair(loss=0.0, seed=0)
    sender = FileSender(a, clock, name="x", data=b"x")
    receiver = FileReceiver(b, clock)
    assert sender.stream_id == FILE_STREAM_ID
    assert receiver.stream_id == FILE_STREAM_ID
