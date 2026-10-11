"""A double quote in a model's answer ends the field, and the rest is lost (crew#618).

On mqucifer/sprint-metrics#555 the Architect's answer, as LiteLLM logged it, read
`"decision":"because accepts exactly two values: " ,"consequences":…`: the values it
wrote in double quotes ended the string, and the record kept only the stub.
"""

from __future__ import annotations

import pytest

from crew_org.crews.delivery_crew import TextEdit
from crew_org.crews.settle_crew import (
    DesignAnswer,
    DesignSettlement,
    Unsettled,
    check_design_cut,
)
from crew_org.cut_text import check_not_cut_at_a_quote, cut_fields
from tests.test_calls import Proxy, ask

# The answer as it reached the crew on 2026-10-11.
SPRINT_METRICS_555 = DesignSettlement(
    settled=[
        DesignAnswer(
            question="Q1",
            decision="because accepts exactly two values: ",
            consequences="CHECK constraint in DDL, enum in intake schema, GROUP BY in Epic 557",
            source="Goal example shows ",
        ),
        DesignAnswer(
            question="Q2",
            decision="Three nullable columns on events table: epic INTEGER, because TEXT",
            consequences="Upsert adds three nullable fields",
            source="store.py upsert pattern",
        ),
    ]
)


def test_a_field_cut_at_a_quote_is_named():
    assert cut_fields(SPRINT_METRICS_555) == ["settled[0].decision", "settled[0].source"]


def test_the_architects_cut_answer_is_sent_back_saying_why():
    with pytest.raises(Unsettled, match="settled\\[0\\].decision.*single quotes"):
        check_design_cut(SPRINT_METRICS_555)


def test_a_finished_answer_passes():
    whole = DesignSettlement(
        settled=[
            DesignAnswer(
                question="Q1",
                decision="because accepts exactly two values: 'sponsor' and 'story'",
                consequences="CHECK constraint in DDL",
                source="Goal example shows 'sponsor'",
            )
        ]
    )
    check_design_cut(whole)
    check_not_cut_at_a_quote(whole)


def test_a_files_own_text_may_end_on_a_colon():
    """YAML the Developer copies exactly ends on a colon, and isn't cut."""
    edit = TextEdit(path=".github/workflows/ci.yml", find="  docker-smoke:", replace="  smoke:")
    assert cut_fields(edit) == []


def test_the_call_layer_asks_again_with_the_reason():
    proxy = Proxy(
        '{"approved": true, "why": "The values are: "}',
        '{"approved": true, "why": "The values are \'sponsor\' and \'story\'"}',
    )
    assert ask(proxy).approved
    retry = proxy.sent[1]["messages"][-1]["content"]
    assert retry.startswith("That answer was refused:") and "single quotes" in retry
