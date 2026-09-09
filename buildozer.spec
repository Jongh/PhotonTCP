[app]

# ---------------------------------------------------------------------------
# PhotonTCP — Android(APK) 패키징 스펙 (M11-T05)
#
# 이 파일은 buildozer가 python-for-android(p4a)를 몰아 APK를 만들 때 읽는 유일한
# 설정이다. 값의 근거·빌드 절차·실패 시 우회는 `docs/mobile-build.md`에 있다.
#
# 형식 규칙(buildozer 문서): 주석은 **줄 단위로만** 쓸 수 있고 값 뒤에 인라인으로
# 붙일 수 없다. 아래 주석이 전부 값 위에 있는 이유가 이것이다.
# ---------------------------------------------------------------------------

# (str) 앱 제목 (런처에 뜨는 이름)
title = PhotonTCP

# (str) 패키지 이름 / 도메인 → 최종 application id = org.photontcp.photontcp
package.name = photontcp
package.domain = org.photontcp

# (str) 소스 루트. 저장소 루트를 그대로 쓴다 — `photontcp/` 패키지가 그 아래에 있고,
# p4a는 이 디렉터리에서 **`main.py`를 파이썬 진입점으로** 찾는다.
#
# 진입점 설정에 대하여 (중요):
#   * p4a의 파이썬 진입 파일 이름은 `main.py`로 **고정**이며 spec 옵션으로 바꿀 수
#     없다. 아래 `android.entrypoint`는 파이썬 파일이 아니라 **자바 Activity 클래스**를
#     가리키는 별개 옵션이므로 혼동하지 말 것.
#   * 따라서 저장소 루트에 다음 내용의 `main.py`가 있어야 한다 (M11-T05 범위 밖 —
#     `docs/mobile-build.md`의 "진입점 main.py" 절 참조):
#         from photontcp.mobile.app import main
#         main()
source.dir = .

# (list) APK에 포함할 확장자. buildozer 기본값 그대로 — **`toml` 을 넣으면 안 된다.**
#
# M11-review 사소 14 가 `pyproject.toml` 을 APK 에 담으려고 `toml` 을 더했었다(기기에서
# `photontcp.__version__` 이 `"0+unknown"` 으로 떨어지는 것을 막으려는 의도). M13-T01 의
# 실빌드에서 **그 한 줄이 앱 코드를 통째로 APK 에서 빼는 원인**임이 드러나 되돌린다.
#
# 기전(p4a `bootstraps/common/build/build.py`):
#
#     use_setup_py = dist_info 의 값        # 이 dist 에서는 True 로 기록돼 있다
#     if not use_setup_py or (setup.py 없음 and pyproject.toml 없음):
#         private_tar_dirs.append(args.private)      # 앱 코드 전체를 담는다
#     else:
#         "Copying main.py's ONLY, since other app data is expected in site-packages."
#
# 즉 `source.dir` 에 `pyproject.toml` 이 **보이기만 해도** p4a 는 "앱은 site-packages 에
# 설치돼 있겠지"라고 판단해 `main.py` 하나만 담는다. 그런데 buildozer 는
# `--ignore-setup-py` 를 넘기므로 그 설치는 **일어나지 않는다**. 결과는 조용한 파국이다 —
# 빌드는 exit 0 으로 성공하고 43MB APK 가 나오지만 그 안에 `photontcp/` 가 없어
# 기기에서 `main.py` 의 `from photontcp.mobile.app import main` 이 즉시 죽는다.
# (M13-T01 실측: `libpybundle.so` 에 segno·camera4kivy·cv2 는 있는데 photontcp 는 0 건.)
#
# 잃는 것: 기기에서 `__version__` 이 `"0+unknown"` 이 된다. **감수한다** — 그 값은 어디에도
# 쓰이지 않고, 대안은 동작하지 않는 APK 다.
source.include_exts = py,png,jpg,kv,atlas

# (list) 패키징에서 제외할 디렉터리. 테스트·문서·빌드 캐시·예제는 APK에 넣지 않는다.
source.exclude_dirs = tests, docs, examples, bin, .buildozer, .git, .tide, .pytest_cache

