"""A review that contradicts the story's own criteria goes to the Product Owner (#252).

On sprint-metrics#145 the Code Reviewer asked three times for the stdout
assertion to go, as the epic's design note said, and QA returned it twice
because the story's criterion required it. The reviewer had been shown the
note and never the criteria. It is now shown both, it names a clash as a
conflict, and a conflict sends the story back to refinement on the first
review, not the sixth delivery.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from crew_org.flows.review import render_review, review_open_pulls, story_criteria
from crew_org.tools.github_project import Card
from tests.test_review import BOT, REJECTION, FakeIssues, crew_pull

CRITERION = 'the exit code is 2, stderr contains "2024-01", and stdout is empty'
BODY = (
    "Serve the prior sprint.\n\n## Acceptance criteria\n\n"
    f"1. **Given** only 2024-02 **When** --prior-sprint 2024-01 **Then** {CRITERION}\n\n"
    "**Estimate** — 3 points\n\n---\n\nSplit from #59."
)

CONFLICT = ReviewVerdict(
    summary="The test asserts stdout is empty; the design note says not to.",
    approve=False,
    findings=[
        Finding(
            file="tests/test_prior_sprint.py",
            concern="asserts captured.out == '' against the design note",
            action="drop the stdout assertion so the later change is a pure addition",
            conflicts_with=CRITERION,
        )
    ],
)


def story(status: str = "Reviewing") -> Card:
    return Card(
        item_id="S6",
        number=6,
        title="Show metric 6",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        parent=59,
    )


class Issues(FakeIssues):
    def __init__(self, pulls):
        super().__init__(pulls)
        self.posted, self.closed, self.labels = [], [], set()

    def get(self, repo, number):
        return {"body": BODY}

    def comments(self, repo, number):
        return []

    def sub_issues(self, repo, number):
        return [{"number": 6}]

    def branches(self, repo):
        return []

    def ensure_label(self, repo, name, *, color, description):
        self.labels.add(name)

    def add_labels(self, repo, number, labels):
        pass

    def comment(self, repo, number, body):
        self.posted.append((number, body))
        return {}

    def close_pull(self, repo, number):
        self.closed.append(number)


class Board:
    def __init__(self, cards):
        self._cards = cards
        self.moves, self.cleared = [], []

    def cards(self):
        return self._cards

    def repo_of(self, item_id):
        return None

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass

    def clear_field(self, item_id, name):
        self.cleared.append((item_id, name))


def review(verdict, monkeypatch, seen=None):
    def judge(*args, **kwargs):
        if seen is not None:
            seen.update(kwargs)
        return verdict

    monkeypatch.setattr(review_flow, "review_diff", judge)
    card = story()
    issues, board = Issues([crew_pull()]), Board([card])
    result = review_open_pulls(
        issues, EventSink(None), repo="sprint-metrics", bot_login=BOT, board=board, cards=[card]
    )
    return result, issues, board


def test_the_reviewer_is_shown_the_storys_criteria(monkeypatch):
    seen: dict = {}
    review(REJECTION, monkeypatch, seen)
    assert CRITERION in seen["acceptance_criteria"]
    assert "Estimate" not in seen["acceptance_criteria"]


def test_a_story_without_criteria_gives_the_reviewer_none():
    class NoBody(Issues):
        def get(self, repo, number):
            return {"body": "No criteria here."}

    assert story_criteria(NoBody([]), story(), "sprint-metrics") == ""
    assert story_criteria(Issues([]), None, "sprint-metrics") == ""


def test_a_conflict_sends_the_story_back_on_the_first_review(monkeypatch):
    result, issues, board = review(CONFLICT, monkeypatch)

    assert result.returned == [(6, 59)]
    assert ("S6", "Ready") in board.moves and ("S6", "In Progress") not in board.moves
    assert ("S6", "Sprint") in board.cleared
    assert NEEDS_REWORK in issues.labels
    (evidence,) = [body for number, body in issues.posted if number == 59]
    assert STORY_PROBLEM_MARKER in evidence and CRITERION in evidence
    assert issues.closed == [14], "no repair round on a pull request that can't settle it"


def test_an_ordinary_rejection_still_goes_back_to_the_author(monkeypatch):
    result, issues, board = review(REJECTION, monkeypatch)
    assert result.returned == []
    assert board.moves == [("S6", "In Progress")] and issues.closed == []


def test_the_author_reads_which_criterion_the_finding_conflicts_with():
    assert f"Conflicts with the story's criterion: {CRITERION}" in render_review(CONFLICT)


def test_a_conflict_that_does_not_block_is_only_a_note():
    verdict = ReviewVerdict(
        summary="Fine.",
        approve=True,
        findings=[
            Finding(
                file="a.py",
                concern="c",
                action="consider renaming the helper",
                blocking=False,
                conflicts_with=CRITERION,
            )
        ],
    )
    assert verdict.conflicts == []


def test_an_approval_still_cannot_carry_a_blocking_conflict():
    with pytest.raises(ValidationError):
        ReviewVerdict(summary="s", approve=True, findings=CONFLICT.findings)


def test_a_review_is_recorded_under_the_card_it_closes_with_its_pull_request(monkeypatch):
    """Keyed by the pull request's number, a review had to be matched to its card
    through the delivery before it (crew#449, discussion 552)."""
    from crew_org.events import EventKind

    monkeypatch.setattr(review_flow, "review_diff", lambda *a, **k: REJECTION)
    card = story()
    issues, board = Issues([crew_pull()]), Board([card])
    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    review_open_pulls(issues, sink, repo="sprint-metrics", bot_login=BOT, board=board, cards=[card])
    work = [e for e in seen if e.kind in (EventKind.AGENT_STARTED, EventKind.AGENT_FINISHED)]
    assert work and all(e.card == card.number for e in work)
    assert all(e.detail["pr"] == crew_pull()["number"] for e in work)
    assert len({e.ctx["run"] for e in work}) == 1
    [finished] = [e for e in work if e.kind == EventKind.AGENT_FINISHED]
    findings = finished.detail["findings"]
    assert findings and all({"file", "statement", "blocking"} == set(f) for f in findings)
    assert findings[0]["statement"] == " ".join(REJECTION.findings[0].concern.split())[:300]
