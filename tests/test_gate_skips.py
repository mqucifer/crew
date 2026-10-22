"""The gate at `done` refuses a skipped test that CI runs (the Sponsor, 2026-10-21; crew#860).

mqucifer/sprint-metrics#717 took four rounds: its database tests skipped in the
Developer's workspace, the gate passed, and they only failed after it. What CI runs is
checkable through the project's declaration: a skip whose reason its workspace file
declares CI-only is one CI makes too. One the branch adds or changes goes back; one
already merged is DevOps's, once the project has the file.
"""

from __future__ import annotations

import json
from pathlib import Path

from crew_org.flows import role_tasks
from crew_org.flows.role_tasks import marked
from crew_org.tools.container import Held
from crew_org.tools.services import Declared, declared
from crew_org.tools.workspace import REPORT, CheckResult, CommandResult, skipped_tests, with_report
from tests.role_task_fakes import Board, Issues, events, sink
from tests.test_gates import MERGED_TESTS, TESTS, at_done, done, git, repo  # noqa: F401
from tests.test_services import ran, worktree  # noqa: F401

NEW = "tests/test_store.py"
DB = "SPRINT_METRICS_DB not set"
DOCKER = "Docker not available"


def report(root: Path, cases: dict[tuple[str, str], str | None]) -> None:
    """pytest's JUnit report: each (classname, name), skipped with its reason or passed."""
    xml = "".join(
        f'<testcase classname="{c}" name="{n}">'
        + (f'<skipped type="pytest.skip" message="{r}">{r}</skipped>' if r else "")
        + "</testcase>"
        for (c, n), r in cases.items()
    )
    (root / REPORT).parent.mkdir(exist_ok=True)
    (root / REPORT).write_text(
        f'<testsuites><testsuite name="pytest">{xml}</testsuite></testsuites>'
    )


def test_the_report_names_each_skipped_test_by_its_file_and_class(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / NEW).write_text("")
    report(
        tmp_path,
        {
            ("tests.test_store", "test_insert"): DB,
            ("tests.test_store.TestQuery", "test_by_sprint[a]"): DB,
            ("tests.test_store", "test_passes"): None,
        },
    )
    assert skipped_tests(tmp_path) == {
        f"{NEW}::test_insert": DB,
        f"{NEW}::TestQuery.test_by_sprint[a]": DB,
    }


def test_no_report_is_no_pytest_and_nothing_is_read(tmp_path):
    assert skipped_tests(tmp_path) is None


def test_every_pytest_run_in_the_workspace_writes_the_report(worktree, ran):  # noqa: F811
    Held(worktree, "x", declared=Declared()).start()
    argv = next(a for a in ran if a[1] == "run")
    assert f"PYTEST_ADDOPTS=--junitxml=/work/{REPORT}" in argv
    assert with_report({"PYTEST_ADDOPTS": "-x"})["PYTEST_ADDOPTS"] == (
        f"-x --junitxml=/work/{REPORT}"
    )


def test_the_ci_only_reasons_are_the_projects_own_setting():
    raw = {"image": "i", "customizations": {"crew": {"ciOnlySkips": [DOCKER]}}}
    assert declared({".devcontainer.json": json.dumps(raw)}.get).ci_only == [DOCKER]


def declare(root: Path, *ci_only: str) -> None:
    """The project's workspace file, merged on the default branch."""
    raw = {"image": "i", "customizations": {"crew": {"ciOnlySkips": list(ci_only)}}}
    (root / ".devcontainer.json").write_text(json.dumps(raw))
    git(root, "checkout", "-q", "main")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "workspace file")
    git(root, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(root, "checkout", "-q", "feat/662-uncap")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "rebase", "-q", "main")


def adds_a_db_test(root: Path) -> None:
    (root / NEW).write_text("def test_insert():\n    assert True\n")


def skipping(skipped: dict[str, str]):
    def run(at: Path) -> CheckResult:
        return CheckResult(results=[CommandResult("uv run pytest -q", 0, "")], skipped=skipped)

    return run


def test_a_test_the_branch_adds_that_skips_goes_back(repo):  # noqa: F811
    """sprint-metrics#717's case: its new database tests skipped, and the gate passed."""
    adds_a_db_test(repo)
    verdict = at_done(repo, done(), run_checks=skipping({f"{NEW}::test_insert": DB}))
    (finding,) = [f for f in verdict.findings if f.rule == "check.gates.these_tests_skipped"]
    assert f"- `{NEW}::test_insert`: {DB}" in finding.says


