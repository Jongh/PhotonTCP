# M11 완료보고서 (impl)

## 개요

M11 "모바일(Android) 1급 지원"의 9개 태스크 중 **8개를 구현하고 1개(T06 APK 실빌드)는 마일스톤이
예고한 폴백 경로로 처리**했다. `qr/decode.py`의 cv2 하드 의존을 디코더 백엔드 seam으로 풀었고,
세션 구동 로직을 `run_peer()`로 헤드리스 추출해 CLI 예제와 모바일 앱이 같은 코드를 쓰게 했으며,
`KivyDisplay`/`KivyCamera` 어댑터 + Kivy 앱 골격 + `buildozer.spec` + 빌드 문서를 갖췄다. 회고
2순위 잔여(Micro-QR 원천 제거, `__version__` 단일 원본)도 함께 닫았다. 디스패치: L0 = T01 ∥ T02 ∥
T03 ∥ T08(4-way 병렬), L1 = T04 ∥ T05 ∥ T07(3-way 병렬), L2 = T09, 그리고 메인이 직접 `main.py`
진입점과 `mobile` extra 보정. **T06은 사용자 판단으로 실빌드를 돌리지 않고 툴체인 상태·사전조건
기록으로 갈음했다**(아래 태스크별 수행 내용과 완료 기준 9).

재작업 라운드(rework): 1

### 재작업 라운드 1 (M11-review 판정 `불가` 반환분)

M11-review가 **차단 1건 + 권장 5건 + 사소 9건**으로 `불가` 판정하여 반환했다. 차단은 구현
결함이 아니라 **검사의 공허함**이었다 — 완료 기준 1(cv2 지연 import)을 무는 테스트가
캐시된 모듈을 돌려받아, `decode.py` 상단에 `import cv2`를 되살려도 전체 스위트가 초록이었다
(리뷰의 되돌림 실측). 이번 라운드는 **그 가드를 실제로 물게 만드는 것**이 주 작업이고,
권장 5건 전부와 사소 7건을 함께 닫았다. 알고리즘·공개 계약 변경은 없다.

**반환 항목 대조** (M11-review 「다음 단계」의 번호 기준):

| 반환 항목 | 등급 | 처리 | 근거 |
|---|---|---|---|
| 1. 지연 import 가드가 공허 | **차단** | **닫힘** | `fresh_qr_import` fixture로 `sys.modules`의 `photontcp.qr*`를 걷어내 **신규 실행**을 강제. 되돌림 실측: eager `import cv2` 복원 시 전체 스위트가 **1 failed**(이전 라운드에서는 240 passed) |
| 2. 버전 파생이 설치 메타데이터 우선 | 권장 | 닫힘 | 파생 순서를 **pyproject 우선**으로 뒤집고, 낡은 metadata를 흉내 내 순서를 무는 테스트 2건 추가 |
| 3. 카메라 버퍼 수명 가정이 미기록 | 권장 | 닫힘 | 기록에 그치지 않고 `copy_on_submit=True`(기본)로 **소유권을 가져오게** 하고, 버퍼 재사용을 흉내 낸 테스트로 고정. README·app.py 「확인 필요」에도 추가 |
| 4. 앱이 `hold=0` 허용 | 권장 | 닫힘 | `run_peer`가 `hold <= 0`을 `ValueError`로 **거부**(라이브러리 층에서 집행) + 앱의 하한을 `MIN_HOLD`로 상향 |
| 5. `done` "always last" 선언이 과함 | 권장(부인 기록) | 닫힘 | 선언을 사실에 맞게 축소 — "`run_peer`가 **반환하면** `done`이 마지막", 예외 경로는 예외가 결과를 전한다고 명시 |
| 6. `mobile-build.md`가 기존 `main.py`를 만들라고 지시 | 권장 | 닫힘 | 0절을 "확인 하나 · 준비 하나"로 바꾸고 0-1을 **확인 절차**로 재작성(덮어쓰기 경고 포함) |
| 7. `_ACCEPTS_DETECTOR` 무한 증가 가능 | 사소 | 닫힘 | `register_decoder_backend` 계약에 "같은 콜러블 객체를 돌려줘라"와 그 이유를 명시 |
| 9. `_arrived.set()`이 락 밖 | 사소 | 닫힘 | `set()`을 그것이 알리는 슬롯과 **같은 임계구역 안**으로 이동 |
| 10. `messages` 검증 없음 | 사소 | 닫힘 | 쌍이 아니면 `IndexError` 대신 `ValueError` + 테스트 |
| 12. 튜닝 상수 이중화 | 사소 | 닫힘 | `examples/optical_link.py`가 값을 복제하지 않고 `photontcp.optical.peer`에서 **재수출** |
| 13. `android.wakelock` 주석 상태 | 사소 | 닫힘 | `android.wakelock = True` + 짝이 되는 `WAKE_LOCK` 권한 |
| 14. APK에서 `__version__`이 `"0+unknown"` | 사소 | 닫힘 | `source.include_exts`에 `toml` 추가 — `pyproject.toml`이 APK에 들어가 기기에서도 같은 값 |
| 15. signoff 문서의 테스트 수 | 사소 | 닫힘 | 수를 빼고 **선언처(`pytest -q` 출력)를 가리키게** 변경 — 후속 메모 8이 예고한 형태 |
| 8. cv2 부재 시 실패 import 반복 | 사소 | **이월** | 완화하려면 가용성 캐시가 필요한데 그것은 `active_decoder_backend()`의 live 보고 성질과 맞바꾸는 설계 결정이다. 리뷰도 "다음 사이클 후속"으로 분류 |
| 11. `optical` 패키지의 eager app/session import | 사소 | **이월** | 지연 재수출(`__getattr__`)이 필요한 구조 변경이라 반환 라운드의 범위를 넘는다. 리뷰도 "다음 사이클 후속"으로 분류 |

