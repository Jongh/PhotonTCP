# M5 리뷰보고서 (review)

## 비판점

### 차단 (0건)

차단 이슈 없음. 완료 기준 1~7 충족, 전체 테스트 109건 통과(QR 의존 테스트 포함; 미설치 시 skip), 예제
5종 정상. 코덱은 순수·결정적, `ImageLoopbackChannel`은 `Channel` 인터페이스를 만족해 기존 스택이
무수정으로 QR 이미지 위에서 동작한다. 이번 사이클도 소스 결함 발견 없음.

### 권장 (3건)

1. **단일 QR 용량 한계가 send 경로에서 가드되지 않음** — `encode_frame`은 데이터가 단일 QR 심볼 용량을
   초과하면 segno `DataOverflowError`를 던지고, 이 예외가 `ImageLoopbackChannel.send_frame`에서 잡히지
   않고 전파된다. 기본 `max_payload=200`(프레임 ≈222B, base64 ~300자)은 안전하나, `arq_max_payload`를
   크게 설정하면 send에서 예외가 날 수 있다. **"프레임 크기(헤더+payload, base64 후) ≤ 단일 QR 용량"
   불변을 문서화하거나, ARQ `max_payload` 상한을 QR 용량에 맞춰 검증**할 것(M6/튜닝). 또는 다중 QR 분할.
2. **cv2 `QRCodeDetector` 공유 인스턴스의 스레드 안전성** — `decode.py`의 모듈 전역 `_DETECTOR`는
   단일 스레드 동기 펌프에서는 안전하나 스레드 안전이 보장되지 않는다. **M6에서 카메라 캡처를 백그라운드
   스레드로 돌릴 경우** detector를 스레드별로 두거나 락을 검토(M2~M4의 단일 스레드 가정 연장선).
3. **cv2 소형 QR 검출 하한(원본 ~7B 미만)** — 아주 작은 QR은 검출 실패→None. 모든 실제 PhotonTCP
   프레임(≥22B 헤더)은 안전하나, 코덱을 다른 용도로 재사용할 때 주의. 빈 입력(`b""`)도 None.

### 사소 (2건)

4. **`ImageLoopbackChannel` 노이즈 모델이 `LoopbackChannel`과 다름** — corrupt/reorder 대신 image
   단위 loss/dup + `degrade`(이미지 노이즈/블러). 광학 매체 특성상 의도된 차이지만, 순서뒤바뀜
   시뮬레이션이 필요하면(실제 카메라 프레임 누락/재정렬) `degrade`나 별도 reorder 추가 검토.
5. **EC/degrade 견딤은 기본 비활성** — `degrade=...`로 노이즈/블러 주입 테스트는 가능하나 기본 클린.
   실제 광학 노이즈(모션 블러·조명 변화)에 대한 본격 EC 견딤 검증은 M6에서.

## 수정 내용

- 차단 0건이라 리뷰 단계의 소스 수정은 없음.

## 검증

- `python -m pytest -q` → **109 passed in 3.80s** (M1~M4 86 + M5 23, 회귀 없음).
- 예제 5종: `echo`·`session`·`reliable`·`chat`·`qr` loopback 전부 정상(`qr_loopback`: 실제 QR 이미지
  222×222 통과, 양방향 MATCH, 양쪽 CLOSED, ~0.3s).
- 풀스택 통합 테스트(`test_image_channel.py`)가 세션·ARQ·채팅을 QR 이미지 위에서 검증(0.68s).
- 잔여 리스크: 권장 1(QR 용량)은 `max_payload` 튜닝 시, 권장 2(detector 스레드)는 M6 카메라 스레드 도입
  시 재검토 필요. 현재 단일 스레드·기본 payload 범위에서는 문제 없음.

## 릴리즈 판정

**가능** — 추천 버전: **v0.5.0 (minor)**

- 완료 기준 1~7 전부 충족, 차단 이슈 0건. QR 코덱 + 이미지 채널(로드맵 4단계) 신규 추가 = 1.0 이전 minor.
  기반 v0.4.0(릴리즈 완료) → 목표 v0.5.0.
- 처음으로 외부 의존성(segno·opencv-python·numpy) 도입 — pyproject optional `[qr]`/`[optical]`에 반영돼
  있고, 핵심 전송 스택은 여전히 stdlib-only(QR은 채널 계층에서만 필요).
- 권장 3건·사소 2건은 모두 후속(M6/튜닝)이 집어갈 항목으로 릴리즈를 막지 않는다.

## 다음 단계

- 릴리즈: **`/tide:release v0.5.0`** (`.tide/release-mode`=`release` 저장돼 질문 없이 release 모드:
  버전 범프 0.4.0→0.5.0 → CHANGELOG → commit → tag → push → `gh release create`).
  - 참고: 릴리즈 시 `pyproject.toml`의 `[qr]`/`[optical]` extra에 `numpy` 명시 여부 점검(opencv가 끌어오나
    명시적이면 견고). README 설치 안내에 `pip install photontcp[qr]` 추가 고려(릴리즈/문서 단계 선택).
- 릴리즈 후: 로드맵 다음은 **5단계 파일 전송** 또는 **6단계 실물 광학(화면+카메라)**. 사용자 우선순위
  (기본 통신 → 파일 전송)를 고려하면 **파일 전송(M6 후보)** 을 권하나, 로드맵 순서상 실물 광학도 후보다.
  다음 사이클 시작 시 우선순위를 확인할 것. 파일 전송 마일스톤에서는 M4 리뷰 권장(flush-on-close)을 필수
  반영하고, QR 용량 한계(권장 1)와의 상호작용(큰 파일 청크 ↔ QR 프레임 크기)도 함께 설계할 것.
