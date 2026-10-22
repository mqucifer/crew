"""Four small guards from Sprint 8 (#321)."""

from __future__ import annotations

from crew_org.events import EventKind, EventSink
from crew_org.flows import session_delivery
from crew_org.flows.board_flow import tick
from crew_org.flows.delivery import INTERRUPTED_MARKER, DeliveryResult, _work_one_card
from tests.test_board_flow import SPLIT, FakeBoard, FakeIssues, epic_card
from tests.test_remote_errors import CARD, Board, Issues, Workspace, _unreadable_reply


def test_no_stories_are_created_under_an_epic_that_closed_mid_split(monkeypatch):
    issues = FakeIssues()
    issues.get = lambda repo, n: {"body": "goal body", "state": "closed"}
    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", lambda *a, **k: SPLIT)
    tick(FakeBoard([epic_card(3)]), issues, EventSink(None), default_repo="sprint-metrics")
    assert issues.created == []


def test_a_blocked_card_records_one_block_event(monkeypatch):
    monkeypatch.setattr(session_delivery, "deliver_by_session", lambda *a, **k: _unreadable_reply())
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    issues = Issues(earlier=[INTERRUPTED_MARKER, INTERRUPTED_MARKER])
    _work_one_card(
        CARD,
        board=Board(),
        issues=issues,
        sink=sink,
        ws=Workspace(),
        result=DeliveryResult(),
        counts={"In Progress": 1},
        sprint="Sprint 11",
        repo="sprint-metrics",
        default_branch="main",
    )
    assert [e.kind for e in seen].count(EventKind.CARD_BLOCKED) == 1
