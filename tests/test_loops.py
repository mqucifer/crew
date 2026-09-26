"""Loops the crew broke, in the retro (#253).

Sprint 7's retro preview listed sprint-metrics#145 only as the story two others
waited on: it was delivered six times, went back to refinement, the Product
Owner decided its question and its epic was split again, and sending it back
had cleared its Sprint field. These tests pin that the event log brings it
back: the return, the decision, the re-split and the points it cost.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from crew_org.crews.retro_crew import Retro
from crew_org.flows.board_flow import PRODUCT_ANSWER_MARKER, STORY_PROBLEM_MARKER
from crew_org.flows.loops import from_comments, loops_text, read_loops, sprint_window
from crew_org.flows.retro import RetroLayout, _retro_body

DAY = ("2026-09-26T05:00:00", "2026-09-27T05:00:00")


def write_events(tmp_path: Path, events: list[dict]) -> Path:
    (tmp_path / "tick.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return tmp_path


def back(card: int, at: str, epic: int = 59) -> dict:
    return {
        "at": at,
        "kind": "card.moved",
        "role": "Developer",
        "card": card,
        "summary": f"back to refinement with epic #{epic}",
        "detail": {"from": "In Progress", "to": "Ready"},
    }


def resplit(at: str, superseded: list[int], epic: int = 59) -> dict:
    return {
        "at": at,
        "kind": "epic.resplit",
        "card": epic,
        "summary": f"epic #{epic} split again after a story problem",
        "detail": {"repo": "sprint-metrics", "superseded": superseded},
    }


def test_a_sprint_is_its_days_on_the_sprint_clock():
    assert sprint_window(date(2026, 9, 26), date(2026, 9, 26), "America/Chicago") == DAY


def test_a_return_and_what_followed_it_are_one_chain(tmp_path):
    events = write_events(
        tmp_path,
        [
            back(145, "2026-09-26T16:54:22"),
            back(146, "2026-09-26T16:54:23"),
            {
                "at": "2026-09-26T16:54:30",
                "kind": "story.returned",
                "card": 145,
                "detail": {"epic": 59, "reason": "gate round trips", "with": [146]},
            },
            {
                "at": "2026-09-26T16:55:58",
                "kind": "product.answered",
                "card": 59,
                "detail": {"answer": "stdout stays empty on errors"},
            },
            resplit("2026-09-26T17:07:03", [145, 146]),
        ],
    )
    (chain,) = read_loops(events, *DAY)
    assert (chain.card, chain.epic, chain.reason) == (145, 59, "gate round trips")
    assert chain.with_ == [146], "a sibling sent back with it is not a second return"
    assert chain.decided == "stdout stays empty on errors"
    assert chain.superseded == [145, 146]


def test_a_return_outside_the_sprint_is_not_this_sprints(tmp_path):
    events = write_events(tmp_path, [back(145, "2026-09-26T04:59:59")])
    assert read_loops(events, *DAY) == []


def test_a_return_only_the_moves_recorded_takes_the_first_card_moved(tmp_path):
    # Before #246, a return was only its moves.
    events = write_events(
        tmp_path, [back(145, "2026-09-26T16:54:22"), back(147, "2026-09-26T16:54:24")]
    )
    (chain,) = read_loops(events, *DAY)
    assert (chain.card, chain.with_, chain.reason) == (145, [147], "")


class Comments:
    def __init__(self, bodies: list[tuple[str, str]]):
        self.bodies = bodies

    def comments(self, repo, number):
        return [{"created_at": at, "body": body} for at, body in self.bodies]


def test_what_only_the_epic_says_is_read_from_its_comments(tmp_path):
    events = write_events(tmp_path, [back(145, "2026-09-26T16:54:22")])
    chains = read_loops(events, *DAY)
    from_comments(
        chains,
        Comments(
            [
                ("2026-09-26T12:00:00Z", f"{PRODUCT_ANSWER_MARKER}\n**Decided:** an older one"),
                (
                    "2026-09-26T16:54:29Z",
                    f"{STORY_PROBLEM_MARKER}\n**#145 went back to refinement: "
                    "the gates kept returning it.**",
                ),
                ("2026-09-26T16:55:58Z", f"{PRODUCT_ANSWER_MARKER}\n**Decided:** stdout empty"),
            ]
        ),
        "sprint-metrics",
    )
    assert chains[0].reason == "the gates kept returning it"
    assert chains[0].decided == "stdout empty", "the decision after the return, not before"


def test_the_retro_shows_each_chain_and_the_points_that_went_back(tmp_path):
    events = write_events(
        tmp_path,
        [
            back(145, "2026-09-26T16:54:22"),
            back(146, "2026-09-26T16:54:23"),
            resplit("2026-09-26T17:07:03", [145, 146]),
        ],
    )
    lines = loops_text(read_loops(events, *DAY), {145: 3, 146: 3})
    text = "\n".join(lines)
    assert "- #145 went back to refinement (why wasn't recorded), taking #146 with it." in text
    assert "No Product Owner decision recorded." in text
    assert "Epic #59 was split again; it superseded #145, #146." in text
    assert "3 points went into stories that went back, not delivered: #145 (3)." in text

    body = _retro_body(
        Retro(summary="s", went=[], needs_you=[], defects=[]),
        "Sprint 7",
        [],
        None,
        "crew",
        RetroLayout(loops=lines),
    )
    assert "## Loops the crew broke" in body


def test_a_sprint_with_no_returns_has_no_loop_section():
    assert loops_text([], {}) == []
    body = _retro_body(
        Retro(summary="s", went=[], needs_you=[], defects=[]),
        "Sprint 7",
        [],
        None,
        "crew",
        RetroLayout(),
    )
    assert "Loops" not in body
