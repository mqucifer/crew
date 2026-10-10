"""The criteria check reads the epic's conclusion (crew#440).

A criterion that goes against a row the conclusion decided is a conflict, the same as
two criteria that can't both pass (crew#428): it is sent back once with the row named,
and one that survives goes to the Product Owner.
"""

from __future__ import annotations

from crew_org.crews import criteria_crew
from crew_org.crews.criteria_crew import CriteriaCheck, CriteriaConflict
from crew_org.flows import board_flow
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from tests.test_board_flow import SPLIT
from tests.test_split_conclusion import BODY, Concluded, proposal, run_tick, story

ROW_CONFLICT = CriteriaCheck(
    conflicts=[
        CriteriaConflict(
            story="Query metrics",
            criterion="Then /metrics returns Prometheus text with no sprint label",
            against="row R1 of the epic's conclusion: each metric labeled by sprint",
            why="a metric without its sprint label is the opposite of what R1 decides",
        )
    ]
)


class Checker:
    def __init__(self, *answers) -> None:
        self.answers, self.calls = list(answers), []

    def __call__(self, **context):
        self.calls.append(context)
        return self.answers.pop(0) if self.answers else CriteriaCheck()


def checked_tick(monkeypatch, checker, *proposals, issues=None):
    monkeypatch.setattr(board_flow, "check_criteria", checker)
    followed = proposal(story("A", "R1", "R2"), story("B", "R3"))
    return run_tick(monkeypatch, *(proposals or (followed, followed)), issues=issues)


# --- what the check is shown ----------------------------------------------------------------


def described(monkeypatch, **kw) -> str:
    seen = {}

    def ask(role, parts, answer, **_):
        seen["parts"] = parts
        return CriteriaCheck()

    monkeypatch.setattr(criteria_crew.calls, "ask", ask)
    criteria_crew.check_criteria(stories="### Query metrics\n1. Given x", **kw)
    return criteria_crew.calls.task_text(seen["parts"], "")


def test_the_conclusion_is_shown_to_the_check_before_the_stories(monkeypatch):
    text = described(monkeypatch, conclusion="## Refinement conclusion\n\n| R1 | accepted |")
    assert "decided before the split" in text and "| R1 | accepted |" in text
    assert text.index("| R1 |") < text.index("## The proposed stories")


def test_a_criterion_against_a_row_or_deciding_an_open_question_is_a_conflict(monkeypatch):
    text = described(monkeypatch, conclusion="C")
    assert "a row of the epic's conclusion above" in text
    assert "decides an open question (Q)" in text


def test_an_epic_without_a_conclusion_is_checked_as_before(monkeypatch):
    text = described(monkeypatch)
    assert "decided before the split" not in text and "conclusion" not in text.split("## Your")[0]


def test_a_conflict_can_name_a_row():
    assert "row of the epic's conclusion" in CriteriaConflict.model_fields["against"].description


def test_the_checker_is_scoped_to_the_rows_too():
    from crew_org.permissions import load_agents

    spec = load_agents()["qa_engineer"]["criteria_check"]
    assert "conclusion" in spec["goal"] and "conclusion" in spec["backstory"]


# --- in the split -----------------------------------------------------------------------------


def test_the_check_is_given_the_conclusion_both_times(monkeypatch):
    checker = Checker(ROW_CONFLICT, CriteriaCheck())
    result, asked, _ = checked_tick(monkeypatch, checker)
    assert len(checker.calls) == 2
    assert all(c["conclusion"].startswith("## Refinement conclusion") for c in checker.calls)
    assert len(result.stories_created) == 2


def test_a_criterion_against_a_row_is_split_again_once_with_the_row_named(monkeypatch):
    checker = Checker(ROW_CONFLICT, CriteriaCheck())
    _, asked, _ = checked_tick(monkeypatch, checker)
    assert len(asked) == 2
    assert "row R1" in asked[1][1]["feedback"] and "no sprint label" in asked[1][1]["feedback"]


def test_a_row_conflict_that_survives_goes_to_the_product_owner_and_creates_nothing(monkeypatch):
    checker = Checker(ROW_CONFLICT, ROW_CONFLICT)
    issues = Concluded()
    result, _, _ = checked_tick(monkeypatch, checker, issues=issues)
    assert result.stories_created == [] and issues.created == []
    assert (3, NEEDS_REWORK) in issues.added_labels
    body = [b for n, b in issues.posted if n == 3][-1]
    assert STORY_PROBLEM_MARKER in body and "row R1" in body


def test_an_epic_without_a_conclusion_gives_the_check_nothing_extra(monkeypatch):
    class Plain(Concluded):
        def get(self, repo, number):
            return {"body": BODY.split("## Refinement conclusion")[0], "state": "open"}

    checker = Checker()
    monkeypatch.setattr(board_flow, "check_criteria", checker)
    run_tick(monkeypatch, SPLIT, issues=Plain())
    assert checker.calls[0]["conclusion"] == ""


# --- the check sees what's already decided (the sprint-metrics#406 proof) ------------------


def test_the_check_is_shown_the_goal_and_the_projects_log_before_the_stories(monkeypatch):
    text = described(
        monkeypatch,
        conclusion="C",
        goal="A user sends it: started, blocked, unblocked, finished, escalated.",
        project_log=(
            "## The project's decision log\n\n### 5. The service accepts five kinds of event"
        ),
    )
    assert "## The Goal, set by the Sponsor" in text and "unblocked, finished" in text
    assert "five kinds of event" in text
    assert text.index("five kinds of event") < text.index("## The proposed stories")


def test_restating_a_decision_is_not_deciding_an_open_question(monkeypatch):
    text = described(monkeypatch, conclusion="C")
    assert "decides an open question (Q)" in text
    assert "restating a decision isn't deciding the question" in text


def test_the_tick_gives_the_check_the_projects_log(monkeypatch):
    from tests.test_project_log import LOG

    class WithLog(Concluded):
        def list_dir(self, repo, path, ref):
            return list(LOG)

        def file_at(self, repo, path, ref):
            return LOG.get(path.rsplit("/", 1)[1])

    checker = Checker()
    checked_tick(monkeypatch, checker, issues=WithLog())
    assert "A card counts where it merges" in checker.calls[0]["project_log"]
    assert checker.calls[0]["goal"] == ""  # this epic has no Goal above it


def test_the_epics_decision_is_shown_to_the_check_as_decided(monkeypatch):
    """sprint-metrics#468 (2026-10-09): the Product Owner named the four new keys, and
    every criterion naming them was refused as settling the open question left to
    the design note, because the check was never shown the answer."""
    text = described(
        monkeypatch,
        conclusion="| Q1 | JSON field names | Settled by: Design note |",
        decided="**Decided:** the four new keys (points_delivered, ...) are additive.",
    )
    assert "## Decided on this epic since it was sent back" in text
    assert text.index("the four new keys") < text.index("## The proposed stories")
    assert "what was decided on this epic" in text
