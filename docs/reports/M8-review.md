# M8 리뷰보고서 (review)

## 비판점

### 차단 (0건)

차단 이슈 없음. 완료 기준 1~9 전부 충족, 전체 **172 테스트 통과**(M1~M7 166 + M8 신규 6, 회귀 0),
예제 인메모리 모드 정상(exit 0, 양방향 MATCH, both CLOSED). 핵심 설계(채널 프레이밍 nonce dedup,
디바이스 추상 격리, 백그라운드 캡처 스레드, M7 권장 3 동시성 정확성 검증)가 정확하고 기존 패턴과
일관된다. 소스 결함 발견 없음.

검증한 핵심 정확성:
- **nonce dedup**: `send_frame`이 1바이트 롤링 nonce를 앞에 붙이고, 캡처 스레드가 디코드한 `channel_frame`을
  직전 인도분과 **바이트 비교**해 같으면(재캡처) 버리고 다르면 nonce를 떼고 인도. 재캡처 1회 인도·연속
  동일 패킷(다른 nonce) 2회 인도가 테스트로 1:1 검증됨. 타이밍 의존(blank gap) 회피한 결정적 설계.
- **identity 빠른 경로**: 메모리 카메라가 유휴 시 같은 배열 객체를 재반환하므로 `img is last_decoded_array`
  로 재디코드를 생략 — 실물 카메라는 매번 다른 배열이라 트리거되지 않고 항상 디코드(정확).
- **스레드 안전성 (M7 권장 3 해소)**: 교차 스레드 공유 상태가 thread-safe 인박스 큐 + 원자적 `_closed`
  뿐이고 `decode_frame`은 스레드로컬 detector를 씀. 동시성 테스트가 양방향 30패킷 동시 송수신에서
  **보낸 집합 == 받은 집합**(손실·오염·교차오염 0)을 단언 — 스모크 초과 정확성. 3회 연속 무플레이키.
- **종료 정합성**: `close()` 멱등, 캡처 스레드 bounded join(교착 없음), 이후 send no-op·recv None.
- **계층 무변경 구동**: 기존 `Session`을 `OpticalChannel.pair` 위에서 핸드셰이크+데이터+graceful close까지
  무수정 구동(채널 교체만으로 동작 입증).

### 권장 (3건)

1. **`Cv2Camera` 실 캡처 경로가 어디서도 실행되지 않음 (v1.0 핵심 잔여)** — `Cv2Camera`/`Cv2Display`는
   클래스 정의가 import될 뿐(완료 기준 8의 "import 가능"은 충족), **인스턴스화·실행이 테스트·예제
   어디에도 없다**. 예제 `--real`은 `Cv2Display`(디스플레이 절반)만 배선하고 캡처는 인메모리로 유지하며,
   `--camera` 인자는 받기만 하고 미사용. 따라서 "실 카메라로 화면을 촬영해 디코드"하는 광학 **왕복**은
   완전 미검증이다. 한 머신이 자기 창을 자기 카메라로 보기 어렵다는 제약은 정당하나(예제 docstring에
   정직하게 명시됨), 이는 마일스톤이 v1.0 후보의 *핵심*으로 둔 부분이므로 **다음 단계 최우선 수동
   검증**(외부 카메라로 화면 촬영, 또는 화면2·카메라2 2-머신 루프)으로 남는다. M8 범위는 "채널 구현 +
   인메모리 검증"으로 명시돼 있어 **릴리즈를 막지 않음**.
2. **`Cv2Camera`의 VideoCapture 버퍼 staleness** — QR 디코드가 느려(수십~수백 ms) 캡처 루프가 카메라
   FPS보다 느리면 `cap.read()`가 드라이버 내부 버퍼의 **오래된 프레임**을 반환해 지연이 누적될 수 있다
   (HighGUI 공통 함정). 실물에서 `cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)` 또는 매 폴링 시 최신 프레임만
   취득(버퍼 드레인)하는 보강이 필요. 권장 1의 실물 검증 때 함께 처리할 것.
3. **dedup이 직전 1개만 비교 — nonce 1바이트(mod 256) 래핑과의 상호작용** — `last_delivered`가 단일
   슬롯이라, 동일 페이로드가 정확히 256프레임 간격으로 같은 nonce를 달고 (그 사이 모든 프레임을 카메라가
   놓친 채) 재등장하면 이론상 거짓 dedup이 가능. 단일 스레드 디스플레이 + 매 send nonce 증가 + ARQ
   재전송 보장 하에선 사실상 발생하지 않으나, 실물에서 캡처 누락률이 매우 높으면 코너 케이스다. 실물
   안정화 후 필요하면 nonce 폭(2바이트)이나 dedup 비교 깊이를 키우는 보강을 검토.

