"""문서가 버전 값을 **복제하지 않는지** 기계가 무는 테스트 (M12-T04).

버전 선언처는 `pyproject.toml`의 `[project].version` 하나다(`docs/conventions.md`).
M11-T08이 **코드**에서 두 번째 선언처를 없앴지만(`tests/test_version_single_source.py`),
문서 두 곳이 값을 복제하고 있어 v0.11.0 릴리즈로 즉시 낡았다. tide 규약이 정한 방향은
"갱신 단계를 추가"가 아니라 **복제 제거**이므로, 값이 다시 문서로 새어 들어오는 것을
여기서 막는다.

**무엇을 무는가 (좁게)**: *"현재 버전"* 류의 문구가 **그 프로젝트의 지금 버전을 값으로
주장**하는 형태만 잡는다. 다음은 **정상**이므로 잡으면 안 된다.

* CHANGELOG의 릴리즈 헤딩(`## [0.11.0]`) — 이력이지 현재 값 주장이 아니다.
* 배경 서술의 과거 버전 언급(*"0.1.0 vs 0.10.0"*, *"기반 버전: v0.11.0"*).
* 버전을 인자로 쓰는 명령 예시(`adb install ... photontcp-0.10.0-....apk`).

**어디를 보는가**: `docs/` 아래 마크다운. 단 `docs/reports/`·`docs/milestones/`는 제외한다 —
보고서·마일스톤은 **그 시점의 사실을 적는 이력**이라 값이 박혀 있는 것이 정상이고, 이력을
사후에 고치는 것이 오히려 규약 위반이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

#: 이력 문서 — 그 시점의 값이 박혀 있는 것이 정상이라 검사 대상이 아니다.
EXCLUDED_DIRS = ("reports", "milestones")

#: "현재(의) 버전은 X.Y.Z" 형태만 문다.
#:
#: 1) ``현재 버전`` … 뒤 짧은 범위 안의 버전 리터럴 (예: ``- 현재 버전: `0.10.0`.``)
#: 2) ``현재`` 바로 뒤에 오는 버전 리터럴 (예: ``… — 현재 `0.10.0`.``)
#:
#: 둘 다 ``현재``를 요구하므로 릴리즈 헤딩·과거 버전 서술·명령 예시는 걸리지 않는다.
#: 뒤따르는 리터럴은 백틱 안이거나 ``v`` 접두 형태만 인정해 산문 속 숫자를 피한다.
_VERSION_LITERAL = r"(?:`v?\d+\.\d+\.\d+[^`\n]*`|\bv\d+\.\d+\.\d+)"
VERSION_DUP_PATTERN = re.compile(
    r"현재(?:의)?\s*버전[^\n]{0,20}?" + _VERSION_LITERAL
    + r"|현재(?:의)?\s*" + _VERSION_LITERAL
)


def governed_docs() -> list[Path]:
    """검사 대상 마크다운 목록(이력 디렉터리 제외)."""
    return sorted(
        path
        for path in DOCS.rglob("*.md")
        if not any(part in EXCLUDED_DIRS for part in path.relative_to(DOCS).parts[:-1])
    )


def _hits(text: str) -> list[str]:
    return [m.group(0) for m in VERSION_DUP_PATTERN.finditer(text)]


def test_governed_set_is_not_empty() -> None:
    """검사 대상이 0개면 이 테스트 전체가 공허하게 통과한다 — 그 상태를 먼저 막는다."""
    names = {p.name for p in governed_docs()}
    assert {"conventions.md", "project-context.md"} <= names, (
        f"검사 대상에 규약·컨텍스트 문서가 없다: {sorted(names)}"
    )


def test_no_current_version_literal_in_docs() -> None:
    """`docs/`(이력 제외)에 '현재 버전 = 값' 복제가 0건이어야 한다."""
    offenders: list[str] = []
    for path in governed_docs():
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for hit in _hits(line):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_no}: {hit}")
    assert not offenders, (
        "문서가 버전 값을 복제하고 있다 — 선언처(pyproject.toml)를 가리키는 문장만 남겨라"
        "(갱신이 아니라 제거로 닫는다):\n  " + "\n  ".join(offenders)
    )


def test_pattern_catches_the_forms_that_were_removed() -> None:
    """가드가 실제로 무는지(공허하지 않은지) — M12-T04가 지운 두 문장 형태로 확인한다."""
    removed = [
        "- 현재 버전: `0.10.0`.",
        "- 버전 단일 원본: `pyproject.toml` 의 `[project].version` — 현재 `0.10.0`.",
        "`<version>`은 스펙이 읽어온 값이다 (현재 `0.10.0`).",
        "현재 버전은 v1.2.3 이다",
    ]
    for line in removed:
        assert _hits(line), f"가드가 이 복제를 놓친다: {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        "## [0.11.0] - 2026-09-09",
        "## [1.0.0]",
        "- 기반 버전: v0.11.0",
        "0.1.0 vs 0.10.0 의 정렬 문제",
        "adb install -r bin/photontcp-0.10.0-arm64-v8a-debug.apk",
        "통과하면 `v1.0.0`을 릴리즈한다.",
        "buildozer --version    # 1.3.0 이상인지 확인",
        "Docker는 설치돼 있으나(v29.4.2) 데몬이 정지 상태",
        "현재 상태는 `docs/v1.0-signoff.md` 참조.",
        "- 버전 단일 원본: `pyproject.toml` 의 `[project].version` (값은 그 파일에서 읽는다).",
    ],
)
def test_pattern_does_not_false_positive(line: str) -> None:
    """이력·과거 언급·명령 예시를 오탐하면 가드가 문서를 못 쓰게 만든다."""
    assert not _hits(line), f"오탐: {line!r}"


def test_excluded_history_still_contains_version_literals() -> None:
    """제외 규칙이 **실측으로** 의미가 있는지 — 보고서·마일스톤에 실제 값이 박혀 있다.

    이 단언이 실패하면 제외가 가상의 문제를 막고 있는 것이므로, 그때는 제외를 지워야 한다.
    """
    literal = re.compile(r"\bv?\d+\.\d+\.\d+\b")
    excluded = [
        p
        for p in DOCS.rglob("*.md")
        if any(part in EXCLUDED_DIRS for part in p.relative_to(DOCS).parts[:-1])
    ]
    assert excluded, "제외 대상 디렉터리에 문서가 없다"
    with_literals = [
        p for p in excluded if literal.search(p.read_text(encoding="utf-8"))
    ]
    assert with_literals, (
        "보고서·마일스톤에 버전 리터럴이 하나도 없다 — 제외 규칙이 불필요해졌다"
    )


def test_changelog_headings_are_not_matched() -> None:
    """CHANGELOG 전체를 실제로 훑어 릴리즈 헤딩이 오탐되지 않음을 실측한다."""
    changelog = REPO_ROOT / "CHANGELOG.md"
    if not changelog.exists():  # pragma: no cover - 레포에 항상 있다
        pytest.skip("CHANGELOG.md 없음")
    text = changelog.read_text(encoding="utf-8")
    assert re.search(r"(?m)^##\s*\[\d+\.\d+\.\d+\]", text), (
        "CHANGELOG에 릴리즈 헤딩이 없다 — 이 오탐 실측이 공허해진다"
    )
    assert not _hits(text), f"CHANGELOG를 오탐한다: {_hits(text)}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
