# M3 완료보고서 (impl)

## 개요

마일스톤 M3(로드맵 2단계 — 신뢰성 ARQ)의 9개 태스크(M3-T01~T09)를 전부 구현했다.
32비트 wraparound-safe 시퀀스 산술, 적응형 RTO/RTT 추정기, Selective Repeat ARQ 엔진(송신 슬라이딩
윈도우·선택 재전송·수신 재정렬·중복제거·누적 ACK+선택 NACK), M2 제어 패킷 재전송 + 수립 타임아웃,
그리고 ARQ 데이터 경로의 Session 통합을 완성했다. 손실·중복·순서뒤바뀜 `LoopbackChannel` + 가상
클럭에서 양 피어가 세션을 수립하고 임의 바이트열을 무손실·순서대로 주고받은 뒤 종료하는 흐름이
결정적으로 검증된다.

확정 설계 결정 4건을 모두 반영: **SR ARQ / 누적 ACK + 선택 NACK / 32비트 wraparound-safe 시퀀스 /
DATA + 제어 모두 범위**. M1 리뷰 권장 ①(seq 범위 mod 2³²)과 M2 리뷰 권장 1(제어 재전송·수립 타임아웃)을
해소했다. 의존성 위상 순서로 진행하고 레벨별 독립 태스크는 병렬 디스패치했다:
L0=T01·T02, L1=T03·T04, L2=T05·T06·T07, L3=T08·T09 (각 레벨 파일 비겹침).

## 태스크별 수행 내용

- **M3-T01** — 32비트 시리얼 산술(`reliability/serial.py`). `SEQ_MOD`, `seq_add`, RFC1982 류 `seq_lt/leq/gt/geq`(반거리 2³¹은 양방향 False), `seq_diff`(부호 순환 거리). 패키지 placeholder `__init__.py` 생성.
- **M3-T02** — 적응형 RTO(`reliability/rto.py`). Jacobson/Karels SRTT/RTTVAR(α=1/8, β=1/4), `rto()=clamp(srtt+4·rttvar,[min,max])`, `on_timeout` 지수 백오프, 음수 rtt ValueError.
- **M3-T03** — SR ARQ 엔진(`reliability/arq.py`, 패키지 re-export). `ArqEndpoint`(송신 윈도우+미ACK 맵+보류 큐, 수신 재정렬·dedup), `send`/`on_packet`(DATA·ACK·NACK)/`on_tick`(RTO 재전송), Karn 규칙 RTT 샘플, 흐름제어(상대 window 반영). NACK는 최저 누락 seq 1개를 `ack` 필드로 표현. `recv_isn` 주입형.
- **M3-T04** — 제어 재전송 + 수립 타임아웃(`session/state_machine.py`, `session/states.py`). 미ACK 제어 패킷(SYN/SYN_ACK/FIN) RTO 재전송, 재시도 한도 초과 시 `CONNECT_FAILED`(수립)/`TIMED_OUT`(종료) + CLOSED. `SessionEvent.CONNECT_FAILED` 추가. M2 무손실 동작 회귀 없이 유지(기본값 호환).
- **M3-T05** — ARQ 데이터 경로 Session 통합(`session/session.py`). 내부 `ArqEndpoint`(독립 시퀀스 공간 send_isn=0/recv_isn=0), `send(data)`(ESTABLISHED 한정)/`recv()->list[bytes]`, pump 라우팅(제어→상태머신 / DATA·NACK→ARQ / ACK는 SYN_RCVD면 상태머신·그 외 ARQ), 양 on_tick 평가. `session_id` 접근자 추가. 통합 중 발견한 버그(응답자 ARQ가 협상 전 placeholder session_id로 DATA 드롭)를 session.py 내 `_sync_arq_session_id()`로 해결.
- **M3-T06** — ARQ 엔진 테스트(`tests/test_arq.py`, 8건): 무손실/손실 RTO 재전송/재정렬/중복 dedup/NACK 복구/윈도우 한도/RTT·Karn.
- **M3-T07** — serial·RTO 단위 테스트(`tests/test_serial_rto.py`, 19건): 랩 경계·a==b·`seq_diff`, RTO 초기화·수렴·클램프·백오프·ValueError.
- **M3-T08** — 신뢰성 세션 통합 테스트(`tests/test_session_reliable.py`): 제어 손실 견딤(핸드셰이크·종료), 수립 타임아웃(침묵 피어/loss=1.0), 손실 채널 신뢰 데이터 전송(단/양방향). + 메인이 추가한 최종-ACK-손실 회귀 테스트.
- **M3-T09** — 손실 채널 신뢰 전송 예제(`examples/reliable_loopback.py`). loss=30% seed=23에서 핸드셰이크(4R)→740B 전송(20R, MATCH)→종료(7R, 양쪽 CLOSED).