### 사소 (2건)

4. **`poll_interval=0` 시 캡처 루프 busy-spin** — `MemoryCamera.read`가 `timeout or 0.0` → 0.0이 되어
   즉시 `Empty`로 떨어져 바쁜 회전(CPU 점유)이 된다. 기본값 0.01에선 무관하나, 0을 넘기면 한 코어를
   태운다. 하한 가드(예: `max(timeout, 1e-3)`)나 문서 경고를 권장.
5. **`OpticalChannel.pair(seed=...)`는 무효** — 인메모리 디바이스는 RNG가 없어 `seed`가 `del`된다(API
   대칭용, docstring에 명시됨). 다만 예제가 `seed=SEED`를 넘겨 "재현성"을 시사하는 점은 약간 오해 소지 —
   인메모리 광학은 시드 무관하게 결정적임을 주석에 한 줄 더 분명히 해도 좋다.

## 수정 내용

- 차단 0건이라 리뷰 단계의 소스 수정은 없음.

## 검증

- `python -m pytest -q` → **172 passed in ~6.7s** (M1~M7 166 + M8 6, 회귀 0).
- 광학 테스트 `test_optical_channel.py`(5) + `test_optical_concurrency.py`(1)를 **3회 연속** 실행 →
  매회 6 passed(~2.3s), 플레이키 없음.
- `python examples/optical_link.py`(인메모리) → exit 0: 핸드셰이크 → 양방향 메시지 MATCH → both CLOSED.
- `issubclass(OpticalChannel, Channel)` → True.
- cv2 가드: cv2 존재 시 `Cv2Display`/`Cv2Camera` 정상 export(서브모듈 import가 패키지 `__init__`를 실행해
  가드 경로가 실제로 통과됨). cv2-부재 except 경로는 환경상 미실행(`pragma: no cover`).
- **잔여 리스크**: 실 카메라 캡처 왕복(권장 1)과 버퍼 staleness(권장 2)는 하드웨어가 필요해 CI/이번
  범위에서 검증 불가 — v1.0 승격 전 수동 검증 필수. 권장 3은 정상 동작 범위 밖의 이론적 코너로 현재
  영향 없음.

## 릴리즈 판정

**가능** — 추천 버전: **v0.8.0 (minor)**

- 완료 기준 1~9 전부 충족, 차단 이슈 0건. 신규 공개 API(`OpticalChannel`·`optical` 서브패키지·
  `DisplaySink`/`CameraSource`·`MemoryDisplay`/`MemoryCamera`/`memory_device_pair`·`Cv2Display`/`Cv2Camera`)
  추가 = minor. 기반 v0.7.0(릴리즈 완료) → 목표 v0.8.0.
- 권장 3건·사소 2건은 모두 **실물 광학(v1.0) 단계가 집어갈 후속**이거나 무영향 코너로, 인메모리로 완전
  검증된 M8 범위의 릴리즈를 막지 않는다. v1.0(빛만으로 실 통신 증명) 승격은 권장 1(실 카메라 왕복)
  수동 검증 이후로 명확히 분리된다.

## 다음 단계

- 릴리즈: **`/tide:release v0.8.0`**.
- 릴리즈 후 로드맵:
  - **실물 광학 검증 → v1.0 후보 확정**(권장 1·2): 외부 카메라로 `Cv2Display` 창을 촬영하거나 화면2·
    카메라2 2-머신 루프로 `Cv2Camera`-기반 `OpticalChannel` 왕복을 수동 검증하고, VideoCapture 버퍼
    staleness(`CAP_PROP_BUFFERSIZE=1`/드레인)와 `hold`/`poll_interval`·정렬·조명을 실물 튜닝.
  - **7단계 최적화**: 적응형 RTO 튜닝·흐름제어·패킷 간 FEC.
  - (후속) 권장 3(nonce 폭/ dedup 깊이), 사소 4(poll_interval 하한 가드), 양방향/다중 파일·재개,
    half-close, M7 잔여(arq max_retx 노출·실패 push).
  다음 사이클 시작 시 우선순위를 확인할 것.
