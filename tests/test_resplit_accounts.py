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


# --- the merged tests the epic changes are rows the re-split accounts for (crew#583, A4) ------

from crew_org.crews.refinement_crew import TestDropped  # noqa: E402
from crew_org.events import EventSink  # noqa: E402
from crew_org.flows import board_flow, conclusion, record, story_problem  # noqa: E402
from crew_org.tools.github_project import Card  # noqa: E402

SCHEMA_TEST = "tests/test_schema_gen.py::test_single_sprint_schema_required_and_cycle_time"
DRIFT_TEST = "tests/test_docs.py::test_drift_check"


def changing(*tests, dropped=()):
    story = make_story("Add the keys", 3).model_copy(
        update={"pinned_behaviour": "contract change: " + ", ".join(tests) if tests else ""}
    )
    return StoryProposal(
        epic_title="Keys",
        stories=[story],
        tests_dropped=[
            TestDropped(test=t, why="the docs no longer list the keys") for t in dropped
        ],
    )


def test_a_re_split_that_drops_a_declared_test_silently_is_refused():
    found = conclusion.problems(changing(SCHEMA_TEST), [], [SCHEMA_TEST, DRIFT_TEST])
    assert found and DRIFT_TEST in found[0] and SCHEMA_TEST not in found[0]


def test_a_test_carried_into_a_story_or_dropped_with_why_is_accounted_for():
    assert (
        conclusion.problems(changing(SCHEMA_TEST, DRIFT_TEST), [], [SCHEMA_TEST, DRIFT_TEST]) == []
    )
    assert (
        conclusion.problems(
            changing(SCHEMA_TEST, dropped=[DRIFT_TEST]), [], [SCHEMA_TEST, DRIFT_TEST]
        )
        == []
    )


def test_a_dropped_test_stays_in_the_record_with_why_and_is_not_asked_for_again():
    base = record.Record().declare(SCHEMA_TEST, "required has 15 keys", "sm#537")
    after = base.drop(SCHEMA_TEST, "superseded by the schema story")
    record.check_change(base, after)
    assert after.declared_tests() == []
    assert after.tests[0].status == "dropped: superseded by the schema story"
    assert record.parse(record.render(after)).tests == after.tests


def test_a_test_row_is_never_deleted():
    base = record.Record().declare(SCHEMA_TEST, "x", "sm#537")
    with pytest.raises(record.RecordRefused, match="C1 was deleted"):
        record.check_change(base, record.Record())


class Issues:
    owner = "mqucifer"

    def __init__(self, body):
        self.body = body
        self.comments_posted: list[str] = []

    def get(self, repo, number):
        return {"body": self.body}

    def edit_issue(self, repo, number, *, body):
        self.body = body

    def comment(self, repo, number, body):
        self.comments_posted.append(body)


WITH_RECORD = "The epic.\n\n## Refinement conclusion\n\n| R1 | accepted | A | B | C | D |\n"


def test_each_storys_declared_tests_become_rows_naming_the_story():
    issues = Issues(WITH_RECORD)
    board_flow.record_split_tests(
        issues,
        EventSink(None),
        "sprint-metrics",
        468,
        changing(SCHEMA_TEST, DRIFT_TEST),
        {"Add the keys": 537},
    )
    found = record.parse(record.split(issues.body)[1])
    assert found.declared_tests() == [SCHEMA_TEST, DRIFT_TEST]
    assert found.tests[0].declared_by == "mqucifer/sprint-metrics#537"
    assert record.has_change(issues.comments_posted, "tests")


def test_an_epic_without_a_record_gets_no_test_rows():
    issues = Issues("The epic, split without the panel.")
    board_flow.record_split_tests(
        issues, EventSink(None), "sprint-metrics", 468, changing(SCHEMA_TEST), {"Add the keys": 537}
    )
    assert issues.body == "The epic, split without the panel." and issues.comments_posted == []


def test_a_story_problems_pinned_tests_become_rows_the_re_split_must_account_for():
    issues = Issues(WITH_RECORD)
    card = Card(item_id="S", number=529, title="Keys in /sprint", parent=468)
    comment = story_problem.evidence(card, {SCHEMA_TEST: "assert 11 == 15"}, 2)
    story_problem.record_pinned(
        issues, EventSink(None), repo="sprint-metrics", epic=468, card=card, comment=comment
    )
    found = record.parse(record.split(issues.body)[1])
    assert found.declared_tests() == [SCHEMA_TEST]
    assert "assert 11 == 15" in found.tests[0].asserts
    assert "sprint-metrics#529" in found.tests[0].declared_by
