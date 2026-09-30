"""The sprint close waits for the merge queue, and carries what hasn't landed (#389).

A story still queued at the close was reported not done; GitHub merged it
minutes later, into a sprint whose report was already written.
"""

from __future__ import annotations

from datetime import date

import pytest

import crew_org.flows.close as close_mod
from crew_org.escalation import EscalationLedger
from crew_org.events import EventSink
from crew_org.flows.close import close_sprint
from crew_org.flows.merge import Landed, Landing
from crew_org.tools.github_issues import QueueState
from crew_org.tools.github_project import Card

SPRINT = "Sprint 11"


def card(number):
    return Card(
        item_id=f"C{number}",
        number=number,
        title=f"Story {number}",
        status="Merging",
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        sprint=SPRINT,
        points=3,
    )


class Sprints:
    def __init__(self, following):
        self._following = following

    def next_iteration(self, title):
        return (self._following, date(2026, 10, 1)) if self._following else None


class Schema:
    def __init__(self, following):
        self._sprints = Sprints(following)

    def field(self, name):
        return self._sprints


class Board:
    def __init__(self, cards, following="Sprint 12"):
        self._cards = {c.item_id: c for c in cards}
        self.schema = Schema(following)
        self.iterations = {}

    def cards(self):
        return list(self._cards.values())

    def set_status(self, item_id, column):
        c = self._cards[item_id]
        self._cards[item_id] = c.model_copy(update={"status": column})

    def set_owner_agent(self, item_id, role):
        pass

    def set_iteration(self, item_id, field, title):
        self.iterations[item_id] = title


class Issues:
    owner = "mqucifer"

    def __init__(self, *, merges_after_polls):
        self._polls = {n: after for n, after in merges_after_polls.items()}
        self.closed = []

    def pull_for_branch(self, repo, branch, *, known=None):
        number = int(branch.split("/")[1].split("-")[0])
        return {"number": 1000 + number, "base": {"ref": "main"}}

    def pull_reviews(self, repo, number):
        return [{"state": "APPROVED"}]

    def review_decision(self, repo, number):
        return "APPROVED"

    def queue_state(self, repo, number, *, branch):
        left = self._polls.get(number)
        if left is None:  # never leaves the queue
            return QueueState(queued=True)
        self._polls[number] = left - 1
        return QueueState(queued=left > 0)

    def pull(self, repo, number):
        merged = self._polls.get(number) is not None and self._polls[number] <= 0
        return {"merged_at": "2026-09-30T20:00:00Z" if merged else None}

    def close(self, repo, number, *, reason="completed"):
        self.closed.append(number)


@pytest.fixture(autouse=True)
def queued_and_no_retro(monkeypatch):
    monkeypatch.setattr(close_mod, "write_retro", lambda *a, **k: None)
    monkeypatch.setattr(close_mod, "land", lambda *a, **k: Landed(Landing.QUEUED))


def close(board, issues, tmp_path):
    ticks = iter(range(0, 10**6, 15))
    return close_sprint(
        board,
        issues,
        EventSink(None),
        EscalationLedger(tmp_path / "escalations.jsonl"),
        sprint=SPRINT,
        repo="sprint-metrics",
        sleep=lambda _s: None,
        clock=lambda: float(next(ticks)),
    )


def test_a_story_that_merges_while_the_close_waits_is_done_in_this_sprint(tmp_path):
    board, issues = Board([card(375)]), Issues(merges_after_polls={1375: 2})
    result = close(board, issues, tmp_path)
    assert result.merged == [375] and result.queued == [] and result.carried == []
    assert issues.closed == [375]
    assert "C375" not in board.iterations


def test_a_story_still_queued_after_the_wait_is_carried_to_the_next_sprint(tmp_path):
    board, issues = Board([card(376)]), Issues(merges_after_polls={})
    result = close(board, issues, tmp_path)
    assert result.merged == []
    assert result.carried == [(376, "Sprint 12")]
    assert board.iterations == {"C376": "Sprint 12"}


def test_with_no_next_sprint_it_stays_and_is_still_named(tmp_path):
    board = Board([card(377)], following=None)
    result = close(board, Issues(merges_after_polls={}), tmp_path)
    assert result.carried == [(377, "")] and board.iterations == {}