def test_a_skip_the_project_declares_ci_only_is_not_refused(repo):  # noqa: F811
    declare(repo, DOCKER)
    adds_a_db_test(repo)
    verdict = at_done(
        repo, done(), run_checks=skipping({f"{NEW}::test_insert": f"Skipped: {DOCKER}"})
    )
    assert verdict.findings == [] and verdict.for_devops == {}


def test_a_merged_test_that_skips_is_devops_not_the_developers(repo):  # noqa: F811
    declare(repo, DOCKER)
    adds_a_db_test(repo)
    merged = f"{TESTS}::test_rate_of_nothing"
    verdict = at_done(repo, done(), run_checks=skipping({merged: DB}))
    assert verdict.findings == []
    assert verdict.for_devops == {merged: DB}


def test_with_no_workspace_file_merged_skips_wait_for_its_first_task(repo):  # noqa: F811
    """Until the project has the file nothing says what's CI-only: DevOps's first
    workspace Task covers them, not a finding per skip."""
    adds_a_db_test(repo)
    merged = f"{TESTS}::test_rate_of_nothing"
    verdict = at_done(repo, done(), run_checks=skipping({merged: DB, f"{NEW}::test_insert": DB}))
    assert [f.rule for f in verdict.findings] == ["check.gates.these_tests_skipped"]
    assert verdict.for_devops == {}


def test_a_changed_merged_test_that_skips_is_the_branchs(repo):  # noqa: F811
    (repo / TESTS).write_text(MERGED_TESTS.replace("rate(0, 0) == 0", "rate(0, 0) == 0, 'db'"))
    changed = f"{TESTS}::test_rate_of_nothing"
    verdict = at_done(
        repo,
        done((changed, "the story changes it")),
        story=f"**Existing tests** — this story changes what they assert: {changed}",
        run_checks=skipping({changed: DB}),
    )
    assert "check.gates.these_tests_skipped" in [f.rule for f in verdict.findings]


# --- DevOps's finding ------------------------------------------------------------------


def test_what_isnt_the_storys_is_one_devops_task_however_many_stories_meet_it(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    skipped = {f"tests/test_service.py::test_{i}": DB for i in range(7)}
    down = {"postgres": "it stopped: FATAL"}
    first = role_tasks.workspace_finding(
        issues, board, s, repo="sm", story=40, skipped=skipped, down=down, cards=[]
    )
    again = role_tasks.workspace_finding(
        issues, board, s, repo="sm", story=41, skipped=skipped, down=down, cards=[]
    )
    assert first == again and len(issues.issues) == 1
    task = issues.issues[first]
    assert {lab["name"] for lab in task["labels"]} == {"role:devops"}
    assert marked(task["body"]).kind == "finding"
    assert f'7 skipped for "{DB}"' in task["body"] and "and 2 more" in task["body"]
    assert "- `postgres`: it stopped: FATAL" in task["body"]
    filed = [e for e in events(tmp_path) if e["kind"] == "finding.filed"]
    assert [e["detail"]["owner"] for e in filed] == ["devops", "devops"]


def test_nothing_to_file_files_nothing(tmp_path):
    issues = Issues()
    found = role_tasks.workspace_finding(
        issues, Board(), sink(tmp_path), repo="sm", story=40, skipped={}, down={}, cards=[]
    )
    assert found is None and issues.issues == {}


def test_the_route_files_it_and_a_failed_filing_costs_the_story_nothing(tmp_path, monkeypatch):
    from crew_org.flows import gates, session_end

    verdict = gates.Verdict(check=CheckResult(results=[], services_down={"postgres": "x"}))

    class Cards(Board):
        def cards(self):
            return []

    def refused(*a, **kw):
        raise RuntimeError("GitHub said no")

    monkeypatch.setattr(role_tasks, "workspace_finding", refused)
    s = sink(tmp_path)
    session_end._for_devops(verdict, repo="sm", story=40, board=Cards(), issues=Issues(), sink=s)
    assert any("task not opened" in e["summary"] for e in events(tmp_path))
