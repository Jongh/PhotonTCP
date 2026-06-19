# M4 완료보고서 (impl)

## 개요

마일스톤 M4(로드맵 3단계 — 스트림 다중화 + 채팅 앱, "기본 통신 완성")의 8개 태스크(M4-T01~T08)를
전부 구현했다. 스트림별 독립 ARQ 다중화기(`StreamMux`), 길이접두+JSON 채팅 코덱·재조립기, `Session`의
단일 ARQ → `StreamMux` 통합(레거시 API 보존), 그 위의 `ChatSession` 채팅 앱을 완성했다. 손실 있는
`LoopbackChannel` + 가상 클럭에서 두 피어가 세션을 수립하고 **양방향 텍스트 메시지를 순서대로 무손실로
교환**하는 것이 결정적으로 검증된다 — 로드맵의 "기본 통신 완성".

확정 설계 결정 4건을 모두 반영: **스트림별 독립 ARQ / 길이접두+JSON / 암묵적 경량 스트림 / M3 권장
연기**. 부수 효과로 ARQ 데이터 ACK가 stream_id≥1을 달고 오므로 핸드셰이크 ACK(stream_id=0)와 라우팅만으로
구분되어 M3의 상태 기반 ACK 분기가 단순해졌다. 의존성 위상 순서로 진행, 레벨별 병렬 디스패치:
L0=T01·T02, L1=T03·T05·T06, L2=T04, L3=T07·T08 (각 레벨 파일 비겹침).

## 태스크별 수행 내용

- **M4-T01** — `StreamMux`(`stream/mux.py`, 패키지 re-export). `CONTROL_STREAM_ID=0`/`DEFAULT_STREAM_ID=1`, `MuxOutput(packets, delivered: dict[int,list[bytes]])`. stream_id별 lazy `ArqEndpoint`(implicit open), `open_stream`(개시자 홀수3,5…/응답자 짝수2,4…), `send`/`on_packet`/`on_tick`/`set_session_id`. 각 스트림 독립 RTO 추정기(`rto_factory`). 가변 기본인자 회피 위해 `rto_factory=None`→내부 폴백.
- **M4-T02** — 채팅 코덱(`app/codec.py`). `ChatMessage(msg_id, timestamp, text)`, `encode_message`(4B big-endian 길이 접두 + JSON `ensure_ascii=False` UTF-8), `StreamReassembler.feed`(부분/다중 프레임 정확 복원). 방어: `MAX_MESSAGE_BYTES=1MiB` 초과·손상 JSON·필드 누락 시 `ValueError`. stdlib only.
- **M4-T03** — `StreamMux`를 Session에 통합(`session/session.py`). 단일 `_arq`→`_mux`, 수신버퍼 stream_id별. 레거시 `send(data)`/`recv()`는 `DEFAULT_STREAM_ID` 위임으로 보존. 스트림 API `open_stream`/`send_on`/`recv_on`/`recv_all` 추가. pump 라우팅 stream_id 기준(0=제어→상태머신, ≥1→mux). `rto=RtoEstimator(...)` 호출 호환 위해 설정 복제 factory로 변환. `set_session_id` 일반화.
- **M4-T04** — `ChatSession`(`app/chat.py`, 패키지 re-export). `send_message(text)->int`(msg_id 자동 증가, timestamp=주입 clock), `pump()->list[ChatMessage]`(session.pump 후 recv_on→reassembler.feed), `connect`/`close`/상태 위임, `received` 누적. 결정적(실시간 미사용).
- **M4-T05** — `StreamMux` 테스트(`tests/test_mux.py`, 6건): 스트림 격리, HOL 없음(한 스트림 구멍이 다른 스트림 인도 안 막음), implicit open, open_stream 패리티, control stream 무시.
- **M4-T06** — 채팅 코덱 테스트(`tests/test_chat_codec.py`, 9건): 단일/다중/경계걸침/혼합 재조립, 유니코드 보존, 방어(ValueError).
- **M4-T07** — 채팅 통합 테스트(`tests/test_chat.py`, 8건): 무손실/손실 양방향 채팅(순서·msg_id 보존), 유니코드, 다수 메시지, 동일 seed 결정적 재현.
- **M4-T08** — 채팅 데모 예제(`examples/chat_loopback.py`). loss=20% seed=7에서 양방향 4+4 메시지 전부 순서대로 도착(MATCH), 양쪽 CLOSED.

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/stream/__init__.py`, `photontcp/stream/mux.py`, `photontcp/app/__init__.py`, `photontcp/app/codec.py`, `photontcp/app/chat.py`, `tests/test_mux.py`, `tests/test_chat_codec.py`, `tests/test_chat.py`, `examples/chat_loopback.py` |
| 수정 | `photontcp/session/session.py`(단일 ARQ → StreamMux 통합, 스트림 API, 레거시 호환) |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest -q` → **95 passed in 2.67s** (M1 13 + M2 17 + M3 42 + M4 23, M1~M3 회귀 없음).
- 예제 4종 전부 정상: `echo_loopback`·`session_loopback`·`reliable_loopback`·`chat_loopback`.
- 완료 기준 1~8 충족: 스트림 독립·HOL 없음, implicit open·패리티, 코덱 재조립·유니코드, 손실 채널 양방향 채팅, 레거시 호환(회귀 없음), 결정성, 예제, pytest 전체 통과.
- 이번 사이클은 서브에이전트가 소스 결함을 발견하지 않았다(M3 최종-ACK-손실 수정 덕에 손실 시나리오가 seed 제약 없이 수렴).

## 미해결·후속 메모

1. **세션 종료 시 스트림 데이터 flush 보장 없음** — 암묵적 경량 스트림이라 `close()`가 in-flight 스트림 데이터의 완전 전달을 기다리지 않는다(graceful 종료와 데이터 완료의 순서 보장 없음). 파일 전송(M5)에서 "전송 완료 후 종료" 보장이 필요하면 다룰 것.
2. **Session `rto` 인자의 factory 변환이 `RtoEstimator` 내부 속성에 약결합** — 전달된 추정기의 `initial/min/max`를 비공개 속성에서 읽어 복제. `getattr` 기본값 방어는 있으나, `RtoEstimator`에 공개 설정 접근자나 `clone()`을 두면 깔끔.
3. **M3 리뷰 권장 4건은 여전히 이월**(M4 비범위 확정): NACK 억제, 데이터 활동 idle 타이머 갱신, 데이터 재전송 상한/파라미터 노출, 종료 이벤트 정합성. 다중 스트림에서 NACK 스톰·idle 결합의 영향이 커질 수 있어 정리 마일스톤 권장.
4. **반이중(half-close)·per-stream close 미지원** — 스트림 종료는 세션 종료와 함께. 스트림 단위 수명주기가 필요해지면(파일 전송 다중화 등) 확장.
5. 여전히 비범위(의식적 이월): 공유 RNG·스레드 안전성, `LoopbackChannel` 전달 지연, 패킷 간 FEC(7단계), QR 코덱·광학(4·6단계).
