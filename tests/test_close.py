"""Sprint close — the Sponsor's second gate.

The crew cannot approve its own pull requests, so reviewing the increment *is*
approving the pull requests that make it up. That makes the wording of what is
outstanding load-bearing: a card reported as waiting on the Sponsor's approval
when the approval is already there sends them to press a button that will not
help.
"""

from __future__ import annotations

import pytest

from crew_org.escalation import EscalationLedger
from crew_org.events import EventSink
from crew_org.flows.close import close_sprint
from crew_org.tools.github_project import Card

SPRINT = "Sprint 3"


def card(number: int, status: str = "Merging") -> Card:
    return Card(
        item_id=f"C{number}",
        number=number,
        title=f"Show metric {number}",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        sprint=SPRINT,
        points=3,
    )


class FakeBoard:
    def __init__(self, cards):
        self._cards = cards
        self.moves = []

    def cards(self):
        return self._cards

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


class FakeIssues:
    def __init__(self, *, reviews, decision):
        self.owner = "mqucifer"
        self._reviews, self._decision = reviews, decision
        self.merged = []

    def pull_for_branch(self, repo, branch):
        return {"number": 100}

    def pull_reviews(self, repo, number):
        return self._reviews

    def review_decision(self, repo, number):
        return self._decision

    def merge_pull(self, repo, number, **kw):
        self.merged.append(number)
        return {}


@pytest.fixture(autouse=True)
def no_retro(monkeypatch):
    """The retro is the Scrum Master writing prose, and it calls the model.
    Nothing here is about the retro, and a close should not reach inference to
    prove how it classified an approval."""
    import crew_org.flows.close as close_mod

    monkeypatch.setattr(close_mod, "write_retro", lambda *a, **k: None)


@pytest.fixture
def ledger(tmp_path):
    return EscalationLedger(tmp_path / "escalations.jsonl")


def run(issues, ledger, cards=None):
    board = FakeBoard(cards or [card(6)])
    return (
        close_sprint(
            board,
            issues,
            EventSink(None),
            ledger,
            sprint=SPRINT,
            repo="sprint-metrics",
        ),
        board,
    )


def test_an_approved_story_is_merged_and_done(ledger):
    issues = FakeIssues(reviews=[{"state": "APPROVED"}], decision="APPROVED")
    result, board = run(issues, ledger)
    assert issues.merged == [100]
    assert result.merged == [6]
    assert ("C6", "Done") in board.moves


def test_a_story_nobody_approved_waits_on_the_sponsor(ledger):
    issues = FakeIssues(reviews=[{"state": "COMMENTED"}], decision="REVIEW_REQUIRED")
    result, _ = run(issues, ledger)
    assert result.awaiting_approval == [(6, 100)]
    assert result.unapprovable == []


def test_an_approval_github_will_not_count_is_not_the_sponsors_to_give(ledger):
    """The reviewing identity approved it and protection still says
    REVIEW_REQUIRED. Reporting that as "waiting on your approval" sends the
    Sponsor to approve a pull request that is already approved."""
    issues = FakeIssues(reviews=[{"state": "APPROVED"}], decision="REVIEW_REQUIRED")
    result, _ = run(issues, ledger)
    assert issues.merged == []
    assert result.unapprovable == [(6, 100)]
    assert result.awaiting_approval == []


def test_a_sprint_holding_an_uncountable_approval_is_not_complete(ledger):
    issues = FakeIssues(reviews=[{"state": "APPROVED"}], decision="REVIEW_REQUIRED")
    result, _ = run(issues, ledger)
    assert not result.complete
