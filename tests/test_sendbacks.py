"""Every send-back records one `work.sent_back` (crew#862).

The done-when: each send-back, run once through its own code path, leaves its event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crew_org import sendbacks, telemetry
from crew_org.crews.criteria_crew import CriteriaCheck
from crew_org.crews.refinement_crew import CriteriaRepair, RepairedStory
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import board_flow
from tests import test_criteria_check as criteria
from tests.test_board_flow import FakeBoard, FakeIssues, epic_card


def sent_back(sink: EventSink) -> list[CrewEvent]:
    return [e for e in sink.replay() if e.kind == EventKind.WORK_SENT_BACK]


def collecting() -> tuple[EventSink, list[CrewEvent]]:
    """A sink with no log, and the send-backs it saw."""
    sink, seen = EventSink(None), []
    sink.subscribe(lambda e: seen.append(e) if e.kind == EventKind.WORK_SENT_BACK else None)
    return sink, seen


def which(seen: list[CrewEvent]) -> list[tuple[Any, ...]]:
    return [(e.card, e.detail["sent_from"], e.detail["sent_to"], e.detail["rule"]) for e in seen]


def test_a_send_back_names_its_steps_rule_round_and_counts(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events" / "tick.jsonl")

    sendbacks.record(
        sink, sendbacks.QA_UNPROVEN, card=12, repo="o/r", role="QA Engineer", round_=2, unproven=3
    )

    (event,) = sent_back(sink)
    assert event.card == 12
    assert event.role == "QA Engineer"
    assert event.detail == {
        "repo": "o/r",
        "sent_from": "qa",
        "sent_to": "build",
        "rule": "gate.qa_unproven",
        "round": 2,
        "counts": {"unproven": 3},
    }


def test_a_send_back_leaves_for_telemetry_as_names_and_numbers(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events" / "tick.jsonl")

    sendbacks.record(sink, sendbacks.MERGE_CONFLICT, card=7, repo="o/r", round_=1)

    out = telemetry.view(sent_back(sink)[0])
    assert {k: out[k] for k in ("sent_from", "sent_to", "rule", "round")} == {
        "sent_from": "merge",
        "sent_to": "build",
        "rule": "gate.merge_conflict",
        "round": 1,
    }
    assert "summary" not in out


def test_each_send_back_is_declared_once_and_goes_to_its_own_step_or_an_earlier_one() -> None:
    order = list(sendbacks.Step)
    # The steps outside the line of the flow: the Sponsor, refinement's own checks
    # across an epic, and the after-merge checks.
    outside = {sendbacks.Step.SPONSOR, sendbacks.Step.MAIN, sendbacks.Step.RELEASE_CHECK}
    outside |= {sendbacks.Step.REVERT, sendbacks.Step.REVISIT, sendbacks.Step.REFINEMENT}
    seen = set()
    for back in sendbacks.CATALOG:
        assert (back.sent_from, back.rule) not in seen
        seen.add((back.sent_from, back.rule))
        if back.sent_from not in outside and back.sent_to != sendbacks.Step.DESIGN:
            assert order.index(back.sent_to) <= order.index(back.sent_from), back


# --- the criteria check (R5 to R7) ---


def split_checked(monkeypatch: pytest.MonkeyPatch, checker: Any) -> list[CrewEvent]:
    criteria.splitting(monkeypatch, checker)
    monkeypatch.setattr(board_flow, "propose_epics", lambda g, **kw: None)
    sink, seen = collecting()
    board_flow.tick(FakeBoard([epic_card(3)]), FakeIssues(), sink, default_repo="sprint-metrics")
    return seen


def test_the_criteria_checks_repair_is_a_send_back_to_the_split(monkeypatch):
    repaired = RepairedStory(title="Cycle time", acceptance_criteria=criteria.FIXED)
    monkeypatch.setattr(
        board_flow, "repair_criteria", criteria.Repairer(CriteriaRepair(stories=[repaired]))
    )

    seen = split_checked(monkeypatch, criteria.Checker(criteria.ON_CYCLE_TIME, CriteriaCheck()))

    assert which(seen) == [(3, "criteria_check", "split", "gate.criteria_repair")]
    assert seen[0].detail["counts"] == {"conflicts": 1, "stories": 1}


def test_the_criteria_checks_whole_re_split_is_a_send_back_to_the_split(monkeypatch):
    seen = split_checked(monkeypatch, criteria.Checker(criteria.CONFLICT, CriteriaCheck()))

    assert which(seen) == [(3, "criteria_check", "split", "gate.criteria_resplit")]


def test_criteria_that_still_conflict_are_sent_to_the_product_owner(monkeypatch):
    seen = split_checked(monkeypatch, criteria.Checker(criteria.CONFLICT, criteria.CONFLICT))

    assert which(seen) == [
        (3, "criteria_check", "split", "gate.criteria_resplit"),
        (3, "criteria_check", "story_problem", "gate.criteria_cannot_all_pass"),
    ]


# --- refinement's other send-backs (R1 to R4) ---


def test_a_goal_the_sponsor_sends_back_is_a_send_back_to_decompose(monkeypatch):
    from tests import test_board_flow as goals

    issues = goals.ReworkIssues(
        comments=[goals.marked(board_flow.EPIC_PROPOSAL_MARKER), goals.sponsor("try again")],
        children=[7],
    )
    board = FakeBoard([goals.reworked(1), goals.card(7, status="Inbox")])
    _, seen = goals.run(board, issues, monkeypatch)

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(1, "sponsor", "decompose", "decision.board_flow.rework_gate")]
    assert sent[0].detail["counts"] == {"superseded": 1}


def test_a_goal_the_sponsor_says_lacks_an_epic_is_a_send_back_to_decompose(monkeypatch):
    from tests import test_board_flow as goals
    from tests import test_goal_lacks_an_epic as lacks

    issues = lacks.Lacking(
        comments=[goals.marked(board_flow.EPIC_PROPOSAL_MARKER), goals.sponsor("not durable")],
        children=[7],
    )
    monkeypatch.setattr(board_flow, "propose_missing_epics", lambda goal, **kw: lacks.DURABLE)
    sink, seen = collecting()
    board_flow.tick(
        FakeBoard([lacks.asking(1), goals.card(7, status="Inbox")]),
        issues,
        sink,
        default_repo="sprint-metrics",
        sponsor="mquarters",
    )

    assert which(seen) == [(1, "sponsor", "decompose", "decision.board_flow.lacking_goals")]
    assert seen[0].detail["counts"] == {"epics": 1}


def test_an_epic_the_sponsor_sends_back_is_a_send_back_to_the_split(monkeypatch):
    from tests import test_resplit_flow as epics

    issues = epics.Issues([epics.SPLIT, "Split it smaller."])
    monkeypatch.setattr("crew_org.flows.board_flow.from_sponsor", lambda issues, c: True)
    _, events = epics.run(monkeypatch, issues, [epics.sent_back(), *epics.stories(*epics.FOUR)])

    sent = [e for e in events if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(epics.EPIC, "sponsor", "split", "sent_back.epic_rework")]
    assert sent[0].detail["counts"] == {"replaced": 4}


def test_a_story_problems_re_split_is_not_the_sponsors_send_back(monkeypatch):
    """The story's own send-back was recorded where it was sent back."""
    from tests import test_resplit_flow as epics

    issues = epics.Issues([epics.SPLIT, epics.PROBLEM, epics.ANSWER])
    _, events = epics.run(monkeypatch, issues, [epics.sent_back(), *epics.stories(*epics.FOUR)])

    assert not [e for e in events if e.kind == EventKind.WORK_SENT_BACK]
    [resplit] = [e for e in events if e.kind == EventKind.EPIC_RESPLIT]
    assert resplit.detail["round"] == 1


def test_an_epic_whose_wait_breaks_is_a_send_back_to_the_split_and_not_the_sponsors(
    monkeypatch,
):
    from tests import test_waits_for_planned as waits

    issues = waits.Issues(earlier=[waits.SPLIT_BEFORE])
    cards = [epic_card(waits.EPIC), waits.story(waits.PLANNED, 180, state="CLOSED")]
    monkeypatch.setattr(
        "crew_org.flows.board_flow.split_epic",
        lambda title, context="", **kw: waits.covered_by(waits.PLANNED),
    )
    sink, seen = collecting()
    board_flow.tick(FakeBoard(cards), issues, sink, default_repo="sprint-metrics")

    assert which(seen) == [(waits.EPIC, "refinement", "split", "skip.board_flow.waits_for_planned")]
    assert seen[0].detail["counts"] == {"closed": 1}
