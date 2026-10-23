"""A project's first workspace in three steps (the Sponsor, 2026-10-23; crew#839).

Found on mqucifer/sprint-metrics#739: DevOps's workspace Task could never pass. Its
session ran the default branch's services, never its own, and the ~180 rotted database
tests its Postgres turned on were the Developer's to fix. So the workspace Task's gate
runs its branch's services and setup; what they turn on that fails, and the branch
didn't cause, is the Developer's bug once it merges; and CI is DevOps's second Task.
Also: a card in Merging with changes requested on its head goes back for rework.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_org.events import EventSink
from crew_org.flows import gates, merge, role_tasks, session_end
from crew_org.flows.role_tasks import marked
from crew_org.tools import base_images, container
from crew_org.tools.github_project import Card
from crew_org.tools.services import Declared, Service
from crew_org.tools.workspace import CheckResult, CommandResult
from tests.role_task_fakes import Board, Issues, sink
from tests.test_gates import TESTS, at_done, done, repo  # noqa: F401
from tests.test_merge import FakeBoard, FakeIssues, card, run

REAL = "sha256:" + "f7" * 32
FAKE = "sha256:" + "0e" * 32
PYTEST = "uv run pytest -q"
ROTTED = {f"{TESTS}::test_rate_of_nothing": "psycopg.OperationalError: relation missing"}


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    """Every tag and digest the tests pin is REAL; any other digest isn't there."""
    monkeypatch.setattr(base_images, "_READ", {})
    monkeypatch.setattr(
        base_images, "LOOKUP", lambda host, name, ref: None if ref == FAKE else REAL
    )


def devops_task() -> Card:
    return Card(
        item_id="T739",
        number=739,
        title="Write the workspace file and the services its tests need",
        repo="sprint-metrics",
        status="In Progress",
        state="OPEN",
        work_type="Task",
        labels=["role:devops"],
    )


def writes_the_workspace(root: Path) -> None:
    (root / ".devcontainer").mkdir(exist_ok=True)
    (root / ".devcontainer/devcontainer.json").write_text(
        json.dumps({"dockerComposeFile": "compose.yaml", "service": "workspace"})
    )
    (root / ".devcontainer/compose.yaml").write_text(
        f"services:\n  workspace:\n    image: python@{REAL}\n"
        f"  db:\n    image: postgres:16@{REAL}\n    user: postgres\n"
    )


def failing(failed: dict[str, str], *more: CommandResult):
    def run_checks(at: Path) -> CheckResult:
        return CheckResult(
            results=[CommandResult(PYTEST, 1, "1 failed"), *more], failed=failed, reported=[PYTEST]
        )

    return run_checks


# --- part 2: what the workspace turns on is the Developer's ----------------------------


def test_a_merged_test_the_workspace_turns_on_is_the_developers_not_the_tasks(repo):  # noqa: F811
    writes_the_workspace(repo)
    verdict = at_done(repo, done(), card=devops_task(), run_checks=failing(ROTTED))
    assert verdict.passed, verdict.findings
    assert verdict.for_developer == ROTTED
    section = "\n".join(gates.pr_sections(verdict))
    assert role_tasks.TURNED_ON_HEADING in section
    assert role_tasks.turned_on(section) == ROTTED


def test_a_story_still_goes_back_for_a_merged_test_that_fails(repo):  # noqa: F811
    """The operator (2026-10-23): no gate is loosened for a story."""
    writes_the_workspace(repo)
    verdict = at_done(repo, done(), run_checks=failing(ROTTED))
    assert "check.delivery.lint_or_tests_failed" in [f.rule for f in verdict.findings]
    assert verdict.for_developer == {}


def test_a_failing_test_the_task_changed_is_the_tasks(repo):  # noqa: F811
    writes_the_workspace(repo)
    (repo / TESTS).write_text((repo / TESTS).read_text() + "\n# touched\n")
    verdict = at_done(repo, done(), card=devops_task(), run_checks=failing(ROTTED))
    assert "check.delivery.lint_or_tests_failed" in [f.rule for f in verdict.findings]


