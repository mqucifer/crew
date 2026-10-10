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
from crew_org.flows.loops import (
    from_comments,
    loops_text,
    read_loops,
    read_resplits,
    sprint_window,
    superseded_text,
)
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
                "detail": {"answer": "stdout stays empty on errors", "story": 145},
            },
            resplit("2026-09-26T17:07:03", [145, 146]),
        ],
    )
    (chain,) = read_loops(events, *DAY)
    assert (chain.card, chain.epic, chain.reason) == (145, 59, "gate round trips")
    assert chain.with_ == [146], "a sibling sent back with it is not a second return"
    assert chain.decided == "stdout stays empty on errors"
    assert chain.superseded == [145, 146]


def test_an_answer_about_another_story_or_about_refinement_is_not_paired(tmp_path):
    """Sprint 20's retro filed an answer about refinement under sm#529, by time (crew#583)."""
    events = write_events(
        tmp_path,
        [
            back(529, "2026-09-26T16:54:22"),
            {
                "at": "2026-09-26T16:55:58",
                "kind": "product.answered",
                "card": 59,
                "detail": {"answer": "names are the design note's"},
            },
            {
                "at": "2026-09-26T16:56:58",
                "kind": "product.answered",
                "card": 59,
                "detail": {"answer": "about #530", "story": 530},
            },
        ],
    )
    (chain,) = read_loops(events, *DAY)
    assert chain.decided == ""


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
    owner = "mqucifer"

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
                ("2026-09-26T12:00:00Z", answered(145, "R3", "an older one")),
                (
                    "2026-09-26T16:54:29Z",
                    f"{STORY_PROBLEM_MARKER}\n**#145 went back to refinement: "
                    "the gates kept returning it.**",
                ),
                ("2026-09-26T16:55:30Z", answered(146, "R4", "about its sibling")),
                ("2026-09-26T16:55:58Z", answered(145, "R5", "stdout empty")),
            ]
        ),
        "sprint-metrics",
    )
    assert chains[0].reason == "the gates kept returning it"
    assert chains[0].decided == "R5 added: Output: stdout empty", (
        "the record's change for this story, after the return"
    )


def answered(story: int, row: str, decision: str) -> str:
    """The record-change comment a Product Owner's answer posts (crew#583, A3 and A6)."""
    from crew_org.flows import record

    return (
        f"{record.CHANGE.format(f'{row} answer')}\n**The epic's record changed**, by the "
        f"Product Owner, for mqucifer/sprint-metrics#{story}.\n\n"
        f"- **{row}** added: Output: {decision}\n\n{PRODUCT_ANSWER_MARKER}\n**Decided:** x"
    )


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


# --- crew#558: a superseded story is called out, not counted ------------------------------


def test_a_resplit_the_sponsor_asked_for_is_read_with_no_return_before_it(tmp_path):
    """sprint-metrics#467's re-split had no return before it, and the retro lost it."""
    event = resplit("2026-09-26T18:54:04", [75, 76])
    event["detail"]["because"] = "sponsor"
    events = write_events(tmp_path, [event])
    assert read_loops(events, *DAY) == []
    (found,) = read_resplits(events, *DAY)
    assert (found.epic, found.because, found.superseded) == (59, "sponsor", [75, 76])


def test_each_superseded_story_is_named_with_the_resplit_that_replaced_it(tmp_path):
    event = resplit("2026-09-26T18:54:04", [75, 76])
    event["detail"]["because"] = "sponsor"
    stories = [
        ("sprint-metrics", 75, "#75", 5),
        ("sprint-metrics", 76, "#76", 3),
        ("sprint-metrics", 80, "#80", 2),
    ]
    text = "\n".join(superseded_text(stories, read_resplits(write_events(tmp_path, [event]), *DAY)))
    assert (
        "- Epic sprint-metrics#59 was split again at the Sponsor's request at 2026-09-26 18:54Z: "
        "#75 (5), #76 (3). 8 points." in text
    )
    assert "- #80 (2): closed as not planned, with no re-split recorded." in text
    assert text.endswith("3 stories, 10 points, superseded.")


def test_a_resplit_in_another_repository_doesnt_claim_a_story_with_its_number(tmp_path):
    events = write_events(tmp_path, [resplit("2026-09-26T18:54:04", [75])])
    text = "\n".join(superseded_text([("crew", 75, "crew#75", 1)], read_resplits(events, *DAY)))
    assert "with no re-split recorded" in text


def test_a_sprint_with_nothing_superseded_has_no_section():
    assert superseded_text([], []) == []
