"""The sprint close keeps the Sponsor's gate on a role's Task (crew#839, crew#938).

A Task whose work is a diagram or a release waits for the Sponsor's own approval
on its current head, whoever else approved it. The tick's merge held it there;
the close took any approving review and would have merged it. Once the close
read another repo's cards in their own repo, mqucifer/crew-presentation#195,
approved only by the approver bot, was the next it would have landed.
"""

from __future__ import annotations

import pytest

import crew_org.flows.close as close_mod
from crew_org.events import EventSink
from crew_org.flows.close import close_sprint
from crew_org.flows.role_tasks import DEVOPS, render
from crew_org.tools.github_project import Card
from tests.test_close_card_repo import Board, Issues

SPRINT = "Sprint 34"
PILOT = "sprint-metrics"
OTHER = "crew-presentation"
SPONSOR = "mquarters"
HEAD = "h2"

BOT = {"user": {"login": "crew-approver[bot]"}, "state": "APPROVED", "commit_id": HEAD}


def theirs(at=HEAD):
    return {"user": {"login": SPONSOR}, "state": "APPROVED", "commit_id": at}


def task(number: int, repo: str = OTHER) -> Card:
    return Card(
        item_id=f"{repo}-{number}",
        number=number,
        title=f"Release {number}",
        status="Merging",
        state="OPEN",
        work_type="Task",
        repo=repo,
        sprint=SPRINT,
        points=1,
    )


def story(number: int, repo: str = OTHER) -> Card:
    return task(number, repo).model_copy(update={"work_type": "Story", "title": f"Story {number}"})


class ReleaseIssues(Issues):
    """Each card's PR at head `h2`, with these reviews; Tasks are release Tasks."""

    def __init__(self, reviews, *, branches):
        super().__init__({(r, n): True for (r, _), n in branches.items()}, branches=branches)
        self._reviews = reviews

    def pull_for_branch(self, repo, branch, *, known=None):
        found = super().pull_for_branch(repo, branch, known=known)
        return found and {**found, "head": {"ref": branch, "sha": HEAD}}

    def pull_reviews(self, repo, number):
        self._known(repo, number)
        return list(self._reviews)

    def get(self, repo, number):
        body = render(DEVOPS, kind="release", key="epic-10", why="w", work="x", done_when=["d"])
        return {"body": body}


@pytest.fixture(autouse=True)
def no_retro(monkeypatch):
    monkeypatch.setattr(close_mod, "write_retro", lambda *a, **k: None)


def run(card, issues, sponsor=SPONSOR):
    ticks = iter(range(0, 10**6, 15))
    return close_sprint(
        Board([card]),
        issues,
        EventSink(None),
        sprint=SPRINT,
        repo=PILOT,
        sponsor=sponsor,
        sleep=lambda _s: None,
        clock=lambda: float(next(ticks)),
    )


def test_a_role_task_approved_only_by_the_bot_is_not_landed_at_close():
    issues = ReleaseIssues([BOT], branches={(OTHER, 177): 195})
    result = run(task(177), issues)
    assert issues.merged == []
    assert result.awaiting_sponsor == [(177, 195)]
    assert result.merged == [] and not result.complete


def test_the_sponsors_approval_of_an_older_head_does_not_count():
    issues = ReleaseIssues([BOT, theirs(at="h1")], branches={(OTHER, 177): 195})
    result = run(task(177), issues)
    assert issues.merged == [] and result.awaiting_sponsor == [(177, 195)]


def test_a_role_task_the_sponsor_approved_on_its_head_is_landed():
    issues = ReleaseIssues([BOT, theirs()], branches={(OTHER, 177): 195})
    result = run(task(177), issues)
    assert issues.merged == [(OTHER, 195)]
    assert result.merged == [177] and result.awaiting_sponsor == []


def test_with_no_sponsor_named_a_role_task_never_lands(monkeypatch):
    from crew_org.flows import role_tasks as rt

    monkeypatch.setattr(rt, "load_trust", lambda: (_ for _ in ()).throw(ValueError("none")))
    issues = ReleaseIssues([BOT, theirs()], branches={(OTHER, 177): 195})
    result = run(task(177), issues, sponsor=None)
    assert issues.merged == []
    assert result.no_sponsor == [(177, 195)] and not result.complete


def test_an_ordinary_story_with_the_bots_approval_still_lands():
    issues = ReleaseIssues([BOT], branches={(OTHER, 12): 40})
    result = run(story(12), issues)
    assert issues.merged == [(OTHER, 40)]
    assert result.merged == [12]