# ---------------------------------------------------------------------------
# 버전
#
# 이 저장소의 버전 **단일 원본은 `pyproject.toml`의 [project].version** 이다
# (`docs/conventions.md` "버전 파일 위치"). buildozer는 `version` 하드코딩(방법 1)과
# `version.regex` + `version.filename`(방법 2) 중 하나만 허용하므로 — 둘을 함께 쓰면
# buildozer가 "version.regex and version.filename conflict with version" 예외를
#던진다 — 여기서는 **방법 2**를 써서 단일 원본을 그대로 가리킨다. 즉 이 파일은
# 버전을 두 번째로 선언하지 않는다.
#
# 정규식 주의: buildozer는 `re.search(regex, data)`를 **플래그 없이** 호출한다
# (MULTILINE 아님). 그래서 `^`로 줄 시작을 잡을 수 없어 앞에 개행을 직접 넣었다.
# `pyproject.toml`에서 `\nversion = "..."` 형태로 나타나는 줄은 [project]의 것
# 하나뿐이므로 첫 매치가 곧 프로젝트 버전이다.
version.filename = ./pyproject.toml
version.regex = \nversion = ['"]([^'"]*)['"]

# ---------------------------------------------------------------------------
# requirements — APK 안에 들어갈 파이썬 의존성 (p4a 레시피 이름 기준)
#
#   python3, kivy      : 런타임 + UI
#   camera4kivy        : CameraX 기반 카메라 프리뷰 (KivyCamera가 쓴다)
#   gestures4kivy      : camera4kivy의 필수 의존성 (camera4kivy 0.3.3의
#                        requires_dist = ["gestures4kivy>=0.1.3"]). 데스크톱에서는
#                        pip가 알아서 끌어오지만 p4a는 requirements에 **명시된 것만**
#                        빌드하므로 여기 직접 적어야 한다.
#   numpy              : 프레임 버퍼 표현 (photontcp 필수 의존성, p4a 레시피 있음)
#   segno              : QR 인코딩 (순수 파이썬 — 레시피 불필요, p4a가 pip로 설치)
#   opencv             : QR 디코딩. **p4a 레시피 이름은 `opencv`이지 `opencv-python`이
#                        아니다** (pyproject의 `optical` extra 이름과 다르다).
#
# ── opencv를 포함하는 이유와 그 위험 (M11-T05 요구: 포함 여부와 사유를 명시) ──
#
# 포함한다. 근거:
#   * M11-T01에서 `photontcp/qr/decode.py`가 cv2를 **지연 import**하도록 바뀌어,
#     cv2가 없어도 패키지는 import되고 `decode_frame`은 예외 대신 `None`을 돌려준다.
#     즉 opencv를 빼도 **앱은 정상적으로 뜬다**. 다만 QR을 한 장도 읽지 못하므로
#     수신이 성립하지 않고 → 핸드셰이크가 끝나지 않고 → **광학 링크 자체가 성립하지
#     않는다**. 폰을 한 피어로 쓴다는 M11의 목적이 통째로 무의미해진다.
#   * 현재 등록된 디코더 백엔드는 `"cv2"` 하나뿐이다. 대체 백엔드(ZXing via pyjnius)는
#     M11이 명시적으로 **다음 사이클로 이월**한 항목이라 지금은 대안이 없다.
#
# 위험(감수하는 쪽):
#   * opencv 레시피는 p4a에서 가장 무거운 빌드 중 하나다 — 소스에서 CMake로 전체를
#     컴파일하므로 최초 빌드 시간과 APK 크기가 크게 늘어난다.
#   * 실패 전례가 있다. p4a 이슈 #3203(2025-09)은 opencv 4.5.1 레시피가 numpy 2.3.0과
#     함께 빌드될 때 `numpy/ndarrayobject.h` 를 찾지 못해 죽는 것을 보고한다.
#     numpy와 opencv의 조합이 이 스펙의 가장 깨지기 쉬운 지점이다.
#
# 실패 시 우회(문서화된 폴백 — `docs/mobile-build.md` "opencv 레시피가 실패하면" 참조):
#   아래 requirements에서 `,opencv`만 지우고 다시 빌드하면 앱은 뜨고 UI·권한·표시
#   경로까지는 실기에서 확인할 수 있다. 그 APK로는 디코드가 불가능하며, 디코드는
#   다음 사이클의 대체 백엔드 구현에 맡긴다(M11이 이월로 규정한 부분).
requirements = python3,kivy,camera4kivy,gestures4kivy,numpy,segno,opencv

# (list) 화면 방향. QR 정렬 상태가 세션 중간에 회전으로 깨지는 것을 막기 위해
# 세로로 고정한다.
orientation = portrait

