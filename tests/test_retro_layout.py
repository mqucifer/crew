"""The retro reads at a glance (#176), and knows what the sprint fixed (#174).

Sprint 6's retro (#173) was one ~450-word paragraph, its defect titles were
`subject: problem` cut at 120 characters, and it re-filed two problems fixed
during the sprint (#170, #171) because it was only shown open issues.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.retro_crew import MAX_TITLE, MAX_WENT, ProcessDefect, Retro
from crew_org.flows.retro import RetroLayout, _retro_body, fixed_this_sprint
from tests.test_retro import CREW, SPRINT, FakeIssues, record

LAYOUT = RetroLayout(
    stories=[("#73", 5, "Done", "Report a range in the table"), ("#74", 3, "Done", "WIP limits")],
    retries="1 of 2 stories landed on their first attempt.\n- SCHEMA: no test (once, on #73)",
    blocked=[("#40", 4), ("#41", None)],
    awaiting=[f"#{n}" for n in range(51, 66)],
)


def body(retro=None, layout=LAYOUT, lines=()) -> str:
    return _retro_body(
        retro or Retro(summary="Delivered epic #50.", went=["#73 was rebuilt from main."]),
        SPRINT,
        list(lines),
        156,
        CREW,
        layout,
    )


# --- #176: sections a Sponsor can scan -------------------------------------------------------


def test_the_retro_has_its_sections_in_order():
    text = body()
    order = [
        "## Delivered",
        "## How it went",
        "## Why work didn't land first time",
        "## Needs you",
        "## Defects filed",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)


def test_delivered_is_a_table_the_crew_builds_from_the_board():
    text = body()
    assert "| #73 | 5 | Done | Report a range in the table |" in text
    assert "2 stories, 8 points." in text


def test_the_first_try_causes_are_a_list():
    text = body()
    assert "1 of 2 stories landed on their first attempt.\n\n- SCHEMA: no test" in text


def test_needs_you_names_blocked_cards_and_summarises_the_queue():
    text = body()
    assert "- #40: blocked 4 days — past the threshold" in text
    assert "- #41: blocked for an unknown time — past the threshold" in text
    assert "15 of them, #51 to #65" in text
    assert "#58" not in text, "a long queue is a range, not every reference"


def test_nothing_needing_the_sponsor_says_so_and_empty_sections_are_left_out():
    text = body(Retro(summary="s"), RetroLayout())
    assert "## Needs you\n\nNothing." in text
    assert "## How it went" not in text and "## Why work didn't land" not in text
    assert "## Defects filed\n\nNone proposed." in text


def test_how_it_went_is_a_few_bullets_not_a_replay():
    with pytest.raises(ValidationError, match=f"at most {MAX_WENT} bullets"):
        Retro(summary="s", went=[f"tick {n}" for n in range(MAX_WENT + 1)])


# --- #176: a defect's title is a short statement of the problem ---------------------------------


def defect(**overrides) -> ProcessDefect:
    params = dict(
        subject="#73 and the same-epic stories #74, #75, #76",
        problem=(
            "#73 became a serial blocker in deliver because its branch conflicted with "
            "main in src/sprint_metrics/crew_performance.py, and three stories waited"
        ),
        change="Split stories that touch one module",
    )
    params.update(overrides)
    return ProcessDefect(**params)


def test_a_long_title_is_refused():
    with pytest.raises(ValidationError, match="short statement of the problem"):
        defect(title="x" * (MAX_TITLE + 1))


def test_a_title_is_used_as_given():
    short = defect(title="A conflict blocked an epic's stories behind one card")
    assert short.issue_title == "A conflict blocked an epic's stories behind one card"


def test_without_a_title_the_problem_is_shortened_at_a_word_not_cut_mid_word():
    title = defect().issue_title
    assert len(title) <= MAX_TITLE and title.endswith("…")
    assert title[:-1] == defect().problem[: len(title) - 1]
    assert not title[:-1].endswith(("m", "conflic")), "ends on a whole word"
    assert title.startswith("#73 became a serial blocker")


def test_a_filed_defects_issue_carries_its_short_title():
    _, issues, _ = record(Retro(summary="s", defects=[defect(title="A conflict blocked stories")]))
    assert issues.created[0]["title"] == "A conflict blocked stories"


# --- #174: what was fixed during the sprint ------------------------------------------------------


class Closed(FakeIssues):
    def __init__(self, closed, **kw):
        super().__init__(**kw)
        self._closed = closed

    def closed_since(self, repo, since):
        return self._closed


FIXED = [
    {
        "number": 158,
        "title": "A returned story whose branch conflicts is rebuilt",
        "labels": [],
        "state_reason": "completed",
    },
    {
        "number": 171,
        "title": "A story must name the file it changes",
        "labels": [],
        "state_reason": "not_planned",
    },
    {"number": 156, "title": "Standup: Sprint 6", "labels": [{"name": "standup"}]},
]


def test_the_sprints_fixes_are_listed_without_its_standup_or_retro():
    numbers, text = fixed_this_sprint(Closed(FIXED), CREW, "2026-09-24T00:00:00Z")
    assert numbers == {158, 171}
    assert text.splitlines() == [
        "- crew#158 (fixed) — A returned story whose branch conflicts is rebuilt",
        "- crew#171 (decided against) — A story must name the file it changes",
    ]


def test_a_defect_a_fix_explains_is_cited_not_filed():
    """What the Sprint 6 retro should have done with #170."""
    explained = defect(explained_by=158)
    out, issues, _ = record(Retro(summary="s", defects=[explained]), known={158})
    assert out.explained and out.filed == []
    assert "explained by #158, not filed again" in issues.created[-1]["body"]


def test_a_number_nobody_showed_the_retro_still_cant_suppress_a_finding():
    out, _, _ = record(Retro(summary="s", defects=[defect(explained_by=999)]), known={158})
    assert out.filed and not out.explained
