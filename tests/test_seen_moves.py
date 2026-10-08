"""Moves the crew didn't make are recorded when a pass sees them (crew#521).

GitHub's issue timelines stopped recording status changes, so the Sponsor's
approvals were in no record. The cases are the real ones: the Sponsor moving
sprint-metrics epics to Needs Refinement on 2026-10-08, the crew's own moves
during a pass, and the board workflow's sweep.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from crew_org.columns import DONE, INBOX, NEEDS_REFINEMENT, QAING, REVIEWING
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import loop
from crew_org.flows.board_moves import RunWindow
from crew_org.flows.moves import move_card
from crew_org.flows.seen_moves import SEEN_FILE, load_seen, save_seen, seen_moves
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card

# The Sponsor's move of sprint-metrics#464, as the board dated it.
APPROVED = datetime(2026, 10, 8, 11, 24, 17, tzinfo=UTC)


def card(number, status, when, repo="sprint-metrics") -> Card:
    return Card(item_id=f"I{number}", number=number, repo=repo, status=status, status_set=when)


def seen(*cards: Card) -> dict:
    return {f"{c.repo}#{c.number}": {"status": c.status} for c in cards}


def crew_move(number, to, when, repo=None) -> CrewEvent:
    detail = {"from": None, "to": to, **({"repo": repo} if repo else {})}
    return CrewEvent(at=when, kind=EventKind.CARD_MOVED, card=number, detail=detail)


def no_runs(since):
    return []


def test_a_card_set_since_the_last_pass_by_no_crew_move_is_a_persons():
    before = card(464, INBOX, APPROVED - timedelta(hours=11))
    now = card(464, NEEDS_REFINEMENT, APPROVED)

    [move] = seen_moves([now], seen(before), [], no_runs)

    assert move.kind is EventKind.CARD_SEEN_MOVED
    assert move.at == APPROVED
    assert move.card == 464
    assert move.detail == {
        "from": INBOX,
        "to": NEEDS_REFINEMENT,
        "repo": "sprint-metrics",
        "by": "person",
    }


def test_the_crews_own_move_is_not_recorded_again():
    now = card(455, QAING, APPROVED)
    logged = [crew_move(455, QAING, APPROVED + timedelta(seconds=2), repo="sprint-metrics")]

    assert seen_moves([now], seen(card(455, REVIEWING, None)), logged, no_runs) == []


def test_a_crew_move_logged_before_moves_named_a_repository_still_counts():
    now = card(455, QAING, APPROVED)
    logged = [crew_move(455, QAING, APPROVED + timedelta(seconds=2))]

    assert seen_moves([now], seen(card(455, REVIEWING, None)), logged, no_runs) == []


def test_the_same_number_moved_in_another_repository_isnt_this_cards_move():
    """crew-presentation and sprint-metrics both have a card 15."""
    now = card(15, QAING, APPROVED)
    logged = [crew_move(15, QAING, APPROVED, repo="crew-presentation")]

    [move] = seen_moves([now], seen(card(15, REVIEWING, None)), logged, no_runs)

    assert move.detail["by"] == "person"


def test_a_crew_move_long_before_the_status_was_set_doesnt_cover_it():
    now = card(464, NEEDS_REFINEMENT, APPROVED)
    logged = [crew_move(464, NEEDS_REFINEMENT, APPROVED - timedelta(hours=1))]

    [move] = seen_moves([now], seen(card(464, INBOX, None)), logged, no_runs)

    assert move.detail["by"] == "person"


def test_a_move_while_the_board_workflow_ran_is_the_platforms():
    now = card(453, DONE, APPROVED)
    run = RunWindow(repo="crew", started=APPROVED - timedelta(seconds=30), finished=APPROVED)

    [move] = seen_moves([now], seen(card(453, QAING, None)), [], lambda since: [run])

    assert move.detail["by"] == "platform"


def test_the_workflows_runs_are_asked_only_from_the_earliest_move():
    asked = []
    early = card(464, NEEDS_REFINEMENT, APPROVED)
    late = card(467, NEEDS_REFINEMENT, APPROVED + timedelta(minutes=5))

    seen_moves(
        [late, early],
        seen(card(464, INBOX, None), card(467, INBOX, None)),
        [],
        lambda since: asked.append(since) or [],
    )

    assert len(asked) == 1
    assert asked[0] <= APPROVED


def test_with_nothing_to_attribute_the_workflows_runs_arent_asked():
    def never(since):
        raise AssertionError("asked for runs with nothing to attribute")

    same = card(464, INBOX, APPROVED)

    assert seen_moves([same], seen(same), [], never) == []


def test_a_card_moved_and_moved_back_reads_as_no_move():
    assert seen_moves([card(464, INBOX, APPROVED)], seen(card(464, INBOX, None)), [], no_runs) == []


def test_a_card_new_since_the_last_pass_came_from_nowhere():
    """The Sponsor filing a Goal: the board adds it, and they set its Status."""
    [move] = seen_moves([card(461, INBOX, APPROVED)], {}, [], no_runs)

    assert move.detail["from"] is None
    assert move.detail["by"] == "person"


def test_a_card_with_no_status_yet_isnt_a_move():
    """The board's auto-add workflows add an item without setting its Status."""
    added = Card(item_id="I9", number=9, repo="sprint-metrics", status=None, status_set=None)

    assert seen_moves([added], {}, [], no_runs) == []


