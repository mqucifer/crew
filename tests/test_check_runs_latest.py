"""A check counts by its latest run at a head, not by every run (crew#1053)."""

from __future__ import annotations

import httpx

from crew_org.tools.github_issues import IssueClient, Trust

QA = "qa (crew-presentation) / qa"


def _run(ident: int, name: str, conclusion: str | None, *, app: int = 15368) -> dict:
    status = "completed" if conclusion else "in_progress"
    return {
        "id": ident,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": app},
    }


def _client(runs: list[dict]) -> IssueClient:
    answer = {"total_count": len(runs), "check_runs": runs}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=answer))
    return IssueClient(
        "token",
        "mqucifer",
        client=httpx.Client(transport=transport),
        trust=Trust(sponsor="mquarters", crew=frozenset(), tools=frozenset()),
    )


def test_a_check_that_failed_twice_then_passed_is_not_failed():
    # mqucifer/qa-suite 59 on 2026-11-11: two failed runs, then a pass, at one head.
    issues = _client(
        [
            _run(3, QA, "success"),
            _run(1, QA, "failure"),
            _run(2, QA, "failure"),
            _run(4, "checks", "success"),
        ]
    )
    assert issues.failed_checks("qa-suite", "abc") == []
    assert {r["id"] for r in issues.check_runs("qa-suite", "abc")} == {3, 4}


def test_a_check_whose_latest_run_failed_is_failed():
    issues = _client([_run(1, QA, "success"), _run(2, QA, "failure")])
    assert [r["id"] for r in issues.failed_checks("qa-suite", "abc")] == [2]


def test_a_rerun_still_running_replaces_the_failure_it_reruns():
    issues = _client([_run(1, QA, "failure"), _run(2, QA, None)])
    assert issues.failed_checks("qa-suite", "abc") == []
    assert [r["status"] for r in issues.check_runs("qa-suite", "abc")] == ["in_progress"]


def test_checks_of_the_same_name_from_different_apps_are_kept_apart():
    issues = _client([_run(1, "build", "failure", app=1), _run(2, "build", "success", app=2)])
    assert [r["id"] for r in issues.failed_checks("qa-suite", "abc")] == [1]
