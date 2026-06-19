"""M3-T06 — ARQ 엔진 테스트.

두 :class:`ArqEndpoint` (A=송신, B=수신) 를 채널 없이 손으로 펌프하면서
Selective Repeat ARQ 의 핵심 동작을 검증한다.

* 시간(``now``) 은 테스트가 직접 증가시키는 float 가상 시계다.
* 손실  = "B 에 전달 생략"
* 순서뒤바뀜 = "B 에 전달하는 순서 변경"
* 중복  = "같은 패킷을 B 에 두 번 전달"

A/B 는 ``send_isn=0, recv_isn=0`` 으로 서로 맞추고 동일 ``session_id`` 를
쓰므로, A 가 보내는 DATA(seq 0,1,2,...) 를 B 가 그대로 기대한다.
"""

from __future__ import annotations

import pytest

from photontcp.packet.types import PacketType
from photontcp.reliability.arq import ArqEndpoint, ArqOutput
from photontcp.reliability.rto import RtoEstimator


SESSION_ID = 7


def _make_pair(*, window_size: int = 8, max_payload: int = 200):
    """동일 세션·서로 맞춘 ISN 을 가진 (A 송신, B 수신) 엔드포인트 쌍을 만든다."""
    rto_a = RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0)
    rto_b = RtoEstimator(initial_rto=1.0, min_rto=0.2, max_rto=60.0)
    a = ArqEndpoint(
        session_id=SESSION_ID,
        send_isn=0,
        recv_isn=0,
        window_size=window_size,
        rto=rto_a,
        max_payload=max_payload,
    )
    b = ArqEndpoint(
        session_id=SESSION_ID,
        send_isn=0,
        recv_isn=0,
        window_size=window_size,
        rto=rto_b,
        max_payload=max_payload,
    )
    return a, b, rto_a, rto_b


def _data_packets(out: ArqOutput):
    return [p for p in out.packets if p.type == PacketType.DATA]


def _ack_packets(out: ArqOutput):
    return [p for p in out.packets if p.type == PacketType.ACK]


def _nack_packets(out: ArqOutput):
    return [p for p in out.packets if p.type == PacketType.NACK]


# ----------------------------------------------------------------------
# 1. 무손실: 다중 청크 → 원본 정확히 복원, 순서 보존
# ----------------------------------------------------------------------
def test_lossless_multi_chunk_in_order_delivery():
    a, b, _, _ = _make_pair(window_size=8, max_payload=10)
    # max_payload(10) 보다 훨씬 큰 데이터 → 여러 DATA 청크 발생.
    original = bytes(range(256))[:55]  # 55바이트 → 6청크(10,10,10,10,10,5)
    now = 0.0

    send_out = a.send(original, now)
    data_pkts = _data_packets(send_out)
    assert len(data_pkts) == 6, "55바이트/10 = 6청크"
    # seq 는 0..5 로 순서대로.
    assert [p.seq for p in data_pkts] == [0, 1, 2, 3, 4, 5]

    delivered = bytearray()
    for pkt in data_pkts:
        now += 0.1
        b_out = b.on_packet(pkt, now)
        for chunk in b_out.delivered:
            delivered.extend(chunk)
        # B 가 낸 ACK 를 A 로 되먹임.
        for ack in b_out.packets:
            now += 0.1
            a.on_packet(ack, now)

    assert bytes(delivered) == original
    # 모두 ACK 되어 outstanding 이 비어야 한다.
    assert a.unacked_count == 0
    assert a.pending_count == 0


# ----------------------------------------------------------------------
# 2. 손실 재전송: DATA 한 개 누락 → RTO 경과 → on_tick 재전송 → 전부 복원
# ----------------------------------------------------------------------
def test_loss_then_retransmit_via_tick_recovers():
    a, b, _, _ = _make_pair(window_size=8, max_payload=10)
    original = b"".join(bytes([i]) * 10 for i in range(4))  # 40바이트 → 4청크
    now = 0.0

    send_out = a.send(original, now)
    data_pkts = _data_packets(send_out)
    assert len(data_pkts) == 4

    delivered = bytearray()

    # seq 1 패킷을 "손실" 시킨다 (B 에 전달하지 않음). 나머지는 전달.
    lost_seq = 1
    for pkt in data_pkts:
        if pkt.seq == lost_seq:
            continue  # 손실
        b_out = b.on_packet(pkt, now)
        for chunk in b_out.delivered:
            delivered.extend(chunk)
        # 이 시점에서 B 의 ACK 를 A 에 전달(누적 ACK 라 구멍 앞까지만 확인).
        for p in b_out.packets:
            a.on_packet(p, now)

    # seq 0 만 in-order 로 delivered, seq 2/3 은 reorder 버퍼에 보관.
    assert bytes(delivered) == original[0:10]
    # seq 1 은 아직 outstanding 이어야 한다.
    assert a.unacked_count >= 1

    # 시간을 RTO 이상 전진시킨 뒤 tick → seq 1 재전송.
    now += 5.0
    tick_out = a.on_tick(now)
    retx = _data_packets(tick_out)
    assert any(p.seq == lost_seq for p in retx), "손실된 seq 1 이 재전송돼야 함"

    # 재전송 패킷을 B 로 전달 → seq 1 채워지면서 1,2,3 연쇄 delivered.
    for pkt in retx:
        b_out = b.on_packet(pkt, now)
        for chunk in b_out.delivered:
            delivered.extend(chunk)
        for p in b_out.packets:
            a.on_packet(p, now)

    assert bytes(delivered) == original
    assert a.unacked_count == 0


