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


# --- in the default branch's workspace (crew#839) ---------------------------------------

DUMP = """test_calc.py ..Timeout (0:01:00)!
Thread 0x0000ffff91954020 (most recent call first):
  File "/work/.venv/lib/python3.12/site-packages/psycopg/connection.py", line 495 in wait
  File "/work/test_calc.py", line 6 in test_doubles
  File "/work/.venv/lib/python3.12/site-packages/_pytest/python.py", line 167 in pytest_pyfunc_call
"""


class _Held:
    """A workspace container: each run writes the report `cases` gives, `path::name` to
    an outcome, or ends in `output`'s hang."""

    made: list[_Held] = []

    def __init__(self, copy: Path, name: str, declared=None, **kw) -> None:
        self.copy, self.name, self.declared = copy, name, declared
        self.ran: list[list[str]] = []
        self.seen: dict[str, str] = {}
        self.stopped = False
        _Held.made.append(self)

    cases: dict[str, str] = {}
    output = ""

    def start(self):
        return _Done(True, "")

    def setup(self):
        return None

    def exec(self, command: list[str], **kw):
        from crew_org.tools.workspace import REPORT as HELD_REPORT

        self.ran.append(command)
        self.seen = {p.name: p.read_text() for p in self.copy.glob("*.py")}
        if not self.output:
            cases = []
            for test, outcome in self.cases.items():
                path, _, name = test.partition("::")
                inner = {
                    "failed": "<failure message='x'/>",
                    "skipped": "<skipped message='no db'/>",
                }
                cases.append(
                    f"<testcase file='{path}' classname='{path[:-3]}' name='{name}'>"
                    f"{inner.get(outcome, '')}</testcase>"
                )
            report = self.copy / HELD_REPORT
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>")
        return _Done(not self.output, self.output)

    def stop(self) -> None:
        self.stopped = True


class _Done:
    def __init__(self, ok: bool, output: str) -> None:
        self.ok, self.output = ok, output


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> type[_Held]:
    """The default branch declares a workspace, run in a fake container."""
    from crew_org.tools import container, services

    _Held.made, _Held.cases, _Held.output = [], {}, ""
    monkeypatch.setattr(container, "Held", _Held)
    monkeypatch.setattr(
        services, "at_main", lambda _w: services.Declared(path=".devcontainer/devcontainer.json")
    )
    return _Held


def test_a_database_bug_reproduces_in_the_workspace_not_the_sandbox(fixed, held):
    """mqucifer/sprint-metrics#742: in the sandbox the database test only ever skipped."""
    repo, merged = fixed
    held.cases = {"test_calc.py::test_needs_a_database": "failed"}
    found = reproduces(repo, merged, [("test_calc.py", "test_needs_a_database")], name="sm-742")
    assert found.failed == ["test_calc.py::test_needs_a_database"] and found.refusal() == ""
    (run,) = held.made
    assert run.name == "sm-742" and run.declared.path and run.stopped
    assert run.ran[0][-1] == "test_calc.py::test_needs_a_database"
    # Run against the merged code: only the test file is the branch's.
    assert "n + 2" in run.seen["calc.py"] and "helper.py" not in run.seen


def test_a_test_that_skips_in_the_workspace_still_reproduces_nothing(fixed, held):
    held.cases = {"test_calc.py::test_needs_a_database": "skipped"}
    found = reproduces(*fixed, [("test_calc.py", "test_needs_a_database")], name="sm-742")
    assert "was skipped, not run: no db" in found.refusal()


def test_a_hang_as_merged_is_refused_naming_the_test(fixed, held):
    held.output = DUMP
    found = reproduces(*fixed, [("test_calc.py", "test_doubles")], name="sm-742")
    assert "`test_calc.py::test_doubles` hung against the code as merged" in found.refusal()


def test_a_project_with_no_workspace_runs_in_the_sandbox_as_before(fixed, monkeypatch):
    from crew_org.tools import container, services

    monkeypatch.setattr(container, "Held", lambda *a, **kw: pytest.fail("no workspace"))
    monkeypatch.setattr(services, "at_main", lambda _w: services.Declared())
    found = reproduces(
        *fixed, [("test_calc.py", "test_doubles")], name="sm-742", sandbox=Sandbox(mode=Mode.OFF)
    )
    assert found.failed == ["test_calc.py::test_doubles"]


# --- a turned-on bug's recorded tests, on the branch (crew#839) --------------------------


def test_recorded_tests_and_files_that_pass_on_the_branch_are_met(fixed, held):
    repo, _ = fixed
    held.cases = {
        "test_calc.py::test_doubles": "passed",
        "test_calc.py::test_already_true": "passed",
    }
    assert (
        reproduction.on_the_branch(repo, ["test_calc.py", "test_calc.py::test_doubles"], "b") == {}
    )
    (run,) = held.made
    # The branch's own code, nothing put back.
    assert "n * 2" in run.seen["calc.py"] and "helper.py" in run.seen


def test_a_recorded_test_that_fails_or_is_gone_is_named(fixed, held):
    repo, _ = fixed
    held.cases = {"test_calc.py::test_doubles": "failed"}
    wrong = reproduction.on_the_branch(
        repo, ["test_calc.py::test_doubles", "test_calc.py::test_renamed"], "b"
    )
    assert wrong == {
        "test_calc.py::test_doubles": "failed",
        "test_calc.py::test_renamed": "not run",
    }


def test_a_recorded_file_with_a_failing_test_is_named_by_it(fixed, held):
    repo, _ = fixed
    held.cases = {
        "test_calc.py::test_doubles": "passed",
        "test_calc.py::test_already_true": "skipped",
    }
    wrong = reproduction.on_the_branch(repo, ["test_calc.py"], "b")
    assert wrong == {"test_calc.py": "`test_calc.py::test_already_true` skipped"}


def test_a_recorded_file_that_still_hangs_is_refused_naming_the_test(fixed, held):
    """mqucifer/sprint-metrics#742's record names `tests/test_service.py`, which hung."""
    repo, _ = fixed
    held.output = DUMP
    wrong = reproduction.on_the_branch(repo, ["test_calc.py"], "b")
    assert list(wrong) == ["test_calc.py"]
    assert wrong["test_calc.py"].startswith("`test_calc.py::test_doubles` hung, waiting in ")