# (bool) 풀스크린 여부. 상태 표시줄을 남겨 두는 편이 디버깅에 낫다.
fullscreen = 0

#
# Android specific
#

# (list) 권한. 카메라 캡처에 CAMERA가 항상 필요하다(런타임 요청 대상 — 앱이
# `request_permissions`로 묻는다). WAKE_LOCK은 아래 `android.wakelock = True`의 짝으로,
# 세션 중 화면이 꺼져 표시 피어가 죽는 것을 막는다(설치 시 자동 부여, 런타임 요청 불필요).
# (사진/영상을 저장하지 않으므로 WRITE_EXTERNAL_STORAGE·RECORD_AUDIO는 불필요하다.)
android.permissions = CAMERA, WAKE_LOCK

# (int) 타깃 Android API.
# camera4kivy가 **33을 요구**한다 — 그 README가 "android.api = 33 (Constrained by
# Android packages imported by camerax_provider)"라고 못박는다. buildozer 최신
# default.spec의 주석 기본값은 36이지만, camerax_provider가 끌어오는 androidx 패키지
# 집합에 맞추는 쪽을 택한다. (스토어 배포가 아니라 실기 테스트용 debug APK이므로
# Play Console의 target API 하한 요구는 이번 범위와 무관하다.)
android.api = 33

# (int) 최소 API. buildozer 최신 default.spec의 기본값과 동일하게 24를 쓴다.
android.minapi = 24

# (int) NDK API. buildozer 문서가 "should usually match android.minapi"라고 하므로
# minapi와 같은 값으로 맞춘다.
android.ndk_api = 24

# (str) NDK 버전.
# **r28c로 확인됨 (M12-T05 실빌드 실측).** buildozer 1.6.1.dev0이 자기 버전에 맞춰
# `r28c`를 내려받았고(`Recommended android's NDK version by p4a is: 28c`), 그 NDK로
# **opencv를 포함한 모든 네이티브 레시피가 컴파일에 성공**했다 — numpy·python3·sdl2·
# pyjnius·opencv 전부. 즉 M11이 우려한 opencv×numpy 조합(p4a 이슈 #3203)은 이 NDK에서
# 발현하지 않는다. 그 빌드가 APK까지 가지 못한 것은 NDK와 무관한 별개 지점이다
# (`docs/mobile-build.md` 5-4의 상류 버그).
#
# 그래도 **값을 박지 않고 주석 처리로 남긴다**: buildozer가 내려받는 기본값이 이미
# r28c이므로 박아도 동작이 같고, 박아 두면 buildozer를 올렸을 때 새 기본값과 어긋나
# 되레 낡는다. 아래 줄은 "무엇이 실제로 통과했는지"의 기록이지 필요한 설정이 아니다.
#android.ndk = 28c

# (list) 빌드 대상 아키텍처.
# arm64-v8a **하나만** 빌드한다. 요즘 실기가 전부 arm64이고, 아키텍처를 늘리면
# opencv 컴파일이 아키텍처 수만큼 반복돼 빌드 시간이 배로 든다.
# camera4kivy 경고: camerax gradle 의존성은 아키텍처별로 구현되므로 armeabi-v7a로만
# 빌드한 앱은 arm64-v8a 기기에서 크래시한다 → arm64 기기를 쓰려면 arm64로 빌드해야 한다.
android.archs = arm64-v8a

# (bool) SDK 라이선스 자동 동의. Docker/CI처럼 대화형 프롬프트를 띄울 수 없는
# 환경에서 첫 빌드가 라이선스 질문에 걸려 멈추는 것을 막는다.
android.accept_sdk_license = True

# (str) 자바 Activity 진입점. Kivy 앱은 기본값이 맞다 — 파이썬 진입점(`main.py`)과는
# 무관한 옵션이다(위 source.dir 주석 참조).
#android.entrypoint = org.kivy.android.PythonActivity

# (bool) 화면 켜짐 유지. **켠다.**
#
# 이 앱의 유스케이스에서 폰은 **표시 피어**다 — 세션 중 화면이 꺼지면 상대 카메라가
# 볼 QR이 사라져 링크가 그 자리에서 죽는다. 기본 off로 두고 "폰 설정에서 화면 꺼짐
# 시간을 늘려라"를 절차로 미루면, 스펙 한 줄로 닫히는 실패를 사람의 준비에 맡기게
# 된다(M11-review 사소 13). 아래 WAKE_LOCK 권한이 이것과 짝이다.
android.wakelock = True