## 메인 통합 중 수정 (T08이 표면화한 소스 결함)

T08이 **핸드셰이크 최종 ACK 손실 시 응답자 복구 불가** 결함을 발견했다(seed 선택으로 우회만 한 상태):
개시자의 마지막 ACK가 손실되면 개시자는 ESTABLISHED가 되지만, 재전송돼 오는 SYN_ACK에 무응답이라
ACK를 다시 보내지 않아 응답자가 SYN_ACK 재전송 한도 후 `CONNECT_FAILED`로 죽고 비대칭 상태가 된다.
이는 완료 기준 5(손실 채널 핸드셰이크 수립)를 정면으로 깨므로, 메인이 `state_machine.py`를 수정했다:

- **ESTABLISHED 상태에서 재전송된 SYN_ACK 수신 시 ACK를 재전송**(표준 TCP 동작, 상태 변화·중복 이벤트 없음).

직접 시나리오 검증(최종 ACK 드롭 → 둘 다 ESTABLISHED 복구) + 결정적 회귀 테스트(`test_final_ack_loss_recovers`)
추가, T08 파일의 stale "미수정 결함" docstring을 "수정됨"으로 정정했다.

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/reliability/__init__.py`, `photontcp/reliability/serial.py`, `photontcp/reliability/rto.py`, `photontcp/reliability/arq.py`, `tests/test_arq.py`, `tests/test_serial_rto.py`, `tests/test_session_reliable.py`, `examples/reliable_loopback.py` |
| 수정 | `photontcp/session/state_machine.py`(제어 재전송 + 최종 ACK 손실 복구), `photontcp/session/states.py`(`CONNECT_FAILED` 등), `photontcp/session/session.py`(ARQ 통합) |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest -q` → **72 passed in 2.69s** (M1 13 + M2 17 + M3 42, M1·M2 회귀 없음).
- 예제: `echo_loopback.py` OK, `session_loopback.py` OK, `reliable_loopback.py` → loss=30% 링크에서 740B MATCH, 양쪽 CLOSED.
- 완료 기준 1~9 충족: 손실/중복/순서 복구, wraparound-safe 시퀀스, 적응형 RTO, 윈도우/흐름제어, 손실 채널 핸드셰이크·종료(최종 ACK 손실 포함) + 수립 타임아웃, 엔드투엔드 신뢰 전송, 결정성, 예제, 회귀 없음.

## 미해결·후속 메모

1. **NACK 표현이 최저 누락 seq 1개** — 구멍이 여러 개여도 라운드당 1개씩 복구(결국 복구되나 비효율). 다중 블록 SACK 유사 표현(payload 인코딩)은 후속 최적화 여지.
2. **종료 시 FIN_ACK 손실 → TIMED_OUT 경로로 CLOSED** — 예제에서 관측됨. 양쪽 CLOSED엔 도달하나 이벤트가 `CLOSED`가 아닌 `TIMED_OUT`일 수 있음. graceful 종료 이벤트 정합성은 리뷰/후속에서 검토.
3. **`Session`이 `control_rto`/`max_control_retries` 미노출** — 통합 테스트가 제어 재시도 예산을 조정 못 함(현재는 불필요했으나 튜닝 시 노출 고려).
4. **반이중(half-close)·스트림 다중화 미지원** — M4(스트림 MUX + 채팅)에서 다룸.
5. **여전히 비범위(의식적 이월)** — 공유 RNG·스레드 안전성(단일 스레드 모델), `LoopbackChannel` 전달 지연(latency/jitter; RTT는 가상 클럭 전진으로 검증), 패킷 간 FEC(7단계).
