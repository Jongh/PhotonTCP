# 모바일(Android) APK 빌드 절차

PhotonTCP를 안드로이드 폰에 올려 **한 피어로** 돌리기 위한 빌드 문서다.
설정의 단일 원본은 저장소 루트의 [`buildozer.spec`](../buildozer.spec)이고, 이 문서는
그 스펙을 실제로 APK로 바꾸는 **명령 절차**를 적는다.

- 대상: Android, `arm64-v8a`, debug APK (스토어 배포·서명은 범위 밖)
- 도구: [buildozer](https://github.com/kivy/buildozer) → [python-for-android](https://github.com/kivy/python-for-android)
- **buildozer는 리눅스에서만 돈다.** Windows에서는 **Docker** 또는 **WSL2** 중 하나를 골라야 한다.

> 이 문서는 M11-T05(스펙 + 문서)의 산출물이다. 실제 빌드 실행은 M11-T06이 맡는다.

---

## 0. 빌드 전 사전조건 — 확인 하나, 준비 둘

**빌드를 시작하기 전에 아래 하니스를 먼저 돌려라.** 이 절과 1·2절의 사전조건을 한 번에
점검해서 무엇이 준비됐고 무엇이 막혀 있는지를 표로 내놓는다(M12-T01).

```bash
python examples/mobile_build_preflight.py
```

- 인자는 없다. 하드웨어도 필요 없다. 각 항목에 `OK` / `WARN` / `BLOCK`과 사유 한 줄이 붙는다.
- **종료 코드**: `BLOCK`이 하나라도 있으면 `1`, 없으면 `0`. `WARN`은 빌드를 막지 않는다.
- 점검 항목: Docker 데몬 응답 · WSL2 일반 배포판 · `adb` · `buildozer` · 루트 `main.py` ·
  `camerax_provider/` · `buildozer.spec` 필수 키 · `version.regex`로 버전 추출 · 디스크 여유 ·
  `java` · `gradle`.
- **도구가 없는 것과 도구가 있는데 실패하는 것을 다른 문구로 구분**한다(예: `docker` 미설치 vs
  데몬 정지) — 대처가 다르기 때문이다. Docker 경로와 WSL2 경로는 **둘 중 하나만 성립하면**
  통과이고, 둘 다 없을 때만 `BLOCK`이다.
- `BLOCK`이 뜬 항목의 대처 명령은 출력 하단 `What to do` 절에 그대로 찍힌다.

`buildozer.spec`만으로는 빌드가 되지 않는다. 0-1은 **이미 저장소에 있으니 확인만** 하면 되고,
0-2와 0-3은 **직접 준비해야** 한다. **0-3(p4a 패치)을 건너뛰면 빌드는 반드시 실패한다.**

### 0-1. 진입점 `main.py` — 이미 저장소에 있다 (확인만)

python-for-android는 `source.dir` 루트의 **`main.py`를 파이썬 진입점으로 고정**해서
찾는다. 파일 이름을 바꾸는 spec 옵션은 없다(`android.entrypoint`는 파이썬 파일이
아니라 자바 Activity 클래스를 가리키는 별개 옵션이다).

**이 파일은 M11에서 저장소 루트에 커밋 대상으로 생성됐다** — `.gitignore`에도 없다.
따라서 새로 만들 것이 아니라 **있는지만 확인**한다(없다면 체크아웃이 잘못된 것이다).

```bash
cat main.py   # 아래 내용이 나와야 한다
```

```python
# main.py — Android(python-for-android) 진입점.
# 앱 본체는 photontcp/mobile/app.py 에 있다.
from photontcp.mobile.app import main

main()
```

> ⚠ 이 파일을 임의로 덮어쓰지 않는다. 실제 파일에는 위 요지에 더해 왜 이름이 고정인지를
> 적은 docstring이 들어 있다.

### 0-2. `camerax_provider/` 디렉터리

`camera4kivy`는 `p4a.hook = camerax_provider/gradle_options.py`를 **필수로** 요구한다.
이 훅이 CameraX에 필요한 p4a 옵션과 아키텍처별 gradle 의존성을 설정한다.
`camerax_provider`는 pip 패키지가 아니라 **별도 저장소**이므로 직접 복제한다
(Camera4Kivy README의 지시 그대로).

```bash
cd <프로젝트 루트>
git clone https://github.com/Android-for-Python/camerax_provider.git
rm -rf camerax_provider/.git
```

> `camerax_provider/`는 서드파티 산출물이므로 이 저장소에 커밋하지 않는다.
> 빌드 환경마다 위 두 줄을 다시 실행한다.

### 0-3. p4a 패치 — **빌드가 성립하려면 반드시 필요하다**

`tools/p4a-android-wheel-url.patch`를 적용하지 않으면 빌드는 **반드시 실패한다**(상류 버그,
상세는 5-4절). 다만 **순서에 함정이 있다**: 패치 대상인 p4a는 **buildozer가 빌드 중에 clone**
하므로 깨끗한 체크아웃에는 그 디렉터리가 **없다.** 따라서 실제 순서는 이렇다.

```bash
# 1) 한 번 빌드를 돌린다 — p4a 를 clone 하는 것이 목적이다.
#    (레시피 빌드까지 진행하다 순수 파이썬 의존성 설치 단계에서 실패한다. 정상이다.)
MSYS_NO_PATHCONV=1 docker run -d --name photontcp-build --memory=6g     --volume photontcp-buildozer:/home/user/.buildozer     --volume "$PWD":/home/user/hostcwd     kivy/buildozer android debug
docker logs -f photontcp-build      # "not a supported wheel on this platform" 에서 멈추면 예정대로다

# 2) clone 된 p4a 에 패치를 적용한다.
cd .buildozer/android/platform/python-for-android
git apply ../../../../tools/p4a-android-wheel-url.patch
# 이미 적용됐는지 확인:  git apply --check --reverse ../../../../tools/p4a-android-wheel-url.patch
cd -

# 3) 재실행 전에 venv 를 지운다 (5-6절 — 재실행 시 pip 이 섞인다).
rm -rf .buildozer/android/platform/build-arm64-v8a/build/venv

# 4) 다시 빌드한다. 이번엔 끝까지 간다.
docker rm -f photontcp-build
MSYS_NO_PATHCONV=1 docker run -d --name photontcp-build --memory=6g     --volume photontcp-buildozer:/home/user/.buildozer     --volume "$PWD":/home/user/hostcwd     kivy/buildozer android debug
```

> **왜 `p4a.source_dir`를 쓰지 않는가**: 마일스톤은 패치된 p4a 체크아웃을 `p4a.source_dir`로
> 가리키는 배선을 상정했으나 M13은 그렇게 하지 않았다 — 그 배선은 **빌드로만 검증되는 변경**이라
> 이번 사이클의 성공한 빌드 구성을 흔들지 않으려 보류했다. 그 대가가 위 4단계이고,
> `.buildozer/`는 `.gitignore` 대상이라 **캐시를 지우면 패치도 함께 사라진다**(그때는 2·3단계를
> 다시 밟는다). 배선을 정식화하는 것은 후속 항목이다.

> **사전조건 하니스는 이 항목을 보지 않는다.** `mobile_build_preflight.py`가 `READY`를 내도
> 패치가 적용되지 않았다면 빌드는 실패한다 — 하니스는 툴체인·스펙·경로만 점검한다.

---

## 1. Docker 경로 (권장 — 호스트를 더럽히지 않는다)

빌드 툴체인(JDK·SDK·NDK·gradle) 전체가 이미 들어 있는 공식 이미지
`kivy/buildozer`를 쓴다. 호스트에는 Docker만 있으면 된다.

### 1-1. Docker Desktop 시동 (정지 상태에서 출발)

이 개발 환경의 실측 상태: **Docker는 설치돼 있으나(v29.4.2) 데몬이 정지 상태**,
WSL 배포판은 `docker-desktop`(Stopped) 하나뿐이다. 그래서 첫 단계가 데몬 시동이다.

PowerShell에서:

```powershell
# 1) 데몬이 살아 있는지 확인 (죽어 있으면 error during connect ... 가 뜬다)
docker info

# 2) 죽어 있으면 Docker Desktop 실행
Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"

# 3) 엔진이 올라올 때까지 기다렸다가 다시 확인 (보통 30~90초)
docker info
```

`docker info`가 `Server:` 블록까지 출력하면 준비된 것이다.
WSL 백엔드를 쓰므로 `docker-desktop` 배포판이 `Running`으로 바뀐다:

```powershell
wsl --list --verbose
```

### 1-2. 이미지 준비

```bash
docker pull kivy/buildozer
docker run --rm kivy/buildozer --version
```

두 번째 명령이 buildozer 버전을 찍으면 이미지가 정상이다.
(camera4kivy는 **buildozer 1.3.0 이상**을 요구한다 — 출력된 버전이 그보다 낮으면
이미지를 갱신하거나 WSL2 경로로 간다.)

이미지가 레지스트리에 없거나 최신이 필요하면 buildozer 저장소에서 직접 빌드한다:

```bash
git clone https://github.com/kivy/buildozer.git
cd buildozer
docker build --tag=kivy/buildozer .
```

### 1-3. 빌드 실행 — 볼륨 마운트 (Windows 경로 주의)

이미지의 진입점이 이미 `buildozer`이므로 **컨테이너 인자에 `buildozer`를 다시 쓰지
않는다**. `android debug`만 넘긴다.

컨테이너 안 경로는 두 개다.
- `/home/user/hostcwd` — 프로젝트(= 이 저장소) 루트
- `/home/user/.buildozer` — SDK/NDK 캐시. **이걸 마운트해야 다음 빌드가 빨라진다.**

> ⚠ **아래 명령은 M12-T05에서 실제로 돌려 고친 형태다.** 이전 판은
> `--interactive --tty --rm` + `$HOME/.buildozer` **호스트 bind mount** 였는데, 그 구성이
> 5-4절이 기록한 장애 넷(root 프롬프트 즉사 · NDK unzip 부분추출 · 호스트 메모리 압박으로
> 컨테이너 사망 · `--rm`으로 로그 소실)을 **그대로 재현한다.** 처음 빌드하기 전에 **5-4절을
> 먼저 읽어라** — 무엇이 왜 바뀌었는지가 거기 있다.

**0) 캐시 volume 준비 (최초 1회)** — 호스트 경로 대신 named volume을 쓴다. Windows bind
mount는 NDK의 수천 개 소파일 추출이 느리고 호스트 메모리를 압박한다.