# (str) p4a 훅. camera4kivy가 **필수로 요구**한다 — 이 훅이 camerax에 필요한 p4a
# 옵션(androidx 등)과 아키텍처별 gradle 의존성을 설정한다.
#
# **주의: 이 경로가 실재해야 한다.** `camerax_provider/`는 pip 패키지가 아니라 별도
# 저장소이며, camera4kivy README의 지시대로 프로젝트 루트에 복제해 두어야 한다:
#     git clone https://github.com/Android-for-Python/camerax_provider.git
#     rm -rf camerax_provider/.git
# 이 디렉터리는 저장소에 커밋하지 않고 빌드 직전에 가져온다(`docs/mobile-build.md`).
p4a.hook = camerax_provider/gradle_options.py

# (str) p4a 브랜치. 기본(master)을 쓴다. develop이 필요해지는 경우는
# `docs/mobile-build.md`의 실패 대처 절에 적었다.
#p4a.branch = master

# (str) p4a 에 그대로 넘기는 추가 인자. **현재 비워 둔다.**
#
# M12-T05 에서 `--skip-prebuilt` 를 시도했으나 효과가 없었다(실측) — 그 옵션은
# `Recipe.check_prebuilt()` 만 끄므로 *레시피*의 프리빌트 휠에만 관여한다. 아래 상류
# 버그는 그것과 다른 경로라 인자로는 우회되지 않는다.
#
# ── 상류 버그와 그 우회 (M13-T01 에서 해결) ──────────────────────────────────
#
# p4a 의 `process_python_modules()` 는 pip 을 `--platform=android_...` 로 돌려 의존성을
# 해석하고, 그 판정을 통과한 휠의 **URL 을 요구사항 목록에 넣는다**(안드로이드 전용 휠도
# 통과한다). 그런데 설치는 `run_pymodules_install()` 에서 **그 platform 태그 없이** 호스트
# pip 으로 실행되므로, p4a 가 스스로 적어 넣은 요구사항을 스스로 거부한다:
#
#     ERROR: charset_normalizer-3.5.1-cp314-cp314-android_24_arm64_v8a.whl
#            is not a supported wheel on this platform.
#
# 이 프로젝트가 걸리는 경로는 camera4kivy -> requests -> charset-normalizer 다.
#
# **우회는 `tools/p4a-android-wheel-url.patch` 다** — 설치 단계에 해석 단계와 같은
# platform 태그를 넘겨 두 단계의 가정을 일치시키는 34 줄 diff. 빌드 전에 clone 된 p4a 에
# 적용한다(절차는 `docs/mobile-build.md` 5-4). 상류가 고치면 패치는 불필요해진다.
#
# **p4a 리비전 고정(`p4a.branch`)은 대안이 되지 못한다**: 문제 기능(커밋 2f107b15,
# 2026-03-28)을 포함하지 않는 가장 최근 릴리즈가 `v2024.01.21` 로 약 2 년 전이라
# Python 3.14 / NDK r28c / buildozer 1.6.1 조합을 지원하지 못한다(M13-T01 실측 판단).
#p4a.extra_args =

[buildozer]

# (int) 로그 수준 (0 = error only, 1 = info, 2 = debug (with command output))
# 첫 빌드는 실패 분석이 핵심이므로 2로 둔다.
log_level = 2

# (int) root로 실행 시 경고할지. **0으로 둔다 (M12-T05 실측으로 확정).**
#
# 주석에 "Docker 이미지는 비-root 사용자로 도므로 그대로 둔다"고 적혀 있었으나 **틀렸다** —
# `kivy/buildozer` 이미지에 호스트 볼륨을 마운트해 돌리면 실제로 root로 실행되고, 그때
# `warn_on_root = 1`은 `input('Are you sure you want to continue [y/n]? ')`로 **대화형 확인을
# 묻는다**. TTY 없이(비대화형·CI·백그라운드) 돌리면 그 자리에서 `EOFError`로 죽는다:
#
#     File ".../buildozer/__init__.py", line 702, in check_root
#       cont = input('Are you sure you want to continue [y/n]? ')
#     EOFError: EOF when reading a line
#
# 문서화된 Docker 경로가 문서 그대로 돌지 않는다는 뜻이라 스펙에서 닫는다. root 실행 자체는
# 컨테이너 안에서 일어나므로 호스트에 미치는 영향이 없다.
warn_on_root = 0
