"""The sprint close reads and lands each card's pull request in the card's own repo.

The board holds cards from several repositories, and the close asked the pilot
repo for every card's pull request: Sprint 34 stopped on a 404 for
crew-presentation's PR 195, asked of sprint-metrics, where 195 is an issue
(crew#938). Where the numbers line up, it would read the wrong pull request's
approval and could merge it.
"""

from __future__ import annotations

import pytest

import crew_org.flows.close as close_mod
from crew_org.events import EventSink
from crew_org.flows.close import close_sprint
from crew_org.flows.merge import Landed, Landing
from crew_org.tools.github_issues import QueueState
from crew_org.tools.github_project import Card

SPRINT = "Sprint 34"
PILOT = "sprint-metrics"
OTHER = "crew-presentation"


def card(number: int, repo: str) -> Card:
    return Card(
        item_id=f"{repo}-{number}",
        number=number,
        title=f"Story {number}",
        status="Merging",
        state="OPEN",
        work_type="Story",
        repo=repo,
        sprint=SPRINT,
        points=3,
    )


class Board:
    schema = None

    def __init__(self, cards):
        self._cards = {c.item_id: c for c in cards}
        self.moves = []

    def cards(self):
        return list(self._cards.values())

    def repo_of(self, item_id):
        return None

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))
        c = self._cards[item_id]
        self._cards[item_id] = c.model_copy(update={"status": column})

    def set_owner_agent(self, item_id, role):
        pass


class Issues:
    """Pull requests by (repo, number); a call for one that isn't there is a 404."""

    owner = "mqucifer"

    def __init__(self, pulls, *, branches, queue=None):
        # (repo, number) -> approved?
        self._pulls = pulls
        # (repo, story) -> its pull request's number
        self._branches = branches
        # (repo, number) -> polls before it leaves the queue merged
        self._queue = dict(queue or {})
        self.read: list[tuple[str, int]] = []
        self.merged: list[tuple[str, int]] = []
        self.closed: list[tuple[str, int]] = []

    def _known(self, repo, number):
        if (repo, number) not in self._pulls:
            raise RuntimeError(f"404 Not Found: {repo} pull {number}")
        self.read.append((repo, number))

    def pull_for_branch(self, repo, branch, *, known=None):
        story = int(branch.split("/")[1].split("-")[0])
        number = self._branches.get((repo, story))
        if number is None:
            return None
        return {"number": number, "base": {"ref": "main"}}

    def pull_reviews(self, repo, number):
        self._known(repo, number)
        return [{"state": "APPROVED"}] if self._pulls[(repo, number)] else []

    def review_decision(self, repo, number):
        self._known(repo, number)
        return "APPROVED" if self._pulls[(repo, number)] else "REVIEW_REQUIRED"

    def pull(self, repo, number):
        self._known(repo, number)
        left = self._queue.get((repo, number))
        return {
            "merged": False,
            "merged_at": "2026-10-29T08:00:00Z" if left is not None and left <= 0 else None,
            "mergeable_state": "clean",
            "mergeable": True,
            "base": {"ref": "main"},
            "head": {"sha": "abc"},
        }

    def check_runs(self, repo, sha):
        return [{"name": "tests", "status": "completed", "conclusion": "success"}]

    def failed_checks(self, repo, sha):
        return []

    def merge_group_failures(self, repo, pull):
        return []

    def pulls_for_branch(self, repo, branch, *, state="all"):
        return []

    def queue_state(self, repo, number, *, branch):
        self._known(repo, number)
        left = self._queue.get((repo, number))
        if left is None:
            return QueueState()
        self._queue[(repo, number)] = left - 1
        return QueueState(queued=left > 0)

    def merge_pull(self, repo, number, **kw):
        self._known(repo, number)
        self.merged.append((repo, number))
        return {}

    def close(self, repo, number, *, reason="completed"):
        self.closed.append((repo, number))


@pytest.fixture(autouse=True)
def no_retro(monkeypatch):
    monkeypatch.setattr(close_mod, "write_retro", lambda *a, **k: None)


def run(board, issues):
    ticks = iter(range(0, 10**6, 15))
    return close_sprint(
        board,
        issues,
        EventSink(None),
        sprint=SPRINT,
        repo=PILOT,
        sleep=lambda _s: None,
        clock=lambda: float(next(ticks)),
    )


def test_each_card_is_read_and_landed_in_its_own_repo():
    board = Board([card(12, PILOT), card(177, OTHER)])
    issues = Issues(
        {(PILOT, 40): True, (OTHER, 195): True},
        branches={(PILOT, 12): 40, (OTHER, 177): 195},
    )
    result = run(board, issues)
    assert sorted(issues.merged) == [(OTHER, 195), (PILOT, 40)]
    assert sorted(result.merged) == [12, 177]
    assert result.unmergeable == []


def test_the_pilots_pull_request_of_the_same_number_is_never_read_for_another_repos_card():
    board = Board([card(177, OTHER)])
    # The pilot's 195 is approved; crew-presentation's 195 is not.
    issues = Issues(
        {(PILOT, 195): True, (OTHER, 195): False},
        branches={(PILOT, 177): 195, (OTHER, 177): 195},
    )
    result = run(board, issues)
    assert (PILOT, 195) not in issues.read
    assert issues.merged == []
    assert result.awaiting_approval == [(177, 195)]
    assert result.merged == []


def test_the_queue_wait_asks_the_cards_own_repo(monkeypatch):
    monkeypatch.setattr(close_mod, "land", lambda *a, **k: Landed(Landing.QUEUED))
    board = Board([card(177, OTHER)])
    issues = Issues(
        {(OTHER, 195): True},
        branches={(OTHER, 177): 195},
        queue={(OTHER, 195): 2},
    )
    result = run(board, issues)
    assert result.merged == [177] and result.queued == []
    assert issues.closed == [(OTHER, 177)]
    assert all(repo == OTHER for repo, _ in issues.read)
