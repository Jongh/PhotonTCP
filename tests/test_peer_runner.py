"""``run_peer`` 헤드리스 드라이버 테스트 (M11-T09, 완료 기준 5).

``photontcp.optical.peer.run_peer`` 가 M11-T02 에서 얻은 성질을 방전한다:

* :meth:`OpticalChannel.pair` 위에서 두 role 을 스레드로 구동하면
  ``established`` · ``messages_match`` · ``closed`` · ``passed`` 가 **모두 참**
  이다 — 하드웨어 없이 인메모리로 성립한다(실 QR 인코드/디코드는 그대로 탄다).
* ``on_event`` 가 내는 ``kind`` 는 전부 :data:`EVENT_KINDS` 안에 있고,
  ``"done"`` 이 **마지막**이며 그 detail 에 최종 :class:`PeerResult` 가 실린다.
* 잘못된 ``role`` 은 ``ValueError`` 다.

**타이밍 규칙(M10 회고)**: 이 모듈은 절대 시간 상한을 박지 않고, 두 실측값을
서로 비교하는 차등 단언도 하지 않는다. 시간에 대해서는 *단측 경계*(양수 경과,
예산 소진 없음)만 단언한다. ``hold`` 는 실측상 안전한 하한대(0.03s)를 쓴다 —
``hold=0.0`` 은 채널의 캡처 스레드에 틈을 주지 않아 실패한다.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")

from photontcp.optical.channel import OpticalChannel  # noqa: E402
from photontcp.optical.peer import (  # noqa: E402
    DEFAULT_MESSAGES,
    EVENT_KINDS,
    PeerResult,
    run_peer,
)


#: 인메모리 pair 위에서 실측된 안전한 페이싱. 0.02s 로도 왕복이 성립했고
#: (총 0.44s), 0.03s 는 그 위의 여유분이다. 0.0 은 캡처 스레드가 돌 틈이
#: 없어 실패하므로 절대 쓰지 않는다.
FAST_HOLD = 0.03

#: 스레드가 걸려도 스위트가 멈추지 않게 하는 예산. 성공 경로는 이 값의
#: 몇십 분의 일만 쓴다 — 상한 단언용 값이 아니라 안전핀이다.
BUDGET = 30.0


def _drive_pair(
    *,
    hold: float = FAST_HOLD,
    on_event_sender=None,
    on_event_receiver=None,
) -> dict[str, PeerResult]:
    """두 role 을 스레드로 동시에 구동하고 ``{role: PeerResult}`` 를 돌려준다."""
    chan_a, chan_b = OpticalChannel.pair(scale=6)
    results: dict[str, PeerResult] = {}
    errors: dict[str, BaseException] = {}

    def drive(channel, role, on_event) -> None:
        try:
            results[role] = run_peer(
                channel,
                role,
                hold=hold,
                timeout=BUDGET,
                on_event=on_event,
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced in the main thread
            errors[role] = exc

    threads = [
        threading.Thread(
            target=drive, args=(chan_a, "sender", on_event_sender), daemon=True
        ),
        threading.Thread(
            target=drive, args=(chan_b, "receiver", on_event_receiver), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        # 스레드가 끝나지 않는 것 자체가 실패이므로 join 에도 안전핀을 둔다.
        thread.join(BUDGET + 10.0)

    try:
        for thread in threads:
            assert not thread.is_alive(), "run_peer thread did not finish"
        if errors:
            raise AssertionError(f"run_peer raised in a worker thread: {errors}")
    finally:
        # run_peer(close_channel=True) 가 이미 닫지만, 실패 경로에서도 캡처
        # 스레드가 남지 않도록 한 번 더 (close 는 멱등).
        chan_a.close()
        chan_b.close()

    return results


# --------------------------------------------------------------------------- #
# 1. 인메모리 왕복 (완료 기준 5)
# --------------------------------------------------------------------------- #


def test_run_peer_roundtrip_in_memory() -> None:
    """두 role 동시 구동 → 양쪽 established/MATCH/closed/PASS."""
    results = _drive_pair()

    assert set(results) == {"sender", "receiver"}
    for role, result in results.items():
        assert isinstance(result, PeerResult)
        assert result.role == role
        assert result.established, f"{role}: handshake did not complete"
        assert result.messages_match, f"{role}: {result.received!r} != {result.expected!r}"
        assert result.closed, f"{role}: did not reach CLOSED"
        assert result.passed, f"{role}: verdict is not PASS"
        assert not result.timed_out


def test_run_peer_exchanges_the_expected_message_sets() -> None:
    """각 피어가 보낸 집합이 상대가 받은 집합과 순서까지 일치한다."""
    results = _drive_pair()
    sender, receiver = results["sender"], results["receiver"]

    assert sender.sent == list(DEFAULT_MESSAGES[0])
    assert receiver.sent == list(DEFAULT_MESSAGES[1])
    assert receiver.received == sender.sent
    assert sender.received == receiver.sent


def test_run_peer_reports_rounds_and_a_positive_wall_clock() -> None:
    """단측 경계만: 라운드 수는 1 이상, 경과는 양수, 예산 소진 없음."""
    results = _drive_pair()

    for result in results.values():
        # 절대 상한은 박지 않는다 (M10 의 플레이키 교훈). 완료했다는 사실은
        # timed_out=False 가 말하고, 시간은 아래 하한만 본다.
        assert result.wall_seconds > 0.0
        assert not result.timed_out
        assert result.handshake_rounds is not None and result.handshake_rounds >= 1
        assert result.close_rounds is not None and result.close_rounds >= 1


def test_run_peer_accepts_custom_message_sets() -> None:
    """``messages`` 로 준 집합이 그대로 오간다 (기본값에 묶여 있지 않다)."""
    custom = (("alpha",), ("beta",))
    chan_a, chan_b = OpticalChannel.pair(scale=6)
    results: dict[str, PeerResult] = {}

    def drive(channel, role) -> None:
        results[role] = run_peer(
            channel, role, messages=custom, hold=FAST_HOLD, timeout=BUDGET
        )

    threads = [
        threading.Thread(target=drive, args=(chan_a, "sender"), daemon=True),
        threading.Thread(target=drive, args=(chan_b, "receiver"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(BUDGET + 10.0)
    chan_a.close()
    chan_b.close()

    assert results["sender"].sent == ["alpha"]
    assert results["sender"].received == ["beta"]
    assert results["receiver"].sent == ["beta"]
    assert results["receiver"].received == ["alpha"]
    assert results["sender"].passed and results["receiver"].passed


# --------------------------------------------------------------------------- #
# 2. on_event 계약
# --------------------------------------------------------------------------- #


def test_on_event_kinds_are_declared_and_done_is_last() -> None:
    """모든 kind 가 EVENT_KINDS 안이고 'done' 이 마지막·결과를 싣는다."""
    events: dict[str, list[tuple[str, dict]]] = {"sender": [], "receiver": []}

    def collector(role):
        def on_event(kind: str, detail: dict) -> None:
            events[role].append((kind, detail))

        return on_event

    results = _drive_pair(
        on_event_sender=collector("sender"),
        on_event_receiver=collector("receiver"),
    )

    for role, log in events.items():
        assert log, f"{role}: no events were emitted"
        kinds = [kind for kind, _ in log]
        unknown = set(kinds) - set(EVENT_KINDS)
        assert not unknown, f"{role}: undeclared event kinds {unknown}"

        assert kinds[0] == "start"
        assert kinds[-1] == "done", f"{role}: last event is {kinds[-1]!r}, not 'done'"
        assert kinds.count("done") == 1

        final_detail = log[-1][1]
        assert isinstance(final_detail["result"], PeerResult)
        # 'done' 이 싣는 것은 반환된 바로 그 결과 객체다.
        assert final_detail["result"] is results[role]

        # 성공 경로의 진행 이벤트가 모두 나왔고, 실패 이벤트는 없다.
        for expected_kind in ("connect", "handshake", "match", "closing", "closed"):
            assert expected_kind in kinds, f"{role}: missing {expected_kind!r} event"
        assert "error" not in kinds


def test_on_event_message_events_track_the_result_fields() -> None:
    """'sent'/'received' 이벤트가 결과의 sent/received 와 일치한다."""
    log: list[tuple[str, dict]] = []
    results = _drive_pair(on_event_sender=lambda kind, detail: log.append((kind, detail)))
    sender = results["sender"]

    assert [d["text"] for k, d in log if k == "sent"] == sender.sent
    assert [d["text"] for k, d in log if k == "received"] == sender.received


def test_on_event_exceptions_do_not_break_the_drive() -> None:
    """콜백이 던져도 세션은 계속 굴러가고 PASS 한다."""

    def exploding(kind: str, detail: dict) -> None:
        raise RuntimeError("UI callback blew up")

    results = _drive_pair(on_event_sender=exploding)
    assert results["sender"].passed
    assert results["receiver"].passed


# --------------------------------------------------------------------------- #
# 3. 인자 검증
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_role", ["", "SENDER", "initiator", "both", None])
def test_invalid_role_raises_value_error(bad_role) -> None:
    """지원하지 않는 role 은 조용히 넘어가지 않고 ValueError 다."""
    chan_a, chan_b = OpticalChannel.pair(scale=6)
    try:
        with pytest.raises(ValueError):
            run_peer(chan_a, bad_role, hold=FAST_HOLD, timeout=BUDGET)
    finally:
        chan_a.close()
        chan_b.close()


@pytest.mark.parametrize("bad_hold", [0.0, 0, -0.1])
def test_non_positive_hold_raises_value_error(bad_hold) -> None:
    """``hold <= 0`` 은 **반드시 실패하는 세션**이므로 시작 전에 거부한다.

    0 은 채널의 캡처 스레드에 틈을 주지 않아 라운드 캡만 태우고 끝난다. 그대로
    돌리면 사용자에게는 handshake 타임아웃(= 하드웨어 문제처럼 보이는 실패)으로
    나타나므로, 돌지 않고 `ValueError` 를 낸다(M11-review 권장 4).
    """
    chan_a, chan_b = OpticalChannel.pair(scale=6)
    try:
        with pytest.raises(ValueError, match="hold"):
            run_peer(chan_a, "sender", hold=bad_hold, timeout=BUDGET)
    finally:
        chan_a.close()
        chan_b.close()


def test_messages_must_be_a_pair() -> None:
    """``messages`` 가 (sender, receiver) 쌍이 아니면 IndexError 대신 ValueError."""
    chan_a, chan_b = OpticalChannel.pair(scale=6)
    try:
        with pytest.raises(ValueError, match="messages"):
            run_peer(
                chan_a, "sender", messages=(("only one half",),),
                hold=FAST_HOLD, timeout=BUDGET,
            )
    finally:
        chan_a.close()
        chan_b.close()


def test_event_kinds_constant_is_a_stable_superset() -> None:
    """EVENT_KINDS 는 프론트엔드가 switch 할 수 있는 안정 집합이다."""
    assert isinstance(EVENT_KINDS, tuple)
    assert len(set(EVENT_KINDS)) == len(EVENT_KINDS)
    assert EVENT_KINDS[-1] == "done"
    for required in ("start", "handshake", "match", "closed", "error", "done"):
        assert required in EVENT_KINDS
