# M12 완료보고서 (impl)

## 개요

M12 "v1.0 사인오프 실측"의 9개 태스크 중 **하드웨어·툴체인이 필요 없는 4개(T01~T04)를 전부
구현**하고, **T05(APK 실빌드)는 실제로 끝까지 돌려 실패 원인을 상류 버그로 특정**했으며, 그에
의존하는 **T06~T09는 미수행**이다. 사용자 판단은 "실빌드 진행 + 실기 사용 가능"이었고 빌드를
실행했으나 APK가 나오지 않아 실기 태스크에 착수할 수 없었다.

**이번 사이클의 실질적 성과는 T05의 실패 그 자체다.** M11이 "최대 불확실성"으로 지목한
**opencv 레시피가 실제로 100% 컴파일을 통과**했고(p4a 이슈 #3203은 이 조합에서 발현하지 않았다),
막고 있는 것이 전혀 다른 지점 — p4a가 안드로이드 휠 URL을 만들어 놓고 호스트 pip으로 설치해
스스로 거부하는 상류 버그 — 임이 밝혀졌다. 이것은 **실빌드를 돌려야만 얻을 수 있는 정보**이고,
M11이 스펙·문서를 1차 출처로 아무리 검증해도 나오지 않았을 종류다.

디스패치: L0 = T01 ∥ T02 ∥ T03 ∥ T04(4-way 병렬, 예상 변경 파일 교집합 0), 이후 T05는 메인이
직접 수행(레벨에 태스크 1개 → 폴백 조건).

재작업 라운드(rework): 0

## 태스크별 수행 내용

- **M12-T01** (L0 병렬) — `examples/mobile_build_preflight.py` 신설. 마일스톤이 요구한 9개 점검에
  `java`·`gradle` 2개를 정보성 행으로 더해 **11개**를 낸다(둘은 절대 BLOCK을 내지 않는다 — Docker
  이미지가 JDK를 싣고 p4a가 gradle wrapper를 받으므로 호스트 값으로 막으면 오탐이다).
  **설계 핵심은 「도구 부재」와 「도구 실패」의 구분**이다: `_run()`이 결과를
  `missing`/`timeout`/`error`/`failed`/`ok`로 분류하고 종류별로 다른 문구를 만들며, 설치 안내
  꼬리말은 부재일 때만 붙는다(대처가 다르기 때문). 현재 환경이 그 구분의 산 증거였다 — `docker`는
  *"present, but the DAEMON IS DOWN"*, `adb`는 *"is NOT on PATH (not installed)"*. 툴체인은 Docker
  경로와 WSL2 경로 중 **하나만 성립하면 OK**이고 둘 다 아닐 때만 양쪽을 BLOCK으로 올린다.
  `version.regex`는 buildozer `get_version()`과 동일하게 **플래그 없는 `re.search`**로 실제 적용해
  본다(M11-impl 이슈 3이 물렸던 회귀 지점). 모든 하위 프로세스에 타임아웃, 각 점검은 개별
  `try/except`로 감싸 하나가 죽어도 나머지가 계속 돈다.
- **M12-T02** (L0 병렬) — **데스크톱 Kivy 선검증이 폴백이 아니라 실제로 성공했다.**
  `pip install --user kivy`(시스템 site 권한 오류로 `--user` 필요) + `kivy_deps.sdl2` 강제
  재설치로 윈도 프로바이더를 살린 뒤, 앱을 실제로 띄워 실측했다 — 위젯 트리 정상(4단 레이아웃,
  초기 상태 줄 `'idle - press Start'`), **`colorfmt="luminance"` 텍스처가 데스크톱 GL 백엔드에서
  수용됨**(GLEW/OpenGL 4.6, GTX 1660 Ti에서 `Texture.create`+`blit_buffer`+`flip_vertical` 통과),
  Start 누름이 크래시 없이 상태 줄로 종료. camera4kivy 설치 후
  **`connect_camera(enable_analyze_pixels=True, camera_id="back")` 키워드가 `TypeError` 없이
  수용**됨을 확인해 M11의 「확인 필요」 하나를 닫았다. **다만 이 머신에 웹캠이 없어**
  (`VIDEOIO(DSHOW): backend … can't be used to capture by index`) 픽셀 콜백이 한 번도 불리지 않아
  `flip_*` 기본값·RGBA 행 순서·버퍼 재사용은 **여전히 미측정**이다.
  `tests/test_mobile_app.py` **45건** 추가 — Kivy 유무 양쪽 분기에서 각각 45 passed를 실측했다
  (`PYTHONNOUSERSITE=1`로 스텁 경로도 확인). 공허성 반증을 **실제 뮤테이션**으로 했다:
  `MIN_HOLD`를 0.0으로 낮추면 4건 실패, `format_summary`의 분기를 상수로 바꾸면 1건 실패.
  `app.py`는 **변경 없음**(md5 동일) — 실측에서 결함이 드러나지 않았다.
- **M12-T03** (L0 병렬) — 이월 사소 2건.
  **① cv2 부재 probe 비용**: `_require_cv2()`가 **실패(부재)만** 캐시한다. **설계 근거**가 핵심이다
  — M11-T01이 캐시를 막은 이유는 **낙관 방향의 stale**(cv2가 사라졌는데 `"cv2"`로 보고)을 막기
  위함인데, 부재만 캐시하면 그 방향으로는 **구조적으로 낡을 수 없고** 낡을 수 있는 방향은
  보수적 쪽(설치됐는데 아직 `None`)뿐이다. 공개 `reset_decoder_backend_probe()`를 두고
  `register_decoder_backend()`·`set_decoder_backend()`가 자동 호출하게 해
  `test_cv2_backend_returns_after_block_is_lifted`("막았다 풀면 회복된다")를 유지했다.
  **② eager import**: `optical/__init__.py`가 PEP 562 모듈 `__getattr__`로 `run_peer`·`PeerResult`를
  지연 재수출한다. `__all__` 불변, cv2 가드 불변. 실측 — 종전에는 `import photontcp.optical`이
  `app`·`session`·`packet`·`reliability`·`stream`까지 끌어왔고 지금은 전송 계층만 로드된다.
  신규 테스트 13건(decode 5 + optical import 8, 후자는 전부 `subprocess` 격리).
- **M12-T04** (L0 병렬) — 문서 버전 복제 제거. `docs/conventions.md`·`docs/project-context.md`에서
  값을 지우고 선언처를 가리키는 문장만 남겼다. 재발 가드 `tests/test_docs_no_version_dup.py`
  **15 케이스** — `현재`를 필수 앵커로 요구해 릴리즈 헤딩(`## [0.11.0]`)·과거 버전 서술
  (`0.1.0 vs 0.10.0`)·명령 예시를 오탐하지 않고, `docs/reports/`·`docs/milestones/`는 제외한다
  (이력이라 값이 박혀 있는 것이 정상). **공허 통과 방지 3중**: 검사 대상 집합 비어 있지 않음 단언 ·
  지운 4개 문장 형태를 실제로 문다는 양성 대조 · 제외 디렉터리에 버전 리터럴이 실재함을 실측
  (없어지면 제외 규칙이 불필요해졌다며 붉는다). `docs/project-context.md`에 `mobile/` 계층 행,
  `main.py`·`buildozer.spec` 행, `mobile` extra, `python -m photontcp.mobile.app` 진입점을 반영하고,
  **테스트 파일 수(`21개 파일`)를 선언처 포인터로 교체**했다(M11이 `docs/v1.0-signoff.md`에서 한 것과
  같은 처리 — 같은 함정을 두 번 밟지 않는다).
  - **범위 예외 1건(보고)**: 담당 서브에이전트가 `docs/mobile-build.md:174`에서 **세 번째 복제**
    (`(현재 \`0.10.0\`)`)를 발견해 함께 지웠다. 지시된 파일 범위 밖이었으나, 가드를 그 줄만
    봐주도록 좁히는 것은 `vacuous-pass` 판례에 정면으로 걸리고 그대로 두면 스위트가 붉는다.
    국소 편집이었고 T01의 같은 파일 편집(0절)과 충돌 없이 공존함을 메인이 확인했다.
- **M12-T05** — **실빌드 수행. APK 미산출(실패). 원인은 p4a 상류 버그로 특정.**
  사용자 판단으로 진행했고(비용 고지 후), 사전조건을 갖춰 하니스가 **READY(BLOCK 0)** 를 낸 뒤
  Docker 경로로 빌드했다. 문서화된 절차가 실제로는 그대로 돌지 않는 지점 **5개**를 만났고
  **4개를 닫았다**:
  1. **root 프롬프트 → `EOFError` 즉사** — 컨테이너가 root로 돌아 `warn_on_root = 1`이 대화형
     확인을 묻는다. 스펙에 `warn_on_root = 0`. (이전 주석의 *"Docker 이미지는 비-root로 돈다"* 가
     **틀렸음이 실측됨**.)
  2. **`unzip -q ...ndk...zip` exit 1** — 덮어쓰기 프롬프트에 EOF → `[N]one` → 부분 추출.
     `unzip -o`로 사전 추출(2.2G 정상).
  3. **호스트 메모리 압박으로 컨테이너 사망** — `.buildozer` 캐시를 named volume으로 옮기고
     `--memory=6g` 부여.
  4. **`--rm`이라 죽는 순간 로그 소실** — detached + `--rm` 제거로 전환.
     - 3의 **2차 피해**도 겪었다: `pip install -U pip` 도중 사망해 venv에 `pip-25.3.dist-info`와
       `pip-26.2.1.dist-info`가 **동시에** 남아 `ImportError: cannot import name
       'BuildDependencyInstallError'`. venv 삭제로 해소. 우리 코드나 이미지의 결함이 아니라
       **중단이 남긴 잔재**임을 두 dist-info의 공존으로 확인했다.
  5. **미해결(상류)** — p4a가 레시피 없는 요구사항의 의존성 중 PyPI에 **안드로이드 전용 휠**이
     있으면 그 URL을 요구사항에 넣고, 정작 `--platform`/`--only-binary` 없이 **호스트 pip**으로
     설치해(`pythonforandroid/build.py:927`) 자기가 넣은 요구사항을 거부한다:
     `ERROR: charset_normalizer-3.5.1-cp314-cp314-android_24_arm64_v8a.whl is not a supported
     wheel on this platform.` 경로는 **camera4kivy → requests → charset-normalizer**이고, 그
     묶음에서 컴파일 확장을 가진 것이 그것 하나뿐이라 그것만 터진다.
     **시도했고 듣지 않은 우회 둘(둘 다 실측)**: `charset-normalizer==3.3.2` 핀 → p4a가 핀과
     무관하게 3.5.1 휠 URL을 따로 덧붙인다 / `p4a.extra_args = --skip-prebuilt` → 인자는
     전달됐으나(p4a 명령줄에서 확인) 효과 없음 — 그 옵션은 `Recipe.check_prebuilt()`만 끄므로
     **레시피**의 프리빌트 휠에만 관여한다. **두 우회 모두 되돌렸다** — 듣지 않는 설정을 켜 둔 채
     주석이 "이걸로 우회된다"고 말하게 두면 그 자체가 사실보다 넓은 주장이 된다.
  - **여기까지는 성공했다(실측)**: buildozer 1.6.1.dev0(camera4kivy 요구 ≥1.3.0 충족) ·
    SDK cmdline-tools 6514223 · NDK **r28c** · 레시피 hostpython3 · libffi · openssl · sqlite3 ·
    python3 · sdl2(+image·mixer·ttf) · **numpy** · setuptools · pyjnius · android · **opencv** ·
    kivy 전부 빌드 완료. **opencv는 100% 컴파일**됐다 — `libopencv_gapi.so` 링크와
    `opencv_python3` 타깃(`cv2.cpp`·`cv2_numpy.cpp` 등)이 로그에 남아 있다.
    **즉 M11이 최대 불확실성으로 지목한 p4a 이슈 #3203(opencv × numpy 2.x)은 이 조합에서
    발현하지 않았다.** 실패는 네이티브 컴파일이 아니라 그 뒤의 순수 파이썬 의존성 설치 단계다.
  - 실측 결과는 `docs/mobile-build.md` **5-4절**에 재현 가능한 형태로 남겼다.
- **M12-T06 ~ M12-T09** — **미수행.** T05가 APK를 내지 못해 착수 조건이 성립하지 않았다.
  실기(폰)는 사용 가능한 상태였으므로 **막은 것은 기기가 아니라 빌드**다. 이 사이클에서
  체크 A·체크 B 실측값은 존재하지 않으며, **v1.0은 이번에도 주장하지 않는다.**

## 완료 기준 대조

| 기준 | 판정 | 근거 |
|---|---|---|
| 1 | 충족 | `python examples/mobile_build_preflight.py`가 11개 항목에 OK/BLOCK/WARN을 낸다(마일스톤의 ①~⑨ + java·gradle). **부재/실패 구분 실측**: 같은 실행에서 `docker`는 "present, but the DAEMON IS DOWN", `adb`는 "is NOT on PATH (not installed)". **예외 격리 실측**: 점검 ③을 `KeyError`, ⑦을 `ZeroDivisionError`로 고의 훼손하니 그 두 행만 `BLOCK — the check itself CRASHED: …`가 되고 **나머지 9개가 정상 판정**, exit 1. `version.regex`를 `^`앵커로 바꾸면 BLOCK(회귀 지점이 실제로 잡힌다), 원복하면 OK. 인자를 주면 exit 2. |
| 2 | 충족 | 폴백이 아니라 **실제 실행 성공**. `python -m photontcp.mobile.app`이 뜨고 위젯 트리가 그려졌으며(4단 레이아웃, 상태 줄 `'idle - press Start'`), **`luminance` 텍스처가 데스크톱 GL에서 수용**됐고, Start 누름이 `camera4kivy not installed …` 상태 줄로 크래시 없이 끝났다. 환경: Windows 11 / Python 3.12 / Kivy 2.3.1 / SDL2 / OpenGL 4.6. **웹캠 부재로 실캡처는 불가**했고 그 사유를 README에 기록했다. |
| 3 | 충족 | `tests/test_mobile_app.py` 45건. 열거된 넷을 모두 단언 — `format_summary()` PASS/FAIL 양쪽 · `_read_settings()`가 `""`·`"abc"`·`"0"`·`"-1"`에서 `run_peer`가 거부하지 않는 `hold`를 낸다(값 단언 + **산출된 hold를 실제 `run_peer`에 넣어** 검증) · `on_event()`가 `EVENT_KINDS` 전 종류 + 미지 kind에서 무예외(`_DETAILS`가 `EVENT_KINDS`와 집합 동일함도 단언해 kind가 늘면 먼저 붉는다) · `_make_preview()`가 camera4kivy 부재에서 `(None, 사유)`. Kivy 미설치 스텁 경로와 설치 경로 **양쪽에서 45 passed** 실측. |
| 4 | 충족 | **캐시를 넣었다.** 실패(부재)만 캐시하고 `reset_decoder_backend_probe()`(공개, `__all__` 등재)로 무효화하며 두 레지스트리 호출이 자동 무효화한다. `test_cv2_backend_returns_after_block_is_lifted`가 **그대로 통과**하고, `active_decoder_backend()`가 stale 값을 보고하지 않음을 단언하는 테스트를 추가했다. 트레이드오프(보수적 방향 stale — 실행 중 cv2가 설치되면 무효화 전까지 `None`)를 `_require_cv2` docstring에 명시하고 `test_reset_probe_reinstates_cv2_after_absence_was_cached`로 고정했다. |
| 5 | 충족 | `tests/test_optical_import.py` 8건(전부 `subprocess` 새 프로세스 격리). `import photontcp.optical` 직후 `sys.modules`에 `photontcp.app`·`photontcp.session` **없음**을 단언하고, 이어 `photontcp.optical.run_peer` 속성 접근과 `from photontcp.optical import run_peer`가 모두 정상임을 단언한다. `__all__`·`dir()`도 종전 이름을 노출한다. `python examples/optical_link.py` **exit 0** 유지. |
| 6 | 충족 | `docs/conventions.md`·`docs/project-context.md`에 버전 리터럴 0건(+ 범위 밖에서 발견된 `docs/mobile-build.md` 1건도 제거). `tests/test_docs_no_version_dup.py` 15건 통과. **오탐 실측**: 제외 디렉터리에 버전 리터럴이 실재하는 상태에서 통과함을 테스트가 스스로 단언하고, `CHANGELOG.md`의 `## [x.y.z]` 헤딩 실재를 먼저 단언한 뒤 매치 0건 확인, `기반 버전: v0.11.0`·`0.1.0 vs 0.10.0`·`adb install …0.10.0….apk` 등 10개 문장을 비매치로 parametrize. **되돌림 실측**: 지운 문장을 임시 복원하니 해당 파일·행을 지목하며 FAIL, 원복 후 그린. `project-context`에 `mobile/` 행·`mobile` extra 반영. |
| 7 | 충족 | **실패로 충족**(기준 문면: 성공·실패·미수행 어느 것이든 기록하면 되고 마일스톤을 깨지 않는다). 멈춘 단계는 p4a `create`의 순수 파이썬 의존성 설치, 오류 요지는 `charset_normalizer-…-android_24_arm64_v8a.whl is not a supported wheel on this platform`, 시도한 우회는 버전 핀과 `--skip-prebuilt` 둘(**둘 다 실측으로 무효 확인 후 되돌림**). 함께 닫은 4개 장애와 성공한 레시피 목록·확정된 NDK(r28c)·buildozer 버전(1.6.1.dev0)도 기록했다. 상세는 `docs/mobile-build.md` 5-4. |
| 8 | 부분 | **남은 조각: 6항목 중 4개**(권한·`flip_*`·콜백 시그니처·버퍼 재사용) — 실기 없이는 확정 불가. 기준 문면이 *"APK가 없으면 이 기준은 미수행으로 방전된다"* 고 규정한다. APK가 없으므로 6항목(권한·luminance·flip·콜백 시그니처·버퍼 재사용·wakelock) 중 어느 것도 실기에서 확정하지 못했다. **다만 T02가 데스크톱에서 `luminance` 수용과 `connect_camera` 키워드 둘을 앞당겨 확정**했고, 그 둘은 README의 「확인 필요」에서 데스크톱 한정으로 범위가 좁혀졌다. |
| 9 | 부분 | **남은 조각: 실측값 전부**(수신율·양쪽 요약 줄) — 기준의 폴백 조항(*"수행 불가면 사유가 T05·T06 결과와 연결돼 적힌다"*)만 만족했다. 체크 A·체크 B 모두 수행 불가 — 사유가 T05(APK 미산출)와 직접 연결된다. **실기는 사용 가능한 상태였으므로 막은 것은 기기가 아니라 빌드다.** 수신율·요약 줄 실측값은 이 사이클에 존재하지 않는다. |
| 10 | 충족 | v1.0 판단을 명시적으로 적었다 — **"두 체크 미수행이므로 v1.0.0 후보가 아니다"** 이고, 남은 것은 ① p4a 휠 URL 버그 우회 ② APK 산출 ③ 체크 A·B 실측이다. `README.md`의 「현재 한계」는 이번에 **실측으로 확정된 것만**(데스크톱 `luminance` 수용, `connect_camera` 키워드) 좁혔고 실기 미검증 항목은 그대로 남겼다 — 실측하지 않은 것을 확정으로 옮기지 않았다. |
| 11 | 충족 | 전체 스위트 **319 passed, 2 skipped**(실패 0). `python examples/qr_decode_bench.py` exit 0, `full >= base-only on every set: YES`. `python examples/optical_link.py` exit 0 / `--role sender` exit 2. `--help` 인자 집합 불변. |

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `examples/mobile_build_preflight.py`, `tests/test_mobile_app.py`, `tests/test_optical_import.py`, `tests/test_docs_no_version_dup.py`, `docs/milestones/M12.md`(마일스톤 단계 산출물), `docs/reports/M12-impl.md`(본 보고서) |
| 수정 | `photontcp/qr/decode.py`, `photontcp/optical/__init__.py`, `tests/test_decode_backend.py`, `buildozer.spec`, `docs/mobile-build.md`, `docs/conventions.md`, `docs/project-context.md`, `README.md` |
| 삭제 | (없음) |

기준선 = v0.11.0 태그 직후의 깨끗한 워킹트리. `photontcp/mobile/app.py`·`kivy_devices.py`는
**변경 없음** — T02·T06이 조건부로 열어 둔 자리였으나 실측에서 결함이 드러나지 않았고(T02),
T06은 미수행이다. `camerax_provider/`와 `.buildozer/`·`bin/`은 `.gitignore` 대상이라 추적되지
않는다(빌드 사전조건·산출물).

## 테스트 결과

아래 값은 **본 보고서를 포함한 모든 산출물이 나온 뒤의 마지막 측정**이다(`measure-after-artifacts`).

- `python -m pytest -q` → **319 passed, 2 skipped** (v0.11.0 기준선 247 passed·1 skipped → **+72 신규**,
  회귀 0). 신규 내역: `test_mobile_app.py` 45 · `test_optical_import.py` 8 · `test_decode_backend.py`
  +5 · `test_docs_no_version_dup.py` 15(합 73, 그중 1건은 기존 테스트 대체).
  **skip이 1→2로 는 것은 환경 변화 때문이다** — T02가 Kivy를 설치해
  `test_mobile_devices.py`의 "Kivy 부재" 분기 2건이 관찰 불가가 되고 반대편 분기가 대신 돈다
  (설계된 양방향 분기). `PYTHONNOUSERSITE=1`(Kivy 비가시)에서는 242 passed·13 skipped로 반대가 된다.
- `python examples/qr_decode_bench.py` → **exit 0**, clean 249/249 = **100.00%**,
  degraded 249/249 = **100.00%**, `full >= base-only on every set: YES`.
- `python examples/optical_link.py` → **exit 0** / `--role sender` → **exit 2**.
- `python examples/mobile_build_preflight.py` → **exit 0**, `11 checks: 7 OK / 4 WARN / 0 BLOCK`,
  `VERDICT: READY`.
- **APK 빌드**: 미산출(위 T05). 컨테이너는 정리했고 캐시 volume `photontcp-buildozer`는 다음
  시도를 위해 보존했다 — 재시도 시 SDK/NDK와 컴파일된 레시피를 다시 받지 않는다.

**구현 중 발견·처리한 이슈**

1. **문서화된 Docker 경로가 비대화형에서 성립하지 않았다(4건)** — root 프롬프트, NDK unzip
   프롬프트, 메모리 압박, `--rm` 로그 소실. 전부 닫고 `docs/mobile-build.md` 5-4에 재현 절차와
   함께 기록했다. **스펙 주석이 사실과 달랐던 자리 하나**(*"Docker 이미지는 비-root로 돈다"*)도
   실측으로 바로잡았다.
2. **중단이 남긴 잔재를 실패로 오인할 뻔했다** — `ImportError: BuildDependencyInstallError`는
   pip 버그처럼 보이지만, venv에 `pip-25.3.dist-info`와 `pip-26.2.1.dist-info`가 공존하는 것을
   확인해 **앞선 컨테이너 사망의 2차 피해**로 특정했다. 원인을 코드에 돌리기 전에 상태를 본 것이
   맞았다.
3. **모니터 필터의 오탐** — `Traceback`을 넓게 잡아 p4a의 정상적인 "프리빌트 휠 탐색 실패 →
   소스 빌드 폴백"까지 실패로 보고했다. 선언 전에 로그를 직접 확인해 오탐임을 밝히고 필터를
   좁혔다. **실패를 선언하기 전에 원자료를 본다**는 규율이 실제로 값을 했다.
4. **`--skip-prebuilt`가 전달됐으나 듣지 않았다** — "안 넘어간" 것과 "안 먹힌" 것을 구분하려고
   p4a 명령줄에서 인자 존재를 확인한 뒤, `Recipe.check_prebuilt()`만 끈다는 것을 소스에서 읽어
   확정했다. 되지 않는 우회를 켜 둔 채 주석이 우회된다고 말하게 두지 않고 되돌렸다.

## 미해결·후속 메모

1. **APK가 여전히 나오지 않았다(최우선 후속).** 막고 있는 것은 **p4a의 안드로이드 휠 URL 버그**
   하나로 좁혀졌다. 다음 사이클의 경로 셋: ① 상류 수정(p4a의 pip 호출에 `--platform`/
   `--only-binary` 추가) 제출·대기 ② `p4a.branch`/`p4a.fork`로 그 경로가 없는 리비전 고정
   ③ `p4a.source_dir`로 패치한 p4a 체크아웃 지정. **우리 코드의 문제가 아니며 opencv를 포함한
   모든 네이티브 레시피는 그 지점 전에 이미 성공한다** — 재시도 비용은 캐시 덕에 작다.
2. **v1.0 사인오프는 5사이클 연속 이월이다**(M8·M9·M10·M11·M12). 다만 이번 이월의 성격은
   이전 넷과 다르다 — 이전에는 "장비가 없어서"였고 이번에는 **장비가 있는데 빌드가 막혀서**다.
   병목이 하드웨어에서 툴체인으로 옮겨 갔고, 그 병목은 원인이 특정된 단일 지점이다.
3. **실기 미검증 항목은 그대로 남는다** — `flip_vertical`/`flip_horizontal` 기본값, RGBA 행 순서,
   분석 버퍼 재사용 여부, 안드로이드 GLES의 `luminance` 수용, wakelock 실동작. T02가 데스크톱에서
   `luminance`와 `connect_camera` 키워드 둘을 앞당겨 확정했으나 **웹캠이 없어 나머지는 손대지
   못했다**. 웹캠이 있는 데스크톱이라면 `flip_*`까지 기기 없이 확정할 수 있다.
4. **개발 환경이 바뀌었다** — 이 세션에서 유저 site에 `kivy 2.3.1`, `kivy_deps.sdl2 0.8.0`,
   `camera4kivy 0.3.3`, `gestures4kivy 0.1.4`가 설치됐다(되돌리려면 `pip uninstall --user`).
   그 결과 `test_mobile_devices.py`의 Kivy-부재 분기 2건이 skip으로 바뀌었다(설계된 거동).
5. **Kivy 폐기예정 경고(미수정)** — `app.py`의 `Image(allow_stretch=True, keep_ratio=True)`에
   Kivy 2.3.1이 폐기예정 경고를 낸다(대체: `fit_mode="contain"`, Kivy 2.2+). 크래시가 아니고
   p4a가 쓰는 Kivy 버전과의 하위호환 판단이 필요해 손대지 않았다.
6. **`photontcp/optical/cv2_devices`는 여전히 패키지 초기화에서 eager import된다**(가드된 채).
   전송 계층 내부라 계층 방향 문제가 아니어서 T03 범위 밖으로 두었으나, cv2 부재 환경에서
   패키지당 1회의 실패 import 비용이 남는다(프레임 비용은 아니다).
7. **`reset_decoder_backend_probe`가 공개 표면에 추가됐다** — `docs/project-context.md`·`README.md`에
   아직 반영되지 않았다(T03·T04가 서로 다른 태스크라 경계에 걸렸다). 다음 사이클 또는 리뷰의
   in-review 수정 대상이다.
8. **`docs/mobile-build.md:264`의 `adb install … photontcp-0.10.0-….apk`** 는 명령 예시라 버전
   가드 대상이 아니지만 릴리즈마다 낡는 리터럴이다. 실빌드가 성공하면 실측 파일명으로 갱신될 자리다.
9. **회고 표 상태 갱신이 남았다** — `docs/reports/retro.md` 최상단 섹션에서 M11이 닫은 세 행
   (Micro-QR 원천 제거, `__version__` 불일치, `docs/v1.0-signoff.md` 미커밋)이 아직 `미반영`이다.
   impl은 회고 문서를 고치지 않으므로 다음 `/tide:retro`가 `반영`으로 옮겨야 한다.