## 태스크별 수행 내용

- **M11-T01** (L0 병렬) — `photontcp/qr/decode.py` **디코더 백엔드 seam**. 모듈 상단 `import cv2`를
  `_require_cv2()` 지연 import로 교체하고, 기존 cv2 경로(전처리 캐스케이드 + 대체 detector 폴백)를
  **알고리즘 무변경으로** `"cv2"` 백엔드로 옮겼다. 공개 API `register_decoder_backend` /
  `set_decoder_backend` / `active_decoder_backend` 추가. 자동 선택은 등록 순서대로 첫 성공,
  `set_decoder_backend(None)`이 자동 복귀, 미등록 이름은 `ValueError`(핫패스가 아닌 설정 API라 오타를
  삼키지 않는다). **가용 백엔드가 없으면 `decode_frame`은 `None`을 반환하고 never-raise** — 서드파티
  백엔드가 던져도 `except Exception`으로 흡수한다. `_resolve_alt_kind`·`_alt_kind_cached`·
  `_thread_detector`·`_decode_variants`·`_alt_decode`는 **모듈 수준 이름을 그대로 유지**했다
  (`docs/v1.0-signoff.md`가 `_resolve_alt_kind`를 명령줄로 안내하고, 기존 테스트의 monkeypatch가
  모듈 전역 조회로 걸린다). **설계 결정**: 백엔드 가용성은 캐시하지 않는다 — cv2가 사라지는 시뮬레이션
  상황에서 `active_decoder_backend()`가 stale `"cv2"`를 보고하지 않게 하기 위함이고, factory는 계약상
  싸다(`sys.modules` 조회). M10의 alt-detector 1회 probe 캐시와 thread-local detector 패턴은 현행 유지.
  `tests/test_qr.py`는 **수정 없이** 통과했다.
- **M11-T02** (L0 병렬) — `photontcp/optical/peer.py` 신설, `examples/optical_link.py`의
  `_drive_real_peer`에서 세션 구동 루프를 추출해 `run_peer(channel, role, *, messages, timeout, hold,
  on_event, close_channel) -> PeerResult`로 만들었다. `PeerResult`는 `established`/`messages_match`/
  `closed`/`wall_seconds`/`passed` + 부가 필드(`handshake_rounds`·`close_rounds`·`sent`·`received`·
  `expected`·`timed_out`). 진행 상황은 콜백 `on_event(kind, detail)`로만 흘리고(kind 집합을 모듈 상수
  `EVENT_KINDS`로 선언: `start, connect, handshake, sent, received, match, closing, closed, error,
  done`), **라이브러리에 print가 없다**. 콜백 예외는 삼켜서 UI 콜백이 하드웨어 세션을 죽이지 못하게
  했다. 예제는 배너·디바이스 생성·출력·exit code만 담당하며 요약 줄 형식은 그대로다. **CLI 계약
  불변**(`--help` diff 무출력). `channel`은 `Channel` 인터페이스만 요구해 `OpticalChannel.pair()`
  인메모리 테스트가 가능하다. 실측: `hold=0.02` → 양 role 전부 PASS(0.44s), **`hold=0.0`은 실패**
  (캡처 스레드에 틈이 없어 라운드 캡 소진) — docstring에 명시.
- **M11-T03** (L0 병렬) — `photontcp/mobile/` 신설. `KivyDisplay(DisplaySink)`는 `show()`가 렌더하지
  않고 **pending 슬롯 1개에 packing만** 한 뒤 `Clock.schedule_once`로 메인 스레드 렌더를 예약하고 즉시
  반환한다(광학 송신 스레드를 막지 않는다). 렌더는 `Texture.create` → `blit_buffer` → `flip_vertical`
  (Kivy 텍스처 원점이 좌하단이라 상하 반전 시 디코드 불가) → `widget.texture` 갱신. `KivyCamera
  (CameraSource)`는 푸시 모델 — `submit_frame(pixels, size, colorfmt)`가 카메라 콜백 스레드에서
  **pending 1장을 덮어쓰고** 끝나며(큐 없음 = 지연 누적 원천 차단, `Cv2Camera` 드레인과 같은 목적),
  `read(timeout)`이 그 1장만 변환한다. 색공간 변환은 `_frame_to_array()` 한 곳에 모았다(RGBA/BGRA/
  RGB/BGR/luminance → `HxWx3 BGR` 또는 2D gray). 새 프레임이 없으면 **직전 배열을 같은 객체로 재반환**
  (채널의 identity 스킵 + 윈도우 dedup이 흡수), 한 번도 없었으면 `None`. `photontcp/mobile/__init__.py`는
  `optical/__init__.py`의 cv2 가드와 **동형**이다. `pyproject.toml`에 `mobile` extra 추가.
