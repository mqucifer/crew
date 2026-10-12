"""A filed bug, triaged into the flow (crew#407, ADR 0027)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from crew_org.columns import NEEDS_REFINEMENT, READY
from crew_org.crews import bug_triage_crew
from crew_org.crews.bug_triage_crew import Triage, triage_bug
from crew_org.crews.refinement_crew import AcceptanceCriterion
from crew_org.flows import bugs
from crew_org.tools.github_project import Card
from tests.asked import capture

REPORT = "**What happens:** `/sprint?label=Sprint%2022` returns nulls.\n**Expected:** its metrics."
CRITERION = AcceptanceCriterion(
    given="a sprint named `Sprint 22` with one finished card",
    when="GET /sprint?label=Sprint%2022 is asked",
    then="the response's throughput is 1",
)


def _triage(outcome: str, **kw: Any) -> Triage:
    fields: dict[str, Any] = {
        "outcome": outcome,
        "reason": "service.py slices the raw path",
        "acceptance_criteria": [],
        "points": None,
        "duplicate_of": None,
        "question": "",
    }
    return Triage(**{**fields, **kw})


def _card(number: int = 593, **kw: Any) -> Card:
    fields: dict[str, Any] = {
        "item_id": f"I{number}",
        "number": number,
        "title": "/sprint can't find a sprint whose name has a space",
        "status": NEEDS_REFINEMENT,
        "state": "OPEN",
        "repo": "sm",
        "labels": frozenset({"bug"}),
    }
    return Card(**{**fields, **kw})


class Board:
    def __init__(self) -> None:
        self.done: list[tuple] = []

    def set_select(self, item: str, field: str, option: str) -> None:
        self.done.append(("select", field, option))

    def set_number(self, item: str, field: str, value: float) -> None:
        self.done.append(("number", field, value))

    def set_status(self, item: str, column: str) -> None:
        self.done.append(("status", column))

    def set_owner_agent(self, item: str, role: str) -> None:
        pass

    def repo_of(self, item: str) -> str:
        return "sm"


class Issues:
    def __init__(self, permission: str = "write") -> None:
        self.granted = permission
        self.done: list[tuple] = []

    def get(self, repo: str, number: int) -> dict:
        return {"body": REPORT, "user": {"login": "quarters-claude"}}

    def permission(self, repo: str, user: str) -> str:
        return self.granted

    def edit_issue(self, repo: str, number: int, *, body: str) -> None:
        self.done.append(("edit", body))

    def comment(self, repo: str, number: int, body: str) -> None:
        self.done.append(("comment", body))

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        self.done.append(("label", *labels))

    def remove_label(self, repo: str, number: int, label: str) -> None:
        pass

    def close(self, repo: str, number: int, *, reason: str) -> None:
        self.done.append(("close", reason))


class Sink:
    def emit(self, event: Any) -> None:
        pass

    def note(self, *a: Any, **kw: Any) -> None:
        pass


def test_an_accepted_bug_has_criteria_and_a_bugs_size():
    _triage("accepted", acceptance_criteria=[CRITERION], points=2)
    with pytest.raises(ValidationError, match="at least one criterion"):
        _triage("accepted", points=2)
    with pytest.raises(ValidationError, match="sized one of"):
        _triage("accepted", acceptance_criteria=[CRITERION], points=8)


def test_each_other_outcome_carries_what_it_needs_and_no_criteria():
    with pytest.raises(ValidationError, match="only an accepted bug has criteria"):
        _triage("not_a_bug", acceptance_criteria=[CRITERION])
    with pytest.raises(ValidationError, match="duplicate_of"):
        _triage("duplicate")
    with pytest.raises(ValidationError, match="asks its question"):
        _triage("more_information")
    _triage("duplicate", duplicate_of=12)
    _triage("more_information", question="Which sprint name did you query?")


def test_only_untyped_open_bugs_not_waiting_on_a_person_are_triaged():
    cards = [
        _card(1),
        _card(2, work_type="Bug"),
        _card(3, labels=frozenset({"bug", "needs:human"})),
        _card(4, labels=frozenset()),
        _card(5, state="CLOSED"),
        _card(6, status=READY),
    ]
    assert [c.number for c in bugs.bugs_to_triage(cards)] == [1]


def test_an_accepted_bug_keeps_its_report_and_gains_the_story_format():
    body = bugs.accepted_body(
        REPORT, _triage("accepted", acceptance_criteria=[CRITERION], points=2)
    )
    assert body.startswith(REPORT)
    assert "## Acceptance criteria\n\n1. **Given** a sprint named `Sprint 22`" in body
    assert "**Estimate** — 2 points" in body
    assert "Triaged as a bug by the Business Analyst: service.py slices the raw path" in body


def test_accepted_becomes_a_bug_in_ready_with_its_points():
    board, issues = Board(), Issues()
    triage = _triage("accepted", acceptance_criteria=[CRITERION], points=2)
    bugs.carry_out(board, issues, Sink(), _card(), repo="sm", report=REPORT, triage=triage)
    assert board.done == [
        ("select", "Work Type", "Bug"),
        ("number", "Points", 2.0),
        ("status", READY),
    ]
    assert issues.done[0][0] == "edit"


@pytest.mark.parametrize(
    ("outcome", "extra", "closed", "said"),
    [
        ("not_a_bug", {}, "not_planned", "Not a bug: "),
        ("duplicate", {"duplicate_of": 12}, "duplicate", "A duplicate of #12: "),
    ],
)
def test_not_a_bug_and_a_duplicate_are_closed_with_why(outcome, extra, closed, said):
    board, issues = Board(), Issues()
    bugs.carry_out(
        board, issues, Sink(), _card(), repo="sm", report=REPORT, triage=_triage(outcome, **extra)
    )
    assert issues.done[0][0] == "comment" and said in issues.done[0][1]
    assert issues.done[-1] == ("close", closed)
    assert board.done == []


def test_more_information_asks_and_waits_on_a_person():
    issues = Issues()
    triage = _triage("more_information", question="Which sprint name did you query?")
    bugs.carry_out(Board(), issues, Sink(), _card(), repo="sm", report=REPORT, triage=triage)
    assert "Which sprint name did you query?" in issues.done[0][1]
    assert issues.done[-1] == ("label", "needs:human")


def test_a_bug_from_someone_not_a_collaborator_is_left_for_a_person(monkeypatch):
    def never(*a: Any, **kw: Any) -> Triage:
        raise AssertionError("a non-collaborator's bug isn't triaged")

    monkeypatch.setattr(bugs, "triage_bug", never)
    issues = Issues(permission="read")
    done = bugs.triage_bugs(
        Board(), issues, Sink(), [_card()], default_repo="sm", context=None, failed=[]
    )
    assert done == [(593, "not a collaborator")]
    assert "only a collaborator files a bug" in issues.done[0][1]
    assert issues.done[-1] == ("label", "needs:human")


def test_triage_is_the_business_analysts_with_the_report_and_the_repository(monkeypatch):
    seen = capture(monkeypatch, answer=_triage("not_a_bug"))
    triage_bug("A title", REPORT, number=593, repository="the map")
    assert seen["role"] == "business_analyst" and seen["step"] == "bug_triage"
    assert seen["form"] is Triage
    assert "## The bug filed, #593: A title" in seen["description"]
    assert "the map" in seen["description"]
    assert bug_triage_crew.BUG_POINTS == (1, 2, 3, 5)
