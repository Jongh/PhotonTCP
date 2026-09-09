"""APK 빌드 사전조건 점검 하니스 (M12-T01).

무엇을 하는가
=============
`docs/mobile-build.md`의 절차대로 `buildozer android debug`를 돌리기 **전에**,
이 환경이 그 빌드를 감당할 수 있는지를 한 번에 판정한다. M11-T06이 **손으로**
확인했던 툴체인 상태 점검(`docker info` / `wsl -l -v` / `adb` / `buildozer` /
디스크 여유 …)을 스크립트로 고정한 것이다. 점검 항목은 11개이고 각각
``OK`` / ``WARN`` / ``BLOCK`` 중 하나와 사유 한 줄을 낸다:

    (1) Docker daemon         (2) WSL2 normal distro    (3) adb
    (4) buildozer             (5) repo root main.py     (6) camerax_provider/
    (7) buildozer.spec keys   (8) version.regex extract  (9) free disk space
    (10) java                 (11) gradle

(10)·(11)은 WSL2/네이티브 경로에서만 의미가 있으므로 절대 BLOCK을 내지 않는다.

왜 필요한가
===========
M11-T06의 실측이 정확히 "도구가 **없다**"와 "도구는 있는데 **실패한다**"의
구분이었다 — `docker`는 v29.4.2로 설치돼 있었지만 데몬이 정지 상태였고, 대처가
완전히 다르다(설치 vs 기동). 그래서 이 하니스는 그 둘을 **다른 문구로** 보고한다:

    missing  : PATH에 없다            -> 설치해야 한다
    daemon down / failed / timed out : 있는데 응답하지 않는다 -> 기동·복구해야 한다

또 준비 목록으로 쓰려면 **첫 실패에서 멈추면 안 된다.** 어떤 점검이 예외로
죽더라도 나머지는 그대로 돌고, 죽은 점검만 ``BLOCK (check crashed: ...)``으로
표시된다. 하위 프로세스 호출에는 전부 타임아웃이 걸려 있어 데몬이 멈춰 있어도
하니스가 매달리지 않는다.

툴체인 판정 규칙
================
Windows에서 buildozer는 **Docker 경로 또는 WSL2 경로** 중 하나로만 돈다.
따라서 (1)과 (2)는 **적어도 하나가 성립하면 OK/WARN**이고(성립한 쪽 OK, 나머지
WARN), **둘 다 성립하지 않으면 둘 다 BLOCK**이다.

실행 방법
=========
저장소 루트에서::

    python examples/mobile_build_preflight.py

하드웨어도 인자도 필요 없다. 종료 코드는 **BLOCK이 하나라도 있으면 1, 아니면
0**이다(WARN은 0을 유지한다 — 경고는 빌드를 막지 않는다). ``--json``은 두지
않았다: 소비자가 없고, 필요해지면 그때 연다.

출력은 다른 `examples/` 하니스와 같이 **영어 전용**이다 — Windows 콘솔의 코드
페이지가 무엇이든 깨지지 않게 하기 위함이다(설명은 이 docstring과 주석에 한국어로
남긴다).
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

# 설치 없이 저장소 루트에서 바로 돌 수 있게 한다(다른 examples/ 하니스와 동일).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

#: 최초 빌드가 SDK/NDK/gradle 캐시 + opencv 소스 트리로 수 GB를 쓴다.
#: `docs/mobile-build.md` 3절이 "최소 20 GB 여유"를 권한다.
MIN_FREE_GB = 20.0

#: `buildozer.spec` [app]에 반드시 있어야 하는 키. 하나라도 비면 빌드가
#: 도중에(때로는 수십 분 뒤에) 죽으므로 여기서 먼저 잡는다.
REQUIRED_SPEC_KEYS = (
    "package.name",
    "package.domain",
    "source.dir",
    "requirements",
    "android.permissions",
    "android.archs",
    "android.api",
    "p4a.hook",
)

#: `wsl -l -v`에 뜨지만 빌드용 배포판이 **아닌** 것들. Docker Desktop이 자기
#: 백엔드로 심는 것이라, 이것만 있는 상태는 "일반 배포판 없음"이다(M11-T06 실측).
_DOCKER_WSL_DISTROS = frozenset({"docker-desktop", "docker-desktop-data"})

OK = "OK"
WARN = "WARN"
BLOCK = "BLOCK"


# --------------------------------------------------------------------------- #
# 결과 표현
# --------------------------------------------------------------------------- #


@dataclass
class Result:
    """한 점검의 판정 + 사유 한 줄 (+ 필요하면 여러 줄의 대처 안내)."""

    status: str
    reason: str
    hint: str = ""


@dataclass
class Outcome:
    """하위 프로세스 한 번의 결과.

    ``kind``가 도구 **부재**(``missing``)와 도구 **실패**(``failed`` /
    ``timeout`` / ``error``)를 가르는 지점이다. 이 구분이 이 하니스의 존재
    이유이므로 문자열을 합치지 않고 종류를 그대로 들고 다닌다.
    """

    kind: str          # "ok" | "missing" | "failed" | "timeout" | "error"
    returncode: int | None
    text: str          # stdout + stderr, 디코드된 것 (한 줄로 접어 쓴다)

    @property
    def ok(self) -> bool:
        return self.kind == "ok"

    def first_line(self, limit: int = 70) -> str:
        """출력의 첫 의미 있는 줄 (표에 한 줄로 넣기 위한 축약)."""
        for raw in self.text.splitlines():
            line = raw.strip()
            if line:
                return _clip(line, limit)
        return ""

    def error_line(self, limit: int = 70) -> str:
        """실패 사유가 담긴 줄. 없으면 첫 줄로 떨어진다.

        `docker info`는 데몬이 죽어 있어도 ``Client:`` 블록을 먼저 찍고 나서
        오류를 내므로, 첫 줄을 그대로 쓰면 사유가 사라진다.
        """
        for raw in self.text.splitlines():
            line = raw.strip()
            if line and ("error" in line.lower() or "cannot" in line.lower()):
                return _clip(line, limit)
        return self.first_line(limit)


def _clip(line: str, limit: int) -> str:
    """표의 한 줄에 들어가도록 자른다."""
    return line if len(line) <= limit else line[: max(limit - 3, 1)] + "..."


def _decode(raw: bytes) -> str:
    """하위 프로세스 출력 디코드.

    `wsl.exe`는 Windows에서 **UTF-16LE**로 출력하므로(널 바이트가 섞여 나온다)
    그것부터 시도하고, 아니면 UTF-8 → 시스템 기본 순으로 떨어진다. 어느 쪽이든
    실패해서 하니스가 죽는 일은 없게 ``errors="replace"``를 쓴다.
    """
    if not raw:
        return ""
    if b"\x00" in raw[:200]:
        try:
            return raw.decode("utf-16-le", errors="replace")
        except (UnicodeError, LookupError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(sys.getdefaultencoding(), errors="replace")


def _run(cmd: list[str], timeout: float) -> Outcome:
    """하위 명령을 **반드시 타임아웃과 함께** 돌리고 결과를 분류한다.

    하니스가 매달리면 준비 목록으로 쓸 수 없으므로 모든 호출에 타임아웃이 있다
    (정지한 Docker 데몬에 붙는 `docker info`가 대표적으로 오래 끈다).
    """
    exe = shutil.which(cmd[0])
    if exe is None:
        return Outcome("missing", None, "")
    try:
        proc = subprocess.run(  # noqa: S603 - 고정된 진단 명령만 부른다
            [exe, *cmd[1:]],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Outcome("timeout", None, "")
    except OSError as exc:  # 실행 파일은 찾았는데 띄우지 못한 경우
        return Outcome("error", None, str(exc))
    text = _decode(proc.stdout or b"") + _decode(proc.stderr or b"")
    kind = "ok" if proc.returncode == 0 else "failed"
    return Outcome(kind, proc.returncode, text)


def _tool_failure_reason(
    name: str, out: Outcome, *, timeout: float, missing_action: str = "install it"
) -> str:
    """도구 부재/실패를 **다른 문구로** 만든다 (이 하니스의 핵심 요구사항).

    ``missing_action``은 도구가 **없을 때만** 붙는 꼬리말이다 — 없는 것과
    있는데 실패한 것은 대처가 다르므로 문구를 공유하지 않는다.
    """
    if out.kind == "missing":
        return f"`{name}` is NOT on PATH (not installed) -- {missing_action}"
    if out.kind == "timeout":
        return f"`{name}` found but timed out after {timeout:g}s -- not responding"
    if out.kind == "error":
        return f"`{name}` found but could not be launched: {out.first_line()}"
    return (
        f"`{name}` found but the command FAILED (exit {out.returncode}): "
        f"{out.first_line()}"
    )


def _read_spec() -> configparser.RawConfigParser:
    """`buildozer.spec`을 buildozer와 같은 방식으로 읽는다.

    ``RawConfigParser``인 이유: 값에 ``%``가 들어가면 기본 보간이 터지고,
    ``version.regex``처럼 정규식 리터럴이 값으로 들어 있는 키를 그대로 얻어야
    한다. buildozer 자신도 보간 없이 읽는다.
    """
    parser = configparser.RawConfigParser(strict=False)
    path = os.path.join(_REPO_ROOT, "buildozer.spec")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


# --------------------------------------------------------------------------- #
# 개별 점검 — 각 함수는 Result를 돌려주거나, 죽어도 된다(runner가 격리한다).
# --------------------------------------------------------------------------- #


def check_docker() -> Result:
    """(1) `docker` 존재 + **데몬 응답**.

    존재와 응답을 두 단계로 나눠 본다. M11-T06의 실측 상태(설치돼 있으나 데몬
    정지)가 정확히 이 두 단계 사이에 있고, 대처가 "설치"가 아니라 "기동"이다.
    """
    ver = _run(["docker", "--version"], timeout=15)
    if ver.kind == "missing":
        return Result(
            WARN,
            "`docker` is NOT on PATH (not installed) -- Docker build path unavailable",
            "Install Docker Desktop, or use the WSL2 path (docs/mobile-build.md 2).",
        )
    if not ver.ok:
        return Result(
            WARN,
            _tool_failure_reason("docker", ver, timeout=15),
            "Repair the Docker installation, or use the WSL2 path.",
        )

    version_line = ver.first_line(40)
    # 데몬 점검. 정지 상태에서는 `error during connect ...`가 뜨고 exit != 0.
    info = _run(["docker", "info"], timeout=25)
    if info.ok:
        return Result(OK, f"{version_line}; daemon responds to `docker info`")
    if info.kind == "timeout":
        return Result(
            WARN,
            f"{version_line} present, but `docker info` timed out after 25s "
            "-- daemon not responding",
            "Restart Docker Desktop and retry (docs/mobile-build.md 1-1).",
        )
    return Result(
        WARN,
        f"{version_line} present, but the DAEMON IS DOWN "
        f"(`docker info` exit {info.returncode}): {info.error_line(45)}",
        'Start it: Start-Process "$env:ProgramFiles\\Docker\\Docker\\Docker '
        'Desktop.exe", then re-run (docs/mobile-build.md 1-1).',
    )


def _parse_wsl_distros(text: str) -> list[tuple[str, str, str]]:
    """`wsl -l -v` 출력을 (name, state, version) 목록으로 판다.

    출력에는 선택 표시 ``*``와 헤더 줄이 섞여 있고, Windows 로캘에 따라 헤더
    문구가 번역된다. 그래서 헤더는 문구로 알아보지 않고 **VERSION 열이 숫자가
    아닌 줄**을 버리는 방식으로 걸러 낸다(로캘 무관).
    """
    distros: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.replace("﻿", "").strip()
        if not line:
            continue
        if line.startswith("*"):
            line = line[1:].strip()
        parts = line.split()
        if len(parts) < 3:
            continue
        name, state, version = parts[0], parts[1], parts[-1]
        if not version.isdigit():
            continue  # 헤더 줄(NAME STATE VERSION 등)
        distros.append((name, state, version))
    return distros


def check_wsl() -> Result:
    """(2) WSL2의 **일반** 배포판 유무.

    `docker-desktop`(+`-data`)만 있는 상태는 WSL2 빌드 경로가 성립하지 않는
    상태다 — buildozer를 돌릴 리눅스가 없다(M11-T06 실측이 정확히 이 상태였다).
    WSL1 배포판만 있으면 buildozer 문서가 금지하므로 성립으로 치지 않는다.
    """
    out = _run(["wsl", "-l", "-v"], timeout=25)
    if out.kind == "missing":
        return Result(
            WARN,
            "`wsl` is NOT on PATH (WSL not installed) -- WSL2 build path unavailable",
            "Install it: wsl --install -d Ubuntu-24.04 (docs/mobile-build.md 2-1).",
        )
    if not out.ok:
        return Result(
            WARN,
            _tool_failure_reason("wsl", out, timeout=25)
            + " -- often means no distro is installed",
            "wsl --install -d Ubuntu-24.04 (docs/mobile-build.md 2-1).",
        )

    distros = _parse_wsl_distros(out.text)
    general = [d for d in distros if d[0].lower() not in _DOCKER_WSL_DISTROS]
    v2 = [d for d in general if d[2] == "2"]
    if v2:
        listing = ", ".join(f"{n} ({s}, v{v})" for n, s, v in v2)
        return Result(OK, f"WSL2 general distro present: {listing}")
    if general:
        listing = ", ".join(f"{n} (v{v})" for n, _s, v in general)
        return Result(
            WARN,
            f"`wsl` works but the general distro(s) are WSL1, not WSL2: {listing}",
            "wsl --set-version <distro> 2 (docs/mobile-build.md 2-1).",
        )
    others = ", ".join(n for n, _s, _v in distros) or "none"
    return Result(
        WARN,
        f"`wsl` works but there is NO general distro (only: {others})",
        "wsl --install -d Ubuntu-24.04 (docs/mobile-build.md 2-1).",
    )


def check_adb() -> Result:
    """(3) `adb` — 빌드가 아니라 **설치**(T06)에 필요하므로 최대 WARN이다."""
    out = _run(["adb", "version"], timeout=15)
    if out.ok:
        return Result(OK, f"{out.first_line(60)}")
    hint = (
        "Get Android SDK Platform-Tools and put it on PATH "
        "(docs/mobile-build.md 4-1). Not needed to BUILD, only to install."
    )
    reason = _tool_failure_reason(
        "adb",
        out,
        timeout=15,
        missing_action="needed only to INSTALL the APK (T06), not to build",
    )
    return Result(WARN, reason, hint)


def check_buildozer() -> Result:
    """(4) 호스트 `buildozer`.

    Windows 호스트에는 없는 것이 **정상**이다 — buildozer는 리눅스에서만 돌고
    Docker 이미지나 WSL2 안에 있으면 된다. 그래서 부재는 BLOCK이 아니라 WARN이다.
    """
    out = _run(["buildozer", "--version"], timeout=30)
    if out.ok:
        return Result(OK, f"host buildozer: {out.first_line(50)}")
    if out.kind == "missing" and sys.platform.startswith("win"):
        return Result(
            WARN,
            "`buildozer` is NOT on PATH -- expected on Windows "
            "(it runs inside Docker/WSL2, not on the host)",
            "Nothing to do if you use the Docker path; the WSL2 path installs "
            "it inside the distro (docs/mobile-build.md 2-4).",
        )
    return Result(
        WARN,
        _tool_failure_reason("buildozer", out, timeout=30),
        "camera4kivy needs buildozer >= 1.3.0 (docs/mobile-build.md 1-2).",
    )


def check_main_py() -> Result:
    """(5) 레포 루트 `main.py` — p4a가 고정된 이름으로 찾는 진입점이다."""
    path = os.path.join(_REPO_ROOT, "main.py")
    if not os.path.isfile(path):
        return Result(
            BLOCK,
            "repo root `main.py` is MISSING -- p4a has no python entry point",
            "It is committed to this repo; a missing file means a bad checkout "
            "(docs/mobile-build.md 0-1).",
        )
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    # 있기만 하면 되는 것이 아니라 앱을 부르는 파일이어야 한다. 이름만 같고
    # 내용이 다른 파일이면 앱이 뜨자마자 죽는다(문서 5-2의 증상).
    if "photontcp.mobile.app" not in body:
        return Result(
            BLOCK,
            "repo root `main.py` exists but does NOT import "
            "photontcp.mobile.app -- wrong entry point",
            "Expected: `from photontcp.mobile.app import main` + `main()` "
            "(docs/mobile-build.md 0-1).",
        )
    return Result(OK, "repo root `main.py` present and imports photontcp.mobile.app")


def check_camerax_provider() -> Result:
    """(6) `camerax_provider/` — `.gitignore` 대상이라 빌드 환경마다 clone해야 한다."""
    root = os.path.join(_REPO_ROOT, "camerax_provider")
    hook = os.path.join(root, "gradle_options.py")
    clone_hint = (
        "git clone https://github.com/Android-for-Python/camerax_provider.git "
        "&& rm -rf camerax_provider/.git   (run in the repo root; "
        "docs/mobile-build.md 0-2)"
    )
    if not os.path.isdir(root):
        return Result(
            BLOCK,
            "`camerax_provider/` is MISSING -- p4a.hook cannot be resolved "
            "(it is .gitignore'd, so every build env must clone it)",
            clone_hint,
        )
    if not os.path.isfile(hook):
        return Result(
            BLOCK,
            "`camerax_provider/` exists but `gradle_options.py` is MISSING "
            "-- p4a.hook points at that file",
            clone_hint,
        )
    return Result(OK, "`camerax_provider/gradle_options.py` present")


def _spec_missing_reason(exc: FileNotFoundError) -> str:
    """`buildozer.spec` 부재를 환경 상태 문구로 옮긴다 (하니스 버그가 아니다)."""
    return (
        f"`buildozer.spec` NOT found at {exc.args[0]} -- run this from the repository "
        "root (the spec is committed; a missing file means a wrong cwd or an "
        "incomplete checkout)"
    )


def check_spec_keys() -> Result:
    """(7) `buildozer.spec`의 필수 키 파싱."""
    try:
        parser = _read_spec()
    except FileNotFoundError as exc:
        # 파일이 없는 것은 하니스의 버그가 아니라 **환경 상태**다 (잘못된 cwd 또는
        # 잘못된 체크아웃). 점검 5가 `main.py` 부재를 환경으로 다루는 것과 같은 종류다
        # — 예외로 흘려보내면 "the check itself CRASHED"로 잘못 안내된다 (M12-review 사소).
        return Result(BLOCK, _spec_missing_reason(exc))
    if not parser.has_section("app"):
        return Result(BLOCK, "buildozer.spec has no [app] section")
    missing = [
        key
        for key in REQUIRED_SPEC_KEYS
        if not parser.has_option("app", key) or not parser.get("app", key).strip()
    ]
    if missing:
        return Result(
            BLOCK,
            f"buildozer.spec [app] missing/empty key(s): {', '.join(missing)}",
        )
    return Result(
        OK,
        f"buildozer.spec [app] has all {len(REQUIRED_SPEC_KEYS)} required keys "
        f"(archs={parser.get('app', 'android.archs').strip()}, "
        f"api={parser.get('app', 'android.api').strip()})",
    )


def check_version_regex() -> Result:
    """(8) `version.regex`를 **실제로 적용**해 버전이 뽑히는지 실측한다.

    회귀 지점이다(M11-impl 이슈 3): buildozer의 ``get_version()``은
    ``re.search(regex, data)``를 **플래그 없이** 부르므로 ``^``가 줄 시작을
    잡지 못한다. 그래서 스펙의 정규식은 앞에 개행을 직접 넣어 두었고, 여기서는
    그 호출을 그대로 흉내 내 본다 — 키가 있다는 것만 봐서는 이 함정을 못 잡는다.
    """
    try:
        parser = _read_spec()
    except FileNotFoundError as exc:
        return Result(BLOCK, _spec_missing_reason(exc))
    if parser.has_option("app", "version"):
        # buildozer는 version과 version.regex의 공존을 예외로 거부한다.
        return Result(
            BLOCK,
            "buildozer.spec declares a hardcoded `version` -- it conflicts with "
            "version.regex/version.filename (buildozer raises)",
        )
    if not parser.has_option("app", "version.regex") or not parser.has_option(
        "app", "version.filename"
    ):
        return Result(
            BLOCK,
            "buildozer.spec has neither `version` nor "
            "`version.regex` + `version.filename`",
        )

    regex = parser.get("app", "version.regex").strip()
    filename = parser.get("app", "version.filename").strip()
    path = os.path.normpath(os.path.join(_REPO_ROOT, filename))
    if not os.path.isfile(path):
        return Result(
            BLOCK,
            f"version.filename points at a missing file: {filename}",
        )
    with open(path, encoding="utf-8") as handle:
        data = handle.read()
    # buildozer.get_version()과 동일: 플래그 없는 re.search + group(1).
    match = re.search(regex, data)
    if match is None:
        return Result(
            BLOCK,
            f"version.regex does NOT match {filename} -- buildozer would abort "
            'with "Unable to find capture version"',
            "re.search is called WITHOUT flags, so `^` does not anchor lines; "
            "the regex must match a literal newline (docs/mobile-build.md 5-2).",
        )
    version = match.group(1)
    if not version:
        return Result(
            BLOCK,
            f"version.regex matched {filename} but captured an EMPTY version",
        )
    return Result(
        OK,
        f"version.regex extracted {version!r} from {filename} "
        f"-> bin/photontcp-{version}-arm64-v8a-debug.apk",
    )


def _free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024**3)


def check_disk() -> Result:
    """(9) 디스크 여유.

    두 곳을 본다: 프로젝트 트리(`.buildozer/`, `bin/`)와 홈(`~/.buildozer`의
    전역 SDK/NDK 캐시). 서로 다른 드라이브일 수 있고, 둘 중 하나만 부족해도
    빌드가 중간에 알 수 없는 I/O 오류로 죽는다(문서 5-2의 마지막 행).
    """
    spots = {"project": _REPO_ROOT, "home": os.path.expanduser("~")}
    frees: dict[str, float] = {}
    for label, path in spots.items():
        try:
            frees[label] = _free_gb(path)
        except OSError as exc:  # 경로가 사라졌거나 접근 불가
            frees[label] = -1.0
            _ = exc
    listing = ", ".join(
        f"{label} {gb:.1f} GB" if gb >= 0 else f"{label} unknown"
        for label, gb in frees.items()
    )
    low = [label for label, gb in frees.items() if 0 <= gb < MIN_FREE_GB]
    if low:
        return Result(
            WARN,
            f"free space below the recommended {MIN_FREE_GB:.0f} GB on "
            f"{', '.join(low)} ({listing})",
            "First build pulls SDK+NDK and compiles opencv from source "
            "(docs/mobile-build.md 3).",
        )
    return Result(OK, f"free space OK (>= {MIN_FREE_GB:.0f} GB): {listing}")


def check_java() -> Result:
    """(10) `java` — WSL2/네이티브 경로에서만 필요하다(Docker 이미지에 들어 있다)."""
    # `java -version`은 stderr로 찍고 exit 0을 낸다 -- 출력 위치에 기대지 않는다.
    out = _run(["java", "-version"], timeout=20)
    if out.ok:
        return Result(OK, f"{out.first_line(60)} (JDK 17 required on the WSL2 path)")
    return Result(
        WARN,
        _tool_failure_reason(
            "java",
            out,
            timeout=20,
            missing_action="needed only on the WSL2/native path",
        ),
        "The Docker image ships its own JDK (docs/mobile-build.md 1); the WSL2 "
        "path needs openjdk-17-jdk (2-3).",
    )


def check_gradle() -> Result:
    """(11) `gradle` — buildozer가 자체 wrapper를 쓰므로 없어도 무방하다."""
    out = _run(["gradle", "--version"], timeout=45)
    if out.ok:
        line = next(
            (ln.strip() for ln in out.text.splitlines() if "Gradle" in ln),
            out.first_line(50),
        )
        return Result(OK, f"host gradle: {line}")
    return Result(
        WARN,
        _tool_failure_reason(
            "gradle",
            out,
            timeout=45,
            missing_action="fine: p4a downloads its own gradle wrapper",
        ),
        "No action required unless the gradle step fails "
        "(docs/mobile-build.md 5-1).",
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #

#: (번호, 제목, 함수). 번호는 마일스톤 M12 완료 기준 1의 열거 순서다.
CHECKS: list[tuple[int, str, Callable[[], Result]]] = [
    (1, "Docker daemon", check_docker),
    (2, "WSL2 general distro", check_wsl),
    (3, "adb", check_adb),
    (4, "buildozer", check_buildozer),
    (5, "repo root main.py", check_main_py),
    (6, "camerax_provider/", check_camerax_provider),
    (7, "buildozer.spec keys", check_spec_keys),
    (8, "version.regex extract", check_version_regex),
    (9, "free disk space", check_disk),
    (10, "java (WSL2 path)", check_java),
    (11, "gradle (optional)", check_gradle),
]

#: (1)·(2)의 번호. 둘 중 **적어도 하나**가 성립해야 빌드 경로가 있다.
_TOOLCHAIN_PATHS = (1, 2)


def _run_checks() -> list[tuple[int, str, Result]]:
    """모든 점검을 **서로 격리해서** 돌린다.

    한 점검이 예외로 죽어도 나머지는 계속 돈다 — 첫 실패에서 멈추는 하니스는
    준비 목록으로 쓸 수 없다. 죽은 점검은 그 자체가 BLOCK이며, 무엇이 어떻게
    죽었는지가 사유 줄에 남는다.
    """
    rows: list[tuple[int, str, Result]] = []
    for number, title, func in CHECKS:
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 - 격리가 목적이다
            result = Result(
                BLOCK,
                f"the check itself CRASHED: {type(exc).__name__}: {exc}",
                "This is a bug in the harness (or an unreadable file), not a "
                "toolchain state; the other checks above/below still ran.",
            )
        rows.append((number, title, result))
    return rows


def _apply_toolchain_rule(rows: list[tuple[int, str, Result]]) -> None:
    """Docker 경로와 WSL2 경로 중 **하나도** 성립하지 않으면 둘 다 BLOCK으로 올린다.

    개별 점검이 각자 WARN을 내는 이유가 이것이다 — 한쪽이 없는 것은 다른 쪽이
    있으면 문제가 아니다. 판정은 둘을 함께 봐야만 내릴 수 있으므로 여기서 한다.
    (점검이 예외로 죽은 경우는 OK가 아니므로 자연히 '성립하지 않음'으로 센다.)
    """
    paths = [row for row in rows if row[0] in _TOOLCHAIN_PATHS]
    if any(result.status == OK for _n, _t, result in paths):
        return
    for _n, _t, result in paths:
        result.status = BLOCK
        result.reason += "  [neither the Docker nor the WSL2 path is usable]"


def _render(rows: list[tuple[int, str, Result]]) -> None:
    """사람이 읽는 표 + 대처 안내를 찍는다."""
    print("=" * 78)
    print("PhotonTCP Android build preflight (M12-T01)")
    print(f"repo root : {_REPO_ROOT}")
    print(f"platform  : {sys.platform}   python {sys.version.split()[0]}")
    print("Run this BEFORE `buildozer android debug`. See docs/mobile-build.md.")
    print("=" * 78)
    print()
    width = max(len(title) for _n, title, _r in rows)
    for number, title, result in rows:
        print(f"[{number:>2}] {result.status:<5} {title:<{width}}  {result.reason}")

    todo = [(n, t, r) for n, t, r in rows if r.status != OK and r.hint]
    if todo:
        print()
        print("-" * 78)
        print("What to do")
        for number, title, result in todo:
            print(f"  ({number}) {title} [{result.status}]")
            print(f"      {result.hint}")

    blocks = sum(1 for _n, _t, r in rows if r.status == BLOCK)
    warns = sum(1 for _n, _t, r in rows if r.status == WARN)
    oks = len(rows) - blocks - warns
    print()
    print("-" * 78)
    print(f"{len(rows)} checks: {oks} OK / {warns} WARN / {blocks} BLOCK")
    if blocks:
        print("VERDICT: NOT READY -- resolve every BLOCK before building.")
    else:
        print("VERDICT: READY -- no BLOCK. WARNs do not stop the build.")
    print("-" * 78)


def main(argv: list[str] | None = None) -> int:
    """모든 점검을 돌리고 종료 코드를 돌려준다 (BLOCK이 있으면 1, 없으면 0).

    인자를 받지 않는다. ``--json``은 의도적으로 없다(마일스톤 M12-T01: 소비자가
    없어서 열지 않는다). 그래서 인자가 오면 조용히 무시하지 않고 알려 준다.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        print(f"This harness takes no arguments (got: {' '.join(argv)}).")
        print("Usage: python examples/mobile_build_preflight.py")
        return 2

    rows = _run_checks()
    _apply_toolchain_rule(rows)
    _render(rows)
    return 1 if any(r.status == BLOCK for _n, _t, r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
