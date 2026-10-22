"""A double quote in a model's answer ends the field, and the rest is lost (crew#618).

On mqucifer/sprint-metrics#555 the Architect's answer, as LiteLLM logged it, read
`"decision":"because accepts exactly two values: " ,"consequences":…`: the values it
wrote in double quotes ended the string, and the record kept only the stub.
"""

from __future__ import annotations

import pytest

from crew_org.crews.settle_crew import (
    GoalConclusion,
    GoalRow,
    GoalSettlement,
    Unsettled,
    check_not_cut,
)
from crew_org.cut_text import check_not_cut_at_a_quote, cut_fields
from tests.test_calls import Proxy, ask


def settled(decision: str, source: str) -> GoalSettlement:
    return GoalSettlement(
        conclusion=GoalConclusion(
            rows=[
                GoalRow(
                    context="Who set it",
                    decision=decision,
                    consequences="CHECK constraint in DDL, enum in intake schema",
                    source=source,
                    settles=[1],
                    epics=["all"],
                )
            ]
        )
    )


# The answer as it reached the crew on 2026-10-11, in the form that settles it now.
SPRINT_METRICS_555 = settled("because accepts exactly two values: ", "Goal example shows ")


def test_a_field_cut_at_a_quote_is_named():
    assert cut_fields(SPRINT_METRICS_555) == [
        "conclusion.rows[0].decision",
        "conclusion.rows[0].source",
    ]


def test_a_cut_answer_is_sent_back_saying_why():
    with pytest.raises(Unsettled, match="rows\\[0\\].decision.*single quotes"):
        check_not_cut(SPRINT_METRICS_555)


def test_a_finished_answer_passes():
    whole = settled(
        "because accepts exactly two values: 'sponsor' and 'story'", "Goal example shows 'sponsor'"
    )
    check_not_cut(whole)
    check_not_cut_at_a_quote(whole)


def test_the_call_layer_asks_again_with_the_reason():
    proxy = Proxy(
        '{"approved": true, "why": "The values are: "}',
        '{"approved": true, "why": "The values are \'sponsor\' and \'story\'"}',
    )
    assert ask(proxy).approved
    retry = proxy.sent[1]["messages"][-1]["content"]
    assert retry.startswith("That answer was refused:") and "single quotes" in retry