# ----------------------------------------------------------------------
# 3. 순서뒤바뀜: DATA 를 뒤섞어 전달 → 재정렬되어 원본 순서로 delivered
# ----------------------------------------------------------------------
def test_out_of_order_delivery_is_reordered():
    a, b, _, _ = _make_pair(window_size=8, max_payload=10)
    original = b"".join(bytes([0x41 + i]) * 10 for i in range(5))  # 50바이트 → 5청크
    now = 0.0

    send_out = a.send(original, now)
    data_pkts = _data_packets(send_out)
    assert len(data_pkts) == 5

    # 역순으로 전달 (4,3,2,1,0).
    shuffled = list(reversed(data_pkts))
    delivered = bytearray()
    for pkt in shuffled:
        now += 0.1
        b_out = b.on_packet(pkt, now)
        for chunk in b_out.delivered:
            delivered.extend(chunk)
        for p in b_out.packets:
            a.on_packet(p, now)

    # 마지막 seq 0 이 도착하는 순간 0,1,2,3,4 전부 순서대로 방출.
    assert bytes(delivered) == original


# ----------------------------------------------------------------------
# 4. 중복: 같은 DATA 를 두 번 → 정확히 1회만 delivered (dedup)
# ----------------------------------------------------------------------
def test_duplicate_data_delivered_once():
    a, b, _, _ = _make_pair(window_size=8, max_payload=10)
    original = b"X" * 10 + b"Y" * 10  # 2청크
    now = 0.0

    data_pkts = _data_packets(a.send(original, now))
    assert len(data_pkts) == 2

    delivered = bytearray()

    # seq 0 을 두 번 전달.
    out1 = b.on_packet(data_pkts[0], now)
    out2 = b.on_packet(data_pkts[0], now)  # 중복
    for o in (out1, out2):
        for chunk in o.delivered:
            delivered.extend(chunk)

    # 중복은 deliver 되지 않아야 한다.
    assert out1.delivered == [b"X" * 10]
    assert out2.delivered == []  # 과거 seq 중복 → discard
    # 중복도 누적 ACK 는 응답한다.
    assert _ack_packets(out2)

    # 나머지(seq 1) 도 두 번 전달.
    out3 = b.on_packet(data_pkts[1], now)
    out4 = b.on_packet(data_pkts[1], now)  # 중복
    for o in (out3, out4):
        for chunk in o.delivered:
            delivered.extend(chunk)
    assert out3.delivered == [b"Y" * 10]
    assert out4.delivered == []

    assert bytes(delivered) == original


# ----------------------------------------------------------------------
# 5. NACK 복구: 구멍 → B 가 NACK → A 가 선택 재전송 → 복구
# ----------------------------------------------------------------------
def test_nack_triggers_selective_retransmit():
    a, b, _, _ = _make_pair(window_size=8, max_payload=10)
    original = b"".join(bytes([0x30 + i]) * 10 for i in range(3))  # 3청크
    now = 0.0

    data_pkts = _data_packets(a.send(original, now))
    assert len(data_pkts) == 3

    delivered = bytearray()

    # seq 0 은 전달하지 않고(구멍), seq 1 을 먼저 전달 → B 가 seq 0 NACK.
    b_out = b.on_packet(data_pkts[1], now)
    nacks = _nack_packets(b_out)
    assert nacks, "구멍 앞 seq 에 대한 NACK 가 나와야 함"
    assert nacks[0].ack == 0, "lowest missing seq=0 을 NACK"
    # seq 1 은 reorder 에 보관, 아직 deliver 안 됨.
    assert b_out.delivered == []

    # NACK 를 A 에 전달 → A 가 seq 0 선택 재전송.
    a_out = a.on_packet(nacks[0], now)
    retx = _data_packets(a_out)
    assert [p.seq for p in retx] == [0], "NACK 대상 seq 0 만 선택 재전송"

    # 재전송 seq 0 을 B 에 전달 → 0,1 연쇄 deliver.
    for pkt in retx:
        rb = b.on_packet(pkt, now)
        for chunk in rb.delivered:
            delivered.extend(chunk)
        for p in rb.packets:
            a.on_packet(p, now)

    # 마지막 seq 2 전달.
    rb = b.on_packet(data_pkts[2], now)
    for chunk in rb.delivered:
        delivered.extend(chunk)
    for p in rb.packets:
        a.on_packet(p, now)

    assert bytes(delivered) == original


