"""A Goal gains an epic without superseding the epics under way (crew#429).

Sprint 12: Goal sprint-metrics#174 said the service keeps the history it's given,
and no epic under it made that durable. The only way back to the Product Owner was
`needs:rework`, which supersedes every unstarted epic and refuses once any work has
started, so the Goal couldn't get the one epic it lacked. `needs:epic` asks for only
what's missing, beside the epics it has.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.columns import INBOX
from crew_org.crews.refinement_crew import EpicProposal, MissingEpics
from crew_org.events import EventSink
from crew_org.flows import board_flow
from crew_org.flows.board_flow import NEEDS_EPIC, NEEDS_REWORK, lacking_goals, tick
from tests.test_board_flow import PROPOSAL, FakeBoard, ReworkIssues, card, marked, sponsor

DURABLE = MissingEpics(
    epics=[PROPOSAL.epics[0].model_copy(update={"title": "Keep the history across restarts"})],
    ordering_rationale="The one thing the Goal still lacks.",
)


class Lacking(ReworkIssues):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ensured: list[str] = []

    def ensure_label(self, repo, name, *, color, description):
        self.ensured.append(name)


def asking(number: int, status: str = INBOX):
    return card(number, status=status).model_copy(update={"labels": frozenset({NEEDS_EPIC})})


def run(board, issues, monkeypatch):
    calls: dict[str, list] = {"missing": [], "whole": []}

    def missing(goal, **kw):
        calls["missing"].append(kw)
        return DURABLE

    def whole(goal, **kw):
        calls["whole"].append(kw)
        return PROPOSAL

    monkeypatch.setattr(board_flow, "propose_missing_epics", missing)
    monkeypatch.setattr(board_flow, "propose_epics", whole)
    result = tick(
        board, issues, EventSink(None), default_repo="sprint-metrics", sponsor="mquarters"
    )
    return result, calls


def test_a_goal_with_work_under_way_gets_only_the_epic_it_lacks(monkeypatch):
    issues = Lacking(
        comments=[
            marked(board_flow.EPIC_PROPOSAL_MARKER),
            sponsor("It never makes history durable"),
        ],
        children=[7, 8],
        branches=["feat/7-report-a-range"],
    )
    board = FakeBoard([asking(1), card(7, status="In Progress"), card(8, status=INBOX)])

    result, calls = run(board, issues, monkeypatch)

    assert result.proposed == [1], "work in flight doesn't stop it, as it stops a rework"
    assert issues.closed == [], "nothing superseded"
    assert calls["whole"] == [] and len(calls["missing"]) == 1
    told = calls["missing"][0]["lacking"]
    assert "It never makes history durable" in told
    assert "#7" in told and "#8" in told, "shown the epics it already has"
    assert (1, NEEDS_EPIC) in issues.unlabelled, "the label is spent"


def test_once_spent_the_goal_is_answered_and_left_alone(monkeypatch):
    issues = Lacking(comments=[marked(board_flow.EPIC_PROPOSAL_MARKER)], children=[7])
    result, calls = run(FakeBoard([card(1)]), issues, monkeypatch)

    assert result.proposed == [] and calls["missing"] == []
    assert result.skipped == [(1, "already answered")]


def test_a_goal_off_the_inbox_is_still_asked(monkeypatch):
    issues = Lacking(comments=[marked(board_flow.EPIC_PROPOSAL_MARKER)], children=[7])
    result, _ = run(FakeBoard([asking(1, status="Needs Refinement")]), issues, monkeypatch)
    assert result.proposed == [1]


def test_the_label_exists_for_the_sponsor_to_use(monkeypatch):
    issues = Lacking(comments=[marked(board_flow.EPIC_PROPOSAL_MARKER)])
    run(FakeBoard([card(1)]), issues, monkeypatch)
    assert issues.ensured == [NEEDS_EPIC]


def test_a_goal_sent_back_for_rework_is_reworked_not_extended():
    both = card(1).model_copy(update={"labels": frozenset({NEEDS_EPIC, NEEDS_REWORK})})
    assert lacking_goals([both]) == []
    assert [c.number for c in lacking_goals([asking(1)])] == [1]
    assert lacking_goals([asking(2)], sponsor="someone-else") == [], "only the Sponsor's Goals"


def test_missing_epics_may_be_one_but_a_decomposition_may_not():
    MissingEpics(epics=DURABLE.epics, ordering_rationale="one")
    with pytest.raises(ValidationError, match="at least one"):
        MissingEpics(epics=[], ordering_rationale="none")
    with pytest.raises(ValidationError, match="at least 2 epics"):
        EpicProposal(epics=DURABLE.epics, ordering_rationale="one")
