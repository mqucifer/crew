"""A story declares the merged tests it changes, and the gates read it (#316, #311).

The Business Analyst writes `contract change: <the tests it updates>` when a
story changes what merged tests assert. Nothing in the regression guard read
it: sprint-metrics's docs redesign ends by removing `tests/test_readme.py`,
declared, and would have been refused three times and blocked, as #200 was. The
gates at `done` read the line against what `done` declares (crew#778,
`test_gates.py`).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import AcceptanceCriterion, Story
from crew_org.flows.board_flow import existing_tests_line, render_story_body
from crew_org.flows.delivery import declared_contract


def split_story(**kw) -> Story:
    criteria = [
        AcceptanceCriterion(given="g", when="w", then="t"),
        AcceptanceCriterion(given="g2", when="w2", then="error"),
    ]
    return Story(
        title="Flag the table",
        as_a="lead",
        i_want="flags",
        so_that="see breaches",
        acceptance_criteria=criteria,
        points=3,
        **kw,
    )


def test_a_story_says_opt_in_or_contract_change_and_nothing_else():
    """sprint-metrics#97 changed what 25 merged tests pinned and said neither (#189)."""
    assert split_story(pinned_behaviour="opt-in: only with --thresholds").pinned_behaviour
    assert split_story(pinned_behaviour="contract change: updates test_default_table_rows")
    with pytest.raises(ValidationError, match="opt-in' or 'contract change"):
        split_story(pinned_behaviour="probably fine")


def test_stories_written_before_311_are_still_read():
    body = "**Behaviour merged tests pin** — contract change: tests/test_readme.py\n"
    assert declared_contract(body) == {"tests/test_readme.py"}


def test_what_is_declared_is_read_from_the_line_only():
    body = "Criterion: tests/test_docs.py regenerates each section.\n\n" + existing_tests_line(
        "contract change: tests/test_readme.py::test_readme_installation"
    )
    assert declared_contract(body) == {"tests/test_readme.py::test_readme_installation"}


# --- the story reads right (#311) ------------------------------------------------------------


def test_opt_in_no_longer_reads_as_optional_tests():
    body = render_story_body(
        split_story(pinned_behaviour="opt-in: only with --thresholds"), 54, "F"
    )
    assert (
        "**Existing tests** — unchanged: the new behaviour is opt-in (only with --thresholds)"
        in (body)
    )
    assert "Behaviour merged tests pin" not in body


def test_a_contract_change_names_what_it_changes():
    line = existing_tests_line("contract change: tests/test_readme.py")
    assert line == "**Existing tests** — this story changes what they assert: tests/test_readme.py"


def test_a_story_that_touches_no_merged_test_has_no_line():
    assert "Existing tests" not in render_story_body(split_story(), 54, "F")


def test_the_story_s_first_sentence_reads_as_english():
    """crew#1009: 24 of 40 split bodies read "As a **The Sponsor…**", as
    mqucifer/sprint-metrics#957 does. The lines a parser reads are unchanged."""
    real = split_story(
        pinned_behaviour="opt-in: only with --range",
        builds_on=[955],
    ).model_copy(
        update={
            "as_a": "The Sponsor, through the per-sprint answers the crew reads back",
            "i_want": "The same entry to also carry each epic's to-date session time",
            "so_that": "I can see the session time an epic has taken in total",
        }
    )
    body = render_story_body(real, 941, "Answer each epic's session time")
    assert body.startswith(
        "As **the Sponsor, through the per-sprint answers the crew reads back**, I want "
        "**the same entry to also carry each epic's to-date session time**, so that "
        "**I can see the session time an epic has taken in total**.\n\n## Acceptance criteria\n"
    )
    for line in ("**Existing tests** —", "**Builds on** — #955", "**Estimate** — 3 points"):
        assert f"\n{line}" in body
    assert "\n1. **Given** g\n   **When** w\n   **Then** t\n" in body
    plain = render_story_body(split_story(), 54, "F")
    assert plain.startswith("As a **lead**, I want **flags**, so that **see breaches**.")
    operator = split_story().model_copy(update={"as_a": "operator", "i_want": "An alert"})
    assert render_story_body(operator, 54, "F").startswith(
        "As an **operator**, I want **an alert**,"
    )