def test_any_other_failure_beside_the_tests_goes_back(repo):  # noqa: F811
    writes_the_workspace(repo)
    lint = CommandResult("uv run ruff check .", 1, "E501")
    verdict = at_done(repo, done(), card=devops_task(), run_checks=failing(ROTTED, lint))
    assert "check.delivery.lint_or_tests_failed" in [f.rule for f in verdict.findings]
    assert verdict.for_developer == {}


def test_a_devops_task_that_leaves_the_workspace_file_alone_is_checked_as_today(repo):  # noqa: F811
    (repo / "CHANGELOG.md").write_text("# Changes\n")
    verdict = at_done(repo, done(), card=devops_task(), run_checks=failing(ROTTED))
    assert "check.delivery.lint_or_tests_failed" in [f.rule for f in verdict.findings]


def test_the_bug_is_filed_once_by_its_tests(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    first = role_tasks.turned_on_bug(issues, board, s, repo="sm", task=739, tests=ROTTED, cards=[])
    again = role_tasks.turned_on_bug(issues, board, s, repo="sm", task=739, tests=ROTTED, cards=[])
    assert first == again and len(issues.issues) == 1
    bug = issues.issues[first]
    assert {lab["name"] for lab in bug["labels"]} == {"bug"}
    assert f"`{TESTS}::test_rate_of_nothing`" in bug["body"] and "#739" in bug["body"]


class Merged(FakeIssues):
    """The workspace Task's pull request, merged, naming what its gate found."""

    def __init__(self, body: str):
        super().__init__()
        self.body = body
        self.filed: list[dict] = []

    def pull(self, name, number):
        return {**super().pull(name, number), "body": self.body}


def test_the_bug_is_filed_when_the_workspace_tasks_pull_request_merges(monkeypatch):
    """The operator (2026-10-23): only once merged do the tests fail on main."""
    filed = []
    monkeypatch.setattr(role_tasks, "turned_on_bug", lambda *a, **kw: filed.append(kw))
    body = "\n".join(["## Why", "", *gates.pr_sections(gates.Verdict(for_developer=ROTTED))])
    task = card(739)
    task.work_type = "Task"

    class WithCards(FakeBoard):
        def cards(self):
            return []

    result = merge.MergeResult()
    seen: list = []
    events = EventSink(None)
    events.subscribe(seen.append)
    merge._done(
        WithCards(),
        events,
        task,
        pull={"number": 100},
        result=result,
        issues=Merged(body),
        repo="sprint-metrics",
    )
    assert [kw["tests"] for kw in filed] == [ROTTED] and filed[0]["task"] == 739
    # Recorded where it was caught, as counts: the names stay on the bug.
    (recorded,) = [e for e in seen if e.kind == "finding.filed"]
    assert recorded.detail["owner"] == "bug"
    assert recorded.detail["counts"] == {"tests": 1, "files": 1}


# --- part 1: the workspace Task's own services -----------------------------------------


def test_the_branchs_own_services_are_read_from_its_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(base_images, "_READ", {})
    monkeypatch.setattr(
        base_images, "LOOKUP", lambda host, name, ref: REAL if ref in ("16", REAL) else None
    )
    writes_the_workspace(tmp_path)
    own = container.own_services(tmp_path)
    assert [s.name for s in own.services] == ["db"] and own.problems == {}


def test_an_own_service_its_registry_doesnt_have_is_never_started(tmp_path, monkeypatch):
    monkeypatch.setattr(base_images, "_READ", {})
    monkeypatch.setattr(base_images, "LOOKUP", lambda host, name, ref: None)
    writes_the_workspace(tmp_path)
    own = container.own_services(tmp_path)
    assert own.services == [] and "doesn't have" in own.problems["db"]


def test_an_own_service_is_held_to_the_default_branchs_rules(tmp_path, monkeypatch):
    """Root and a tag are refused before any registry is asked, by `services._service`."""
    monkeypatch.setattr(base_images, "LOOKUP", lambda *a: REAL)
    writes_the_workspace(tmp_path)
    (tmp_path / ".devcontainer/compose.yaml").write_text(
        f"services:\n  db:\n    image: postgres:16@{REAL}\n    user: root\n"
        "  cache:\n    image: redis:7\n    user: redis\n"
    )
    own = container.own_services(tmp_path)
    assert own.services == [] and set(own.problems) == {"db", "cache"}


def test_a_workspace_tasks_gate_runs_in_a_container_with_its_branchs_services(
    tmp_path, monkeypatch
):
    from crew_org.flows import session_delivery

    made = []

    class Own:
        down: dict = {}
        lost = False

        def __init__(self, worktree, name, declared=None):
            made.append((name, declared))

        def start(self):
            return container.Result("start", 0, "")

        def setup(self):
            return container.Result("uv sync", 0, "")

        def stop(self):
            made.append("stopped")

    branch = Declared(services=[Service("db", f"postgres@{REAL}", "postgres")])
    monkeypatch.setattr(session_delivery, "Held", Own)
    monkeypatch.setattr(session_delivery, "own_services", lambda at: branch)
    monkeypatch.setattr(
        session_delivery, "check", lambda at, held, since, deselect: CheckResult([])
    )
    session_delivery.own_checks(tmp_path, "sm-739-done", "base")
    assert made == [("sm-739-done", branch), "stopped"]


def test_its_setup_failing_is_the_checks_failure_after_a_second_try(tmp_path, monkeypatch):
    from crew_org.flows import session_delivery

    tries = []

    class Own:
        down = {"db": "it stopped"}
        lost = False

        def __init__(self, *a, **kw):
            pass

        def start(self):
            return container.Result("start", 0, "")

        def setup(self):
            tries.append(1)
            return container.Result("uv sync", 1, "no network")

        def stop(self):
            pass

    monkeypatch.setattr(session_delivery, "Held", Own)
    monkeypatch.setattr(session_delivery, "own_services", lambda at: Declared())
    checked = session_delivery.own_checks(tmp_path, "x", "base")
    assert len(tries) == 2 and not checked.ok and checked.services_down == {"db": "it stopped"}


def test_devops_finds_no_devops_finding_on_its_own_task(tmp_path, monkeypatch):
    filed = []
    monkeypatch.setattr(role_tasks, "workspace_finding", lambda *a, **kw: filed.append(kw))
    verdict = gates.Verdict(check=CheckResult(results=[], services_down={"db": "x"}))
    session_end._for_devops(
        verdict,
        repo="sm",
        story=739,
        board=Board(),
        issues=Issues(),
        sink=sink(tmp_path),
        role=role_tasks.DEVOPS,
    )
    assert filed == []


# --- part 3: CI's Task -----------------------------------------------------------------


def test_cis_task_waits_for_the_first_workspace_task_to_close(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    first = role_tasks.marker(role_tasks.DEVOPS, role_tasks.FIRST_WORKSPACE, "repository")
    issues.add(739, body=first, labels=("role:devops",))
    assert role_tasks.ci_services_task(issues, board, s, repo="sm", cards=[]) is None
    issues.issues[739]["state"] = "closed"
    opened = role_tasks.ci_services_task(issues, board, s, repo="sm", cards=[])
    assert opened is not None and opened.new
    body = issues.issues[opened.number]["body"]
    assert marked(body).kind == role_tasks.CI_SERVICES and "ciOnlySkips" in body
    again = role_tasks.ci_services_task(issues, board, s, repo="sm", cards=[])
    assert again is not None and again.number == opened.number and not again.new


def test_a_story_passing_with_mains_services_up_opens_cis_task(tmp_path, monkeypatch):
    opened = []
    up = Declared(services=[Service("db", f"postgres@{REAL}", "postgres")])
    monkeypatch.setattr(session_end.services, "at_main", lambda at: up)
    monkeypatch.setattr(role_tasks, "ci_services_task", lambda *a, **kw: opened.append(kw))

    class Cards(Board):
        def cards(self):
            return []

    green = gates.Verdict(check=CheckResult(results=[CommandResult(PYTEST, 0, "")]))
    session_end._for_devops(
        green,
        repo="sm",
        story=40,
        board=Cards(),
        issues=Issues(),
        sink=sink(tmp_path),
        worktree=tmp_path,
    )
    assert [kw["repo"] for kw in opened] == ["sm"]
    monkeypatch.setattr(session_end.services, "at_main", lambda at: Declared())
    session_end._for_devops(
        green,
        repo="sm",
        story=41,
        board=Cards(),
        issues=Issues(),
        sink=sink(tmp_path),
        worktree=tmp_path,
    )
    assert len(opened) == 1


# --- part 6: changes requested in Merging ----------------------------------------------


class Requested(FakeIssues):
    def __init__(self, on: str):
        super().__init__(
            reviews=[
                {"state": "APPROVED", "commit_id": "abc", "user": {"login": "reviewer"}},
                {"state": "CHANGES_REQUESTED", "commit_id": on, "user": {"login": "mquarters"}},
            ]
        )

    def pull_for_branch(self, name, branch, *, known=None):
        return {"number": 100, "head": {"sha": "abc"}}


def test_changes_requested_on_the_head_send_a_merging_card_back_for_rework():
    """mqucifer/sprint-metrics#740 waited in Merging with the Sponsor's request on it."""
    issues = Requested(on="abc")
    result, board, seen = run([card(740)], issues)
    assert result.changes_requested == [(740, 100)] and issues.merged == []
    assert board.moves == [("C740", "In Progress")]


def test_changes_requested_on_an_older_head_dont():
    issues = Requested(on="older")
    result, board, _ = run([card(740)], issues)
    assert result.changes_requested == [] and issues.merged == [100]


# --- a Task's session runs its gate's checks itself (the Sponsor, 2026-10-23) ----------

GOOD = '```mermaid\nC4Context\nPerson(u, "User")\nSystem(s, "S")\nRel(u, s, "uses")\n```\n'


def names(specs):
    return [s["function"]["name"] for s in specs]


def test_each_role_has_its_own_gates_check_and_the_developer_neither():
    from crew_org.session.tools import SPECS

    architect = names(role_tasks.done_specs(role_tasks.ARCHITECT))
    devops = names(role_tasks.done_specs(role_tasks.DEVOPS))
    assert "diagram_check" in architect and "workspace_check" not in architect
    assert "workspace_check" in devops and "diagram_check" not in devops
    assert not {"diagram_check", "workspace_check"} & set(names(SPECS))
    assert devops.index("workspace_check") < devops.index("done")


def session_tools(root: Path):
    from crew_org.session.tools import Tools
    from tests.test_session import CHECKS, Fake

    return Tools(root, Fake(), CHECKS)


def test_diagram_check_is_the_gates_check_on_the_file_as_it_is(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/architecture.md").write_text("```mermaid\nflowchart LR\n```\n")
    failed = session_tools(tmp_path).call("diagram_check", {})
    assert failed.kind == "failed" and "isn't one the crew checks" in failed.text
    assert failed.rule == "check.role_tasks.diagram_renders"  # the gate's own rule
    (tmp_path / "docs/architecture.md").write_text(GOOD)
    assert session_tools(tmp_path).call("diagram_check", {}).kind == "passed"


def test_workspace_check_finds_what_the_gate_refuses_without_starting_anything(tmp_path):
    writes_the_workspace(tmp_path)
    (tmp_path / ".devcontainer/compose.yaml").write_text(
        f"services:\n  workspace:\n    image: python@{REAL}\n"
        "  db:\n    image: postgres:16\n    user: root\n"
    )
    reply = session_tools(tmp_path).call("workspace_check", {})
    assert reply.kind == "failed" and reply.rule == "check.role_tasks.workspace_checks"
    assert f"postgres:16@{REAL}" in reply.text  # the tag, with the digest held now
    assert "`db`:" in reply.text  # and the service as the crew would start it


def test_workspace_check_passes_a_workspace_the_gate_would_pass(tmp_path):
    from tests.test_services import PROJECT

    (tmp_path / ".crew").mkdir()
    (tmp_path / ".crew/project.yaml").write_text(PROJECT)  # its design names the checks
    writes_the_workspace(tmp_path)
    (tmp_path / ".devcontainer/devcontainer.json").write_text(
        json.dumps(
            {
                "dockerComposeFile": "compose.yaml",
                "service": "workspace",
                "postCreateCommand": ["uv", "sync"],
            }
        )
    )
    reply = session_tools(tmp_path).call("workspace_check", {})
    assert reply.kind == "passed", reply.text


# --- a hang names itself, and a workspace Task's gate runs the rest (crew#839) ---------

# The dump faulthandler printed on sprint-metrics' test copy, its other threads trimmed:
# a test's `init_db` waited on a lock a reconnected service connection held.
HUNG = "tests/test_service.py::test_post_events_503_content_type_is_application_json"
DUMP = """tests/test_service.py ....FF..Timeout (0:01:00)!
Thread 0x0000ffff8cf2f180 (most recent call first):
  File "/usr/local/lib/python3.12/selectors.py", line 415 in select
  File "/usr/local/lib/python3.12/socketserver.py", line 235 in serve_forever
  File "/usr/local/lib/python3.12/threading.py", line 1012 in run

Thread 0x0000ffff91954020 (most recent call first):
  File "/work/.venv/lib/python3.12/site-packages/psycopg/connection.py", line 495 in wait
  File "/work/.venv/lib/python3.12/site-packages/psycopg/cursor.py", line 113 in execute
  File "/work/src/sprint_metrics/store.py", line 72 in init_db
  File "/work/tests/test_service.py", line 3208 in {name}
  File "/work/.venv/lib/python3.12/site-packages/_pytest/python.py", line 167 in pytest_pyfunc_call
  File "/work/.venv/lib/python3.12/site-packages/pluggy/_callers.py", line 121 in _multicall
""".replace("{name}", HUNG.partition("::")[2])
WAITS = "psycopg/connection.py:495 in wait, from src/sprint_metrics/store.py:72 in init_db"
# Tests the `repo` fixture has, so the gate can tell the branch didn't cause them.
STUCK = f"{TESTS}::test_rate_of_nothing"
CAPPED = f"{TESTS}::test_rate_is_capped"


def test_a_hung_test_is_named_by_its_dump_with_where_it_waits(tmp_path):
    from crew_org.tools.workspace import hung_test

    assert hung_test(DUMP, tmp_path) == (HUNG, WAITS)
    assert hung_test("1 failed in 0.2s", tmp_path) is None


def test_a_hung_method_is_named_by_its_class(tmp_path):
    from crew_org.tools.workspace import hung_test

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_service.py").write_text(
        "class TestPost:\n" + "\n" * 3204 + "    def test_post_events_503_content_type_is_"
        "application_json(self):\n" + "        pass\n" * 5
    )
    found = hung_test(DUMP, tmp_path)
    assert found is not None
    assert (
        found[0]
        == "tests/test_service.py::TestPost::test_post_events_503_content_type_is_application_json"
    )


def test_the_checks_set_hung_tests_aside_when_asked(tmp_path):
    from crew_org.tools.workspace import check

    ran = []

    class Held:
        down: dict = {}

        def resync(self):
            return None

        def exec(self, command, timeout=0):
            ran.append(command)
            return container.Result(" ".join(command), 0, "")

    from tests.test_services import PROJECT

    (tmp_path / ".crew").mkdir()
    (tmp_path / ".crew/project.yaml").write_text(PROJECT)
    writes_the_workspace(tmp_path)
    check(tmp_path, held=Held(), deselect={HUNG: WAITS})
    pytest_runs = [c for c in ran if "pytest" in c]
    assert pytest_runs and all(c[-2:] == ["--deselect", HUNG] for c in pytest_runs)


def hangs_then(*results, monkeypatch):
    """`own_checks` with a container that starts, and checks that answer `results` in turn."""
    from crew_org.flows import session_delivery

    given = []

    class Own:
        down: dict = {}
        lost = False

        def __init__(self, *a, **kw):
            pass

        def start(self):
            return container.Result("start", 0, "")

        def setup(self):
            return None

        def stop(self):
            pass

    answers = list(results)

    def checks(at, held, since, deselect):
        given.append(dict(deselect))
        return answers.pop(0)

    monkeypatch.setattr(session_delivery, "Held", Own)
    monkeypatch.setattr(session_delivery, "own_services", lambda at: Declared())
    monkeypatch.setattr(session_delivery, "check", checks)
    return given


def hung(test: str) -> CheckResult:
    return CheckResult(results=[CommandResult(PYTEST, 1, DUMP)], hung={test: WAITS})


def test_a_workspace_tasks_gate_sets_each_hang_aside_and_runs_the_rest(repo, monkeypatch):  # noqa: F811
    from crew_org.flows import session_delivery

    writes_the_workspace(repo)
    other = "tests/test_metrics.py::test_rate_is_capped"
    last = CheckResult(
        results=[CommandResult(PYTEST, 1, "1 failed")], failed=ROTTED, reported=[PYTEST]
    )
    given = hangs_then(hung(STUCK), hung(other), last, monkeypatch=monkeypatch)
    checked = session_delivery.own_checks(repo, "x", "base")
    assert given == [{}, {STUCK: WAITS}, {STUCK: WAITS, other: WAITS}]
    assert checked.hang_rounds == 2


def test_past_the_bound_the_hang_is_left_for_the_gates_finding(repo, monkeypatch):  # noqa: F811
    from crew_org.flows import session_delivery

    writes_the_workspace(repo)
    monkeypatch.setattr(session_delivery, "HANG_ROUNDS", 1)
    other = "tests/test_metrics.py::test_rate_is_capped"
    hangs_then(hung(STUCK), hung(other), monkeypatch=monkeypatch)
    checked = session_delivery.own_checks(repo, "x", "base")
    assert checked.hung == {other: WAITS} and checked.hang_rounds == 1


def test_a_hang_the_branch_caused_is_never_set_aside(repo, monkeypatch):  # noqa: F811
    from crew_org.flows import session_delivery

    writes_the_workspace(repo)
    (repo / TESTS).write_text((repo / TESTS).read_text() + "\n# touched\n")
    mine = f"{TESTS}::test_rate_is_capped"
    given = hangs_then(hung(mine), monkeypatch=monkeypatch)
    checked = session_delivery.own_checks(repo, "x", "base")
    assert given == [{}] and checked.hung == {mine: WAITS}


def test_the_hangs_set_aside_are_the_developers_beside_the_failures(repo):  # noqa: F811
    writes_the_workspace(repo)

    def run_checks(at):
        return CheckResult(
            results=[CommandResult(PYTEST, 1, "1 failed")],
            failed=ROTTED,
            reported=[PYTEST],
            deselected={CAPPED: WAITS},
        )

    verdict = at_done(repo, done(), card=devops_task(), run_checks=run_checks)
    assert verdict.passed, verdict.findings
    assert verdict.for_developer == {**ROTTED, CAPPED: f"hung: waiting in {WAITS}"}


def test_a_hang_is_its_own_finding_never_a_failed_test(repo):  # noqa: F811
    """Stories are refused with it, no loosening; so is a Task whose hang stayed."""
    writes_the_workspace(repo)
    for task in (devops_task(), None):
        kw = {"card": task} if task else {}
        verdict = at_done(repo, done(), run_checks=lambda at: hung(HUNG), **kw)
        (finding,) = [f for f in verdict.findings if f.rule == "check.gates.the_checks_timed_out"]
        assert f"`{HUNG}`: hung, waiting in {WAITS}" in finding.says
        assert "check.delivery.lint_or_tests_failed" not in [f.rule for f in verdict.findings]


def test_a_run_stopped_at_its_bound_is_the_same_finding():
    stopped = CheckResult(
        results=[CommandResult(PYTEST, 124, "(stopped: still running after 300s)")]
    )
    verdict = gates.Verdict(check=stopped)
    gates._checked(verdict)
    assert [f.rule for f in verdict.findings] == ["check.gates.the_checks_timed_out"]


def test_the_bug_says_how_to_read_a_hang(tmp_path):
    issues = Issues()
    tests = {**ROTTED, HUNG: f"hung: waiting in {WAITS}"}
    number = role_tasks.turned_on_bug(
        issues, Board(), sink(tmp_path), repo="sm", task=739, tests=tests, cards=[]
    )
    body = issues.issues[number]["body"]
    assert f"- `{HUNG}`: hung: waiting in {WAITS}" in body
    assert "The 1 that hang" in body and "2 fail, error or hang" in body