def test_the_view_is_saved_and_read_back(tmp_path):
    path = tmp_path / SEEN_FILE
    assert load_seen(path) is None

    save_seen(path, [card(464, NEEDS_REFINEMENT, APPROVED)])

    assert load_seen(path) == {
        "sprint-metrics#464": {"status": NEEDS_REFINEMENT, "set": APPROVED.isoformat()}
    }


# --- in the tick --------------------------------------------------------------


class Crew:
    """What `_record_seen_moves` reads from the crew."""

    def __init__(self, tmp_path, issues):
        self.sink = EventSink(tmp_path / "tick.jsonl")
        self.repos = {"sprint-metrics"}
        self.crew_repo = "crew"
        self.issues = issues


def issues_with_runs(runs, asked):
    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path, request.url.params.get("created")))
        return httpx.Response(200, json={"workflow_runs": runs})

    return IssueClient(
        "tok", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def recorded(crew) -> list[CrewEvent]:
    path = crew.sink.path
    return (
        [
            CrewEvent.model_validate_json(line)
            for line in path.read_text().splitlines()
            if '"card.seen_moved"' in line
        ]
        if path.exists()
        else []
    )


def test_the_first_pass_has_nothing_to_compare_and_saves_its_view(tmp_path):
    asked = []
    crew = Crew(tmp_path, issues_with_runs([], asked))

    loop._record_seen_moves(crew, [card(464, INBOX, APPROVED)])

    assert recorded(crew) == []
    assert asked == []
    assert load_seen(tmp_path / SEEN_FILE) is not None


def test_the_next_pass_records_the_sponsors_move(tmp_path):
    asked = []
    crew = Crew(tmp_path, issues_with_runs([], asked))
    loop._record_seen_moves(crew, [card(464, INBOX, APPROVED - timedelta(hours=11))])

    loop._record_seen_moves(crew, [card(464, NEEDS_REFINEMENT, APPROVED)])

    [move] = recorded(crew)
    assert move.detail["by"] == "person"
    assert move.at == APPROVED
    # The board workflow's runs in both repositories, from just before the move.
    assert sorted(path for path, _ in asked) == [
        "/repos/mqucifer/crew/actions/workflows/board.yml/runs",
        "/repos/mqucifer/sprint-metrics/actions/workflows/board.yml/runs",
    ]
    assert all(created and created.startswith(">=2026-10-08T11:2") for _, created in asked)


def test_a_failure_leaves_the_view_unsaved_so_the_next_pass_catches_up(tmp_path):
    def handler(request):
        return httpx.Response(500)

    issues = IssueClient(
        "tok", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    crew = Crew(tmp_path, issues)
    loop._record_seen_moves(crew, [card(464, INBOX, APPROVED - timedelta(hours=11))])

    with pytest.raises(httpx.HTTPStatusError):
        loop._record_seen_moves(crew, [card(464, NEEDS_REFINEMENT, APPROVED)])

    assert load_seen(tmp_path / SEEN_FILE) == {
        "sprint-metrics#464": {
            "status": INBOX,
            "set": (APPROVED - timedelta(hours=11)).isoformat(),
        }
    }


# --- the crew's own moves name their repository ---------------------------------


class Board:
    def repo_of(self, item_id):
        return {"I455": "sprint-metrics"}.get(item_id)

    def set_status(self, item_id, column):
        pass

    def set_owner_agent(self, item_id, role):
        pass


def test_a_crew_move_names_its_cards_repository():
    sink, out = EventSink(None), []
    sink.subscribe(out.append)

    move_card(Board(), sink, item_id="I455", to=QAING, by="Code Reviewer", card=455, frm=REVIEWING)

    assert out[0].detail == {"from": REVIEWING, "to": QAING, "repo": "sprint-metrics"}


def test_a_crew_move_on_a_card_not_read_yet_names_no_repository():
    sink, out = EventSink(None), []
    sink.subscribe(out.append)

    move_card(Board(), sink, item_id="I1", to=QAING, by="Code Reviewer", card=1, frm=REVIEWING)

    assert "repo" not in out[0].detail
