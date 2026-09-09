# 프로젝트 컨텍스트 (PhotonTCP)

`/tide:milestone`·`/tide:impl`이 매번 코드베이스를 재조사하지 않도록 하는 **개요 캐시**다.
상세 서술의 원본은 `README.md`이고, 규약은 `docs/conventions.md`다. 여기서 그 둘을 재서술하지
않고 작업에 필요한 좌표만 적는다.

## 스택 · 언어 · 의존성

- 언어: **Python** (`requires-python >= 3.10`), 패키지명 `photontcp`.
- 버전 단일 원본: `pyproject.toml` 의 `[project].version` — **값은 그 파일에서 읽는다**
  (문서에 복제하지 않는다).
- **런타임 필수 의존성 없음**(`dependencies = []`). 기능별 optional extras로 분리:
  - `qr` = segno, qrcode / `optical` = opencv-python, pyzbar / `app` = msgpack /
    `mobile` = kivy, camera4kivy, gestures4kivy / `dev` = pytest
- 빌드 백엔드: setuptools (`>=61`).

## 최상위 디렉터리

| 경로 | 역할 |
|---|---|
| `photontcp/` | 라이브러리 본체 (아래 계층 표) |
| `tests/` | pytest 스위트 (계층별 + 광학/QR 견고성 + 모바일. 파일·테스트 수는 사이클마다 변하므로 여기 박지 않는다 — `python -m pytest -q` 출력이 단일 출처다) |
| `examples/` | 실행 가능한 데모·하니스 (`*_loopback.py`, `optical_link.py`, `optical_selfcheck.py`, `qr_decode_bench.py`) |
| `docs/` | tide 산출물 — `milestones/`, `reports/`, `conventions.md`, 이 문서, `v1.0-signoff.md` |
| `.tide/` | 사이클 상태·선호도 파일 (`docs/conventions.md`의 "상태 파일 규약") |
| `main.py` | Android(python-for-android) 파이썬 진입 파일 — p4a가 `source.dir` 루트에서 이 이름으로 **고정** 탐색한다. `photontcp.mobile.app.main()`을 부르기만 한다 |
| `buildozer.spec` | APK 패키징 스펙(버전은 `version.regex`로 `pyproject.toml`에서 읽는다). 절차·우회는 `docs/mobile-build.md` |

### `photontcp/` 계층 (아래에서 위로)

| 패키지 | 역할 |
|---|---|
| `channel/` | 채널 추상 `Channel` + `LoopbackChannel`·`ImageLoopbackChannel`(가상) |
| `optical/` | 실물 광학 채널 `OpticalChannel` + `DisplaySink`/`CameraSource` 추상 + `Cv2Display`/`Cv2Camera` + 하드웨어 없는 피어 드라이버 `run_peer`/`PeerResult`(`peer.py` — 핸드셰이크→채팅→종료를 한 자리에서 몰고 `on_event`로 진행을 알린다) |
| `qr/` | bytes ↔ QR 프레임 인코드/디코드 (`decode_frame`은 전처리 캐스케이드 + 대체 detector 폴백). 디코더 백엔드 seam 공개 API: `register_decoder_backend` / `set_decoder_backend` / `active_decoder_backend` / `reset_decoder_backend_probe`(cv2 부재 캐시 무효화 — cv2 는 지연 import 되고 부재만 캐시된다) |
| `packet/` | 헤더 직렬화 · 패킷 타입 · CRC32 |
| `reliability/` | 슬라이딩 윈도우 ARQ · 재전송 · RTO 추정 · 직렬화 |
| `session/` | SYN/SYN_ACK/ACK 핸드셰이크 · FIN 종료 · 하트비트 · 상태머신 · 클록 |
| `stream/` | stream_id 다중화 (0 = 제어 스트림) |
| `app/` | Chat 앱 · File 앱 + 각 코덱 |
| `mobile/` | Kivy/Android 어댑터 — `KivyDisplay`(QR을 Kivy `Texture`로 올려 위젯에 붙이는 `DisplaySink`) · `KivyCamera`(camera4kivy `Preview` 콜백이 먹이는 `CameraSource`, 최신 1프레임만 보관) + 앱 골격 `PhotonTCPApp`/`main`(위젯 트리 + 채널 조립 + 워커 스레드에서 `run_peer` 구동, 프로토콜 로직 없음). Kivy 부재 시 재수출이 `None`으로 가드돼 `import photontcp.mobile`은 깨지지 않는다 |

**설계 불변**: 세션·신뢰성·앱 계층은 `Channel` 인터페이스에만 의존한다. 채널 구현(루프백 ↔ 실물
광학)을 갈아 끼워도 상위 계층은 바뀌지 않는다.

