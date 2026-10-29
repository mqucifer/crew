"""A tick on a project does that project's QA work in qa-suite (crew#945).

The operator ticks one project at a time, and QA's Tasks were worked only in a tick on
qa-suite: no story merged since 2026-10-26 opened its test Task, and a story waiting on
QA's dispute couldn't go on in its own project's tick.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from crew_org.columns import READY, SPRINT_BACKLOG
from crew_org.flows import loop
from crew_org.flows.review import review_open_pulls
from crew_org.flows.sprint import plan_sprint
from crew_org.tools.github_project import Card, within
from crew_org.work_types import SUITE, TASK, open_in, works
from tests.role_task_fakes import sink


def _task(number: int, project: str, status: str = READY) -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title=f"QA {number}",
        state="OPEN",
        status=status,
        work_type=TASK,
        labels=frozenset({"role:qa", f"project:{project}"}),
        repo=SUITE,
        points=2,
    )


def test_a_qa_suite_task_is_worked_in_its_project_s_tick_and_no_other():
    assert works(_task(1, "sm"), {"sm"})
    assert not works(_task(1, "other"), {"sm"})
    # A tick on qa-suite, or a bare tick, works all of them, as before.
    assert works(_task(1, "other"), {SUITE})
    assert works(_task(1, "other"), None)
    story = Card(item_id="S", number=2, title="s", repo="sm", labels=frozenset({"project:sm"}))
    assert works(story, {"sm"}) and not works(story, {"other"})


def test_its_tasks_are_admitted_delivered_and_landed_in_the_project_s_tick():
    ours, theirs = _task(1, "sm"), _task(2, "other")
    plan = plan_sprint([ours, theirs], {}, sprint="S1", capacity=10, repos={"sm"})
    assert [c.number for c in plan.admitted] == [1]
    backlog = [_task(1, "sm", SPRINT_BACKLOG), _task(2, "other", SPRINT_BACKLOG)]
    assert [c.number for c in open_in(backlog, SPRINT_BACKLOG, repos={"sm"})] == [1]
    assert [c.number for c in within([ours, theirs], {"sm"})] == [1]


def _crew(repos: set[str], delivery: list[str], not_ready=None):
    return SimpleNamespace(
        repos=repos, org={"delivery": {"repos": delivery}}, not_ready=not_ready or {}
    )


def test_a_project_s_tick_does_qa_s_work_when_the_crew_works_in_qa_suite():
    assert loop.works_in_suite(_crew({"sm"}, ["sm", SUITE]))
    assert loop.works_in_suite(_crew({"sm", SUITE}, ["sm", SUITE]))
    assert not loop.works_in_suite(_crew({"sm"}, ["sm"]))
    assert not loop.works_in_suite(_crew({"sm"}, ["sm", SUITE], {SUITE: "not ready"}))


class Reviewed(Exception):
    pass


class Pulls:
    """qa-suite with a pull request for another project's Task, then one for ours."""

    def __init__(self) -> None:
        self.read: list[int] = []

    def open_pulls(self, repo):
        return [
            {"number": 12, "head": {"ref": "task/2-qa-2", "sha": "b"}, "user": {"login": "c"}},
            {"number": 11, "head": {"ref": "task/1-qa-1", "sha": "a"}, "user": {"login": "c"}},
        ]

    def pull_reviews(self, repo, number):
        # The review starts here: far enough to know which it took up.
        self.read.append(number)
        raise Reviewed


def test_a_project_s_tick_reviews_only_its_own_tasks_pull_requests_in_qa_suite(
    tmp_path, monkeypatch
):
    from crew_org.flows import review

    ours = _task(1, "sm")
    monkeypatch.setattr(review, "cards_by_branch", lambda cards: {"task/1-qa-1": ours})
    issues = Pulls()
    with pytest.raises(Reviewed):
        review_open_pulls(
            issues,
            sink(tmp_path),
            repo=SUITE,
            bot_login="rev",
            board=SimpleNamespace(),
            cards=[ours],
            only_cards=True,
        )
    assert issues.read == [11]
