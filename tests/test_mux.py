"""M4-T05 — StreamMux 테스트.

두 :class:`StreamMux` (개시자/응답자, 동일 ``session_id``) 를 채널 없이 손으로
펌프하면서 스트림 다중화의 핵심 동작을 검증한다.

* 시간(``now``) 은 테스트가 직접 증가시키는 float 가상 시계다.
* 손실  = "상대 ``on_packet`` 에 전달 생략"
* 순서뒤바뀜 = "상대에 전달하는 순서 변경"

각 스트림의 endpoint 는 ``send_isn=0, recv_isn=0`` 으로 시작하고 양쪽이 동일
``session_id`` 를 쓰므로, 한쪽이 어떤 stream_id 로 보낸 DATA(seq 0,1,2,...) 를
상대가 그대로 기대한다. ``StreamMux`` 는 stream_id 별로 독립 endpoint 를 가지므로
한 스트림의 구멍이 다른 스트림 인도를 막지 않아야 한다(HOL 블로킹 없음).
"""

from __future__ import annotations

from photontcp.packet.types import PacketType
from photontcp.reliability.rto import RtoEstimator
from photontcp.stream import (
    CONTROL_STREAM_ID,
    DEFAULT_STREAM_ID,
    MuxOutput,
    StreamMux,
)


SESSION_ID = 11


def _fast_rto() -> RtoEstimator:
    """짧은 RTO 로 on_tick 재전송을 결정적으로 트리거하기 쉽게 만든다."""
    return RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0)


def _make_pair(*, window_size: int = 8, max_payload: int = 200):
    """동일 세션의 (개시자, 응답자) StreamMux 쌍을 만든다."""
    initiator = StreamMux(
        session_id=SESSION_ID,
        is_initiator=True,
        window_size=window_size,
        max_payload=max_payload,
        rto_factory=_fast_rto,
    )
    responder = StreamMux(
        session_id=SESSION_ID,
        is_initiator=False,
        window_size=window_size,
        max_payload=max_payload,
        rto_factory=_fast_rto,
    )
    return initiator, responder


def _data_packets(out: MuxOutput):
    return [p for p in out.packets if p.type == PacketType.DATA]


def _deliver(dst: StreamMux, packets, now: float) -> MuxOutput:
    """패킷 리스트를 한 개씩 *dst* 에 전달하고 산출 + delivered 를 합산한다."""
    merged = MuxOutput()
    for pkt in packets:
        out = dst.on_packet(pkt, now)
        merged.packets.extend(out.packets)
        for sid, chunks in out.delivered.items():
            merged.delivered.setdefault(sid, []).extend(chunks)
    return merged


# ---------------------------------------------------------------------------
# 1. 스트림 격리: 여러 스트림이 각자 키로 분리되어 순서대로 인도된다.
# ---------------------------------------------------------------------------
def test_stream_isolation_delivers_per_stream_in_order():
    a, b = _make_pair(max_payload=10)
    now = 0.0

    data1 = b"stream-one-payload-data"  # > max_payload -> 여러 청크
    data3 = b"third-stream-bytes-here"

    out1 = a.send(1, data1, now)
    out3 = a.send(3, data3, now)

    # 송신측은 두 스트림 모두 DATA 를 낸다(stream_id 가 다름).
    pkts1 = _data_packets(out1)
    pkts3 = _data_packets(out3)
    assert pkts1 and pkts3
    assert all(p.stream_id == 1 for p in pkts1)
    assert all(p.stream_id == 3 for p in pkts3)

    delivered = _deliver(b, pkts1 + pkts3, now)

    # delivered 가 stream_id 키로 분리되어야 한다.
    assert set(delivered.delivered) == {1, 3}
    assert b"".join(delivered.delivered[1]) == data1
    assert b"".join(delivered.delivered[3]) == data3

    # 응답자측에 두 스트림 endpoint 가 생겨야 한다.
    assert b.has_stream(1)
    assert b.has_stream(3)


