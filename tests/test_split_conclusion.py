"""The split reads the epic's conclusion (crew#440).

The Business Analyst is shown the conclusion apart from the epic's own text, may not
contradict a row, and names the rows each story follows. Every row reaches a story,
or is named as one that is not for stories. The Goal's look and its settle run before
it when `refinement.look` is on (crew#858).
"""

from __future__ import annotations

import contextlib
import copy

import pytest
from pydantic import ValidationError

from crew_org.config import load_org
from crew_org.crews import refinement_crew
from crew_org.crews.refinement_crew import (
    AlreadyDelivered,
    RowNotForStories,
    Story,
    StoryProposal,
)
from crew_org.events import EventSink
from crew_org.flows import board_flow, record
from crew_org.flows import conclusion as flow
from tests import asked
from tests.test_board_flow import FakeBoard, FakeIssues, epic_card, make_story

APPROVED = "# Run as a service\n\nThe approved text."
CONCLUSION = (
    "## Refinement conclusion\n\n"
    "Ready to split: 3 decided, 1 open for the design note, 0 dismissed.\n\n"
    "| ID | Status | Context | Decision | Consequences | Source |\n"
    "|---|---|---|---|---|---|\n"
    "| R1 | accepted | History | Postgres | A client | Goal decision D1 |\n"
    "| R2 | accepted | Counting | Where it merges | Group by merge date | Goal decision D3 |\n"
    "| R3 | accepted | Proof | CI only | A CI check | Goal decision D6 |\n\n"
    "| ID | Open question | Impact | Settled by |\n|---|---|---|---|\n"
    # Left to the implementer: a question open for the Architect holds the split (ADR 0018).
    "| Q1 | Which key? | The store | Implementer |\n\n"
    "_Settled by the Product Owner._"
)
BODY = f"{APPROVED}\n\n{CONCLUSION}\n"


def story(title, *follows):
    return make_story(title).model_copy(update={"follows": list(follows)})


def proposal(*stories, left_out=()):
    return StoryProposal(
        epic_title="E",
        stories=list(stories),
        not_for_stories=[RowNotForStories(row=r, why="guides the design note") for r in left_out],
    )


# --- reading the conclusion -----------------------------------------------------------------


def test_the_epics_own_text_and_its_conclusion_are_told_apart():
    own, conclusion = record.split(BODY)
    assert own == APPROVED
    assert conclusion.startswith("## Refinement conclusion")
    assert conclusion.endswith("_Settled by the Product Owner._")


def test_an_epic_with_no_conclusion_is_all_its_own_text():
    assert record.split(APPROVED) == (APPROVED, "")


def test_the_rows_are_the_decisions_not_the_questions():
    _, conclusion = record.split(BODY)
    assert record.parse(conclusion).binding_ids() == ["R1", "R2", "R3"]
    assert record.parse("").binding_ids() == []


def test_a_story_names_the_rows_it_follows_in_a_line():
    assert flow.follows_line(["R1", "R4"]) == "Follows the epic's R1, R4."


# --- the schema -----------------------------------------------------------------------------


def test_a_story_names_rows_by_id_and_a_duplicate_is_folded():
    base = make_story("S").model_dump()
    assert Story.model_validate({**base, "follows": ["r1", "R1", "R2"]}).follows == ["R1", "R2"]
    with pytest.raises(ValidationError):
        Story.model_validate({**base, "follows": ["row 1"]})


def test_a_row_left_out_of_stories_is_named_by_id():
    assert RowNotForStories(row="r3", why="x").row == "R3"
    with pytest.raises(ValidationError):
        RowNotForStories(row="three", why="x")


# --- every row reaches a story ---------------------------------------------------------------


ROWS = ["R1", "R2", "R3"]


def test_a_split_that_follows_every_row_has_no_problems():
    assert flow.problems(proposal(story("A", "R1", "R2"), story("B", "R3")), ROWS) == []


def test_a_row_no_story_follows_is_a_problem_named_by_id():
    found = flow.problems(proposal(story("A", "R1"), story("B", "R2")), ROWS)
    assert len(found) == 1 and "No story follows R3" in found[0]


def test_a_row_named_as_not_for_stories_is_accounted_for():
    assert flow.problems(proposal(story("A", "R1", "R2"), left_out=["R3"]), ROWS) == []


def test_a_story_that_follows_a_row_the_epic_does_not_have_is_a_problem():
    found = flow.problems(proposal(story("A", "R1", "R2", "R3", "R9")), ROWS)
    assert found == ["Story 'A' follows R9, not rows of the epic."]


