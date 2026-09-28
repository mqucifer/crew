"""An epic covered only by planned stories waits for them; it isn't closed (#329).

sprint-metrics#254 (create CHANGELOG.md) was split into no stories and closed
as "already delivered by #216, #217". Both were open Ready stories whose epic's
rework was about to supersede them, so nothing would have built the file.
"""

from __future__ import annotations

from crew_org.crews.refinement_crew import AlreadyDelivered, StoryProposal
from crew_org.events import EventSink
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_SPLIT_MARKER, WAITS_FOR, tick
from crew_org.flows.delivered import Delivered, DeliveredStory
from tests.test_board_flow import FakeBoard, FakeIssues, epic_card
from tests.test_planned_elsewhere import story

EPIC = 254
PLANNED = 216


class Issues(FakeIssues):
    """With comments to read back and issues to close."""

    def __init__(self, earlier: list[str] | None = None) -> None:
        super().__init__()
        self.earlier = earlier or []
        self.closed: list[int] = []

    def comments(self, repo, number):
        return [{"body": b} for b in self.earlier] + [
            {"body": b} for n, b in self.posted if n == number
        ]

    def close(self, repo, number, *, reason="completed"):
        self.closed.append(number)
        return {}


def covered_by(number: int) -> StoryProposal:
    return StoryProposal(
        epic_title="Create CHANGELOG.md",
        stories=[],
        already_delivered=[
            AlreadyDelivered(title="CHANGELOG.md", by=number, why="it creates the file")
        ],
    )


def run(monkeypatch, issues, cards, *, delivered=(), proposal=None):
    splits: list[str] = []

    def split(title, context="", **kw):
        splits.append(title)
        return proposal or covered_by(PLANNED)

    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", split)
    monkeypatch.setattr(
        "crew_org.flows.board_flow.delivered",
        lambda issues, cards, repo: Delivered(
            stories=[DeliveredStory(n, f"Story {n}", ()) for n in delivered]
        ),
    )
    sink = EventSink(None)
    seen = []
    sink.subscribe(seen.append)
    tick(FakeBoard(cards), issues, sink, default_repo="sprint-metrics")
    return splits, [e.summary or "" for e in seen]


# --- at the split --------------------------------------------------------------------------


def test_covered_only_by_a_planned_story_it_waits_rather_than_closing(monkeypatch):
    issues = Issues()
    _, notes = run(monkeypatch, issues, [epic_card(EPIC), story(PLANNED, 180)])
    assert issues.closed == []
    [body] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert WAITS_FOR.format(numbers=PLANNED) in body
    assert "planned, not yet delivered" in body
    assert any(f"#{EPIC} waits for planned #{PLANNED}" in s for s in notes)


def test_covered_by_a_delivered_story_it_still_closes(monkeypatch):
    """#220 as it was: everything it asks for already exists."""
    issues = Issues()
    run(monkeypatch, issues, [epic_card(EPIC)], delivered=[150], proposal=covered_by(150))
    assert issues.closed == [EPIC]


# --- on a later pass -----------------------------------------------------------------------

SPLIT_BEFORE = f"{STORY_SPLIT_MARKER}\nsplit into 0 stories\n{WAITS_FOR.format(numbers=PLANNED)}"


def test_it_closes_once_what_it_waited_for_lands(monkeypatch):
    issues = Issues(earlier=[SPLIT_BEFORE])
    cards = [epic_card(EPIC), story(PLANNED, 180, status="Done", state="CLOSED")]
    splits, notes = run(monkeypatch, issues, cards, delivered=[PLANNED])
    assert issues.closed == [EPIC] and splits == []
    assert any(f"#{EPIC} closed: delivered by #{PLANNED}" in s for s in notes)


def test_it_is_split_again_when_what_it_waited_for_is_superseded(monkeypatch):
    """#180's rework closes #216 as not planned: it will never land."""
    issues = Issues(earlier=[SPLIT_BEFORE])
    cards = [epic_card(EPIC), story(PLANNED, 180, state="CLOSED")]
    splits, notes = run(monkeypatch, issues, cards, proposal=covered_by(PLANNED))
    assert (EPIC, NEEDS_REWORK) in issues.added_labels
    assert splits == ["Epic 254"], "split again in the same pass"
    assert any("closed without being delivered" in s for s in notes)


def test_it_waits_while_what_it_waits_for_is_still_open(monkeypatch):
    issues = Issues(earlier=[SPLIT_BEFORE])
    splits, _ = run(monkeypatch, issues, [epic_card(EPIC), story(PLANNED, 180)])
    assert splits == [] and issues.closed == []