- **M11-T04** (L1 병렬) — `photontcp/mobile/app.py` Kivy 앱 골격. 4단 레이아웃(설정 행 / QR 표시 +
  Preview 슬롯 / 상태 줄 / Start·Stop). 세션은 데몬 워커 스레드에서 `run_peer(...)` **한 번만** 호출하고,
  모든 UI 변경은 단일 마샬링 지점 `_ui()` → `Clock.schedule_once`를 거친다. 채널은
  `OpticalChannel(display, camera, scale=…, error="q", hold=…)`로 조립(생성자 시그니처를 소스에서 확인).
  안드로이드 런타임 카메라 권한을 `check_permission`/`request_permissions`로 처리하고, 거부 시 상태
  줄에 사유만 표시하고 **크래시 없이** 멈춘다. camera4kivy `Preview` 서브클래스의
  `analyze_pixels_callback`은 뒤쪽 인자를 `*args`로 흡수해 시그니처가 어긋나도 배선이 깨지지 않게
  했고, 콜백 내부 예외는 전부 삼킨다(CameraX 분석 스레드로 예외를 올리지 않는다). **설계 결정**:
  `run_peer`에 취소 훅이 없어 Stop이 최대 60초를 태우는 문제를 UI 계층 전용
  `_CancellableChannel`(위임 래퍼 + `SessionCancelled`)로 풀었다 — 래퍼는 프레임을 들여다보지 않으므로
  프로토콜 판단이 앱에 새로 생기지 않는다. 진입점: `PhotonTCPApp` + `main()`,
  `python -m photontcp.mobile.app`.