```bash
docker volume create photontcp-buildozer
```

**1) 빌드 실행 — Git Bash** (이 저장소의 권장 셸):

```bash
# MSYS_NO_PATHCONV=1 이 없으면 Git Bash가 /home/user/... 를 C:\... 로 바꿔버린다
# --rm 을 쓰지 않는다: 컨테이너가 죽으면 진단에 필요한 로그까지 사라진다.
MSYS_NO_PATHCONV=1 docker run -d --name photontcp-build --memory=6g \
    --volume photontcp-buildozer:/home/user/.buildozer \
    --volume "$PWD":/home/user/hostcwd \
    kivy/buildozer android debug

docker logs -f photontcp-build          # 진행 상황
docker inspect -f '{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}' photontcp-build
docker rm -f photontcp-build            # 끝난 뒤 정리 (캐시 volume 은 남긴다)
```

**2) NDK 사전 추출** — 첫 빌드가 `unzip -q ...ndk...zip` exit 1로 멈추면 5-4절 ②의
`unzip -o` 절차를 한 번 돌린 뒤 위 명령을 다시 실행한다.

**PowerShell** (같은 형태 — named volume · detached · `--rm` 없음):

```powershell
docker run -d --name photontcp-build --memory=6g `
    --volume photontcp-buildozer:/home/user/.buildozer `
    --volume "${PWD}:/home/user/hostcwd" `
    kivy/buildozer android debug
