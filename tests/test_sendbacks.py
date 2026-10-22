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