# ---------------------------------------------------------------------------
# 2. HOL 없음: 스트림 1의 첫 청크 손실에도 스트림 3은 정상 인도되고,
#    이후 스트림 1의 누락분이 복구되면 스트림 1도 순서대로 완성된다.
# ---------------------------------------------------------------------------
def test_no_head_of_line_blocking_across_streams():
    a, b = _make_pair(max_payload=10)
    now = 0.0

    data1 = b"AAAAAAAAAABBBBBBBBBBCCCCC"  # 3 청크 (10/10/5)
    data3 = b"333-stream-three-data"

    out1 = a.send(1, data1, now)
    out3 = a.send(3, data3, now)

    pkts1 = _data_packets(out1)
    pkts3 = _data_packets(out3)
    assert len(pkts1) >= 2

    # 스트림 1의 첫 청크(base, seq 0)를 일부러 전달하지 않는다(손실).
    base = pkts1[0]
    assert base.seq == 0
    rest1 = pkts1[1:]

    # 스트림 1의 나머지(미래 seq)와 스트림 3 전부를 전달.
    delivered = _deliver(b, rest1 + pkts3, now)

    # 스트림 1은 base 가 없어 아직 아무것도 인도하지 못한다(HOL 가능성).
    assert 1 not in delivered.delivered
    # 하지만 스트림 3은 막힘 없이 정상 인도된다(HOL 없음).
    assert b"".join(delivered.delivered.get(3, [])) == data3

    # base 누락분을 RTO 후 on_tick 재전송으로 복구한다.
    now = 2.0  # initial_rto=1.0 초과
    retx = a.on_tick(now)
    retx_data = _data_packets(retx)
    # 미확인(base 포함) 청크들이 재전송된다.
    retx_base = [p for p in retx_data if p.stream_id == 1 and p.seq == 0]
    assert retx_base, "스트림 1의 base 청크가 재전송되어야 한다"

    delivered2 = _deliver(b, retx_data, now)

    # base 가 도착하면 버퍼링됐던 후속 청크까지 한꺼번에 순서대로 인도된다.
    assert b"".join(delivered2.delivered.get(1, [])) == data1


# ---------------------------------------------------------------------------
# 3. implicit open: open_stream 없이 임의 stream_id 로 보낸 DATA 를
#    수신측이 자동으로 새 endpoint 를 만들어 인도한다.
# ---------------------------------------------------------------------------
def test_implicit_open_on_inbound_data():
    a, b = _make_pair()
    now = 0.0

    # 송신측도 open_stream 없이 임의 id 로 보낸다(송신측 implicit open).
    sid = 7
    assert not a.has_stream(sid)
    out = a.send(sid, b"hello-implicit", now)
    assert a.has_stream(sid)  # 송신으로 endpoint 자동 생성

    pkts = _data_packets(out)
    assert pkts and all(p.stream_id == sid for p in pkts)

    # 수신측은 그 stream_id 를 모르는 상태.
    assert not b.has_stream(sid)
    delivered = _deliver(b, pkts, now)

    # 수신 DATA 만으로 수신측 endpoint 가 생성되고 인도된다.
    assert b.has_stream(sid)
    assert sid in b.stream_ids()
    assert b"".join(delivered.delivered[sid]) == b"hello-implicit"


# ---------------------------------------------------------------------------
# 4. open_stream 패리티: 개시자=홀수(3,5,...), 응답자=짝수(2,4,...), 충돌 없음.
# ---------------------------------------------------------------------------
def test_open_stream_parity_no_collision():
    a, b = _make_pair()

    a_ids = [a.open_stream() for _ in range(4)]
    b_ids = [b.open_stream() for _ in range(4)]

    # 개시자는 홀수(>=3, 기본 스트림 1 제외).
    assert a_ids == [3, 5, 7, 9]
    assert all(i % 2 == 1 and i >= 3 for i in a_ids)

    # 응답자는 짝수(>=2).
    assert b_ids == [2, 4, 6, 8]
    assert all(i % 2 == 0 and i >= 2 for i in b_ids)

    # 두 피어가 발급한 id 집합은 서로 겹치지 않는다.
    assert set(a_ids).isdisjoint(set(b_ids))

    # 기본 공유 스트림(1)은 open_stream 이 절대 반환하지 않는다.
    assert DEFAULT_STREAM_ID not in a_ids
    assert DEFAULT_STREAM_ID not in b_ids

    # open_stream 은 endpoint 를 즉시 생성한다.
    for i in a_ids:
        assert a.has_stream(i)
    for i in b_ids:
        assert b.has_stream(i)


def test_open_stream_skips_existing_ids():
    a, _ = _make_pair()
    # 임의 송신으로 id 5 endpoint 를 미리 점유.
    a.send(5, b"x", 0.0)
    assert a.has_stream(5)

    allocated = [a.open_stream() for _ in range(3)]
    # 이미 존재하는 5 는 건너뛰어 충돌 없이 할당된다.
    assert 5 not in allocated
    assert allocated == [3, 7, 9]
    assert len(set(allocated)) == len(allocated)


# ---------------------------------------------------------------------------
# 5. 제어 스트림(0): on_packet 에 줘도 무시(빈 delivered, endpoint 미생성).
# ---------------------------------------------------------------------------
def test_control_stream_packet_is_ignored():
    _, b = _make_pair()
    now = 0.0

    # stream_id=0 의 DATA 패킷을 직접 구성해 전달.
    from photontcp.packet.header import Packet

    ctrl = Packet(
        type=PacketType.DATA,
        session_id=SESSION_ID,
        stream_id=CONTROL_STREAM_ID,
        seq=0,
        ack=0,
        window=8,
        payload=b"control-bytes",
    )

    out = b.on_packet(ctrl, now)

    # 무시: 빈 delivered, 산출 패킷 없음, endpoint 미생성.
    assert out.delivered == {}
    assert out.packets == []
    assert not b.has_stream(CONTROL_STREAM_ID)
    assert b.stream_ids() == []
