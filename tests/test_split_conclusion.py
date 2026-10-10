"""The split reads the epic's conclusion (crew#440).

The Business Analyst is shown the conclusion apart from the epic's own text, may not
contradict a row, and names the rows each story follows. Every row reaches a story,
or is named as one that is not for stories. The panel and the settle step run before
it when `refinement.panel` is on.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from crew_org.config import load_org
from crew_org.crews.refinement_crew import (
    AlreadyDelivered,
    RowNotForStories,
    Story,
    StoryProposal,
)
from crew_org.events import EventSink
from crew_org.flows import board_flow, record, refine_panel
from crew_org.flows import conclusion as flow
from crew_org.flows import panel as panel_flow
from crew_org.flows import settle as settle_flow
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
    "| Q1 | Which key? | The store | Design note |\n\n"
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


# --- panel_step: before the split ---------------------------------------------------------


class Epic:
    number = 406
    title = "Run as a service"

    def __init__(self, parent=174):
        self.parent = parent


class Gh:
    def __init__(self, body=APPROVED):
        self.body = body

    def get(self, repo, number):
        return {"body": self.body}


def stub(monkeypatch, *, notes=None, outcome=settle_flow.Outcome.WRITTEN, fail=None):
    calls = {"gather": [], "panel": 0, "post": 0, "settle": 0}
    context = SimpleNamespace(epic_ref="x", decisions="D", project_log="", siblings=[])

    def gather(issues, repo, epic, *, project, search):
        calls["gather"].append((project, search))
        return context

    def run(ctx):
        calls["panel"] += 1
        return SimpleNamespace(answers={"architect": object()}, failed={})

    def settle(issues, sink, **kw):
        calls["settle"] += 1
        calls["known"] = kw["known"]
        if fail:
            raise fail
        return settle_flow.Settled(outcome)

    monkeypatch.setattr(panel_flow, "gather", gather)
    monkeypatch.setattr(panel_flow, "panel_on", lambda i, r, n: notes)
    monkeypatch.setattr(panel_flow, "post", lambda *a: calls.__setitem__("post", calls["post"] + 1))
    monkeypatch.setattr(refine_panel, "run_panel", run)
    monkeypatch.setattr(settle_flow, "settle_epic", settle)
    return calls


def step(issues=None, epic=None, project="THE RECORD", search=("crew", "sprint-metrics")):
    result = SimpleNamespace(failed=[], skipped=[])
    ok = refine_panel.panel_step(
        issues or Gh(),
        EventSink(None),
        result,
        epic or Epic(),
        "sprint-metrics",
        project=project,
        search=list(search),
    )
    return ok, result


def test_an_epic_with_no_goal_is_split_as_before(monkeypatch):
    calls = stub(monkeypatch)
    ok, _ = step(epic=Epic(parent=None))
    assert ok and calls["panel"] == 0 and calls["gather"] == []


def test_an_epic_with_a_conclusion_is_left_alone(monkeypatch):
    calls = stub(monkeypatch)
    ok, _ = step(issues=Gh(BODY))
    assert ok and calls["panel"] == 0 and calls["settle"] == 0


def test_the_panel_runs_posts_and_is_settled_before_the_split(monkeypatch):
    calls = stub(monkeypatch)
    ok, result = step()
    assert ok and result.failed == [] and result.skipped == []
    assert (calls["panel"], calls["post"], calls["settle"]) == (1, 1, 1)


def test_the_epics_own_repository_is_searched_first_for_the_decisions(monkeypatch):
    calls = stub(monkeypatch)
    step(search=["crew", "infra"])
    assert calls["gather"] == [("THE RECORD", ["sprint-metrics", "crew", "infra"])]
    assert calls["known"] == {"sprint-metrics", "crew", "infra"}


def test_a_panel_already_run_is_not_run_again(monkeypatch):
    calls = stub(monkeypatch, notes=SimpleNamespace(answers={"architect": object()}, failed={}))
    ok, _ = step()
    assert ok and calls["panel"] == 0 and calls["post"] == 0 and calls["settle"] == 1


def test_an_epic_waiting_for_the_sponsor_is_not_split(monkeypatch):
    stub(monkeypatch, outcome=settle_flow.Outcome.WAITING)
    ok, result = step()
    assert not ok
    assert result.skipped == [(406, "waits for the Sponsor's answer on the epic")]


def test_a_panel_no_member_of_answered_stops_the_split_and_says_why(monkeypatch):
    calls = stub(monkeypatch)
    monkeypatch.setattr(
        refine_panel,
        "run_panel",
        lambda ctx: SimpleNamespace(answers={}, failed={"architect": "TimeoutError: slow"}),
    )
    ok, result = step()
    assert not ok and calls["settle"] == 0
    assert "architect: TimeoutError: slow" in result.failed[0][1]


def test_a_settlement_that_fails_stops_the_split_and_says_why(monkeypatch):
    stub(monkeypatch, fail=settle_flow.SettleFailed("refused 3 times"))
    ok, result = step()
    assert not ok and "refused 3 times" in result.failed[0][1]


def test_an_epic_the_panel_cannot_place_under_a_goal_is_split_as_before(monkeypatch):
    stub(monkeypatch)

    def gather(*a, **kw):
        raise panel_flow.NotUnderAGoal("no parent")

    monkeypatch.setattr(panel_flow, "gather", gather)
    ok, _ = step()
    assert ok


# --- the split itself, through the tick -------------------------------------------------------


class Concluded(FakeIssues):
    """The epic's body carries its conclusion."""

    def get(self, repo: str, number: int) -> dict:
        return {"body": BODY, "state": "open"}


