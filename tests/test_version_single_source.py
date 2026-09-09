"""버전 선언처가 하나임을 기계가 무는 테스트.

단일 원본은 `pyproject.toml`의 `[project].version`(`docs/conventions.md`).
`photontcp.__version__`은 그 값의 파생이어야 하며, 둘이 갈라지면 여기서 실패한다.
릴리즈가 `pyproject.toml`만 범프해도 이 테스트가 계속 통과해야 정상이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import photontcp

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_version() -> str:
    """`pyproject.toml`을 패키지 코드와 **독립적으로** 파싱한다(순환 방지).

    `requires-python = ">=3.10"`이므로 `tomllib`(3.11+)가 없을 수 있다. skip으로 도망가면
    검사가 공허해지므로 정규식 폴백으로 3.10에서도 실제로 비교한다.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - 3.10 경로
        section = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
        assert section is not None, "pyproject.toml에 [project] 테이블이 없다"
        match = re.search(
            r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', section.group(1)
        )
        assert match is not None, "[project].version 을 찾지 못했다"
        return match.group(1)
    return tomllib.loads(text)["project"]["version"]


def test_module_version_matches_pyproject() -> None:
    declared = _declared_version()
    assert photontcp.__version__ == declared, (
        "버전 선언처가 갈라졌다: "
        f"photontcp.__version__={photontcp.__version__!r} != "
        f"pyproject [project].version={declared!r}. "
        "단일 원본은 pyproject.toml 이다 (docs/conventions.md)."
    )


def test_version_is_not_the_unknown_fallback() -> None:
    """폴백 값이 조용히 유효한 버전 행세를 하지 않는지."""
    assert photontcp.__version__ != photontcp._UNKNOWN_VERSION


def test_no_hardcoded_version_literal_in_package_init() -> None:
    """`__version__ = "x.y.z"` 형태의 두 번째 선언처가 되살아나지 않는지."""
    source = Path(photontcp.__file__).read_text(encoding="utf-8")
    assert re.search(r'(?m)^\s*__version__\s*=\s*["\']\d', source) is None, (
        "photontcp/__init__.py에 하드코딩된 버전 상수가 다시 생겼다 — "
        "단일 원본(pyproject.toml)에서 파생시켜라."
    )


def test_pyproject_fallback_parser_agrees_with_declared_version() -> None:
    """미설치 트리에서 쓰이는 파생 경로 자체도 같은 값을 낸다."""
    assert photontcp._version_from_pyproject() == _declared_version()


def test_pyproject_wins_over_stale_installed_metadata() -> None:
    """소스 트리에서는 `pyproject.toml`이 설치 메타데이터를 **이긴다**.

    `pip install -e .` 된 트리에서 metadata 는 설치 시점 값으로 굳는다. 파생이
    metadata 를 먼저 보면, 릴리즈가 `pyproject.toml`만 범프한 순간 둘이 갈라져
    `test_module_version_matches_pyproject`가 실패하고 **릴리즈 절차 자체가 막힌다**
    (M11-review 권장 2). 그래서 소스 트리에서는 파일이 우선이다.

    낡은 metadata 를 흉내 내 파생 순서를 직접 문다 — 순서를 뒤집으면 여기가 붉는다.
    """
    import importlib.metadata as md

    real_version = md.version
    md.version = lambda _name: "0.0.1-stale-from-editable-install"  # type: ignore[assignment]
    try:
        assert photontcp._resolve_version() == _declared_version(), (
            "설치 메타데이터가 pyproject.toml 을 덮었다 — 파생 순서가 뒤집혔다."
        )
    finally:
        md.version = real_version  # type: ignore[assignment]


def test_unknown_fallback_only_when_no_source_and_no_metadata() -> None:
    """pyproject 도 metadata 도 없으면 `"0+unknown"` (APK 등 패키징 경로)."""
    import importlib.metadata as md

    real_version = md.version
    real_from_pyproject = photontcp._version_from_pyproject
    md.version = lambda _name: (_ for _ in ()).throw(md.PackageNotFoundError("photontcp"))  # type: ignore[assignment]
    photontcp._version_from_pyproject = lambda: None  # type: ignore[assignment]
    try:
        assert photontcp._resolve_version() == photontcp._UNKNOWN_VERSION
    finally:
        md.version = real_version  # type: ignore[assignment]
        photontcp._version_from_pyproject = real_from_pyproject  # type: ignore[assignment]


def test_declared_version_is_pep440_ish() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+([.\w+-]*)", _declared_version()), (
        "예상치 못한 버전 형식"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
