"""A re-split accounts for every story it supersedes (#248).

sprint-metrics#59's re-split dropped #147 (`--schema`) and #148 (`/json`)
without a word; the Sponsor wanted both kept.
"""

from __future__ import annotations

import pytest

from crew_org.crews.refinement_crew import Accounted, StoryProposal, check_accounted
from crew_org.flows.board_flow import render_split
from tests.test_board_flow import make_story

SUPERSEDED = [(145, "Prior sprint in JSON"), (146, "Error on stdout"), (147, "--schema")]


def proposal(*accounted: Accounted) -> StoryProposal:
    return StoryProposal(
        epic_title="API", stories=[make_story("Prior sprint in JSON", 3)], accounted=list(accounted)
    )


def test_a_split_that_leaves_one_out_is_refused_naming_it():
    split = proposal(
        Accounted(number=145, how="rewritten", note="Prior sprint in JSON"),
        Accounted(number=146, how="dropped", note="the decision keeps stdout empty on errors"),
    )
    with pytest.raises(ValueError, match="what became of #147"):
        check_accounted(split, SUPERSEDED)


def test_a_split_accounting_for_all_passes_and_lists_what_it_dropped():
    split = proposal(
        Accounted(number=145, how="rewritten", note="Prior sprint in JSON"),
        Accounted(number=146, how="dropped", note="the decision keeps stdout empty on errors"),
        Accounted(number=147, how="rewritten", note="--schema"),
    )
    check_accounted(split, SUPERSEDED)

    class Decision:
        required = False
        reason = "small"

    text = render_split("API", split, Decision(), {"Prior sprint in JSON": 166})
    assert "### Dropped from the earlier split" in text
    assert "- #146: the decision keeps stdout empty on errors" in text


def test_a_first_split_has_nothing_to_account_for():
    check_accounted(proposal(), [])
