"""A criterion that only names a quality is sent back until it names what to check (#281).

sprint-metrics#48's epics ("the tool presents, not just reports") state
qualities: a five-second scan, at a glance, clear. Right for an epic, wrong for
a criterion: QA can't prove "clear". All 213 criteria sprint-metrics had when
this landed pass; it bites only on quality-only ones.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import AcceptanceCriterion, Epic


def criterion(then: str) -> AcceptanceCriterion:
    return AcceptanceCriterion(given="a sprint's cards", when="the report is run", then=then)


@pytest.mark.parametrize(
    ("then", "word"),
    [
        ("the report is clear and readable at a glance", "clear"),
        ("the health summary is clearly visible", "clearly"),
        ("the table is easy to read", "easy"),
        ("the report's summary is clear", "clear"),  # an apostrophe isn't a quote
    ],
)
def test_a_quality_alone_is_refused_naming_the_word(then, word):
    """#281, criterion 1."""
    with pytest.raises(ValidationError, match=f"'{word}' names a quality"):
        criterion(then)


@pytest.mark.parametrize(
    "then",
    [
        "the report's first line states the overall status as one of OK, WATCH, ACT",
        "sections appear in this order: summary, raw values, definitions",
        "the 'Status' heading is prominent",
        "the summary is clear: it is 3 lines long",
        "stdout contains `api_version`",
        "the exit code is 2",
    ],
)
def test_a_quality_with_something_to_check_is_accepted(then):
    """#281, criterion 2: a word is fine beside a position, order, value or string."""
    assert criterion(then).then == then


def test_an_epic_may_state_a_quality():
    """#281, criterion 3: stories make it testable, not epics."""
    epic = Epic(
        title="Lead the report with a health summary",
        outcome="A reader sees at a glance whether the crew is doing well",
        rationale="The Sponsor wants a five-second scan of the top of the report.",
        separately_deliverable=(
            "A reader could scan the summary alone and know whether to look further."
        ),
        changes_what_readers_see=True,
    )
    assert "at a glance" in epic.outcome