def run_tick(monkeypatch, *proposals, org_panel=False, issues=None):
    asked: list[tuple[str, dict]] = []
    queue = list(proposals)

    def split(title, context="", **kw):
        asked.append((context, kw))
        return queue.pop(0)

    monkeypatch.setattr(board_flow, "split_epic", split)
    monkeypatch.setattr(board_flow, "propose_epics", lambda g, **kw: None)
    # A copy: load_org is cached, and writing to its dict would leak into every test after.
    org = copy.deepcopy(load_org())
    org["refinement"] = {"panel": org_panel}
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


def test_a_row_no_story_follows_is_split_again_once_with_it_named(monkeypatch):
    missing = proposal(story("A", "R1", "R2"))
    fixed = proposal(story("A", "R1", "R2"), story("B", "R3"))
    result, asked, _ = run_tick(monkeypatch, missing, fixed)
    assert len(asked) == 2 and "No story follows R3" in asked[1][1]["feedback"]
    assert len(result.stories_created) == 2


def test_a_row_that_is_still_not_followed_fails_the_split_and_creates_nothing(monkeypatch):
    missing = proposal(story("A", "R1", "R2"))
    result, asked, issues = run_tick(monkeypatch, missing, missing)
    assert len(asked) == 2 and result.stories_created == [] and issues.created == []
    assert "doesn't fit the epic's conclusion" in result.failed[0][1]


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


def test_the_panel_runs_only_while_the_org_has_it_on(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("the panel ran")

    monkeypatch.setattr(board_flow, "panel_step", boom)
    result, _, _ = run_tick(monkeypatch, proposal(story("A", "R1", "R2", "R3")), org_panel=False)
    assert len(result.stories_created) == 1


def test_the_panel_is_on_in_the_crews_own_config():
    assert load_org()["refinement"]["panel"] is True


def test_with_the_panel_on_it_runs_first_looking_in_the_crews_and_delivery_repositories(
    monkeypatch,
):
    seen = {}

    def panel_step(issues, sink, result, epic, repo, *, project, search):
        seen.update(repo=repo, search=search)
        return True

    monkeypatch.setattr(board_flow, "panel_step", panel_step)
    run_tick(monkeypatch, proposal(story("A", "R1", "R2", "R3")), org_panel=True)
    assert seen["repo"] == "sprint-metrics"
    assert seen["search"][0] == "crew" and "sprint-metrics" in seen["search"]


def test_an_epic_the_panel_holds_back_is_not_split(monkeypatch):
    monkeypatch.setattr(board_flow, "panel_step", lambda *a, **kw: False)
    result, asked, issues = run_tick(monkeypatch, org_panel=True)
    assert asked == [] and issues.created == [] and result.stories_created == []


def test_the_switch_must_be_true_or_false():
    from crew_org.config import _validate

    org = copy.deepcopy(load_org())
    org["refinement"] = {"panel": "yes"}
    with pytest.raises(ValueError, match="refinement.panel"):
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
    from types import SimpleNamespace

    from crew_org.crews import refinement_crew

    seen = {}

    class Crew:
        def __init__(self, **_):
            pass

        def kickoff(self):
            return SimpleNamespace(pydantic=None)

    class Agents(dict):
        def __missing__(self, key):
            return object()

    monkeypatch.setattr(refinement_crew, "build_agents", lambda *a, **k: Agents())
    monkeypatch.setattr(refinement_crew, "Task", lambda **k: seen.update(k) or object())
    monkeypatch.setattr(refinement_crew, "Crew", Crew)
    with contextlib.suppress(Exception):
        refinement_crew.split_epic("E", "body", conclusion=CONCLUSION)
    assert "Open questions (Q) and items for infra (I) need no entry" in seen["description"]