def test_a_split_that_finds_everything_delivered_has_nothing_to_follow_the_rows():
    delivered = StoryProposal(
        epic_title="E",
        already_delivered=[AlreadyDelivered(title="T", by=5, why="its outcome covers it")],
    )
    assert flow.problems(delivered, ROWS) == []


def test_an_epic_with_no_conclusion_has_no_rows_to_follow():
    assert flow.problems(proposal(story("A")), []) == []


def test_what_the_analyst_is_told_names_each_problem():
    text = flow.feedback(["No story follows R3."])
    assert "No story follows R3." in text and "never contradicts them" in text


# --- the split itself, through the tick -------------------------------------------------------


class Concluded(FakeIssues):
    """The epic's body carries its conclusion."""

    def get(self, repo: str, number: int) -> dict:
        return {"body": BODY, "state": "open"}


def run_tick(monkeypatch, *proposals, org_look=False, issues=None):
    asked: list[tuple[str, dict]] = []
    queue = list(proposals)

    def split(title, context="", **kw):
        asked.append((context, kw))
        found = queue.pop(0)
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(board_flow, "split_epic", split)
    monkeypatch.setattr(board_flow, "propose_epics", lambda g, **kw: None)
    # A copy: load_org is cached, and writing to its dict would leak into every test after.
    org = copy.deepcopy(load_org())
    org["refinement"] = {"look": org_look}
    issues = issues or Concluded()
    result = board_flow.tick(
        FakeBoard([epic_card(3)]),
        issues,
        EventSink(None),
        default_repo="sprint-metrics",
        org=org,
        crew_repo="crew",
    )
    return result, asked, issues


def test_the_analyst_is_shown_the_conclusion_apart_from_the_epics_text(monkeypatch):
    ok = proposal(story("A", "R1", "R2"), story("B", "R3"))
    result, asked, _ = run_tick(monkeypatch, ok)
    context, kw = asked[0]
    assert "The approved text." in context and "Refinement conclusion" not in context
    assert kw["conclusion"].startswith("## Refinement conclusion") and "| R2 |" in kw["conclusion"]
    assert len(result.stories_created) == 2


def test_each_story_says_which_rows_it_follows(monkeypatch):
    ok = proposal(story("A", "R1", "R2"), story("B", "R3"))
    _, _, issues = run_tick(monkeypatch, ok)
    bodies = {i["title"]: i["body"] for i in issues.created}
    assert "Follows the epic's R1, R2." in bodies["A"]
    assert "Follows the epic's R3." in bodies["B"]


def test_a_story_that_follows_nothing_says_nothing_of_rows(monkeypatch):
    ok = proposal(story("A", "R1", "R2", "R3"), story("B"))
    _, _, issues = run_tick(monkeypatch, ok)
    assert "Follows the epic's" not in {i["title"]: i["body"] for i in issues.created}["B"]


def test_a_row_no_story_follows_is_refused_at_done_with_it_named(monkeypatch):
    """Refused in the split's session, not by a second split (crew#807)."""
    missing = proposal(story("A", "R1", "R2"))
    fixed = proposal(story("A", "R1", "R2"), story("B", "R3"))
    result, asked, _ = run_tick(monkeypatch, fixed)
    kw = asked[0][1]
    assert list(kw["rows"]) == ["R1", "R2", "R3"]
    assert any("No story follows R3" in f for f in flow.problems(missing, kw["rows"]))
    assert flow.problems(fixed, kw["rows"]) == []
    assert len(asked) == 1 and len(result.stories_created) == 2


def test_the_split_refuses_at_done_what_doesnt_follow_the_record(monkeypatch):
    seen = asked.capture(monkeypatch)
    with contextlib.suppress(asked.Stop):
        refinement_crew.split_epic("E", "body", rows=["R1", "R2", "R3"])
    with pytest.raises(ValueError, match="No story follows R3"):
        seen["check"](proposal(story("A", "R1", "R2")))
    seen["check"](proposal(story("A", "R1", "R2"), story("B", "R3")))


def test_a_split_whose_session_stops_fails_and_creates_nothing(monkeypatch):
    from crew_org.session import steps
    from crew_org.session.conversation import Ending

    stopped = steps.StepStopped("split", Ending("blocked", {"missing": "100 calls"}, "ceiling"))
    result, asked, issues = run_tick(monkeypatch, stopped)
    assert result.stories_created == [] and issues.created == []
    assert "split's session stopped (ceiling)" in result.failed[0][1]


