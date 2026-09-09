# CHANGELOG

본 프로젝트의 모든 주목할 만한 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/) 를, 버전은 [Semantic Versioning](https://semver.org/) 을 따릅니다.

## [Unreleased]

## [0.13.0] - 2026-09-09

**빛만으로 실 통신이 성립했다.** 안드로이드 2대(폰 ↔ 태블릿)로 화면과 카메라를 교차 배치해 사인오프 체크 B(2-머신 왕복)를 **통과**시켰다. 그 과정에서 APK가 앱 코드를 담지 못하던 원인과 p4a 상류 버그를 특정해 닫았고, 실기 실측으로 확정된 항목을 문서에서 「확인 필요」 밖으로 내보냈다. 다만 **v1.0은 주장하지 않는다** — 사인오프 게이트의 다른 조건인 체크 A(단일 기기 셀프체크 수신율 ≥ 80%)가 **장비 부재**로 수행 불가로 남았다.

### Added

- **`tools/p4a-android-wheel-url.patch`** — python-for-android의 `PyProjectRecipe` 설치 단계가 `--platform` 태그를 넘기지 않아 안드로이드 휠을 받지 못하던 상류 버그의 최소 수정(34줄). 적용 절차는 `docs/mobile-build.md` 0-3절이 **정상 빌드 경로**로 적는다. 대가도 함께 적었다 — `--only-binary=:all:`을 무조건 붙이므로 **PyPI에 휠이 없는 sdist-only 순수 파이썬 의존성은 소스 빌드로 넘어가지 못한다**(현재 의존성 11개는 전부 휠이 있어 영향 없음).
- **`tests/conftest.py`** — 매 테스트 뒤 `reset_decoder_backend_probe()`를 부르는 autouse 안전망. 음성 probe 캐시가 테스트 간에 새는 것을 막는다(없으면 `test_qr.py` **12건이 실패**함을 되돌림으로 실측).

### Changed

- **`buildozer.spec`의 `source.include_exts`에서 `toml`을 제거** — `pyproject.toml`이 `source.dir`에 들어가면 p4a가 `use_setup_py` 경로로 전환해 **앱 코드를 통째로 APK에서 빼고 `main.py` 하나만 넣는다**. 빌드는 성공하는데 앱이 아무것도 못 하던 원인이었다. 대가: 기기에서 `__version__`이 `"0+unknown"`으로 보인다.
- **`app.py`의 `on_start` → `on_start_pressed`** — Kivy `App`의 라이프사이클 훅과 이름이 충돌해 v0.11.0부터 앱 시작 시 버튼 핸들러가 인자 없이 불렸다. 앱 내부 핸들러라 공개 계약은 아니다.
- **`fit_mode="contain"`** 로 교체(폐기예정 `allow_stretch`/`keep_ratio` 경고 2건 제거), `android.wakelock = True` + `WAKE_LOCK` 권한 추가, 최소 hold 하한 `MIN_HOLD = 0.01`.
- **`photontcp.optical`의 지연 재수출에 서브모듈 폴백** 추가(`importlib.util.find_spec` 게이트). 실재하지만 임포트할 수 없는 서브모듈은 `ImportError`가 속성 접근 밖으로 나가므로(`hasattr`가 삼키지 않는다) 가용성 확인은 가드된 이름(`Cv2Display is None`)으로 한다 — docstring에 계약으로 적었다.
- **실기 실측으로 확정된 항목을 문서에서 내렸다** — 위젯 트리·GLES `luminance` 수용·카메라 권한·wakelock 보유·카메라 경로 동작. `README.md`와 `app.py`/`kivy_devices.py` docstring의 「확인 필요」에서 빠졌다. 남은 미검증 둘은 camera4kivy 콜백의 **정확한 인자 목록**과 **분석 버퍼 재사용 여부**다.

### Fixed

- **v0.11.0 릴리즈 노트의 「뒤집힌 QR은 디코드되지 않으므로 flip 플래그가 실기 첫 확인 항목」은 사실이 아니다.** `cv2.QRCodeDetector`는 90/180/270° 회전과 **좌우·상하 거울상을 모두 디코드**한다 — 4개 페이로드 × 4개 EC 레벨 × 4개 변형 = **16건 전부 성공**했고, 전처리 캐스케이드 없이 기본 detector 단독으로도 읽었다. 따라서 `flip_horizontal`/`flip_vertical`은 **정규화 손잡이이지 정확성 요건이 아니며**(출하되는 cv2 백엔드 한정), 실기 진단의 첫 확인 항목도 아니다. 이 오류가 M11~M12 내내 실패 원인 탐색 공간을 잘못 좁혔다.

### Notes

- 전체 스위트 **326 passed / 2 skipped**, 회귀 0. 벤치 clean·degraded 각 100.00%, `full >= base-only on every set: YES`.
- **사인오프 체크 B 실측** — 실내 형광등, 거리 약 20 cm, 폰↔태블릿 교차 배치. 양방향 왕복 성립. 수신 실패의 대부분은 **카메라 정렬**에서 왔다. 기기가 이미 분리되어 이 수치는 **단일 관찰**로 남으며 재현되지 않았다. wakelock은 「세션보다 오래 화면이 켜져 있는가」라는 관찰이 아니라 **락 보유**라는 대리 지표로 확정했다.
- **v1.0 게이트 (a) 미해결** — 체크 A에는 PC에 붙는 카메라가 필요하다. 선택지는 셋이고 어느 것도 구현이 정할 사안이 아니다: USB 웹캠 확보 / 앱에 셀프체크 모드 추가 / 게이트 정의 재검토(체크 B 통과가 실질적으로 대체하는지). 상세는 `docs/reports/M13-review.md`.
- **후속(리뷰 이월)**: 앱의 캡처 경로 관측 수단 부재(광학 문제와 캡처 경로 문제를 밖에서 구분할 수 없다), `p4a.source_dir` 미배선(`.buildozer/` 캐시를 지우면 패치가 조용히 사라진다), 고밀도 화면에서의 위젯 잘림, 상류 p4a 제출.

## [0.12.0] - 2026-09-09

v1.0 사인오프를 목표로 한 사이클. **APK 실빌드를 처음으로 끝까지 돌렸고, 실패 원인을 상류 버그 한 지점으로 특정**했다. 하드웨어·툴체인이 필요 없는 작업 넷은 전부 완료됐다. 사인오프 실측(체크 A·체크 B)은 APK가 나오지 않아 수행하지 못했으므로 **v1.0은 이번에도 주장하지 않는다**.

### Added

- **빌드 사전조건 하니스** `examples/mobile_build_preflight.py`: Docker 데몬·WSL2 배포판·`adb`·`buildozer`·루트 `main.py`·`camerax_provider/`·`buildozer.spec` 필수 키·`version.regex` 추출·디스크 여유·`java`·`gradle` 11개 항목에 `OK`/`WARN`/`BLOCK`을 낸다. **「도구가 없다」와 「도구가 있는데 실패한다」를 다른 문구로 구분**하고(대처가 다르므로), 한 점검이 예외로 죽어도 나머지가 계속 돈다. 종료 코드는 BLOCK 유무를 따른다.
- **`reset_decoder_backend_probe()`** (공개 API): 모듈이 캐시하는 환경 probe(cv2 부재 여부, 대체 detector 종류)를 무효화한다. `register_decoder_backend()`·`set_decoder_backend()`가 자동으로 호출한다.
- **`app.py` 상태 전이 테스트** 45건 — `format_summary()`·`_read_settings()`·`on_event()`·`_make_preview()`. Kivy 미설치 환경에서는 최소 스텁을 심어 **실제 모듈**을 로드해 검증하고, 설치 환경에서는 실물로 돈다(양쪽 모두 통과 실측).
- **문서 버전 복제 재발 가드** `tests/test_docs_no_version_dup.py` 15건, **레이어 경계 가드** `tests/test_optical_import.py` 8건(새 프로세스 격리).

### Changed

- **cv2 부재 probe 비용 제거**: 실패한 `import cv2`는 `sys.modules`에 캐시되지 않아 프레임마다 `sys.path`를 전수 탐색했다. 이제 **부재만** 캐시한다 — 그 방향으로는 낙관적 stale(cv2가 사라졌는데 `"cv2"`로 보고)이 구조적으로 불가능하고, 보수적 방향만 남으며 그것은 위 무효화 지점이 받는다. opencv 없는 안드로이드 빌드가 문서가 권하는 경로이므로 그 비용이 정확히 거기 얹혔다.
- **`photontcp.optical`이 상위 계층을 더는 끌어오지 않는다**: `run_peer`/`PeerResult`를 PEP 562 모듈 `__getattr__`로 **지연 재수출**한다. `import photontcp.optical`이 `photontcp.app`·`photontcp.session`을 로드하던 것이 사라졌다(전송 계층이 응용 계층에 의존하던 역방향 자리). `__all__`·`from … import run_peer`는 불변이다.
- **문서의 버전 복제 제거**: `docs/conventions.md`·`docs/project-context.md`·`docs/mobile-build.md` 세 곳이 버전 값을 복제해 릴리즈마다 낡았다. 값을 지우고 선언처(`pyproject.toml`)를 가리키게 했다 — 갱신 단계를 늘리는 것이 아니라 복제를 없애는 방향이다.
- **`buildozer.spec` 실측 반영**: `warn_on_root = 0`(컨테이너가 root로 돌아 대화형 확인에서 죽는 것을 막는다 — 이전 주석의 *"비-root로 돈다"* 는 서술이 실측으로 반증됐다), `android.ndk` 주석을 **r28c 확인됨**으로 갱신.
- **`docs/mobile-build.md` Docker 절차를 실제로 돌아가는 형태로 교체**: named volume · `-d --memory` · `--rm` 제거. 이전 명령은 이번에 닫은 장애 넷을 그대로 재현하는 구성이었다.

### Notes

- 전체 스위트 **319 passed / 1 skipped**(환경에 Kivy가 설치되면 2). v0.11.0 기준선 247 → **+72 신규**, 회귀 0. 벤치: clean 249/249 = 100%, degraded 249/249 = 100%.
- **APK 빌드 실측** — 문서화된 Docker 경로가 비대화형에서 깨지는 지점 **다섯**을 만나 **넷을 닫았다**: ① root 프롬프트로 인한 즉시 종료 ② NDK 압축 해제의 덮어쓰기 프롬프트 ③ 호스트 캐시 마운트로 인한 메모리 압박 ④ 컨테이너 사망 시 로그 소실. 재현 절차는 `docs/mobile-build.md` 5-4절.
- **미해결 하나가 APK를 막는다**: p4a가 의존성 해석에서 안드로이드 전용 휠의 URL을 요구사항에 넣고, 정작 `--platform` 없이 **호스트 pip**으로 설치해 자기가 넣은 요구사항을 거부한다. 걸리는 경로는 camera4kivy → requests → charset-normalizer. 우회 둘(버전 핀, `--skip-prebuilt`)을 시도했고 **둘 다 듣지 않아 되돌렸다**.
- **opencv는 통과했다**: `libopencv_gapi.so` 링크와 `opencv_python3` 타깃이 빌드됐다. v0.11.0이 *"이 이식의 최대 불확실성"* 으로 적었던 opencv × numpy 조합은 이 NDK(r28c)에서 발현하지 않았다. **실패는 네이티브 컴파일이 아니라 그 뒤의 순수 파이썬 의존성 설치 단계**다.
- **데스크톱 선검증으로 앞당겨 확정된 것 둘**: `colorfmt="luminance"` 텍스처가 데스크톱 GL 백엔드에서 수용되고, `connect_camera(enable_analyze_pixels=…, camera_id=…)` 키워드가 camera4kivy 0.3.3에서 그대로 받아들여진다. **안드로이드 GLES와 실제 픽셀 콜백은 여전히 미검증**(개발 머신에 웹캠 없음).
- **v1.0 잔여**: ① 위 상류 버그 우회 ② APK 산출 + 폰 기동 스모크 ③ 체크 A(수신율 ≥ 80%)·체크 B(폰+PC 왕복) 실측. 5사이클 연속 이월이지만 성격이 바뀌었다 — 이전 넷은 *"두 번째 기기가 없어서"* 였고 이번엔 **기기는 있는데 빌드가 막혀서**다. 상태는 `docs/v1.0-signoff.md` 3절.

## [0.11.0] - 2026-09-09

모바일(Android) 1급 지원. 안드로이드 폰이 자기 화면으로 QR을 띄우고 자기 카메라로 상대 QR을 잡아 **한 피어로 동작**할 수 있는 상태가 됨 — v1.0 사인오프의 체크 B(2-머신 왕복)에 두 번째 PC가 없어도 되는 경로가 열림. 세션·신뢰성·앱 계층은 한 줄도 바뀌지 않음(`Channel` 인터페이스만 의존). 공개 계약(`decode_frame` 시그니처·None 계약, CLI 인자 집합·종료 코드) 불변.

### Added

- **`photontcp.mobile` 패키지**: `KivyDisplay`(`DisplaySink`) + `KivyCamera`(`CameraSource`) 어댑터와 Kivy 앱 골격(`PhotonTCPApp`). `KivyDisplay.show()`는 렌더하지 않고 pending 슬롯 1개에 packing만 한 뒤 메인 스레드 렌더를 예약해 광학 송신 스레드를 막지 않음. `KivyCamera`는 푸시 모델로 최신 1장만 보관(큐 없음 = 지연 누적 원천 차단)하고 제출 시점에 버퍼 소유권을 가져옴(`copy_on_submit`, 카메라 버퍼 재사용으로 인한 프레임 찢김 차단). Kivy 미설치 데스크톱에서도 `import photontcp.mobile`은 성공하며 어댑터·앱 진입점은 `None`으로 노출(`optical/__init__.py`의 cv2 가드와 동형).
- **`photontcp.optical.peer.run_peer()`**: 핸드셰이크 → 메시지 교환 → 정상 종료 구동 루프를 UI·argparse와 분리한 헤드리스 드라이버. CLI 예제와 모바일 앱이 **같은 코드**를 사용. 진행 상황은 `on_event(kind, detail)` 콜백으로만 흐르고(kind 집합은 `EVENT_KINDS` 상수) 라이브러리는 print하지 않음. `Channel` 인터페이스만 요구해 인메모리 `OpticalChannel.pair()` 위에서 하드웨어 없이 왕복 검증 가능.
- **디코더 백엔드 seam**: `register_decoder_backend` / `set_decoder_backend` / `active_decoder_backend`. `qr/decode.py`가 cv2를 **지연 import**해 cv2 없는 플랫폼에서도 패키지가 import되고 `decode_frame`은 예외 대신 `None` 반환(None 계약·never-raise 불변). p4a의 무거운 opencv 레시피 실패에 대비한 위험 분산이며, 기존 cv2 경로(전처리 캐스케이드 + 대체 detector 폴백)는 알고리즘 무변경으로 `"cv2"` 백엔드에 이식.
- **APK 빌드 파이프라인**: `buildozer.spec`(진입점 `main.py`, `requirements`에 opencv 포함 사유 주석, `android.permissions = CAMERA, WAKE_LOCK`, `android.wakelock = True`, `android.archs = arm64-v8a`, api 33/minapi 24) + `docs/mobile-build.md`(Docker 경로·WSL2 경로를 각각 명령 단위로, `adb install` 절차, 실패 진단표, opencv 우회).
- 테스트 59건 추가 — 백엔드 seam 13, `run_peer` 17, 모바일 어댑터 18(+1 skip), 버전 단일 원본 7 등.

### Changed

- **Micro-QR 블라인드스폿을 인코더에서 원천 제거**: `encode_frame`이 `segno.make(..., micro=False)`로 **일반 QR만** 내보냄. `cv2.QRCodeDetector`는 Micro QR을 디코드하지 못하므로 이것은 튜닝 값이 아니라 **코덱 불변식**이며, `micro`를 인자로 열지 않음. 5·8·9바이트 페이로드가 이제 100% 왕복하고, 코퍼스 하한(12B) 제약을 테스트·벤치 양쪽에서 해제(207→249프레임). 실 패킷 경로(≥22B)의 심볼 크기는 불변임을 segno designator 비교로 실측 확인.
- **버전 선언처를 하나로**: `photontcp/__init__.py`의 하드코딩 상수(`0.1.0`, pyproject와 불일치)를 제거하고 `pyproject.toml`에서 파생. 소스 트리에서는 파일을 먼저 보고(설치 배포판에서만 `importlib.metadata`), 둘 다 없으면 `"0+unknown"`. 두 값이 갈라지면 실패하는 테스트가 이를 기계적으로 고정.
- `examples/optical_link.py`가 세션 구동을 `run_peer`에 위임하고 출력·종료 코드만 담당. 튜닝 상수도 `photontcp.optical.peer`에서 재수출해 실모드와 인메모리 데모가 갈라지지 않게 함. **CLI 계약은 완전 불변**(`--help` 인자 집합 diff 무출력, 기본 실행 exit 0, `--role` 단독 exit 2).
- `run_peer`가 `hold <= 0`과 잘못된 `messages` 형태를 `ValueError`로 거부 — 0 페이싱은 채널의 캡처 스레드를 굶겨 **반드시 실패하는** 세션이므로 돌리지 않고 막음.

### Notes

- 신규 59 테스트 추가(전체 247 passed·1 skipped, 회귀 0). 벤치: clean 249/249 = 100%, degraded 249/249 = 100%, `full >= base-only on every set: YES`.
- **APK 실빌드는 미수행** — 마일스톤이 규정한 폴백 경로(툴체인 상태·미충족 사전조건 기록)를 적용. 스펙·문서·앱·어댑터까지 갖췄고 남은 것은 실행이며, **opencv p4a 레시피가 통과하는지가 이 이식의 최대 불확실성**으로 남음.
- **실기 검증 전무**: Kivy·camera4kivy 미설치, 안드로이드 기기 없음. 미검증 항목 — camera4kivy 콜백 시그니처·RGBA 행 순서·분석 버퍼 재사용 여부, Android GLES의 `luminance` 텍스처 수용, `flip_vertical`/`flip_horizontal` 기본값 정합성, wakelock 실동작. **뒤집힌 QR은 디코드되지 않으므로 flip 플래그가 실기 첫 확인 항목.**
- **v1.0은 이번에도 주장하지 않음** — 실 카메라 셀프체크 수신율 ≥80% + 2-머신 왕복 사인오프는 여전히 미수행. 폰+PC 조합 절차가 `docs/v1.0-signoff.md` 2-B에 추가됨.
- **후속(리뷰 이월)**: cv2 부재 시 실패 import 반복(가용성 캐시 ↔ `active_decoder_backend()` live 보고의 트레이드오프), `photontcp.optical`의 eager `app`/`session` import, `unregister_decoder_backend()`/`registered_decoder_backends()` 짝 API, `run_peer(should_stop=…)` 취소 훅.

## [0.10.0] - 2026-06-23

cv2 QR 디코드 견고화(로드맵 후속). 카메라가 잡은 QR 프레임의 디코드 성공률을 끌어올려, v1.0 사인오프의 셀프체크 수신율 게이트 통과 여유를 키움. 공개 디코드 API·시그니처 불변.

### Added

- **전처리 변형 캐스케이드** (`decode_frame`): 저비용→고비용 변형을 결정적으로 시도하고 첫 성공에서 단락 — (1) 원본 그레이스케일, (2) Otsu 이진화, (3) 샤프닝 후 이진화, (4) 이진화·샤프닝 결과의 2.0×/3.0× 업스케일. 깨끗한 프레임은 변형 1에서 즉시 성공(핫패스 추가비용 0), 나머지는 지연 평가.
- **대체 detector 폴백**: 1차 `cv2.QRCodeDetector`가 모든 변형에서 실패하면 빌드가 제공하는 대체 detector(`cv2.wechat_qrcode_WeChatQRCode` 우선, 없으면 `cv2.QRCodeDetectorAruco`)로 동일 변형을 재시도해 콘텐츠 의존 블라인드스폿을 회복. 둘 다 없는 환경은 폴백을 조용히 생략(캐스케이드 단독 동작).
- **디코드 견고성 벤치/테스트**: `examples/qr_decode_bench.py`(하드웨어 불필요, base-only vs 풀 디코드율 비교), `tests/test_qr_robustness.py`(코퍼스 임계 회귀 가드 + 블라인드스폿 회복 단언).
- **광학 채널 EC 견고화**: `OpticalChannel(error=…)`의 트레이드오프 문서화 + EC 배선 회귀 테스트(`tests/test_optical_ec.py`). 기본값 `"m"` 유지.

### Changed

- **채널 프레이밍 nonce 1→2바이트**: 디코드 견고화로 nonce 폭이 QR 콘텐츠 블라인드스폿에서 분리됨에 따라, M9가 보류한 다중 바이트 nonce를 2바이트(mod-65536)로 확대. 윈도우 dedup이 실질 차단하던 래핑 거짓-dedup 코너를 산술적으로도 제거. 1B/2B 디코드율 스윕 양쪽 100%로 재검증.

### Notes

- 신규 13 테스트 추가(전체 188, 회귀 0). 벤치 측정: 깨끗 코퍼스 base 96.1%→풀 100%, 열화 코퍼스 97.1%→풀 100%.
- **디코드 핫패스 불변**: 깨끗한 QR은 변형 1에서 단락하므로 추가 전처리 비용이 없음. None 계약·never-raise 불변.
- **후속(리뷰 권장)**: 작은 페이로드의 Micro-QR 한계는 인코더가 원천(짧은 데이터에 Micro QR 생성) — 인코더에서 일반 QR 강제로 원천 제거 가능(실 패킷은 ≥22B라 비영향). 코어 환경은 대체 detector 부재 시 회복폭 축소 → 실 배포에 contrib 권장.
- **v1.0 잔여(수동)**: 실 카메라 셀프체크 수신율 ≥80% + 2-머신 왕복(ESTABLISHED→MATCH→CLOSED) 사인오프는 미수행 — 통과 시 v1.0.0 승격.

## [0.9.0] - 2026-06-22

실물 광학 링크 하드닝(로드맵 6단계 하드닝). `OpticalChannel`의 실 카메라 경로를 동작·검증 가능한 상태로 보강하고, 실 하드웨어 검증 하니스를 추가. v1.0("빛만으로 실 통신") 승격은 수동 하드웨어 사인오프 통과를 전제로 분리.

### Added

- **디스플레이 프레임 페이싱**: `OpticalChannel(hold=…)` — 연속 표시 사이 최소 간격(단조 시계)을 보장해 실 카메라가 각 QR을 잡을 시간을 확보. `hold=0`(기본)은 동작 변화 없음.
- **`Cv2Camera` 신선 프레임**: `CAP_PROP_BUFFERSIZE=1`(best-effort) + 고정 한도 드레인(`drain_to_latest`)으로 드라이버 버퍼 staleness 완화. 선택적 해상도 힌트(`width`/`height`).
- **실물 검증 하니스**: `examples/optical_selfcheck.py`(한 기기 반이중 — 화면→자기 카메라 수신율 PASS/FAIL), `examples/optical_link.py --real --role {sender,receiver}`(2-머신 실 왕복).

### Changed

- **재캡처 dedup 윈도우화**: 단일 last-delivered 슬롯 → 최근 N(8) 프레임 deque+set. 순서 흔들림(이전 프레임 재캡처)에도 거짓 인도/누락 없이 1회 인도.
- **파라미터 가드**: `poll_interval<=0`은 양수 하한으로 클램프(캡처 루프 busy-spin 차단), `hold<0`은 0으로.

### Notes

- 신규 3 테스트 추가(전체 175, 회귀 0). 상위(세션·신뢰성·앱·코덱) 계층 무수정 — `Channel` 인터페이스만으로 채널 보강.
- nonce는 1바이트 유지(다중 바이트는 한 프레임을 cv2 QR detector의 콘텐츠 의존 블라인드스폿에 빠뜨려 디코드 회귀). 윈도우 dedup + ARQ 특성(재전송 상한·고유 seq)이 결합해 래핑 거짓 dedup을 실질 제거.
- **v1.0 잔여(수동)**: 실 카메라 셀프체크 수신율 ≥80% + 2-머신 왕복(ESTABLISHED→MATCH→CLOSED) 사인오프, cv2 디코드 견고화(수신율↑), 조명/정렬/`hold`·`scale` 튜닝. 통과 시 v1.0.0 승격.

## [0.8.0] - 2026-06-22

실물 광학 채널(로드맵 6단계). QR 프레임을 화면에 표시하고 카메라로 캡처하는 `OpticalChannel`을 추가 — 상위 계층은 무수정으로 빛 위에서 동작(v1.0 후보의 기술 전제).

### Added

- **`OpticalChannel`** (`photontcp/optical/channel.py`): 디스플레이+카메라를 `Channel`로 감싸는 전이중 광학 채널. 백그라운드 캡처 스레드가 프레임을 디코드하고, **1바이트 롤링 nonce 채널 프레이밍**으로 재캡처를 dedup(연속 동일 ARQ 패킷은 nonce가 달라 각각 인도). `OpticalChannel.pair()`로 하드웨어 없는 인메모리 전이중 페어 생성.
- **디바이스 추상** (`photontcp/optical/devices.py`): `DisplaySink`/`CameraSource` 인터페이스 + 인메모리 페이크 `MemoryDisplay`/`MemoryCamera`/`memory_device_pair()`(유휴 시 같은 프레임 재반환으로 카메라 재캡처 모사) — 채널 로직을 하드웨어 없이 결정적으로 검증.
- **cv2 실물 어댑터** (`photontcp/optical/cv2_devices.py`): `Cv2Display`(`cv2.imshow`)/`Cv2Camera`(`cv2.VideoCapture`). import는 하드웨어·창 비접촉, cv2 부재 시 패키지 re-export는 가드.
- 광학 데모 예제(`examples/optical_link.py`, 기본 인메모리 / `--real` 실 디스플레이), 광학 테스트 6건(왕복·재캡처 dedup·연속 동일 패킷·종료·세션 통합·동시성 정확성).

### Notes

- **M7 권장 3 해소**: 양방향 동시 송수신에서 "보낸 집합 == 받은 집합"을 단언해 캡처 스레드의 동시 `decode_frame`(스레드로컬 detector) 정확성을 스모크 초과로 검증. 전체 172 테스트 통과(회귀 0).
- 상위(세션·신뢰성·앱) 계층 무수정 — `Channel` 인터페이스만으로 채널 교체. `ImageLoopbackChannel`은 시뮬레이션 레퍼런스로 보존.
- **잔여(v1.0)**: 실 카메라 캡처 왕복은 미검증(`--real`은 디스플레이 절반만 실물). 실 카메라 왕복·VideoCapture 버퍼 staleness·정렬/조명 튜닝은 v1.0 승격 전 수동 검증 대상.

## [0.7.0] - 2026-06-19

신뢰성 정리/하드닝. M1~M6 누적 리뷰 권장을 한데 모아 견고성·캡슐화·입력 방어·스레드 안전성을 강화(동작 보존, 회귀 0).

### Added

- `RtoEstimator.clone()` + 공개 설정 접근자(`initial_rto`/`min_rto`/`max_rto`).
- `Session(control_rto=, max_control_retries=)` 노출 · `Session.acked_bytes(stream_id)` · `Session.data_failed_streams()`.
- `ArqEvent.SEND_FAILED`(데이터 재전송 상한 초과 시) · `ArqEndpoint.acked_bytes`/`is_failed`.
- `QRCapacityError`(단일 QR 용량 초과를 명확한 예외로).

### Changed

- **ARQ 견고성**: NACK를 구멍당 1회만 보내 NACK 스톰 방지 · 데이터 재전송 상한(`max_retx`)으로 무한 재전송 차단.
- **세션 견고성**: 데이터 평면 트래픽이 세션 idle 타이머를 갱신(`note_data_activity`) · 종료 진행 중(FIN_WAIT/CLOSE_WAIT) 타임아웃은 `CLOSED`로 일관 보고.
- **입력 방어**: `FileReceiver`가 OFFER 필드(name/size/sha256)를 검증해 손상 OFFER를 거부.
- **스레드 안전성**: 루프백 채널 RNG를 락으로 보호 · QR `cv2.QRCodeDetector`를 스레드로컬로(단일 스레드 결정성·동작 보존).
- **앱 정리**: `ChatSession.received`가 복사본 반환 · 파일 전송 전용 stream(`FILE_STREAM_ID=2`, 채팅과 병행) · `FileSender.progress`를 ACK된 바이트 기준으로.

### Notes

- 하드닝 테스트 27건 추가(전체 166). 스레드 안전성은 스모크 수준 검증 — 실제 동시 송수신 정확성은 실물 광학(카메라 스레드) 마일스톤에서 본격 검증 예정.

## [0.6.0] - 2026-06-19

파일 전송(로드맵 5단계). 신뢰성 있는 단방향 파일 전송 + 앱 레벨 완료 핸드셰이크.

### Added

- **파일 전송 앱** (`photontcp/app/file.py`): `FileSender`/`FileReceiver` — OFFER(name·size·sha256) → ACCEPT → CHUNK* → DONE → ACK/NACK. 전체 파일 **SHA-256 무결성 검증**, 진행률, 손실 채널에서 ARQ 재전송으로 무손실 전달.
- **앱 레벨 완료 핸드셰이크 = flush-on-close 보장**: 송신측은 수신측 FILE_ACK(신뢰성 스트림)를 받은 뒤에만 종료 → 전 청크 전달·검증 보장(M4 리뷰 후속 해소).
- **파일 프레임 코덱** (`photontcp/app/file_codec.py`): 타입 구분 길이접두 프레임(제어 JSON + 청크 바이너리 인터리브), `FileFrameReassembler`, `sha256_hex`.
- 파일 전송 예제(`examples/file_loopback.py`), 파일 테스트 30건(코덱·통합·무결성 실패·진행률·QR 채널 위 전송).
- 회고 문서(`docs/reports/retro.md`) — M1~M5 누적 회고.

### Notes

- Session 계층 무수정(채팅처럼 순수 추가). 파일 기본 stream_id는 채팅 기본(1)과 공유(병행 시 인자로 분리). 양방향·다중 파일·재개(resume)는 후속. M3/M4 잔여 리뷰 권장은 "신뢰성 정리 마일스톤"으로 이월.

## [0.5.0] - 2026-06-19

QR 코덱 실물 통합(로드맵 4단계). 패킷이 실제 QR 이미지로 인코딩/디코딩되어 채널을 통과.

### Added

- **QR 코덱** (`photontcp/qr/`): `encode_frame`(segno로 bytes→QR 그레이스케일 numpy 이미지), `decode_frame`(OpenCV `QRCodeDetector`로 이미지→bytes). 패킷 바이트를 base64로 ASCII-safe 래핑해 바이너리 무결성 보장.
- **ImageLoopbackChannel** (`photontcp/channel/image_loopback.py`): QR 이미지를 메모리로 주고받는 전이중 채널 — 모든 프레임이 실제 QR 인코드/디코드를 거침. 프레임 단위 loss/dup + 선택적 이미지 degrade(노이즈/블러로 EC 견딤 테스트).
- 기존 세션·ARQ·스트림·채팅 스택이 **수정 없이** QR 이미지 위에서 동작(`Channel` 인터페이스만 구현). QR 루프백 예제(`examples/qr_loopback.py`).
- QR 테스트 23건(코덱 라운드트립·바이너리 무결성·풀스택 통합; `pytest.importorskip`로 라이브러리 부재 시 skip).

### Dependencies

- 광학 코덱용 선택 의존성 도입: `segno`(QR 생성), `opencv-python`(QR 디코드), `numpy`. 핵심 전송 스택은 여전히 표준 라이브러리만 사용하며, QR은 채널 계층에서만 필요(`pip install photontcp[qr]` / `[optical]`).

### Notes

- 단일 QR 프레임 용량 한계가 있어 `max_payload`는 QR 용량 내로 유지해야 함. 실제 화면/카메라(실물 광학)는 후속 마일스톤. M3/M4 리뷰 권장(NACK 억제·flush-on-close 등) 계속 이월.

## [0.4.0] - 2026-06-19

스트림 다중화 + 채팅 앱(로드맵 3단계) — **기본 통신 완성**.

### Added

- **스트림 다중화** (`photontcp/stream/`): `StreamMux` — 하나의 세션 위에 stream_id별 독립 ARQ. 한 스트림의 손실/구멍이 다른 스트림을 막지 않음(HOL 블로킹 제거). `stream_id=0`=제어, `≥1`=앱 스트림, implicit open + 개시자/응답자 패리티 할당.
- **채팅 앱** (`photontcp/app/`): `ChatSession` — 신뢰성 스트림 위 길이접두(4B)+JSON 메시지(`{msg_id, timestamp, text}`). `StreamReassembler`로 청크 경계 무관 재조립, 유니코드 보존. 손실 채널 양방향 채팅 예제(`examples/chat_loopback.py`).
- `Session` 스트림 API: `open_stream()` / `send_on()` / `recv_on()` / `recv_all()`.
- 다중화·채팅 테스트 23건(mux 격리·implicit open·패리티 / 코덱 재조립 / 손실 양방향 채팅).

### Changed

- `Session` 데이터 경로를 단일 ARQ → `StreamMux`로 통합. 레거시 `send()`/`recv()`(기본 스트림 1)는 그대로 보존(M2·M3 회귀 없음). 데이터 ACK가 stream_id≥1을 달아 핸드셰이크 ACK와 라우팅만으로 구분(ACK 분기 단순화).

### Notes

- flush-on-close·반이중(half-close)·per-stream 수명주기, M3 리뷰 권장(NACK 억제 등)은 후속(M5 파일 전송/정리 마일스톤)으로 이월.

## [0.3.0] - 2026-06-19

신뢰성 계층(로드맵 2단계). 손실·중복·순서뒤바뀜 채널 위에서 신뢰성 있는 순서 보장 전송.

### Added

- **신뢰성 계층** (`photontcp/reliability/`): Selective Repeat(SR) ARQ.
- **SR ARQ 엔진** (`ArqEndpoint`): 송신 슬라이딩 윈도우 + 선택 재전송, 수신 재정렬·중복제거 버퍼, 누적 ACK + 선택 NACK, 기본 흐름 제어(상대 광고 윈도우 반영).
- **적응형 RTO** (`RtoEstimator`): Jacobson/Karels SRTT/RTTVAR, min/max 클램프, 타임아웃 지수 백오프, Karn 규칙 RTT 샘플링.
- **32비트 wraparound-safe 시퀀스 산술** (`serial`): RFC1982 류 시리얼 번호 비교.
- **제어 패킷 재전송 + 수립 타임아웃**: SYN/SYN_ACK/FIN 손실 시 RTO 재전송, 재시도 한도 초과 시 `CONNECT_FAILED`/`TIMED_OUT`. 핸드셰이크 최종 ACK 손실도 복구.
- **Session 신뢰 데이터 경로**: `Session.send(data)` / `Session.recv()` — 손실 채널에서 바이트열 무손실·순서 전달. 손실 채널 신뢰 전송 예제(`examples/reliable_loopback.py`).
- 신뢰성 테스트 42건(serial·RTO·ARQ 엔진·신뢰성 세션 통합).

### Changed

- 세션 상태머신/드라이버가 제어 재전송과 ARQ 데이터 경로를 통합(M2 무손실 동작은 회귀 없이 유지).

### Notes

- M1 리뷰 권장(seq mod 2³²)·M2 리뷰 권장(제어 재전송·수립 타임아웃) 해소. NACK 다중 블록·반이중(half-close)·스트림 다중화·패킷 간 FEC는 후속(M4/최적화)으로 이월.

## [0.2.0] - 2026-06-19

세션 계층(로드맵 1단계). M1 전송 토대 위에 연결 지향 세션 관리를 추가.

### Added

- **세션 계층** (`photontcp/session/`): I/O와 분리된 순수 세션 상태머신 + 동기 펌프 드라이버(백그라운드 스레드 없음).
- **3-way 핸드셰이크**(SYN/SYN_ACK/ACK): `session_id` 합의 + 초기 시퀀스(ISN) 교환.
- **graceful 종료**(FIN/FIN_ACK): 대칭 auto-close — 단일 `close()`로 양쪽이 CLOSED에 수렴, 수동측에 `PEER_CLOSED` 통지.
- **하트비트/타임아웃**: 유휴 시 HEARTBEAT 송출, 무수신 `idle_timeout` 초과 시 `TIMED_OUT` + CLOSED.
- **주입 가능한 클럭**: `Clock` 인터페이스, 테스트용 `ManualClock`(가상 시간), 실운영용 `MonotonicClock`. 하트비트·타임아웃을 real sleep 없이 결정적으로 검증.
- `Session` 고수준 API(`connect`/`close`/`pump`/`run_until`, `state`/`is_established`/`is_closed`) + 세션 데모 예제(`examples/session_loopback.py`).
- 세션 단위 테스트 17건(핸드셰이크·종료·하트비트·타임아웃).

### Notes

- M2 범위는 무손실 `LoopbackChannel` 가정. 제어 패킷 손실 재전송·수립 타임아웃, `seq`/`ack` 범위 정책(mod 2³²), 스레드 안전성, 클럭 latency/jitter는 M3(ARQ)로 이월.

## [0.1.0] - 2026-06-19

최초 릴리즈. PhotonTCP의 최하위 두 계층(채널 추상화 + 패킷 직렬화) 토대.

### Added

- tide 개발 사이클 골격 초기화 (kickoff): 규약·마일스톤/리포트 구조·CHANGELOG.
- **패킷 계층**: 22바이트 고정 헤더(big-endian) + CRC32 무결성. `Packet` 데이터클래스의 `pack()`/`unpack()`, 예외 계층(`PacketError`/`ChecksumError`/`MalformedPacketError`).
- **패킷 타입·플래그**: `PacketType`(SYN/SYN_ACK/ACK/DATA/NACK/FIN/FIN_ACK/HEARTBEAT), `Flags`, `PROTOCOL_VERSION`.
- **채널 계층**: 교체 가능한 `Channel` 추상 인터페이스.
- **LoopbackChannel**: 메모리 큐 기반 전이중 가상 채널 + 노이즈 시뮬레이션(loss/dup/corrupt/reorder), seed 기반 결정적 재현.
- 단위 테스트(13건) 및 에코 예제(`examples/echo_loopback.py`).