## 진입점 · 빌드/테스트

- 테스트: `python -m pytest -q` (레포 루트에서). 전 스위트는 하드웨어 없이 결정적으로 돈다.
- 데모(하드웨어 불필요): `python examples/optical_link.py`, `python examples/qr_decode_bench.py`
- 실물 검증(수동, 하드웨어 필요): `python examples/optical_selfcheck.py --camera 0 --hold 0.3 --count 20`,
  `python examples/optical_link.py --real --role sender|receiver` — 절차와 합격 기준은 `README.md`의
  "실물 검증 절차" 및 `docs/v1.0-signoff.md`.
- 모바일 앱(데스크톱에서도 뜬다, Kivy 필요): `python -m photontcp.mobile.app`
  (`pip install -e .[mobile]`). Android에서는 레포 루트 `main.py`가 같은 `main()`을 부른다 —
  APK 빌드 절차·툴체인·실패 시 우회는 `docs/mobile-build.md`.
- 설치형 CLI 진입점(`[project.scripts]`)은 없다 — 예제 스크립트를 직접 실행한다.

## 핵심 도메인 개념

- **Channel** — 프레임 단위 송수신 추상. 상위 계층의 유일한 하드웨어 경계.
- **Packet / CRC32** — 고정 형식 헤더 + 무결성 검사.
- **ARQ** — 슬라이딩 윈도우, ACK/NACK, 재전송 상한(`max_retx`)과 `SEND_FAILED`, 재정렬·중복제거.
- **Session** — 핸드셰이크·graceful close·하트비트·idle 타이머.
- **Stream MUX** — `stream_id` 다중화(0 = 제어, 2 = 파일 기본 스트림).
- **QR 프레임** — base64 페이로드를 QR로 실어 나르는 광학 매체 단위. 재캡처 dedup은 최근 N프레임 윈도우.
- **페이싱(`hold`)** — 카메라가 각 QR을 최소 1회 잡도록 디스플레이 프레임을 붙잡는 시간.

## 확인 필요

- ~~`__version__` 불일치~~ **해결(M11-T08)** — `photontcp/__init__.py`의 상수를 제거하고
  `importlib.metadata`(설치 시) → 개발 트리의 `pyproject.toml` 직접 파싱(미설치 시) 순으로
  파생시켜 선언처를 `pyproject.toml` 하나로 만들었다. `tests/test_version_single_source.py`가
  두 값의 분기·상수 부활을 모두 잡는다.
- v1.0 사인오프(셀프체크 수신율 ≥ 80% + 2-머신 왕복)는 **실물 하드웨어 수동 검증**이라
  CI/자동 검증 범위 밖이다. 현재 상태는 `docs/v1.0-signoff.md` 참조.

## 이월 항목 처분 원장

`/tide:retro`가 후속 항목을 **`처분`** 으로 옮길 때 같은 편집에서 사유 한 줄을 여기에 남긴다
(회고 표의 상태 값과 짝이 되는 유일한 기록처 — `docs/conventions.md`의 "회고 후속 항목의 소비").

| 날짜 | 항목 | 처분 사유 |
|---|---|---|
| 2026-09-08 | docstring 한/영 혼재 통일 (M1-review 사소) | **수용(사소)** — 3회 회고 연속 미반영. 기능·계약에 영향이 없고 신규 코드는 한국어 주석으로 일관돼, 일괄 개정 비용이 이득을 넘는다고 판단해 추적 종료. |
| 2026-09-08 | `OpticalChannel.pair(seed=…)` 무효 문구 (M8-review 사소5) | **수용(설계)** — 인메모리 광학은 RNG가 없어 시드가 의미 없으나, loopback 채널과의 **API 대칭**을 위해 인자를 유지한다. docstring이 무효임을 이미 명시한다. |
| 2026-09-08 | 캐스케이드의 이론적 base64 거짓-성공 (M10-review 사소3) | **수용(설계)** — QR 자체 EC·포맷 정보가 오검출을 사실상 차단하고 패킷에는 상위 CRC가 있어 무해하며, M10 이전 단일패스도 동일 노출이라 신규 리스크가 아니다(리뷰가 "기록만"으로 판정). |
| 2026-09-08 | `_alt_decode`의 변형 재계산 (M10-review 사소4) | **수용(설계)** — 1차 detector가 전 변형 실패한 **콜드패스에서만** 발생해 핫패스 비용이 불변이다. 폴백이 잦은 환경이 실제로 생기면 그때 변형 캐싱을 새 항목으로 연다. |
