# M2 리뷰보고서 (review)

## 비판점

### 차단 (0건)

없음. 마일스톤 완료 기준 1~7을 모두 충족하며 전체 테스트(30건: M1 13 + M2 17)가 통과한다. impl 중
표면화된 종료 결함(순차 종료 시 수동측 CLOSE_WAIT 잔류)은 impl 단계에서 메인이 대칭 auto-close로
이미 수정·검증했다.

### 권장 (3건)

1. **핸드셰이크/연결 수립 타임아웃 부재** — `on_tick`의 idle 타임아웃은 `last_recv is not None`일 때만
   동작한다. 따라서 개시자가 `SYN_SENT`에서 상대 응답을 한 번도 못 받으면(`last_recv` 영구 `None`)
   아무리 시간이 흘러도 타임아웃되지 않는다. M2는 무손실 가정이라 정상 미발생이지만, 손실이 있는
   M3에서는 "수립 단계 타임아웃 + SYN/FIN 재전송"이 필요하다. **M3(ARQ)에서 제어 패킷 재전송과 함께 처리**.
2. **`Session`에 식별자/시퀀스 공개 접근자 부재** — 핸드셰이크 테스트가 `session_id` 합의 검증을 위해
   내부 속성(`session._machine.session_id`)에 접근했다. 캡슐화 관점에서 `Session.session_id`(읽기 전용)
   같은 공개 접근자를 두면 테스트·상위 계층이 내부 구조에 의존하지 않는다. 다음 마일스톤에서 보강 권장.
3. **반이중(half-close) 미지원** — 종료가 대칭 auto-close라 한 방향만 닫고 다른 방향으로 데이터를 계속
   보내는 시나리오는 불가. 데이터 스트림(M3+)·파일 전송에서 필요해지면 종료 의미론 확장 검토.

### 사소 (2건)

4. **idle 타임아웃이 teardown 중에도 활성** — `FIN_WAIT`/`CLOSE_WAIT`에서 상대가 침묵하면 `TIMED_OUT`이
   `CLOSED` 대신 surface될 수 있다(여전히 CLOSED로는 전이). 무손실 M2에선 발생하지 않으나, 의미를
   명확히 하려면 종료 진행 중에는 타임아웃 사유 구분을 검토.
5. **`Clock` Protocol이 `runtime_checkable` 아님** — 의도된 설계(구조적 타이핑). `isinstance` 검사는
   불가하나 사용에는 문제없음. 기록만.

## 수정 내용

- **권장 외 문서-구현 정합성(리뷰 중 직접 수정)**: `docs/milestones/M2.md`의 종료 절 산문이 "수동측이
  이후 직접 `close()` 호출"(model B)로 적혀 있었으나 실제 구현은 **대칭 auto-close**(model A)다. 마일스톤
  서술을 구현에 맞게 정정해 스펙-코드 드리프트를 제거했다(완료 기준 2 자체는 양쪽 모두 변경 전부터 충족).
- 차단 이슈는 0건이라 소스 코드 수정은 없음(impl 단계의 종료 로직 수정은 M2-impl 보고서에 기록됨).

## 검증

- `python -m pytest -q` → **30 passed in 2.60s** (M1 회귀 0). M2 신규 17건(handshake 4 + heartbeat 13).
- `python examples/echo_loopback.py` → 전체 MATCH (M1 회귀 없음).
- `python examples/session_loopback.py` → 핸드셰이크 → 하트비트 → 양쪽 CLOSED, exit 0.
- 종료 시나리오 직접 재확인: active close / 동시 close 모두 양쪽 CLOSED + 올바른 이벤트(PEER_CLOSED, CLOSED).
- 잔여 리스크: 권장 1(수립 타임아웃)·3(half-close)은 손실/데이터 전송이 들어오는 M3+에서 재검증 필요.
  현재 무손실·제어 전용 범위에서는 문제 없음.

## 릴리즈 판정

**가능** — 추천 버전: **v0.2.0 (minor)**

- 완료 기준 1~7 전부 충족, 차단 이슈 0건. 세션 계층(핸드셰이크·종료·하트비트) 신규 추가 = 1.0 이전 minor.
- 기반 v0.1.0(릴리즈 완료) → 목표 v0.2.0. 이전 릴리즈가 존재하므로 정상적으로 minor 범프한다.
- 권장 3건은 모두 후속(M3/이후)이 자연히 집어가는 항목으로 릴리즈를 막지 않는다.

## 다음 단계

- 릴리즈: **`/tide:release v0.2.0`** (프리플라이트 → 버전 범프 0.1.0→0.2.0 → CHANGELOG → commit → tag → push).
  게시 모드(`pr`/`release`)는 인자로 지정 가능.
- 릴리즈 후: **`/tide:milestone`**(또는 `/tide:cycle`)로 로드맵 2단계(M3 — 신뢰성 ARQ: 슬라이딩 윈도우 ·
  ACK/NACK · 재전송 · 재정렬 · 중복제거) 정의. 이때 본 리뷰 권장 1(수립 타임아웃+제어 재전송), M1 리뷰
  권장(① seq/ack 범위 mod 2³², ② 공유 RNG·스레드 안전성), 그리고 가상 클럭 latency/jitter 보강을 M3
  완료 기준에 명시 반영할 것.
