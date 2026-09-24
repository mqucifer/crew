"""Sprint close — the Sponsor's second gate.

The crew cannot approve its own pull requests, so reviewing the increment *is*
approving the pull requests that make it up. That makes the wording of what is
outstanding load-bearing: a card reported as waiting on the Sponsor's approval
when the approval is already there sends them to press a button that will not
help.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from crew_org.escalation import EscalationLedger
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows.close import close_sprint
from crew_org.process import ProcessRules
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

    def pull_for_branch(self, repo, branch, *, known=None):

        if known is not None:
            return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
        return {"number": 100}

    def pull_reviews(self, repo, number):
        return self._reviews

    def review_decision(self, repo, number):
        return self._decision

    def merge_pull(self, repo, number, **kw):
        self.merged.append(number)
        return {}

    def pull(self, repo, number):
        return getattr(self, "detail", {})

    def update_branch(self, repo, number, *, head=None):
        self.updated = getattr(self, "updated", []) + [number]


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


# --- §12: an aging blocked card is raised at the review ------------------

BLOCKED = "Blocked"

AGING_ORG = {
    "board": {
        "columns": ["Sprint Backlog", "In Progress", "Merging", "Done"],
        "blocked_column": BLOCKED,
        "human_gates": [],
    },
    "wip_limits": {"In Progress": 3},
    "sprint": {"blocked_aging_days": 3},
}

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def blocked_log(tmp_path, card_number: int, days: int):
    """An event log in which `card_number` was blocked `days` ago."""
    sink = EventSink(tmp_path / "deliver.jsonl")
    sink.emit(
        CrewEvent(
            at=NOW - timedelta(days=days),
            kind=EventKind.CARD_MOVED,
            card=card_number,
            detail={"from": "In Progress", "to": BLOCKED},
        )
    )
    return tmp_path


def close_with_log(issues, ledger, events_dir, cards):
    board = FakeBoard(cards)
    return close_sprint(
        board,
        issues,
        EventSink(None),
        ledger,
        sprint=SPRINT,
        repo="sprint-metrics",
        rules=ProcessRules.from_config(AGING_ORG),
        events_dir=events_dir,
        now=NOW,
    )


def test_a_card_blocked_past_the_threshold_is_raised(ledger, tmp_path):
    """§12 says these are raised at sprint review, blocked_aging_days is
    configured with a comment saying exactly that, and `aging_blocked` has
    always been able to answer which. Nothing joined them, so on 2026-09-19 a
    card blocked most of a day went unmentioned."""
    stuck = card(12, status=BLOCKED)
    result = close_with_log(
        FakeIssues(reviews=[], decision=None), ledger, blocked_log(tmp_path, 12, 4), [stuck]
    )
    assert result.aging_blocked == [("#12", 4)]


def test_a_card_blocked_yesterday_is_not_raised(ledger, tmp_path):
    stuck = card(12, status=BLOCKED)
    result = close_with_log(
        FakeIssues(reviews=[], decision=None), ledger, blocked_log(tmp_path, 12, 1), [stuck]
    )
    assert result.aging_blocked == []


def test_a_close_given_no_log_raises_nothing(ledger):
    """Without the rules and the log, the raise cannot happen at all. A caller
    that only wants the merge still gets one."""
    result, _ = run(FakeIssues(reviews=[{"state": "APPROVED"}], decision="APPROVED"), ledger)
    assert result.aging_blocked == []


def test_a_story_behind_main_is_brought_up_to_date_at_close(ledger):
    """#116: the same 405 as the tick, on the Sponsor's own gate."""
    issues = FakeIssues(reviews=[{"state": "APPROVED"}], decision="APPROVED")
    issues.detail = {"mergeable_state": "behind", "head": {"sha": "c0ffee"}}
    result, board = run(issues, ledger)
    assert issues.merged == [] and issues.updated == [100]
    assert result.updating == [(6, 100)]
    assert not result.complete, "a story still to land is not a finished sprint"
