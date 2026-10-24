"""DevOps's CI Task opened from main itself, at most once per head (crew#839).

In the reset an existing project's stories wait on the step this Task finishes, so a
story's `done` could never open it. Each pass also asks main, cheapest first.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from crew_org.flows import ci_from_main, gates, role_tasks, session_end
from crew_org.flows.ci_from_main import CHECKS, PASSED, MainCheck, ci_services_from_main
from crew_org.tools.services import Declared, Service
from crew_org.tools.workspace import CheckResult, CommandResult
from tests.role_task_fakes import Board, Issues, events, sink

REAL = "sha256:" + "f7" * 32
FIRST = role_tasks.marker(role_tasks.DEVOPS, role_tasks.FIRST_WORKSPACE, "repository")
CI = role_tasks.marker(role_tasks.DEVOPS, role_tasks.CI_SERVICES, "repository")
DEVCONTAINER = json.dumps({"dockerComposeFile": "compose.yaml", "service": "workspace"})
COMPOSE = (
    f"services:\n  workspace:\n    image: python@{REAL}\n"
    f"  db:\n    image: postgres:16@{REAL}\n    user: postgres\n"
)


class Main(Issues):
    """A repository whose main is at `head`, with the files it declares."""

    def __init__(self) -> None:
        super().__init__()
        self.head = "a" * 40
        self.files = {
            ".devcontainer/devcontainer.json": DEVCONTAINER,
            ".devcontainer/compose.yaml": COMPOSE,
        }

    def head_sha(self, repo: str, branch: str) -> str:
        return self.head


class Cards(Board):
    def cards(self):
        return []


class Workspace:
    """`ws`: each `at_main` counted, so a pass that starts nothing is seen to."""

    def __init__(self, path) -> None:
        self.path, self.asked = path, 0

    def for_repo(self, repo):
        return self

    def at_main(self):
        self.asked += 1
        return self.path


@pytest.fixture
def main(tmp_path, monkeypatch):
    issues = Main()
    issues.add(739, body=FIRST, labels=("role:devops",), state="closed")
    # What ran is the worktree's head, which is main's as the test has it.
    monkeypatch.setattr(ci_from_main, "_head", lambda worktree: issues.head)
    return issues


def runs(*outcomes: MainCheck):
    """Main's checks, doing each of `outcomes` in turn; `ran` counts the runs."""
    left = list(outcomes)

    def run_checks(worktree, name):
        run_checks.ran += 1  # type: ignore[attr-defined]
        return left.pop(0)

    run_checks.ran = 0  # type: ignore[attr-defined]
    return run_checks


def a_pass(issues, tmp_path, run_checks, ws=None, **kw):
    return ci_services_from_main(
        issues,
        Board(),
        sink(tmp_path),
        ws or Workspace(tmp_path),
        repos={"sm"},
        cards=[],
        store=tmp_path / "store",
        run_checks=run_checks,
        **kw,
    )


def ci_tasks(issues) -> list[int]:
    return [n for n, i in issues.issues.items() if CI in (i.get("body") or "")]


def test_main_passing_with_its_services_opens_cis_task_naming_the_path(main, tmp_path):
    done = a_pass(main, tmp_path, runs(MainCheck(PASSED)))
    assert [repo for repo, _ in done.opened] == ["sm"] and len(ci_tasks(main)) == 1
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert [e["detail"]["path"] for e in opened] == ["main"]


def test_it_waits_for_the_first_workspace_task_to_close(main, tmp_path):
    main.issues[739]["state"] = "open"
    run_checks, ws = runs(), Workspace(tmp_path)
    done = a_pass(main, tmp_path, run_checks, ws)
    assert done.opened == [] and ci_tasks(main) == []
    assert run_checks.ran == 0 and ws.asked == 0
    del main.issues[739]
    assert a_pass(main, tmp_path, run_checks, ws).opened == [] and run_checks.ran == 0


@pytest.mark.parametrize("state", ["open", "closed"])
def test_once_cis_task_is_there_nothing_runs(main, tmp_path, state):
    main.add(800, body=CI, labels=("role:devops",), state=state)
    run_checks, ws = runs(), Workspace(tmp_path)
    done = a_pass(main, tmp_path, run_checks, ws)
    assert done.opened == [] and ci_tasks(main) == [800]
    assert run_checks.ran == 0 and ws.asked == 0
    assert done.held == [("sm", "its CI Task is already opened")]


def test_main_with_no_services_runs_nothing_and_is_kept(main, tmp_path):
    main.files = {}
    run_checks, ws = runs(), Workspace(tmp_path)
    assert a_pass(main, tmp_path, run_checks, ws).opened == []
    assert run_checks.ran == 0 and ws.asked == 0
    assert ci_from_main.checked_at("sm", main.head, store=tmp_path / "store") == MainCheck(
        "no services"
    )