```

Windows 쪽 주의점:
- 마운트할 드라이브가 Docker Desktop의 **File Sharing**에 포함돼 있어야 한다
  (Settings → Resources → File Sharing). 이 저장소는 `D:` 에 있으므로 `D:` 공유 여부를 먼저 확인한다.
- 호스트 경로는 **절대 경로**여야 한다. `${PWD}`(PowerShell)·`$PWD`(bash)를 쓰고
  상대 경로(`.`)를 쓰지 않는다.
- `$HOME/.buildozer`가 없으면 미리 만든다(`mkdir -p "$HOME/.buildozer"` /
  `New-Item -ItemType Directory -Force "$env:USERPROFILE\.buildozer"`).
- **컨테이너는 실제로 root로 돈다**(M12-T05 실측). 이 문서의 이전 판은 비-root(`user`)로
  돈다고 적었으나 틀렸다 — 호스트 볼륨을 마운트해 돌리면 root다. 그래서 `warn_on_root = 0`이
  스펙에 필요하다(5-4절 ①). `--user`를 손댈 이유는 없다.

### 1-4. 산출물

```
bin/photontcp-<version>-arm64-v8a-debug.apk
```

`<version>`은 `buildozer.spec`이 `version.regex`로 `pyproject.toml`에서 읽어온 값이다
(값의 단일 출처는 그 파일이다 — 여기에 복제하지 않는다). 버전을 올리려면
`pyproject.toml`만 고치면 된다 — 스펙에 버전이 두 번 선언돼 있지 않다.

---

## 2. WSL2 경로 (호스트에 툴체인을 직접 설치)

Docker를 쓰지 않거나 빌드 내부를 직접 파고들어야 할 때 쓴다.
이 환경에는 **일반 리눅스 배포판이 없다** (`docker-desktop`만 있다). 그러니 설치부터 시작한다.

### 2-1. Ubuntu 배포판 설치

PowerShell(관리자)에서:

```powershell
wsl --list --online          # 설치 가능한 배포판 확인
wsl --install -d Ubuntu-24.04
wsl --list --verbose         # VERSION 열이 2 인지 확인 (WSL1이면 안 된다)
```

WSL1이라면 반드시 WSL2로 올린다:

```powershell
wsl --set-version Ubuntu-24.04 2
```

### 2-2. 프로젝트를 리눅스 파일시스템으로 복사

**`/mnt/d/...`에서 직접 빌드하지 말 것.** buildozer 문서가 명시적으로
"Windows 파일시스템(보통 `/mnt/c` 아래)의 프로젝트 디렉터리를 WSL 파일시스템(`~` 아래)으로
복사하라"고 한다. `/mnt/`는 느리고 퍼미션 의미가 달라 빌드가 깨진다.

```bash
mkdir -p ~/src
cp -r /mnt/d/Code/private/PhotonTCP ~/src/PhotonTCP
cd ~/src/PhotonTCP
```

### 2-3. 시스템 의존성

buildozer 설치 문서(Ubuntu 24.04)의 목록 그대로다.

```bash
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip \
  python3-virtualenv autoconf libtool pkg-config zlib1g-dev \
  libncurses5-dev libncursesw5-dev libtinfo6 cmake libffi-dev \
  libssl-dev automake autopoint gettext
