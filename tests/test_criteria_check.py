"""A split whose criteria can't all pass goes to the Product Owner, not the board (#428).

sprint-metrics#184's split gave #393 two criteria for one request, a
`card_blocked` for a card never created: 404 in one, 400 in the other.
"""

from __future__ import annotations

from crew_org.crews.criteria_crew import CriteriaCheck, CriteriaConflict
from crew_org.crews.refinement_crew import StoryProposal
from crew_org.flows import board_flow, criteria_check
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from tests.test_board_flow import SPLIT, FakeBoard, FakeIssues, epic_card, make_story

CONFLICT = CriteriaCheck(
    conflicts=[
        CriteriaConflict(
            story="Accept blocked events",
            criterion="Then the response has HTTP status 400",
            against="criterion 4 of the same story: Then the response has HTTP status 404",
            why="both post card_blocked for a card that was never created",
        )
    ]
)


class Checker:
    def __init__(self, *answers) -> None:
        self.answers, self.calls = list(answers), []

    def __call__(self, **context):
        self.calls.append(context)
        return self.answers.pop(0) if self.answers else CriteriaCheck()


def splitting(monkeypatch, checker, proposals=None):
    asked: list[dict] = []
    answers = list(proposals or [SPLIT, SPLIT])

    def split(title, context="", **kw):
        asked.append(kw)
        return answers.pop(0) if answers else SPLIT

    monkeypatch.setattr(board_flow, "check_criteria", checker)
    monkeypatch.setattr(board_flow, "split_epic", split)
    return asked


def test_a_split_whose_criteria_pass_creates_its_stories(monkeypatch):
    checker = Checker()
    issues = FakeIssues()
    result = run_split_checked(monkeypatch, issues, checker)
    assert len(result.stories_created) == 2 and len(checker.calls) == 1


def test_a_conflict_is_split_again_once_with_the_conflict_named(monkeypatch):
    checker = Checker(CONFLICT, CriteriaCheck())
    issues = FakeIssues()
    asked = []
    result = run_split_checked(monkeypatch, issues, checker, asked=asked)
    assert len(asked) == 2 and "HTTP status 400" in asked[1]["feedback"]
    assert len(result.stories_created) == 2


def test_a_conflict_that_survives_goes_to_the_product_owner_and_creates_nothing(monkeypatch):
    checker = Checker(CONFLICT, CONFLICT)
    issues = FakeIssues()
    result = run_split_checked(monkeypatch, issues, checker)
    assert result.stories_created == []
    assert (3, NEEDS_REWORK) in issues.added_labels
    body = [b for n, b in issues.posted if n == 3][-1]
    assert STORY_PROBLEM_MARKER in body and "HTTP status 400" in body and "404" in body


def test_the_check_reads_each_proposed_criterion_and_what_other_epics_plan():
    proposal = StoryProposal(epic_title="E", stories=[make_story("Accept blocked events", 3)])
    text = criteria_check.render_split(proposal)
    assert "### Accept blocked events" in text and "Given" in text and "Then" in text

    class Planned:
        def get(self, repo, number):
            body = "As a user.\n\n## Acceptance criteria\n\n1. **Given** x\n\n**Estimate** — 2"
            return {"body": body}

    planned = criteria_check.planned_criteria(Planned(), "r", [(397, "Schema", 185)])
    assert "#397 Schema (epic #185)" in planned and "**Given** x" in planned
    assert "Estimate" not in planned


def run_split_checked(monkeypatch, issues, checker, asked=None):
    seen = splitting(monkeypatch, checker)
    result = run_split_tick(monkeypatch, issues)
    if asked is not None:
        asked.extend(seen)
    return result


def run_split_tick(monkeypatch, issues):
    from crew_org.events import EventSink

    monkeypatch.setattr(board_flow, "propose_epics", lambda g, **kw: None)
    board = FakeBoard([epic_card(3)])
    return board_flow.tick(board, issues, EventSink(None), default_repo="sprint-metrics")