def test_a_head_whose_checks_failed_isnt_run_again_and_a_new_head_is(main, tmp_path):
    failed = MainCheck(CHECKS, commands_failed=1, tests_failed=3, skipped=2)
    run_checks = runs(failed, MainCheck(PASSED))
    assert a_pass(main, tmp_path, run_checks).opened == []
    assert a_pass(main, tmp_path, run_checks).opened == [] and run_checks.ran == 1
    # Recorded with why, in counts only.
    [note] = [e for e in events(tmp_path) if e["detail"].get("sha") == main.head]
    assert note["detail"]["outcome"] == CHECKS and note["detail"]["tests_failed"] == 3
    main.head = "b" * 40
    assert len(a_pass(main, tmp_path, run_checks).opened) == 1 and run_checks.ran == 2


@pytest.mark.parametrize("problem", ["start", "setup", "services", "lost"])
def test_a_container_that_failed_is_tried_again_next_pass(main, tmp_path, problem):
    run_checks = runs(MainCheck(problem), MainCheck(PASSED))
    assert a_pass(main, tmp_path, run_checks).opened == []
    assert ci_from_main.checked_at("sm", main.head, store=tmp_path / "store") is None
    assert len(a_pass(main, tmp_path, run_checks).opened) == 1 and run_checks.ran == 2


def test_read_only_it_says_it_would_open_and_files_and_keeps_nothing(main, tmp_path):
    """The proof's run: `held_by` without `keep`, and nothing opened."""
    why = ci_from_main.held_by(
        main,
        sink(tmp_path),
        Workspace(tmp_path),
        repo="sm",
        store=tmp_path / "store",
        run_checks=runs(MainCheck(PASSED)),
        keep=False,
    )
    assert why == "" and ci_tasks(main) == [] and not (tmp_path / "store").exists()


def test_github_refusing_is_recorded_and_the_next_pass_tries_again(main, tmp_path):
    main.fail = "create"
    done = a_pass(main, tmp_path, runs(MainCheck(PASSED)))
    assert done.opened == [] and "not opened" in done.held[0][1]
    main.fail = None
    # The pass is kept, so the next one opens it without running main's checks again.
    run_checks = runs()
    assert len(a_pass(main, tmp_path, run_checks).opened) == 1 and run_checks.ran == 0


def story_done(issues, tmp_path, monkeypatch):
    up = Declared(services=[Service("db", f"postgres@{REAL}", "postgres")])
    monkeypatch.setattr(session_end.services, "at_main", lambda at: up)
    green = gates.Verdict(check=CheckResult(results=[CommandResult("uv run pytest", 0, "")]))
    session_end._for_devops(
        green,
        repo="sm",
        story=40,
        board=Cards(),
        issues=issues,
        sink=sink(tmp_path),
        worktree=tmp_path,
    )


def test_a_story_then_main_files_one_task(main, tmp_path, monkeypatch):
    story_done(main, tmp_path, monkeypatch)
    run_checks = runs()
    assert a_pass(main, tmp_path, run_checks).opened == [] and run_checks.ran == 0
    assert len(ci_tasks(main)) == 1
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert [e["detail"]["path"] for e in opened] == ["story"]


def test_main_then_a_story_files_one_task(main, tmp_path, monkeypatch):
    a_pass(main, tmp_path, runs(MainCheck(PASSED)))
    story_done(main, tmp_path, monkeypatch)
    assert len(ci_tasks(main)) == 1


class Held:
    """The container, starting as `start` says and running setup once."""

    start_ok = True

    def __init__(self, worktree, name, declared=None):
        self.down, self.lost = {}, False

    def start(self):
        return SimpleNamespace(ok=self.start_ok)

    def setup(self):
        return None

    def stop(self):
        pass


def test_mains_checks_say_whose_failure_it_was(tmp_path, monkeypatch):
    from crew_org.tools import container, workspace  # noqa: PLC0415

    monkeypatch.setattr(container, "Held", Held)
    red = CheckResult(
        results=[CommandResult("uv run pytest", 1, "")],
        failed={"tests/test_db.py::test_rate": "boom"},
        skipped={"tests/test_db.py::test_other": "no db"},
    )
    monkeypatch.setattr(workspace, "check", lambda worktree, held: red)
    assert ci_from_main.run_main_checks(tmp_path, "main-sm") == MainCheck(
        CHECKS, commands_failed=1, tests_failed=1, skipped=1
    )
    red.services_down = {"db": "it stopped"}
    assert ci_from_main.run_main_checks(tmp_path, "main-sm").outcome == "services"
    monkeypatch.setattr(Held, "start_ok", False)
    assert ci_from_main.run_main_checks(tmp_path, "main-sm").outcome == "start"
