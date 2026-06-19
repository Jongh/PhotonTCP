# M3 리뷰보고서 (review)

## 비판점

### 차단 (0건 — impl 단계에서 처리됨)

새로 발견된 차단 이슈 없음. impl 중 표면화된 **핸드셰이크 최종 ACK 손실 복구 불가** 결함은 메인이
`state_machine.py`에서 수정(ESTABLISHED에서 재전송 SYN_ACK 수신 시 ACK 재전송)하고 결정적 회귀
테스트(`test_final_ack_loss_recovers`)를 추가했다. 전체 테스트 72건 통과.

### 권장 (4건)

1. **NACK 스톰(효율)** — `ArqEndpoint._on_data`는 재정렬 버퍼에 구멍이 있는 동안 **수신하는 매 DATA마다**
   최저 누락 seq에 대한 NACK를 동봉한다. 송신측 `_on_nack`는 NACK마다 그 패킷을 즉시 재전송하므로,
   한 구멍에 대해 후속 DATA가 도착할 때마다 동일 패킷이 반복 재전송될 수 있다. 정확성은 수신 dedup으로
   보장되나 대역 낭비다. NACK 억제(구멍당 1회 또는 RTO 기반 rate-limit)를 후속에서 검토.
2. **데이터 활동이 세션 idle 타이머를 갱신하지 않음** — DATA/ACK는 ARQ로만 라우팅되어 세션 상태머신의
   `last_recv`를 갱신하지 않는다. 따라서 대용량 전송 중 링크 생존은 **HEARTBEAT에만 의존**한다(기본
   heartbeat 1.0s < idle 3.0s라 실사용은 안전하나 결합이 암묵적). DATA/ACK 수신도 idle 타이머를 리셋하거나
   "heartbeat_interval < idle_timeout" 불변을 명시적으로 강제하는 편이 견고하다.
3. **DATA 재전송 무한(상한 없음)** — 제어 패킷은 `max_control_retries`가 있으나 ARQ DATA는 `on_tick`이
   상대가 죽어도 무한 재전송한다(세션 idle 타임아웃이 유일한 backstop). TCP류 동작으로 수용 가능하나,
   데이터 경로에도 재시도/사망 판정을 두는 것을 고려. 더불어 `Session`이 `control_rto`/`max_control_retries`를
   미노출해 통합 테스트가 제어 재시도 예산을 조정할 수 없다(튜닝 시 노출 권장).
4. **graceful 종료 시 FIN_ACK 손실 → `TIMED_OUT` 이벤트로 CLOSED** — 예제에서 관측. 양쪽이 CLOSED에는
   도달하나 종료 완료 이벤트가 `CLOSED`가 아니라 `TIMED_OUT`으로 표면화될 수 있다. 종료 경로의 FIN/FIN_ACK
   재전송이 정상 동작하면 `CLOSED`로 끝나야 하므로, 종료 이벤트 정합성을 후속에서 점검.

### 사소 (2건)

5. **ACK/NACK 패킷의 `seq` 필드 = `next_seq`** — 수신측이 ACK/NACK의 seq를 사용하지 않으므로 무해(장식적).
6. **제로 윈도우 프로빙 미구현** — 상대 광고 윈도우가 0이면 송신 정지하나, head 패킷의 RTO 재전송이 수신
   버퍼를 비워 윈도우를 다시 열어 교착이 풀린다(영구 교착 없음). 명시적 zero-window probe는 7단계 여지.

## 수정 내용

- 차단 0건이라 리뷰 단계의 추가 소스 수정은 없음. (impl 단계의 최종-ACK-손실 복구 수정은 M3-impl 보고서
  및 회귀 테스트로 기록됨.)

## 검증

- `python -m pytest -q` → **72 passed in 2.69s** (M1 13 + M2 17 + M3 42, M1·M2 회귀 없음).
- 예제 3종: `echo_loopback.py`·`session_loopback.py` 정상, `reliable_loopback.py` → loss=30% 링크에서 740B
  **MATCH**, 양쪽 CLOSED.
- 직접 회귀 확인: 최종 ACK 손실 시 양 피어 ESTABLISHED 복구.
- 잔여 리스크: 권장 1·2·4는 손실률이 높거나 장기 전송에서 효율·이벤트 정합성에 영향. 현재 완료 기준
  범위(결정적 무손실 인도·손실 복구)에서는 문제 없음. M4(스트림/채팅)·후속 최적화에서 재검토 권장.

## 릴리즈 판정

**가능** — 추천 버전: **v0.3.0 (minor)**

- 완료 기준 1~9 전부 충족, 차단 이슈 0건(발견된 1건은 impl 단계에서 수정·회귀 테스트화).
- 신뢰성 ARQ 계층(SR · 누적ACK+선택NACK · 32비트 wraparound-safe · DATA+제어 재전송·수립 타임아웃) 신규
  추가 = 1.0 이전 minor. 기반 v0.2.0(릴리즈 완료) → 목표 v0.3.0.
- 권장 4건은 모두 후속(M4/최적화)이 집어갈 항목으로 릴리즈를 막지 않는다.

## 다음 단계

- 릴리즈: **`/tide:release v0.3.0`** (게시 모드는 `.tide/release-mode`=`release`로 저장돼 있어 질문 없이
  release 모드로 진행: 버전 범프 0.2.0→0.3.0 → CHANGELOG → commit → tag → push → `gh release create`).
- 릴리즈 후: **`/tide:milestone`**(또는 `/tide:cycle`)로 로드맵 3단계(M4 — 스트림 다중화 + 채팅 앱 =
  기본 통신 완성) 정의. 이때 본 리뷰 권장 1~4(NACK 억제, 데이터 idle 타이머, 데이터 재시도/파라미터 노출,
  종료 이벤트 정합성)와 반이중(half-close) 지원 필요 여부를 함께 반영할 것.
