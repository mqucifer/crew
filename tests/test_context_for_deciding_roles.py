"""Roles that decide what to build see the tests by name, not in full (#230).

On the Architect's design note for sprint-metrics#59, test bodies were 123k
characters of a 225k-character prompt.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.tools.repo_context import repository_context


def project(tmp_path: Path) -> Path:
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/report.py").write_text(
        "def format_json_report(cards):\n    return BODY_MARK\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_report.py").write_text(
        "def test_json_has_a_version():\n    assert ASSERTION_MARK\n"
    )
    return tmp_path


def test_a_deciding_role_sees_source_in_full_and_tests_by_name(tmp_path: Path):
    shown = repository_context(project(tmp_path), editing=False)
    assert "BODY_MARK" in shown, "source in full"
    assert "test_json_has_a_version" in shown, "tests named in the index"
    assert "ASSERTION_MARK" not in shown, "but not their bodies"
    assert "Test files are shown above by the tests they define" in shown


def test_the_developer_still_sees_the_tests_whole(tmp_path: Path):
    shown = repository_context(project(tmp_path), editing=True)
    assert "ASSERTION_MARK" in shown and "shown above by the tests" not in shown