```

자바가 여러 버전이면 **17**을 고른다:

```bash
sudo update-alternatives --config java
sudo update-alternatives --config javac
```

Rust(일부 의존성 빌드에 필요, 기본 옵션으로 설치):

```bash
curl https://sh.rustup.rs -sSf | sh
. "$HOME/.cargo/env"
```

### 2-4. buildozer 설치

```bash
cd ~
python3 -m venv venv_p4a
source venv_p4a/bin/activate
pip install --upgrade pip
pip install buildozer setuptools cython==0.29.34
buildozer --version    # 1.3.0 이상인지 확인 (camera4kivy 요구사항)
```

> `cython==0.29.34` 고정은 buildozer 설치 문서의 지시다. 최신 Cython(3.x)은 p4a
> 레시피 일부와 맞지 않는다.

### 2-5. 빌드

0절의 `main.py`·`camerax_provider/`를 만든 뒤:

```bash
cd ~/src/PhotonTCP
buildozer android debug
```

APK는 `~/src/PhotonTCP/bin/` 에 떨어진다. Windows에서 꺼내려면:

```bash
cp bin/*.apk /mnt/c/Users/<사용자>/Downloads/
```

---

## 3. 시간과 캐시 — 첫 빌드는 오래 걸린다

첫 빌드는 툴체인을 통째로 내려받고 opencv를 소스에서 컴파일하므로 **수십 분~수 시간**
규모다. 네트워크와 CPU에 따라 편차가 크다.

| 항목 | 대략 |
|---|---|
| Android SDK + build-tools | 수백 MB |
| Android NDK | 1 GB 이상 (압축 해제 후 더 큼) |
| p4a 빌드 트리 + gradle 캐시 | 수 GB |
| **여유 디스크 권장** | **최소 20 GB** |
| 첫 빌드 소요 | 30분 ~ 수 시간 (opencv 컴파일이 지배적) |
| 재빌드(코드만 수정) | 수 분 |

캐시 위치:

- `.buildozer/` — **프로젝트 로컬** 빌드 트리(p4a 소스, 배포판, 중간 산출물).
  `.gitignore` 처리 대상.
- `~/.buildozer/` — **전역** SDK/NDK 캐시. Docker 경로에서 이 디렉터리를 마운트하는
  이유가 이것이다. 마운트하지 않으면 컨테이너를 지울 때마다 NDK를 다시 받는다.
- `bin/` — APK 산출물. `.gitignore` 처리 대상.

재빌드는 캐시를 그대로 쓴다. 캐시가 오염됐다고 판단될 때만:

```bash
buildozer android clean        # 프로젝트 빌드 트리만 비운다
rm -rf .buildozer              # 더 강하게: 프로젝트 캐시 통째로
# ~/.buildozer 는 SDK/NDK 재다운로드를 부르므로 마지막 수단으로만 지운다
```

---

## 4. 폰에 설치하기 (`adb install`)

### 4-1. adb 구하기

**이 개발 환경에는 `adb`가 없다.** Android SDK Platform-Tools를 따로 받는다.

- 다운로드: <https://developer.android.com/tools/releases/platform-tools>
  ("SDK Platform-Tools for Windows" zip)
- 압축을 풀고 그 디렉터리를 `PATH`에 넣거나 전체 경로로 호출한다.

```powershell
# 예: C:\platform-tools 에 풀었다면
$env:PATH += ";C:\platform-tools"
adb version
```

> Docker 경로로 빌드했다면 adb는 **호스트(Windows)** 쪽에 있으면 된다.
> WSL2 경로라면 리눅스 쪽에 `sudo apt install -y android-tools-adb`로 넣거나,
> APK를 Windows로 복사한 뒤 Windows의 adb를 쓴다(USB 장치 접근이 더 단순하다).

### 4-2. 폰에서 USB 디버깅 켜기

1. 설정 → 휴대전화 정보 → **빌드 번호**를 7번 연타 → "개발자가 되었습니다"
2. 설정 → 시스템 → **개발자 옵션** → **USB 디버깅** 켜기
3. USB로 PC에 연결 → 폰에 뜨는 **"USB 디버깅을 허용하시겠습니까?"**에서 허용
   (이 PC를 항상 허용 체크)

### 4-3. 설치와 로그

```bash
adb devices                                   # device 로 잡히는지 확인
adb install -r bin/photontcp-0.10.0-arm64-v8a-debug.apk
```

- `-r` = 같은 패키지가 이미 있으면 덮어쓴다(재설치).
- `INSTALL_FAILED_UPDATE_INCOMPATIBLE`이 나면 서명이 달라진 것이다 →
  `adb uninstall org.photontcp.photontcp` 후 다시 설치.

앱 로그(파이썬 예외 포함)는 logcat으로 본다:

```bash
adb logcat -s python
# 또는 전부:  adb logcat *:S python:D
```

buildozer가 배포·실행까지 대신 해 줄 수도 있다(빌드 환경에서 USB가 보일 때만):

```bash
buildozer android debug deploy run logcat
```

Docker 컨테이너에서는 USB가 보이지 않으므로 이 조합은 WSL2/네이티브 리눅스 경로에서만 쓴다.

---

## 5. 실패했을 때

### 5-1. 로그 위치

- **콘솔 출력이 1차 원본이다.** `buildozer.spec`의 `log_level = 2`가 하위 명령의
  출력까지 그대로 흘린다. 파이프해서 남겨 두면 분석이 쉽다:

  ```bash
  buildozer android debug 2>&1 | tee build.log
  ```

- `.buildozer/android/platform/build-<arch>/build/other_builds/<recipe>/` —
  레시피별 빌드 트리. 어떤 레시피에서 죽었는지 알면 여기부터 본다.
- `.buildozer/android/platform/build-arm64-v8a/dists/photontcp/` —
  gradle 프로젝트. gradle 단계에서 죽었으면 이 아래의 gradle 출력을 본다.
- 런타임(설치 후) 문제는 빌드 로그가 아니라 `adb logcat -s python`이다.

### 5-2. 흔한 실패와 대처

| 증상 | 원인 | 대처 |
|---|---|---|
| `Unable to find capture version in ./pyproject.toml` | `version.regex`가 안 맞음(파일 형식이 바뀜) | `pyproject.toml`에 `version = "..."` 줄이 그대로 있는지 확인. 정규식은 `re.search`를 **플래그 없이** 쓰므로 `^` 앵커가 통하지 않는다 |
| `No such file or directory: camerax_provider/gradle_options.py` | 0-2절을 건너뜀 | `camerax_provider`를 프로젝트 루트에 clone |
| 앱이 뜨자마자 죽고 logcat에 `No module named photontcp` | 루트 `main.py` 없음 또는 `source.exclude_dirs`가 패키지를 걸러냄 | 0-1절의 `main.py` 확인, `photontcp/`가 제외 목록에 없는지 확인 |
| 특정 레시피에서 컴파일 실패 (`opencv`, `numpy`) | 레시피 버전 조합 문제 | 5-3절 |
| NDK 버전 불일치 / `NDK is missing`, `ndk-api` 경고 | spec의 NDK 값과 p4a 요구가 어긋남 | `buildozer.spec`의 `android.ndk`는 지금 **주석 처리**돼 있어 buildozer 기본값을 쓴다. 오류가 특정 버전을 요구하면 그 값으로 `android.ndk`를 켠다. `android.ndk_api`(24)는 `android.minapi`(24)와 같아야 한다 |
| `Aidl not found` / SDK 컴포넌트 누락 | SDK 라이선스 미동의 또는 부분 다운로드 | `android.accept_sdk_license = True`가 켜져 있는지 확인, `~/.buildozer/android/platform/android-sdk` 삭제 후 재시도 |
| 빌드 중간에 알 수 없는 I/O 오류 | **디스크 공간 부족** | `df -h` (WSL) / Docker Desktop의 디스크 이미지 크기 확인. 최소 20 GB 여유 |
| Docker: `error during connect` | 데몬 정지 | 1-1절 |
| Docker: 마운트가 비어 보임 | 드라이브 File Sharing 미포함 또는 상대 경로 | 1-3절 주의점 |
| WSL: 빌드가 극단적으로 느리거나 퍼미션 오류 | `/mnt/d`에서 빌드함 | 2-2절대로 `~` 아래로 복사 |
| gradle 단계에서 네트워크 타임아웃 | 첫 빌드의 대량 다운로드 | 그대로 재실행. buildozer는 받은 것을 캐시하므로 진행이 누적된다 |

p4a 자체의 버그로 막혔다고 판단되면 개발 브랜치를 시도할 수 있다
(`buildozer.spec`의 `p4a.branch` 주석을 해제):

```
p4a.branch = develop
```

바꾼 뒤에는 `.buildozer/`를 비우고 다시 빌드한다.

### 5-3. opencv 레시피가 실패하면 (문서화된 우회)

opencv는 이 스펙에서 **가장 깨지기 쉬운 부분**이다. p4a는 opencv를 소스에서 CMake로
전부 컴파일하며, 실패 전례가 있다 — p4a 이슈
[#3203](https://github.com/kivy/python-for-android/issues/3203)(2025-09)은 opencv 4.5.1
레시피가 numpy 2.3.0과 함께 빌드될 때 `numpy/ndarrayobject.h`를 찾지 못해 죽는 것을
보고한다.

**우회 절차:**

1. `buildozer.spec`의 `requirements`에서 `,opencv`만 지운다.

   ```
   requirements = python3,kivy,camera4kivy,gestures4kivy,numpy,segno
   ```

2. `.buildozer/`를 비우고 다시 빌드한다(requirements가 바뀌면 배포판을 다시 만든다).

   ```bash
   buildozer android clean
   buildozer android debug
   ```

**이 APK로 무엇이 되고 무엇이 안 되는가:**

- **된다**: 앱이 정상적으로 뜬다. `photontcp/qr/decode.py`는 M11-T01에서 cv2를
  **지연 import**하도록 바뀌었고, cv2가 없으면 `decode_frame`이 예외 대신 `None`을
  돌려준다. 그래서 UI·카메라 권한 요청·카메라 프리뷰·QR 표시(인코딩은 순수 파이썬
  `segno`가 한다)까지 실기에서 확인할 수 있다.
- **안 된다**: QR을 한 장도 디코드하지 못한다 → 수신이 성립하지 않고 → 핸드셰이크가
  끝나지 않는다. **광학 링크 자체가 성립하지 않는다.** 이 APK로는 v1.0 사인오프
  체크 B(폰+PC 왕복)를 돌릴 수 없다.

**그 다음은 다음 사이클이다.** M11은 대체 디코더 백엔드(ZXing via pyjnius 등)의
실제 구현을 명시적으로 **이월**했고, "opencv 레시피가 실기에서 실패하는 것이
확인되면 그때 구현이 정당화된다"고 적었다. 즉 이 우회로 빌드가 성공했다는 사실
자체가 다음 사이클에서 대체 백엔드를 구현할 근거가 된다. 백엔드를 꽂을 자리
(`register_decoder_backend` / `set_decoder_backend`)는 M11-T01에서 이미 만들어졌다.

### 5-4. M12-T05 실빌드 실측 — 실제로 걸린 것들

M12-T05에서 이 문서의 Docker 경로를 **처음으로 끝까지 돌려 봤다.** 문서대로 해도 걸리는
지점이 다섯 개 있었고, 넷은 닫았으며 하나가 **미해결로 남아 APK가 나오지 않았다.**

| # | 증상 | 성격 | 조치 |
|---|---|---|---|
| 1 | 빌드 즉시 `EOFError` | 스펙 결함 | `buildozer.spec`에 `warn_on_root = 0` |
| 2 | `unzip -q ...ndk...zip` exit 1 | 도구 동작 | NDK를 `unzip -o`로 사전 추출 |
| 3 | 빌드 중 호스트 메모리 압박으로 컨테이너 사망 | 환경 | named volume + `--memory=6g` |
| 4 | 컨테이너가 죽으면 로그가 사라짐 | 절차 | `--rm` 없이 detached 실행 |
| 5 | `charset_normalizer-…-android_….whl is not a supported wheel` | **상류 버그(미해결)** | 아래 |

**1 — root 프롬프트.** 이 이미지에 호스트 볼륨을 마운트해 돌리면 컨테이너가 **root로**
실행되고, buildozer의 `warn_on_root = 1`이 `input()`으로 대화형 확인을 묻는다. TTY 없이
돌리면 그 자리에서 `EOFError`로 죽는다. 스펙에서 `warn_on_root = 0`으로 닫았다.
(그 값의 이전 주석이 *"Docker 이미지는 비-root 사용자로 돈다"* 라고 적고 있었는데 **틀렸다.**)

**2 — NDK 압축 해제.** buildozer가 `unzip -q`(덮어쓰기 플래그 없음)로 NDK를 풀다가
덮어쓰기 확인 프롬프트를 만나고, TTY가 없어 EOF → `[N]one`으로 처리돼 **부분 추출** 후
exit 1. 사전에 `unzip -o`로 풀어 두면 buildozer가 그 디렉터리를 그대로 쓴다:

```bash
docker volume create photontcp-buildozer
MSYS_NO_PATHCONV=1 docker run --rm \
    --volume photontcp-buildozer:/home/user/.buildozer \
    --entrypoint bash kivy/buildozer -c \
    'cd /home/user/.buildozer/android/platform && unzip -o -q android-ndk-r28c-linux.zip'
```

**3·4 — 실행 방식.** `.buildozer` 캐시를 Windows 호스트 경로에 bind mount 하면 수천 개
소파일 추출이 느리고 호스트 메모리를 압박한다. **named volume**으로 옮기고 컨테이너에
메모리 상한을 준 뒤 안정됐다. 또 `--rm`으로 돌리면 죽는 순간 로그까지 사라지므로,
진단이 목적일 때는 `--rm` 없이 detached로 돌리고 `docker logs`로 본다:

```bash
docker run -d --name photontcp-build --memory=6g \
    --volume photontcp-buildozer:/home/user/.buildozer \
    --volume "$PWD":/home/user/hostcwd \
    kivy/buildozer android debug
docker logs -f photontcp-build
```

> 3의 2차 피해로 **venv의 pip이 깨질 수 있다.** 컨테이너가 `pip install -U pip` 도중
> 죽으면 `.buildozer/.../build/venv`에 두 버전의 pip 파일이 섞여
> `ImportError: cannot import name 'BuildDependencyInstallError'`가 난다. 그 venv를
> 지우고 다시 돌리면 해소된다(`site-packages`에 `pip-*.dist-info`가 둘이면 이 상태다).

**5 — 해결됨(M13-T01): p4a의 안드로이드 휠 URL.**

p4a는 레시피 없는 순수 파이썬 요구사항의 의존성을 해석할 때, PyPI에 **안드로이드 전용
휠**이 있으면 그 휠의 URL을 요구사항 목록에 넣는다. 그러나 설치는
`--platform`/`--only-binary` 없이 **호스트 pip**으로 실행하므로
(`pythonforandroid/build.py`) 자기가 넣은 요구사항을 스스로 거부한다:

```
ERROR: charset_normalizer-3.5.1-cp314-cp314-android_24_arm64_v8a.whl
       is not a supported wheel on this platform.
```

**우회는 `tools/p4a-android-wheel-url.patch`다** — 설치 단계에 해석 단계와 같은 platform
태그를 넘겨 두 단계의 가정을 일치시키는 34줄 diff. 빌드 전에 clone된 p4a에 적용한다:

```bash
cd .buildozer/android/platform/python-for-android
git apply ../../../../tools/p4a-android-wheel-url.patch
# 이미 적용됐는지 확인: git apply --check --reverse ../../../../tools/p4a-android-wheel-url.patch
```

적용 후 순수 파이썬 의존성 11개가 정상 설치된다(M13-T01 실측):
`camera4kivy-0.3.3 certifi-2026.7.22 chardet-7.6.0 charset-normalizer-3.5.1 filetype-1.2.0
gestures4kivy-0.1.4 idna-3.19 requests-2.34.2 segno-1.6.6 six-1.17.0 urllib3-2.7.0`

**p4a 리비전 고정(`p4a.branch`)은 대안이 아니다** — 문제 기능(커밋 `2f107b15`, 2026-03-28)을
포함하지 않는 가장 최근 릴리즈가 `v2024.01.21`로 약 2년 전이라 Python 3.14 / NDK r28c /
buildozer 1.6.1 조합을 지원하지 못한다.

### 5-5. **조용한 파국** — 빌드가 성공했는데 APK에 앱 코드가 없다 (M13-T01)

위 5를 우회한 뒤 빌드가 **exit 0으로 성공하고 43MB APK가 나왔는데, 그 안에 `photontcp/`가
한 파일도 없었다.** `main.pyc`는 `from photontcp.mobile.app import main`을 하므로 기기에서
즉시 죽는다. 빌드 로그에는 오류가 **하나도 없다.**

기전 — p4a `bootstraps/common/build/build.py`:

```python
use_setup_py = dist_info 의 값          # 이 dist 에서는 True 로 기록돼 있었다
if not use_setup_py or (setup.py 없음 and pyproject.toml 없음):
    private_tar_dirs.append(args.private)   # 앱 코드 전체를 담는다
else:
    "Copying main.py's ONLY, since other app data is expected in site-packages."
```

`source.dir`에 **`pyproject.toml`이 보이기만 해도** p4a는 "앱은 site-packages에 설치돼
있겠지"라고 판단해 `main.py` 하나만 담는다. 그런데 buildozer는 `--ignore-setup-py`를
넘기므로 **그 설치는 일어나지 않는다.**

**대처**: `source.include_exts`에서 `toml`을 뺀다(그래서 `pyproject.toml`이 스테이징
디렉터리로 복사되지 않는다). M11-review 사소 14가 기기의 `__version__`을 위해 `toml`을 넣었었는데, 그
편의는 동작하지 않는 APK와 맞바꿀 수 없다 — 기기에서 `__version__`은 `"0+unknown"`이 된다.

**검증 방법(중요)** — APK가 나왔다고 끝이 아니다. 다음을 확인한다:

```bash
python - <<'EOF'
import zipfile, tarfile, io
z = zipfile.ZipFile("bin/photontcp-<버전>-arm64-v8a-debug.apk")
t = tarfile.open(fileobj=io.BytesIO(z.read("assets/private.tar")))
ph = [n for n in t.getnames() if "photontcp" in n]
print("photontcp 엔트리:", len(ph))          # 0 이면 앱 코드 없음 = 실패
b = tarfile.open(fileobj=io.BytesIO(z.read("lib/arm64-v8a/libpybundle.so"))).getnames()
print("cv2.so:", sum(1 for n in b if "cv2.so" in n))   # 디코더가 들어갔는가
EOF
```

M13-T01의 최종 APK 실측: private.tar 52 엔트리 중 **photontcp 46건**(`mobile/app.pyc`
포함), `libpybundle`에 `cv2.so`·segno·camera4kivy·charset_normalizer·numpy 전부 존재.

### 5-6. 재실행 시 venv pip 혼재 (M13-T01에서 원인 정정)

M12는 이 증상을 *"컨테이너가 OOM으로 죽으며 남긴 2차 피해"* 로 기록했으나 **틀렸다.**
M13에서 OOM 없이 재발했다. 실제 원인은 **재실행 자체**다 — 빌드를 다시 돌리면
`python -m venv venv`가 기존 venv에 번들 pip(25.3)을 다시 깔고, 그 위에 이미 업그레이드된
pip(26.2.1) 파일이 남아 섞인다:

```
ImportError: cannot import name 'BuildDependencyInstallError' from 'pip._internal.exceptions'
```

**대처**: 빌드를 재실행하기 전에 그 venv를 지운다(`site-packages`에 `pip-*.dist-info`가
둘이면 이 상태다).

```bash
rm -rf .buildozer/android/platform/build-arm64-v8a/build/venv
```

**여기까지는 성공했다(실측):** SDK cmdline-tools 6514223 · NDK r28c · buildozer
1.6.1.dev0 · 레시피 hostpython3 · libffi · openssl · sqlite3 · python3 · sdl2(+image·
mixer·ttf) · **numpy** · setuptools · pyjnius · android · **opencv** · kivy 전부 빌드 완료.
특히 **opencv는 100% 컴파일됐다** — `libopencv_gapi.so` 링크와 `opencv_python3` 타깃
(`cv2.cpp`·`cv2_numpy.cpp` 등) 빌드가 로그에 남아 있다. 즉 M11이 최대 불확실성으로
지목했던 **p4a 이슈 #3203(opencv × numpy 2.x)은 이 조합에서 발현하지 않았다.**
실패는 네이티브 컴파일이 아니라 그 뒤의 **순수 파이썬 의존성 설치 단계**에서 났다.

---

## 6. 실기에서 돌릴 때의 주의 (요약)

세부 절차는 [`docs/v1.0-signoff.md`](v1.0-signoff.md)에 있다. 빌드 관점에서만 짚으면:

- **화면 꺼짐**: 세션 도중 화면이 꺼지면 표시 피어가 죽는다. **스펙이 이미 막아 둔다** —
  `buildozer.spec`에 `android.wakelock = True`와 짝이 되는 `WAKE_LOCK` 권한이 들어 있어
  별도 조치가 필요 없다. 다만 wakelock이 실기에서 실제로 화면을 붙잡는지는 **미검증**이므로,
  첫 실기 실행에서 화면이 꺼진다면 폰 설정의 화면 꺼짐 시간을 세션보다 길게 늘려 본다
  (개발자 옵션의 "충전 중 화면 켜짐 유지"도 같은 효과).
- **자동 회전**: 스펙이 `orientation = portrait`로 고정하므로 세션 중 회전으로 정렬이
  깨지지는 않는다.
- **화면 밝기**: 최대로 올린다. QR 대비가 디코드율을 좌우한다.
- **아키텍처**: `arm64-v8a`만 빌드한다. 구형 32비트 기기에서는 설치되지 않는다.

---

## 참고 문헌

- buildozer — 저장소 / Docker 사용법: <https://github.com/kivy/buildozer>
- buildozer 기본 스펙(옵션 이름·기본값의 원본): <https://github.com/kivy/buildozer/blob/master/buildozer/default.spec>
- buildozer 설치 문서(Ubuntu 의존성·WSL 주의): <https://github.com/kivy/buildozer/blob/master/docs/source/installation.rst>
- Camera4Kivy README (`android.api = 33`, `p4a.hook`, `camerax_provider`, arm64 경고): <https://github.com/Android-for-Python/Camera4Kivy>
- camerax_provider: <https://github.com/Android-for-Python/camerax_provider>
- camera4kivy + opencv 실전 예제 spec: <https://github.com/Android-for-Python/c4k_opencv_example/blob/main/buildozer.spec>
- p4a opencv 레시피 실패 사례: <https://github.com/kivy/python-for-android/issues/3203>
- Android SDK Platform-Tools (adb): <https://developer.android.com/tools/releases/platform-tools>
