"""Every send-back records one `work.sent_back` (crew#862).

The done-when: each send-back, run once through its own code path, leaves its event.
"""

from __future__ import annotations

from pathlib import Path

from crew_org import sendbacks, telemetry
from crew_org.events import CrewEvent, EventKind, EventSink


def sent_back(sink: EventSink) -> list[CrewEvent]:
    return [e for e in sink.replay() if e.kind == EventKind.WORK_SENT_BACK]


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