def test_a_row_named_as_not_for_stories_needs_no_second_split(monkeypatch):
    ok = proposal(story("A", "R1", "R2"), left_out=["R3"])
    result, asked, _ = run_tick(monkeypatch, ok)
    assert len(asked) == 1 and len(result.stories_created) == 1


def test_an_epic_without_a_conclusion_is_split_exactly_as_before(monkeypatch):
    class Plain(FakeIssues):
        def get(self, repo, number):
            return {"body": APPROVED, "state": "open"}

    result, asked, _ = run_tick(monkeypatch, proposal(story("A")), issues=Plain())
    assert asked[0][1]["conclusion"] == "" and len(result.stories_created) == 1


# --- the switch -------------------------------------------------------------------------------


def test_the_look_runs_only_while_the_org_has_it_on(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("the look ran")

    monkeypatch.setattr(board_flow, "look_step", boom)
    result, _, _ = run_tick(monkeypatch, proposal(story("A", "R1", "R2", "R3")), org_look=False)
    assert len(result.stories_created) == 1


def test_the_look_is_on_in_the_crews_own_config():
    assert load_org()["refinement"]["look"] is True


def test_with_the_look_on_it_runs_first_looking_in_the_crews_and_delivery_repositories(
    monkeypatch,
):
    seen = {}

    def look_step(board, issues, sink, result, epic, repo, *, cards, turns, search, reading):
        seen.update(repo=repo, search=search, reading=reading)
        return True

    monkeypatch.setattr(board_flow, "look_step", look_step)
    run_tick(monkeypatch, proposal(story("A", "R1", "R2", "R3")), org_look=True)
    assert seen["repo"] == "sprint-metrics"
    # The epic's own repository first: that is where its Goal's cards are.
    assert seen["search"][:2] == ["sprint-metrics", "crew"]


def test_an_epic_the_look_holds_back_is_not_split(monkeypatch):
    monkeypatch.setattr(board_flow, "look_step", lambda *a, **kw: False)
    result, asked, issues = run_tick(monkeypatch, org_look=True)
    assert asked == [] and issues.created == [] and result.stories_created == []


def test_the_switch_must_be_true_or_false():
    from crew_org.config import _validate

    org = copy.deepcopy(load_org())
    org["refinement"] = {"look": "yes"}
    with pytest.raises(ValueError, match="refinement.look"):
        _validate(org)


def test_the_old_panel_switch_is_a_startup_error_naming_the_new_one():
    from crew_org.config import _validate

    org = copy.deepcopy(load_org())
    org["refinement"] = {"panel": True}
    with pytest.raises(ValueError, match="Rename it to refinement.look"):
        _validate(org)


# --- the 406 proof's refusal: open questions and infra items in `not_for_stories` ---------------


def test_the_split_the_406_proof_was_refused_for_is_now_accepted():
    # The model listed the conclusion's open questions and infra item as "not for stories".
    split = StoryProposal(
        epic_title="E",
        stories=[story("A", "R1"), story("B", "R2")],
        not_for_stories=[
            RowNotForStories(row="Q1", why="settled by the design note"),
            RowNotForStories(row="Q2", why="settled by the design note"),
            RowNotForStories(row="I1", why="infra provides it"),
        ],
    )
    assert flow.problems(split, ["R1", "R2"]) == []


def test_an_open_question_listed_does_not_excuse_a_row_no_story_follows():
    split = proposal(story("A", "R1"), left_out=["Q1", "I1"])
    assert flow.problems(split, ["R1", "R2"]) == [
        "No story follows R2. Name the story that does in its `follows`, or list the row in "
        "`not_for_stories` with why it is not for stories."
    ]


def test_a_story_following_a_question_or_infra_item_keeps_only_its_rows():
    base = make_story("S").model_dump()
    assert Story.model_validate({**base, "follows": ["R1", "Q1", "I1", "R2"]}).follows == [
        "R1",
        "R2",
    ]


def test_the_split_is_told_questions_and_infra_items_need_no_entry(monkeypatch):
    import contextlib

    from crew_org.crews import refinement_crew

    seen = asked.capture(monkeypatch)
    with contextlib.suppress(Exception):
        refinement_crew.split_epic("E", "body", conclusion=CONCLUSION)
    assert "Open questions (Q) and items for infra (I) need no entry" in seen["description"]


OPEN_FOR_ARCHITECT = BODY.replace("| The store | Implementer |", "| The store | Architect |")
