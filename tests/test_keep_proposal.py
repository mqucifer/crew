"""What the Developer proposed is kept, applied or refused (#127, #129 were guessed at)."""

from __future__ import annotations

import json
from pathlib import Path

from crew_org.crews.delivery_crew import FileEdit, Implementation
from crew_org.events import EventSink
from crew_org.flows.delivery import _keep_proposal


def test_a_proposal_is_saved_beside_the_event_log(tmp_path: Path):
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    proposal = Implementation(
        summary="Move card code",
        edits=[FileEdit(path="a.py", operation="delete", target="Card")],
    )
    _keep_proposal(sink, "sprint-metrics", 129, proposal)
    (saved,) = (tmp_path / "proposals" / "sprint-metrics" / "129").iterdir()
    assert json.loads(saved.read_text())["edits"][0]["target"] == "Card"
    assert any("proposal kept" in e.summary for e in sink.replay())


def test_without_a_log_nothing_is_written(tmp_path: Path):
    proposal = Implementation(
        summary="s", edits=[FileEdit(path="a.py", operation="delete", target="Card")]
    )
    _keep_proposal(EventSink(), "r", 1, proposal)
    assert not (tmp_path / "proposals").exists()


def test_a_refusal_says_nothing_was_applied():
    """#132's third attempt sent only the new piece, taking the rest as applied."""
    from crew_org.flows.delivery import NOT_APPLIED

    assert "Nothing from this attempt was applied" in NOT_APPLIED
    assert "Send the whole change again" in NOT_APPLIED


def test_answers_the_form_refused_are_kept_too(tmp_path: Path):
    """crew-presentation#59's third attempt blocked the story, and what it sent was
    lost: a proposal is kept only once it parses (crew#690)."""
    from crew_org.calls import Refused
    from crew_org.flows.delivery import _keep_refused

    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    refused = Refused("why", 2, answers=("Refused: one\n\n{}", "Refused: two\n\n{...}"))
    _keep_refused(sink, "crew-presentation", 59, refused.answers)

    saved = sorted(p.name for p in (tmp_path / "proposals" / "crew-presentation" / "59").iterdir())
    assert [name.rsplit(".", 2)[1:] for name in saved] == [
        ["refused-1", "txt"],
        ["refused-2", "txt"],
    ]
    assert any("2 refused answers kept" in e.summary for e in sink.replay())
    _keep_refused(sink, "crew-presentation", 60, ())
    assert not (tmp_path / "proposals" / "crew-presentation" / "60").exists()