# ----------------------------------------------------------------------
# 6. 윈도우: unacked_count 가 window_size 를 초과하지 않고, ACK 시 보류분 방출
# ----------------------------------------------------------------------
def test_window_limits_outstanding_and_flushes_on_ack():
    window = 3
    a, b, _, _ = _make_pair(window_size=window, max_payload=10)
    # 윈도우(3)보다 많은 6청크.
    original = b"".join(bytes([0x61 + i]) * 10 for i in range(6))
    now = 0.0

    send_out = a.send(original, now)
    data_pkts = _data_packets(send_out)
    # 윈도우 한도까지만 즉시 방출, 나머지는 pending.
    assert len(data_pkts) == window
    assert a.unacked_count == window
    assert a.unacked_count <= window
    assert a.pending_count == 6 - window
    assert [p.seq for p in data_pkts] == [0, 1, 2]

    delivered = bytearray()
    flushed_seqs: list[int] = []

    # 단일 큐로 결정적 펌프: A 가 낸 DATA 를 도착 순서대로 B 에 전달하고,
    # B 가 낸 ACK/NACK 를 A 에 되먹인다. ACK 로 윈도우가 열리면 A 가 새
    # 보류분 DATA 를 방출하며, 그 패킷도 같은 큐에 넣어 끝까지 전달한다.
    # in-order 로 전달되므로 reorder 버퍼가 비어 deadlock 없이 진행된다.
    queue: list = list(data_pkts)  # A -> B 로 전달할 DATA
    initial_seqs = {p.seq for p in data_pkts}

    while queue:
        pkt = queue.pop(0)
        # 불변식: 어느 순간에도 미ACK 수가 윈도우를 넘지 않는다.
        assert a.unacked_count <= window
        b_out = b.on_packet(pkt, now)
        for chunk in b_out.delivered:
            delivered.extend(chunk)
        for resp in b_out.packets:
            a_out = a.on_packet(resp, now)
            for newp in _data_packets(a_out):
                # ACK 로 윈도우가 열려 방출된 보류분.
                if newp.seq not in initial_seqs:
                    flushed_seqs.append(newp.seq)
                queue.append(newp)
        # 방출 직후에도 불변식 유지.
        assert a.unacked_count <= window

    # 보류분(seq 3,4,5)이 ACK 에 의해 방출되었다.
    assert set(flushed_seqs) == {3, 4, 5}
    assert a.unacked_count == 0
    assert a.pending_count == 0
    assert bytes(delivered) == original


# ----------------------------------------------------------------------
# 7. RTT/RTO: 재전송 없이 ACK 받으면 RTT 샘플이 RtoEstimator 에 반영됨 (결정적)
# ----------------------------------------------------------------------
def test_rtt_sample_updates_rto_estimator():
    a, b, rto_a, _ = _make_pair(window_size=8, max_payload=10)
    original = b"Z" * 10  # 1청크
    t_send = 0.0

    data_pkts = _data_packets(a.send(original, t_send))
    assert len(data_pkts) == 1

    # 샘플 전: srtt 는 아직 None, rto 는 초기값.
    assert rto_a.srtt is None
    assert rto_a.rttvar is None
    assert rto_a.rto() == pytest.approx(1.0)

    # B 가 받아 ACK 생성.
    b_out = b.on_packet(data_pkts[0], t_send)
    acks = _ack_packets(b_out)
    assert acks

    # RTT = 0.5초 경과 후 ACK 가 A 에 도착.
    t_ack = t_send + 0.5
    a.on_packet(acks[0], t_ack)

    # 재전송 없이 ACK 받았으므로 Karn 조건 통과 → 샘플 반영.
    assert rto_a.srtt == pytest.approx(0.5), "첫 샘플 srtt = rtt"
    assert rto_a.rttvar == pytest.approx(0.25), "첫 샘플 rttvar = rtt/2"
    # rto = clamp(srtt + 4*rttvar, min, max) = 0.5 + 1.0 = 1.5.
    assert rto_a.rto() == pytest.approx(1.5)
    # outstanding 비워짐.
    assert a.unacked_count == 0


def test_retransmitted_packet_yields_no_rtt_sample_karn():
    """Karn: 재전송된 패킷에 대한 ACK 는 RTT 샘플을 만들지 않는다."""
    a, b, rto_a, _ = _make_pair(window_size=8, max_payload=10)
    data_pkts = _data_packets(a.send(b"Q" * 10, 0.0))
    assert len(data_pkts) == 1

    # 첫 DATA 를 손실시키고, RTO 경과 후 tick 으로 재전송(retx_count=1).
    now = 5.0
    retx = _data_packets(a.on_tick(now))
    assert [p.seq for p in retx] == [0]

    # 재전송분을 B 에 전달해 ACK 받기.
    b_out = b.on_packet(retx[0], now)
    acks = _ack_packets(b_out)
    a.on_packet(acks[0], now + 0.3)

    # Karn: retx 된 패킷이라 RTT 샘플 미반영 → srtt 여전히 None.
    assert rto_a.srtt is None
    assert a.unacked_count == 0