- **M11-T05** (L1 병렬) — `buildozer.spec` + `docs/mobile-build.md` + `.gitignore`. 스펙 값은 전부
  1차 출처(buildozer master `default.spec`·`installation.rst`·`get_version()` 소스, Camera4Kivy
  README, PyPI 메타데이터, `c4k_opencv_example` spec, p4a 이슈 #3203)로 확인하고 근거 주석을 달았다.
  `android.api = 33`(camera4kivy가 camerax_provider 제약으로 요구), `minapi/ndk_api = 24`,
  `android.archs = arm64-v8a`, `android.permissions = CAMERA`, `orientation = portrait`.
  **`android.ndk`는 주석 처리 + "확인 필요"** — 실빌드로만 확정되는 값이라 틀린 값을 박지 않고
  buildozer 기본값에 맡겼다. **opencv는 requirements에 포함**했다: 빼면 seam 덕에 앱은 뜨지만
  `decode_frame`이 항상 `None`이라 **광학 링크 자체가 성립하지 않기** 때문이며(대체 백엔드는 M11이
  명시적으로 이월), 대신 빌드 실패 위험(p4a 이슈 #3203: opencv 4.5.1 레시피 × numpy 2.3.0)과 우회
  경로를 주석·문서에 적었다. 마일스톤에 없던 필수 항목 둘을 발견해 반영했다 — `gestures4kivy`
  (camera4kivy의 `requires_dist`, p4a는 명시된 것만 빌드) 와 `p4a.hook = camerax_provider/
  gradle_options.py`(camerax_provider는 pip 패키지가 아니라 별도 저장소라 빌드 전 clone 필요).
  **버전 선언처를 늘리지 않았다** — `version.regex`/`version.filename`으로 `pyproject.toml`을 직접
  가리키고, buildozer의 `get_version()`이 `re.search`를 플래그 없이 부르는 것을 소스에서 확인해
  정규식 앞에 개행을 넣었다(실측: `0.10.0` 추출 성공). 문서는 Docker 경로(정지 상태에서 시동부터)와
  WSL2 경로(배포판 설치부터), 시간·캐시 표, `adb` 획득·설치 절차, 실패 진단 12행 표, opencv 우회를
  담는다.
- **M11-T06** — **실빌드 미수행(폴백 경로).** 마일스톤이 완료 기준 9에 규정한 폴백을 적용했고, 그
  판단은 사용자가 내렸다(비용: 수 GB 다운로드 + opencv 소스 컴파일 포함 최초 빌드 30~90분 이상).
  착수 시 실측한 툴체인 상태 —
  - `docker --version` → **29.4.2**, `docker info` → **데몬 정지**(Docker Desktop Stopped)
  - `wsl -l -v` → 배포판은 **`docker-desktop`(Stopped) 하나** — 일반 Ubuntu 배포판 없음
  - `adb` **없음**, `buildozer` **없음**, `gradle` **없음**, `java`는 있음(Oracle javapath)
  - Kivy·camera4kivy **미설치**(cv2·segno·numpy는 설치됨), 디스크 여유 D: 1.4T
  - 미충족 사전조건: ① `camerax_provider/` clone(빌드 전 필수, `.gitignore` 처리됨) ② Docker Desktop
    기동 또는 WSL2 일반 배포판 설치 ③ `android.ndk` 값 확정
  따라서 이 사이클에서 **opencv 레시피가 실제로 통과하는지는 미확인**이며, 실빌드는 다음 사이클의
  첫 항목이다.
- **M11-T07** (L1 병렬) — `encode_frame`이 `segno.make(..., micro=False)`로 **일반 QR만** 내보낸다.
  **`micro`를 공개 인자로 열지 않았다** — Micro QR 프레임은 이 코덱이 내보낼 수는 있으나 절대 읽을 수
  없는 프레임이라 선택지가 아니라 불변식이고, 인자로 열면 호출자가 블라인드스폿을 되살릴 수 있다
  (사유를 모듈·함수 docstring과 인라인 주석에 기록). 연동 수정: `test_micro_qr_boundary_excluded`
  (8B가 *실패*함을 단언)를 삭제하고 두 개로 교체 — 5·8·9B 각 20개 왕복 100%, 그리고 렌더된 비트맵의
  **모듈 수 ≥21**을 단언해 인코더가 Micro를 다시 내보내면 깨지는 기계적 가드. 코퍼스 하한(12B)은
  테스트·벤치 양쪽에서 해제했다. **회귀 없음을 측정으로 확인**: segno 심볼 designator를 직접 비교해
  22B/24B/32B 페이로드가 default와 `micro=False`에서 **동일 심볼**(3-Q/2-M/4-Q, 모듈 수 29/25/33
  불변)임을 확인했다 — 실 패킷 경로의 프레임 크기는 바이트 단위로 변하지 않는다. 광학 테스트 3회
  연속 13 passed(4.49/4.41/4.41초, 플레이키 없음).
- **M11-T08** (L0 병렬) — `photontcp/__init__.py`의 `__version__ = "0.1.0"` 상수를 제거하고
  `importlib.metadata.version("photontcp")` → (미설치면) `pyproject.toml` 직접 파싱
  (`tomllib`, 3.10 대응 정규식 폴백) → `"0+unknown"` 순으로 **파생**시켰다. **설계 결정**: 미설치
  폴백조차 같은 단일 원본(`pyproject.toml`)을 읽는다 — 폴백을 하드코딩 상수로 두면 선언처가 다시
  둘이 되어 방금 고친 불일치가 재발하기 때문이고, 최종 폴백은 실제 릴리즈와 겹치지 않는
  `"0+unknown"`이라 "모름"이 유효한 버전 행세를 하지 못한다. `tests/test_version_single_source.py`
  5건 추가. `docs/project-context.md`의 "확인 필요"에서 해결된 항목만 정리했다.
- **M11-T09** (L2) — 테스트 3종 + 문서 2종. `tests/test_decode_backend.py`(13): `sys.meta_path`
  블로커로 cv2를 지운 상태에서 import 성공·`decode_frame` None·never-raise, 가짜 백엔드 등록/선택/
  복귀, 미등록 이름 `ValueError`, factory가 `None`/raise면 "없음" 취급. `tests/test_peer_runner.py`
  (13): `OpticalChannel.pair(scale=6)` 두 스레드 왕복 PASS, `on_event` 시퀀스(첫 `start`, 마지막
  `done` 1회, `detail["result"]`가 반환값과 동일 객체), 잘못된 role 5종 `ValueError`. **타이밍은
  단측 경계만**(절대 상한·차등 비교 없음 — M10에서 얻은 규칙). `tests/test_mobile_devices.py`(17+1
  skip): Kivy 미설치 환경의 가드 경로를 무조건 단언하고, 어댑터 계약은 자립 스텁을 `sys.modules`에
  심어 **실제** `kivy_devices.py`를 로드해 검증(프레임 교체 의미, RGBA→BGR 채널 순서, 동일 객체
  재반환, 큐 없음). Kivy가 설치된 환경을 위한 반대 분기 테스트도 함께 둬서 어느 환경에서도 검사가
  공허해지지 않게 했다. 문서: `README.md`에 "모바일(Android) (M11)" 절 + **현재 한계**(실기 미검증·
  camera4kivy 아카이브 상태·flip/colorfmt 확인 필요), `docs/v1.0-signoff.md`에 **2-B 체크 B 변형 —
  폰 + PC 조합**(역할 배정·물리 배치·폰 실전 주의 표·조합 특유 실패 표)을 기존 절차를 보존한 채 추가.
- **메인 직접** — `main.py`(레포 루트) 생성: p4a는 진입 파일 이름을 `main.py`로 고정하며 spec으로 바꿀
  수 없어(T05가 buildozer 소스에서 확인) `photontcp.mobile.app:main`을 부르는 3줄 파일이 필요하다.
  `pyproject.toml`의 `mobile` extra에 `gestures4kivy` 추가(buildozer requirements와 대칭).

### 재작업 라운드 1의 변경 (태스크별 수행 내용 보충)

- **[차단 1] `tests/test_decode_backend.py`** — `fresh_qr_import` fixture 신설. `sys.modules`에서
  `photontcp.qr` 및 `photontcp.qr.*`를 걷어낸 뒤 `import_module`을 불러 **모듈 상단이 실제로 다시
  돌게** 한다(종료 시 재실행으로 생긴 인스턴스를 걷고 원본을 복원해, 이 파일 상단이 잡은
  `_decode_mod`와 `sys.modules`가 갈라진 채 새지 않게 한다). 테스트는 신규 모듈 객체가 기존
  것과 **다름**을 먼저 단언해(= 캐시를 돌려받지 않았음을 증명) 검사가 다시 공허해지는 경로를
  닫고, 그 신규 모듈에서 `active_decoder_backend() is None`·`decode_frame(...) is None`까지 본다.
  **검수 기준을 docstring에 명문화**했다 — "`decode.py` 상단에 `import cv2`를 되살리면 여기가
  붉어야 한다".
- **[권장 2] `photontcp/__init__.py`** — 파생 순서를 `pyproject.toml` → `importlib.metadata`로
  뒤집었다. **설계 결정**: 소스 트리에서는 *파일이 곧 진실*이다. metadata를 먼저 보면 editable
  설치 트리에서 릴리즈가 pyproject만 범프한 순간 둘이 갈라져, "선언처가 하나"를 무는 테스트가
  **릴리즈 절차 자체를 막는다**. 설치된 배포판에는 곁에 `pyproject.toml`이 없으므로 자연히
  metadata로 떨어지고, 그 값 역시 빌드 시점의 같은 파일에서 나온다 — 선언처는 여전히 하나다.
  `tests/test_version_single_source.py`에 순서를 무는 테스트 2건 추가(낡은 metadata 흉내 /
  둘 다 없을 때의 `"0+unknown"`).
- **[권장 3] `photontcp/mobile/kivy_devices.py`** — `KivyCamera(copy_on_submit=True)` 신설.
  `submit_frame`이 `_own()`으로 버퍼 소유권을 가져온다(ndarray는 `.copy()`, bytes-like는
  `bytes()`; 그 밖은 그대로 통과). **설계 결정**: 기록만 남기지 않고 기본 동작을 바꾼 이유는,
  변환을 `read`로 미룬 설계 때문에 submit~read 사이에 어댑터가 **남의 버퍼를 참조만** 하고,
  CameraX가 그것을 재활용하면 증상이 "조용한 디코드 실패"라 실기에서 flip 문제와 구분되지 않기
  때문이다. 비용은 콜백 스레드의 memcpy 한 장이고(색공간 변환은 그대로 `read`에 남는다),
  제출은 큐가 아니라 교체이므로 언제나 한 프레임 분량뿐이다. 옵트아웃 인자를 남겨 계약의 반대쪽
  끝도 테스트로 고정했다.
- **[권장 4] `photontcp/optical/peer.py` + `photontcp/mobile/app.py`** — `run_peer`가 `hold <= 0`을
  `ValueError`로 거부한다(**라이브러리 층에서 집행** — 앱만 고치면 다른 프론트엔드가 같은 함정에
  빠진다). `_round()`의 `if hold > 0` 죽은 가지를 제거했다. 앱은 `MIN_HOLD = 0.01`로 **위로**
  클램프해 UI가 애초에 그 값을 만들 수 없게 한다(방어 이중화).
- **[권장 5] `EVENT_KINDS`** — `done` 주석을 사실에 맞췄다: 반환하는 모든 경로(핸드셰이크·종료
  실패 포함)에서 마지막이지만, 채널이 예외를 던져 드라이브가 풀리면(앱의 Stop) 나오지 않으며
  그때는 예외가 결과를 전한다.
- **[사소 9] `KivyCamera.submit_frame`** — `_arrived.set()`을 락 안으로 옮겨 허위 기상을 제거.
- **[사소 10] `run_peer`** — `messages`가 쌍이 아니면 `ValueError`.
- **[사소 12] `examples/optical_link.py`** — `ROUND_DT`/`HEARTBEAT_INTERVAL`/`IDLE_TIMEOUT`/
  `MAX_*_ROUNDS`/`WALL_DEADLINE_S`/`MESSAGES_A`·`MESSAGES_B`를 `photontcp.optical.peer`에서
  재수출. 인메모리 데모가 계속 쓰므로 이름은 남기되 값의 출처는 하나로 모았다. `ROUND_SLEEP`은
  이 데모 고유값이라 로컬로 남기고 그 사실을 주석에 적었다.
- **[사소 7·13·14·15]** — `register_decoder_backend` 계약에 "같은 콜러블을 돌려줘라" 명시 /
  `android.wakelock = True` + `WAKE_LOCK` 권한 / `source.include_exts`에 `toml` /
  `docs/v1.0-signoff.md`가 테스트 수 대신 선언처를 가리킴.
- **문서** — `docs/mobile-build.md` 0절을 "확인 하나 · 준비 하나"로 재편(0-1 `main.py`는 이미
  커밋된 파일이므로 **확인** 절차 + 덮어쓰기 경고), `README.md`·`app.py`의 「확인 필요」에 카메라
  버퍼 재사용 항목 추가.

## 완료 기준 대조

| 기준 | 판정 | 근거 |
|---|---|---|
| 1 | 충족 | **라운드 1에서 재방전**(라운드 0의 근거는 공허했다 — 아래 주 참조). `tests/test_decode_backend.py`의 `no_cv2`(meta_path 블로커 + `sys.modules`에서 `cv2*` 제거) + **`fresh_qr_import`**(`photontcp.qr*` 제거로 신규 실행 강제) 아래: `import photontcp.qr` 성공 · 그것이 **캐시가 아닌 새 모듈 객체**임을 단언 · `encode_frame` 2D uint8 정상 · `decode_frame` → `None` · 손상 입력 3종 포함 never-raise · `active_decoder_backend() is None`. **되돌림 실측**: `decode.py` 상단에 `import cv2`를 되살린 복제 트리에서 전체 스위트 **1 failed, 239 passed, 1 skipped**(라운드 0에서는 같은 되돌림에 240 passed로 전부 초록이었다). |
| 2 | 충족 | 전체 스위트 247 passed·1 skipped(실패 0; 라운드 1에서 +7 신규). `python examples/qr_decode_bench.py` exit 0, `full >= base-only on every set: YES`. **full 디코드율 clean 100.00% / degraded 100.00%** — 코퍼스를 207→249프레임(5·8·9B 포함)으로 넓힌 뒤에도 M10 실측(100%/100%) 유지. |
| 3 | 충족 | `tests/test_decode_backend.py` — 가짜 백엔드 등록·선택 시 실제 호출(전달 이미지 객체 동일성까지 확인), `set_decoder_backend(None)` 후 `active_decoder_backend() == "cv2"` + 실 payload 왕복 성공. |
| 4 | 충족 | T02가 변경 전후 `--help` 출력을 저장·비교 → **diff 무출력**. `python examples/optical_link.py` exit 0(handshake=4 rounds, MATCH, both CLOSED). `python examples/optical_link.py --role sender` → **exit 2**(`error: --role requires --real`). |
| 5 | 충족 | `tests/test_peer_runner.py` — `OpticalChannel.pair(scale=6)` + 두 role 스레드(`hold=0.03`)에서 `established`·`messages_match`·`closed`·`passed` 전부 참, `timed_out` False. 연속 5회 실행 13 passed × 5(플레이키 없음). |
| 6 | 충족 | `tests/test_mobile_devices.py` — 스텁 주입 후 실제 `kivy_devices.py` 로드해 `issubclass(KivyDisplay, DisplaySink)`·`issubclass(KivyCamera, CameraSource)` 참. Kivy 미설치 실측: `import photontcp.mobile` 성공, `KivyDisplay`/`KivyCamera`/`PhotonTCPApp`/`main` 전부 `None`, `__all__ == []`. |
| 7 | 충족 | `buildozer.spec` — `package.name = photontcp`, `package.domain = org.photontcp`, `source.dir = .` + 진입점(`main.py` 고정 사실을 주석에 명시, 레포 루트에 실제 생성), `requirements`(python3·kivy·camera4kivy·gestures4kivy·numpy·segno·opencv + 포함 사유 주석), `android.permissions = CAMERA`, `android.archs = arm64-v8a`, `orientation = portrait`, api 33 / minapi 24 / ndk_api 24. configparser 파싱 검증 통과. |
| 8 | 충족 | `docs/mobile-build.md` — 1절 Docker 경로(Desktop 시동부터 마운트·빌드 명령), 2절 WSL2 경로(`wsl --install -d Ubuntu-24.04`부터 apt·JDK·venv·빌드), 4절 `adb` 획득(platform-tools)·USB 디버깅·`adb install`·logcat, 5절 로그 위치 3곳 + 증상 12행 표 + opencv 우회. |
| 9 | 부분 | **실빌드 미수행** — 사용자 판단으로 폴백 경로를 택했다(기준 9가 명시한 폴백). 기록한 것: 툴체인 실측(docker 29.4.2·데몬 정지, WSL 배포판은 `docker-desktop`뿐, adb·buildozer·gradle 부재, Kivy 미설치, 디스크 1.4T 여유)과 미충족 사전조건 3개(`camerax_provider` clone, Docker 기동 또는 WSL 배포판, `android.ndk` 확정). **남은 조각**: APK 경로·크기·소요 시간·opencv 레시피 통과 여부는 이 사이클에서 알 수 없다. |
| 10 | 충족 | `tests/test_qr_robustness.py::test_short_payloads_roundtrip` — 5·8·9B 각 20개(총 60프레임) 왕복 100%. `test_micro_qr_boundary_excluded`는 삭제하고 `test_encoder_never_emits_micro_qr`(모듈 수 ≥21 단언)로 교체. `tests/test_qr.py::test_roundtrip_short_payload` 추가. |
| 11 | 충족 | `tests/test_version_single_source.py`(**7건** — 라운드 1에서 파생 **순서**를 무는 2건 추가). `python -c "import photontcp; print(photontcp.__version__)"` → `0.10.0`. **갈라짐 실측**: 복제 트리에서 상수를 되살리면 2 failed, pyproject만 0.11.0으로 어긋내면 2 failed, pyproject만 정상 범프하면 5 passed. |
| 12 | 충족 | `docs/v1.0-signoff.md`의 신설 `## 2-B. 체크 B 변형 — 폰 + PC 조합`이 역할 배정·물리 배치·폰 실전 주의(밝기·화면 꺼짐 방지·자동회전·카메라 선택·`scale`/`hold` 일치)를 담고, **실측 수행이 다음 사이클 항목임을 인용 블록으로 명시**. 기존 체크 A·2-머신 PC 절차는 보존. |

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/mobile/__init__.py`, `photontcp/mobile/kivy_devices.py`, `photontcp/mobile/app.py`, `photontcp/optical/peer.py`, `main.py`, `buildozer.spec`, `docs/mobile-build.md`, `tests/test_decode_backend.py`, `tests/test_peer_runner.py`, `tests/test_mobile_devices.py`, `tests/test_version_single_source.py`, `docs/milestones/M11.md`(마일스톤 단계 산출물), `docs/reports/M11-impl.md`(본 보고서) |
| 수정 | `photontcp/qr/decode.py`, `photontcp/qr/encode.py`, `photontcp/optical/__init__.py`, `photontcp/__init__.py`, `examples/optical_link.py`, `examples/qr_decode_bench.py`, `tests/test_qr.py`, `tests/test_qr_robustness.py`, `pyproject.toml`, `.gitignore`, `README.md`, `docs/v1.0-signoff.md`, `docs/project-context.md`, `docs/conventions.md`, `docs/reports/retro.md` |
| 삭제 | (없음) |

**라운드 1이 실제로 건드린 파일**(위 표의 부분집합 — 반환분 처리):
`tests/test_decode_backend.py`(차단 1), `photontcp/__init__.py`·`tests/test_version_single_source.py`(권장 2),
`photontcp/mobile/kivy_devices.py`·`tests/test_mobile_devices.py`(권장 3, 사소 9),
`photontcp/optical/peer.py`·`photontcp/mobile/app.py`·`tests/test_peer_runner.py`(권장 4·5, 사소 10),
`docs/mobile-build.md`(권장 6), `photontcp/qr/decode.py`(사소 7 — docstring만),
`examples/optical_link.py`(사소 12), `buildozer.spec`(사소 13·14),
`docs/v1.0-signoff.md`·`README.md`(사소 15, 권장 3의 문서 몫), `docs/reports/M11-impl.md`(본 보고서).
`photontcp/qr/encode.py`·`tests/test_qr*.py`·`pyproject.toml` 등 나머지는 라운드 0 이후 무변경이다.

기준선 = 이 단계 시작 시점의 워킹트리. **아래 다섯은 이 사이클 이전의 미커밋 산출물 위에 얹혔다**:
`docs/v1.0-signoff.md`·`docs/project-context.md`는 git 기준 신규 파일이고, `docs/conventions.md`·
`.gitignore`·`docs/reports/retro.md`는 2026-09-08 **tide 구성 최신화**와 **3차 회고**의 결과로 이미
수정 상태였다(그 둘은 이 마일스톤의 태스크가 아니라 직전 세션의 산출물이며, 릴리즈 커버리지 체크가
"어떤 보고서도 설명하지 않는 변경"으로 advisory를 내지 않도록 여기서 함께 설명한다).

## 테스트 결과

아래 값은 **본 보고서를 포함한 모든 산출물이 나온 뒤의 마지막 측정**이다(`measure-after-artifacts`).

- `python -m pytest -q` → **247 passed, 1 skipped** (베이스라인 188 → 라운드 0에서 +52, 라운드 1에서
  **+7**, 회귀 0). 라운드 1 신규 7건: 버전 파생 순서 2 · `hold<=0` 거부 3(파라미터화) ·
  `messages` 쌍 검증 1 · 카메라 버퍼 소유권 1.
  skip 1건은 `test_exported_names_are_usable_when_kivy_is_present` — Kivy 미설치 환경의 반대 분기이며
  Kivy가 설치되면 실행된다(그 자리를 비워 두지 않으려고 양쪽 분기를 모두 뒀다).
- `python examples/qr_decode_bench.py` → **exit 0**, clean 249/249 = **100.00%**, degraded 249/249 =
  **100.00%**, `full decoder rate >= base-only rate on every set: YES`.
- `python examples/optical_link.py` → **exit 0** (핸드셰이크 → 양방향 MATCH → both CLOSED).
- `python examples/optical_link.py --role sender` → **exit 2**(argparse 거부, 계약 유지).
- `python -c "import photontcp.mobile as m; ..."` → 네 이름 `None`, `__all__ == []`, 크래시 없음.
- 광학 경로(`test_optical_channel` 5 + `test_optical_concurrency` 1 + `test_optical_pacing` 4 +
  `test_optical_ec` 3) **3회 연속** 13 passed(4.49/4.41/4.41초) — Micro-QR 변경 후에도 플레이키 없음.
- `tests/test_peer_runner.py` **5회 연속** 13 passed(3.0~3.2초, 라운드 0) — 신규 스레드 테스트 플레이키
  없음. 라운드 1에서 17 passed로 늘었고 재실행에서도 플레이키 없음.
- **라운드 1의 되돌림 실측 2건**(가드가 공허하지 않음을 증명):
  ⑴ `decode.py` 상단에 `import cv2` 복원 → 전체 스위트 **1 failed, 239 passed, 1 skipped**
  (같은 되돌림이 라운드 0에서는 240 passed였다 — 차단 1이 실제로 닫혔다).
  ⑵ `KivyCamera._own()`의 소유권 획득을 무력화 → `test_submitted_buffer_is_owned_...` **1 failed**.
  두 되돌림 모두 **스크래치 복제 트리**에서만 수행했고 작업 트리에는 흔적을 남기지 않았다(확인함).

**구현 중 발견·처리한 이슈**

1. **`micro=False`의 실 경로 영향 없음을 측정으로 확정** — QR 버전 상승은 b64 ≤ ~16자(원시 ≤12B)
   에서만 일어나고, PhotonTCP 실 프레임은 최소 22B 헤더(+2B nonce)라 항상 그 위다. segno designator
   비교로 22/24/32B가 동일 심볼임을 확인했다(추정이 아니라 실측).
2. **기존 코퍼스 하한 12B가 우연히 안전했던 것** — 12B는 내용에 따라 default에서도 Micro/일반이
   갈렸고(압축 잘 되는 b64는 M4), 랜덤 내용 덕에 100%였다. 이번 변경이 그 우연을 불변식으로 바꿨다.
3. **buildozer `version.regex`의 MULTILINE 미적용** — buildozer `get_version()`이 `re.search`를 플래그
   없이 부르는 것을 소스에서 확인하고 정규식 앞에 개행을 넣어 해결(실측으로 `0.10.0` 추출 확인).
   그 결과 버전 선언처가 늘지 않았다.
4. **마일스톤에 없던 빌드 필수 항목 둘** — `gestures4kivy`(camera4kivy 의존, p4a는 명시된 것만 빌드),
   `p4a.hook = camerax_provider/gradle_options.py`(camerax_provider는 pip 패키지가 아님). 스펙·문서·
   `pyproject.toml`에 반영했다.
5. **`main.py`가 필요하다는 사실** — p4a가 진입 파일 이름을 고정한다는 것을 buildozer 소스에서 확인해
   메인이 레포 루트에 생성했다(마일스톤의 파일 목록에 없던 항목).

## 미해결·후속 메모

1. **APK 실빌드가 미수행이다(최우선 후속).** 이 사이클은 스펙·문서·앱·어댑터까지 갖췄고 남은 것은
   실행이다. 다음 사이클 첫 항목: ① `camerax_provider` clone ② Docker Desktop 기동 또는 WSL2 일반
   배포판 설치 ③ `buildozer android debug` ④ 실패 시 `android.ndk` 조정 또는 opencv 우회. **opencv
   레시피가 통과하는지가 이 이식의 최대 불확실성으로 남아 있다**(p4a 이슈 #3203).
2. **실기 검증은 전부 미수행.** Kivy·camera4kivy 미설치, 안드로이드 실기 없음. 구체적으로 검증하지
   못한 것 — (a) 실제 Kivy 위젯 트리 렌더와 `Clock` 마샬링, (b) camera4kivy `Preview`의 실제 콜백
   시그니처·`connect_camera` 인자, (c) `colorfmt="luminance"` 텍스처의 Android GLES 수용 여부
   (거부되면 `KivyDisplay(..., colorfmt="rgb")` 한 줄 교체), (d) **`flip_vertical`/`flip_horizontal`
   기본값의 실기 정합성** — 상하/좌우가 뒤집힌 QR은 디코드되지 않으므로 **실기 첫 실행에서 가장 먼저
   확인할 항목**이다.
3. **앱 골격에는 자동 테스트가 없다.** 테스트 가능한 로직을 `run_peer`로 내렸기 때문에 의도된 결과지만,
   `app.py`의 상태 전이(Start/Stop, 권한 거부, camera4kivy 부재)는 스텁 수동 확인에 머문다.
4. **`register_decoder_backend`의 짝 API가 없다** — 해제·열거가 없어 테스트가 사설 dict를 스냅샷/복원해
   우회했다. 서드파티도 같은 문제를 만나므로 `unregister_decoder_backend()` 또는
   `registered_decoder_backends()` 추가를 후속으로 제안한다.
5. **`run_peer`에 취소 훅이 없다** — 앱이 `_CancellableChannel` 래퍼로 우회했다. `run_peer(...,
   should_stop=callable)` 같은 훅이 생기면 그 래퍼는 제거 가능하다(구조적 부채로 기록).
6. ~~**`hold=0.0`이 인메모리에서 실패하는 성질을 테스트로 고정하지 않았다**~~ **해결(라운드 1)** —
   느린 실패 경로를 도는 대신 `run_peer`가 `hold <= 0`을 **시작 전에 `ValueError`로 거부**하도록
   바꿔, 성질을 비싸게 관찰하는 대신 값싸게 집행한다(`tests/test_peer_runner.py` 3건).
7. **`camera4kivy`가 아카이브된 프로젝트다**(0.3.3, 2023-11 이후 유지보수 없음). `android.api = 33`
   제약도 여기서 온다. 유지보수되는 대체 카메라 레이어가 없어 그대로 채택했으나, 실빌드가 여기서
   막히면 대안 탐색이 별도 후속이 된다.
8. ~~**`docs/v1.0-signoff.md`의 테스트 개수가 사이클마다 밀린다**~~ **해결(라운드 1)** — 수를 빼고
   `python -m pytest -q`의 출력을 선언처로 가리키게 바꿨다. 이 메모가 예고한 그대로다.
9. **회고 표 상태 갱신이 남았다** — `docs/reports/retro.md` 최상단 섹션에서 이번에 닫힌 세 행
   (Micro-QR 원천 제거, `__version__` 불일치, `docs/v1.0-signoff.md` 미커밋)이 아직 `미반영`이다.
   impl은 회고 문서를 고치지 않으므로 다음 `/tide:retro`가 `반영`으로 옮겨야 한다.
10. **라운드 1이 이월한 사소 2건** — ⑴ **cv2 부재 시 프레임마다 실패 import 반복**: 완화하려면
    가용성을 캐시해야 하는데, 그것은 `active_decoder_backend()`가 stale 값을 보고하지 않는다는
    M11-T01의 설계 결정과 정면으로 맞바꾸는 일이라 반환 라운드에서 즉흥 결정할 사안이 아니다.
    ⑵ **`photontcp.optical`이 `peer` 경유로 `app`·`session`을 eager import**: 계층 경계가 흐려진
    자리이나, 고치려면 패키지 `__getattr__` 지연 재수출이라는 구조 변경이 필요하다. 둘 다 리뷰가
    "다음 사이클 후속"으로 분류한 항목이고, 기능 회귀는 없다.
11. **`copy_on_submit`의 필요성 자체는 여전히 미검증이다** — camera4kivy가 실제로 분석 버퍼를
    재사용하는지 확인할 기기가 없다. 기본을 "복사"로 둔 것은 **틀렸을 때의 비용이 비대칭**이기
    때문이다(불필요한 memcpy 한 장 vs. 원인 찾기 어려운 조용한 디코드 실패). 실기에서 재사용이
    없음이 확인되면 기본값을 되돌릴 수 있다.
12. **앱 UI 층(`MIN_HOLD` 클램프 등)에는 여전히 자동 테스트가 없다**(메모 3과 같은 이유 — Kivy
    미설치). `hold` 하한은 라이브러리 층(`run_peer`)이 집행하므로 UI 클램프는 방어 이중화이고,
    그쪽은 테스트로 고정돼 있다.
