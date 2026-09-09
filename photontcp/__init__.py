"""PhotonTCP — a TCP-like reliable transport over a pluggable Channel abstraction.

All higher layers depend only on the swappable :class:`~photontcp.channel.Channel`
interface and a fixed-format packet, so transports (loopback, optical, etc.) can be
substituted without changing session or reliability logic.

버전 단일 원본
--------------
버전의 유일한 선언처는 `pyproject.toml`의 `[project].version`이다
(`docs/conventions.md`의 "버전 파일 위치"). 여기의 `__version__`은 **상수가 아니라 파생값**이라
릴리즈의 버전 범프는 `pyproject.toml` 한 곳만 고치면 끝난다.

파생 순서:

1. 패키지 상위에 `pyproject.toml`이 **있으면**(= 소스 트리에서 돌고 있다) 그것을 직접 읽는다.
   **`importlib.metadata`보다 먼저 보는 이유**가 중요하다 — `pip install -e .` 된 개발 트리에서
   metadata는 **설치 시점 값으로 고정**되므로, 릴리즈가 `pyproject.toml`만 범프하면 재설치
   전까지 둘이 갈라지고 "선언처가 하나"를 무는 테스트가 오히려 릴리즈를 막는다(M11-review
   권장 2). 소스 트리에서는 **파일이 곧 진실**이므로 파일을 먼저 본다.
2. 그렇지 않으면(설치된 배포판 — 소스 트리 밖이라 `pyproject.toml`이 없다)
   `importlib.metadata.version("photontcp")`. 이 값 역시 빌드 시점의 `pyproject.toml`에서 나오므로
   같은 단일 원본의 파생이다.
3. 둘 다 불가능한 예외적 상황(메타데이터도 pyproject도 없는 트리 — 예: `pyproject.toml`을 담지
   않은 채 소스만 묶인 안드로이드 APK)에서는 import를 깨뜨리는 대신 `"0+unknown"`을 노출한다.
   이 값은 어떤 실제 릴리즈와도 겹치지 않아 "모름"이 조용히 유효한 버전으로 오인되지 않는다.

**낡은 상수를 폴백으로 두지 않는 이유**: 상수를 두면 선언처가 다시 둘이 되고, M11-T08이 고친
바로 그 불일치(0.1.0 vs 0.10.0)가 재발한다.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["__version__"]

_UNKNOWN_VERSION = "0+unknown"


def _version_from_pyproject() -> str | None:
    """개발 트리의 `pyproject.toml`에서 `[project].version`을 읽는다(없으면 None)."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - 3.10 경로
        tomllib = None

    if tomllib is not None:
        try:
            return tomllib.loads(text)["project"]["version"]
        except (KeyError, TypeError, ValueError):
            return None

    # 3.10 폴백: `[project]` 테이블 안의 첫 `version = "..."` 만 본다.
    section = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
    if section is None:
        return None
    match = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', section.group(1))
    return match.group(1) if match else None


def _resolve_version() -> str:
    # 소스 트리(pyproject.toml 이 곁에 있다)면 파일이 곧 진실이다 — editable 설치의
    # 낡은 metadata 가 릴리즈 범프를 가리지 않게 파일을 먼저 본다(모듈 docstring 참조).
    from_file = _version_from_pyproject()
    if from_file is not None:
        return from_file

    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("photontcp")
    except PackageNotFoundError:
        return _UNKNOWN_VERSION


__version__ = _resolve_version()
