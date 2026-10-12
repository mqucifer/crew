"""A bug's test must fail against the code as merged (crew#407, B3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from crew_org.tools import reproduction
from crew_org.tools.regression import Merged
from crew_org.tools.reproduction import Reproduction, reproduces
from crew_org.tools.sandbox import Mode, Sandbox
from crew_org.tools.workspace import Toolchain

TESTS = """
import pytest
from calc import double

def test_doubles():
    assert double(3) == 6

def test_already_true():
    assert callable(double)

@pytest.mark.skip(reason="needs a database")
def test_needs_a_database():
    assert double(3) == 6
"""


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Merged]:
    """A project whose merged `double` is wrong, with the fix and its tests applied."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def double(n):\n    return n + 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "merged")
    merged = Merged(repo, _git(repo, "rev-parse", "HEAD"))
    (repo / "calc.py").write_text("def double(n):\n    return n * 2\n")
    (repo / "helper.py").write_text("X = 1\n")
    (repo / "test_calc.py").write_text(TESTS)
    monkeypatch.setattr(reproduction, "pytest_command", lambda _w: [sys.executable, "-m", "pytest"])
    monkeypatch.setattr(
        reproduction,
        "toolchain",
        lambda _w: Toolchain(image=None, setup=None, autofix=(), verify=(), problem=None),
    )
    return repo, merged


def _run(repo: Path, merged: Merged, *names: str) -> Reproduction:
    return reproduces(
        repo, merged, [("test_calc.py", n) for n in names], sandbox=Sandbox(mode=Mode.OFF)
    )


def test_a_test_that_fails_as_merged_reproduces_the_bug(fixed):
    found = _run(*fixed, "test_doubles")
    assert found.failed == ["test_calc.py::test_doubles"] and not found.unchecked
    assert found.refusal() == ""


def test_an_edge_that_already_works_may_pass_beside_the_reproduction(fixed):
    found = _run(*fixed, "test_doubles", "test_already_true")
    assert found.passed == ["test_calc.py::test_already_true"]
    assert found.refusal() == ""


def test_tests_that_all_pass_as_merged_are_refused(fixed):
    found = _run(*fixed, "test_already_true")
    assert "none of the tests reproduces the bug" in found.refusal()


def test_a_skipped_test_never_ran_and_is_refused(fixed):
    found = _run(*fixed, "test_needs_a_database")
    assert found.skipped == {"test_calc.py::test_needs_a_database": "needs a database"}
    assert "was skipped, not run: needs a database" in found.refusal()


def test_the_worktree_itself_is_left_as_it_was(fixed):
    repo, merged = fixed
    _run(repo, merged, "test_doubles")
    assert "n * 2" in (repo / "calc.py").read_text()
    assert (repo / "helper.py").exists()
    assert not (repo / reproduction.REPORT).exists()


def test_a_project_without_pytest_is_noted_not_refused(fixed, monkeypatch):
    monkeypatch.setattr(reproduction, "pytest_command", lambda _w: None)
    found = _run(*fixed, "test_doubles")
    assert found.unchecked and found.refusal() == ""


class _Answer:
    class _Test:
        path, test = "test_calc.py", "test_doubles"

    criteria_tests = [_Test()]


class _Sink:
    def note(self, *a, **kw) -> None:
        pass


def test_delivery_checks_only_a_bug(monkeypatch, tmp_path):
    from crew_org.flows import delivery
    from crew_org.tools.github_project import Card

    monkeypatch.setattr(delivery.regression, "merged_base", lambda _w: Merged(tmp_path, "abc"))
    monkeypatch.setattr(
        delivery.reproduction,
        "reproduces",
        lambda *a, **kw: Reproduction(passed=["test_calc.py::test_doubles"]),
    )
    story = Card(item_id="S", number=1, work_type="Story")
    bug = Card(item_id="B", number=2, work_type="Bug")
    assert delivery.not_reproduced(story, tmp_path, _Answer(), _Sink()) == ""
    assert "none of the tests reproduces the bug" in delivery.not_reproduced(
        bug, tmp_path, _Answer(), _Sink()
    )
