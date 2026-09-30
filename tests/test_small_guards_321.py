"""Four small guards from Sprint 8 (#321)."""

from __future__ import annotations

from crew_org.events import EventKind, EventSink
from crew_org.flows import delivery
from crew_org.flows.board_flow import tick
from crew_org.flows.delivery import INTERRUPTED_MARKER, DeliveryResult, _work_one_card
from crew_org.flows.design_notes import needs_note as needs_design_note
from crew_org.flows.presentation_notes import needs_note as needs_presentation_note
from crew_org.tools.github_project import Card
from tests.test_board_flow import SPLIT, FakeBoard, FakeIssues, epic_card
from tests.test_remote_errors import CARD, Board, Issues, Workspace, _unreadable_reply


def epic(*labels):
    return Card(
        item_id="E",
        number=64,
        title="Surface anomalies",
        status="Ready",
        state="OPEN",
        work_type="Epic",
        repo="sprint-metrics",
        labels=frozenset(labels),
    )


def test_an_epic_waiting_for_a_person_gets_no_note_attempt():
    assert needs_design_note(epic("needs:design"))
    assert not needs_design_note(epic("needs:design", "needs:human"))
    assert needs_presentation_note(epic("needs:ux"))
    assert not needs_presentation_note(epic("needs:ux", "needs:human", "blocked"))


def test_no_stories_are_created_under_an_epic_that_closed_mid_split(monkeypatch):
    issues = FakeIssues()
    issues.get = lambda repo, n: {"body": "goal body", "state": "closed"}
    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", lambda *a, **k: SPLIT)
    tick(FakeBoard([epic_card(3)]), issues, EventSink(None), default_repo="sprint-metrics")
    assert issues.created == []


def test_a_blocked_card_records_one_block_event(monkeypatch):
    monkeypatch.setattr(delivery, "deliver_story", lambda *a, **k: _unreadable_reply())
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    issues = Issues(earlier=[INTERRUPTED_MARKER, INTERRUPTED_MARKER])
    _work_one_card(
        CARD,
        board=Board(),
        issues=issues,
        sink=sink,
        ws=Workspace(),
        policy=None,
        ledger=None,
        result=DeliveryResult(),
        counts={"In Progress": 1},
        sprint="Sprint 11",
        repo="sprint-metrics",
        default_branch="main",
    )
    assert [e.kind for e in seen].count(EventKind.CARD_BLOCKED) == 1
