"""Later steps are shown only the rows of the epic's conclusion their story names (crew#440).

A story's issue says "Follows the epic's R1, R4." The Developer, Code Reviewer, QA and
the deploy review get those rows and nothing else of the conclusion: a long one doesn't
bloat every story's context, and nothing the story relies on goes missing.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from crew_org.crews import deploy_review_crew, epic_rows, qa_crew, review_crew
from crew_org.events import EventSink
from crew_org.flows import conclusion as flow
from crew_org.tools.github_project import Card
from tests.test_delivery_flow import harness  # noqa: F401

CONCLUSION = (
    "## Refinement conclusion\n\n"
    "Ready to split: 3 decided, 1 open for the design note, 0 dismissed.\n\n"
    "| ID | Status | Context | Decision | Consequences | Source |\n"
    "|---|---|---|---|---|---|\n"
    "| R1 | accepted | History | Postgres, own schema | A client | Goal decision D1 |\n"
    "| R2 | accepted | Counting | Counted where it merges | Group by merge date | D3 |\n"
    "| R3 | accepted | Proof | CI only | A CI check | D6 |\n\n"
    "| ID | Open question | Impact | Settled by |\n|---|---|---|---|\n"
    "| Q1 | Which key? | The store | Design note |\n\n"
    "_Settled by the Product Owner._"
)
EPIC = f"# Run as a service\n\nThe approved text.\n\n{CONCLUSION}\n"
STORY = (
    "As a **user**, I want **x**, so that **y**.\n\n## Acceptance criteria\n\n1. **Given** a\n\n"
    "Follows the epic's R3, R1.\n\n**Estimate** — 3 points\n"
)


def card(number=7, parent=6, repo="sprint-metrics", status="Reviewing") -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title="A story",
        status=status,
        state="OPEN",
        work_type="Story",
        repo=repo,
        parent=parent,
    )


class Issues:
    def __init__(self, bodies=None, fail=False):
        self.bodies = bodies or {6: EPIC, 7: STORY}
        self.fail = fail
        self.reads: list[int] = []

    def get(self, repo, number):
        self.reads.append(number)
        if self.fail:
            raise RuntimeError("GitHub is down")
        return {"body": self.bodies.get(number, "")}


# --- reading a story's rows ------------------------------------------------------------------


def test_the_rows_a_story_follows_are_read_from_its_line():
    assert flow.parse_follows(STORY) == ["R3", "R1"]
    assert flow.parse_follows("Follows the epic's R4.") == ["R4"]


def test_a_story_that_follows_nothing_names_no_rows():
    assert flow.parse_follows("As a user.\n\n**Estimate** — 3 points") == []


def test_a_mention_of_the_line_in_prose_is_not_the_line():
    assert flow.parse_follows("This Follows the epic's R1, but only in a sentence.") == []


def test_only_the_named_rows_come_back_under_the_tables_header_in_table_order():
    _, conclusion = flow.split_conclusion(EPIC)
    rows = flow.rows_for(conclusion, ["R3", "R1"])
    assert rows.split("\n")[0].startswith("| ID | Status |")
    assert [line.split("|")[1].strip() for line in rows.split("\n")[2:]] == ["R1", "R3"]
    assert "R2" not in rows and "Q1" not in rows and "Settled by the Product Owner" not in rows


def test_a_row_the_conclusion_no_longer_has_is_left_out_and_none_gives_nothing():
    _, conclusion = flow.split_conclusion(EPIC)
    assert "R9" not in flow.rows_for(conclusion, ["R1", "R9"])
    assert flow.rows_for(conclusion, ["R9"]) == ""
    assert flow.rows_for("", ["R1"]) == ""
    assert (
        flow.rows_for(
            "## Refinement conclusion\n\nReady to split: the panel raised nothing.", ["R1"]
        )
        == ""
    )


def test_a_story_is_given_only_its_rows_from_its_epic():
    issues = Issues()
    rows = flow.story_rows(issues, card(), "sprint-metrics")
    assert "Postgres, own schema" in rows and "CI only" in rows
    assert "Counted where it merges" not in rows
    assert sorted(issues.reads) == [6, 7]


def test_a_story_with_no_follows_line_reads_no_epic():
    issues = Issues({6: EPIC, 7: "As a user.\n\n**Estimate** — 3 points"})
    assert flow.story_rows(issues, card(), "sprint-metrics") == ""
    assert issues.reads == [7]


def test_an_epic_with_no_conclusion_gives_nothing():
    assert flow.story_rows(Issues({6: "# Epic\n\nText.", 7: STORY}), card(), "r") == ""


def test_a_story_with_no_epic_or_no_card_gives_nothing():
    assert flow.story_rows(Issues(), card(parent=None), "r") == ""
    assert flow.story_rows(Issues(), None, "r") == ""


def test_a_failed_read_gives_nothing_and_stops_nothing():
    assert flow.story_rows(Issues(fail=True), card(), "r") == ""


def test_the_cards_own_repository_wins_over_the_default():
    seen = []

    class Where(Issues):
        def get(self, repo, number):
            seen.append(repo)
            return super().get(repo, number)

    flow.story_rows(Where(), card(repo="infra"), "sprint-metrics")
    assert set(seen) == {"infra"}


# --- the block each step gets ------------------------------------------------------------------


def test_the_rows_come_under_a_heading_with_what_the_step_does_with_them():
    text = epic_rows.block("| R1 |", "Judge against them.")
    assert text == (
        "## What the epic decided that this story follows\n\n| R1 |\n\nJudge against them.\n\n"
    )
    assert epic_rows.block("| R1 |", "x", level=1).startswith("# What the epic decided")


def test_no_rows_means_no_block():
    assert epic_rows.block("", "Judge against them.") == ""


# --- what each crew's prompt carries -----------------------------------------------------------


def prompt(monkeypatch, module, call, **kw) -> str:
    seen = {}

    class Crew:
        def __init__(self, **_):
            pass

        def kickoff(self):
            return SimpleNamespace(pydantic=object())

    class Agents(dict):
        def __missing__(self, key):
            return object()

    monkeypatch.setattr(module, "build_agents", lambda *a, **k: Agents(), raising=False)
    monkeypatch.setattr(module, "Task", lambda **k: seen.update(k) or object())
    monkeypatch.setattr(module, "Crew", Crew)
    with contextlib.suppress(Exception):  # only the prompt is under test, not the verdict
        call(**kw)
    return seen["description"]


ROWS = "| ID | Status |\n|---|---|\n| R1 | accepted |"


@pytest.mark.parametrize(
    ("module", "call", "kw", "judges"),
    [
        (
            review_crew,
            review_crew.review_diff,
            {"title": "t", "diff": "d"},
            "Judge the diff against them as well",
        ),
        (
            qa_crew,
            qa_crew.verify_story,
            {"story": "s", "test_output": "o", "test_code": "c"},
            "hold it unproven if the code does what a row rules out",
        ),
        (
            deploy_review_crew,
            deploy_review_crew.review_deploy,
            {"title": "t", "diff": "d", "evidence": "e"},
            "judge the change against it as well",
        ),
    ],
)
def test_each_judging_step_is_shown_the_rows_with_its_own_instruction(
    monkeypatch, module, call, kw, judges
):
    text = prompt(monkeypatch, module, call, decisions=ROWS, **kw)
    assert "## What the epic decided that this story follows" in text
    assert "| R1 | accepted |" in text and judges in text


@pytest.mark.parametrize(
    ("module", "call", "kw"),
    [
        (review_crew, review_crew.review_diff, {"title": "t", "diff": "d"}),
        (qa_crew, qa_crew.verify_story, {"story": "s", "test_output": "o", "test_code": "c"}),
        (
            deploy_review_crew,
            deploy_review_crew.review_deploy,
            {"title": "t", "diff": "d", "evidence": "e"},
        ),
    ],
)
def test_a_story_with_no_rows_gets_the_prompt_it_always_did(monkeypatch, module, call, kw):
    assert "What the epic decided" not in prompt(monkeypatch, module, call, **kw)


# --- the flows hand them over ------------------------------------------------------------------


def test_the_developer_is_shown_only_the_rows_its_story_names(harness, monkeypatch):  # noqa: F811
    from tests.test_delivery_flow import FakeIssues, green
    from tests.test_delivery_flow import story as delivery_story

    monkeypatch.setattr(
        FakeIssues,
        "get",
        lambda self, repo, n: {"body": {6: EPIC, 7: STORY}.get(n, "As a Sponsor…")},
    )
    child = delivery_story(7).model_copy(update={"parent": 6})
    _, _, _, _, calls, _ = harness(checks=[green()], cards=[child])
    context = calls["context"][0]
    assert "# What the epic decided that this story follows" in context
    assert "Postgres, own schema" in context and "CI only" in context
    assert "Counted where it merges" not in context
    assert "## What the epic decided" not in context


def test_the_code_reviewer_is_shown_the_rows_its_story_names(monkeypatch):
    from crew_org.flows import review as review_flow
    from tests.test_review import APPROVAL
    from tests.test_review import FakeIssues as ReviewIssues

    issues = ReviewIssues(
        [
            {
                "number": 88,
                "user": {"login": "crew[bot]"},
                "draft": False,
                "title": "t",
                "head": {"sha": "h", "ref": "feat/7-a-story"},
            }
        ]
    )
    issues.comments = lambda repo, n: []
    issues.get = lambda repo, n: {"body": {6: EPIC, 7: STORY}.get(n, "")}
    shown = {}

    def reviewer(title, diff, **kwargs):
        shown.update(kwargs)
        return APPROVAL

    monkeypatch.setattr(review_flow, "review_diff", reviewer)

    class Board:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    review_flow.review_open_pulls(
        issues,
        EventSink(None),
        repo="sprint-metrics",
        bot_login="reviewer[bot]",
        board=Board(),
        cards=[card()],
    )
    assert "Postgres, own schema" in shown["decisions"]
    assert "Counted where it merges" not in shown["decisions"]


def test_qa_is_shown_the_rows_its_story_names(monkeypatch, tmp_path):
    from crew_org.crews.qa_crew import QAVerdict
    from crew_org.flows import acceptance as flow_qa
    from tests.test_acceptance import _green, _QABoard, _QAIssues, _QAWorkspace, criterion

    seen = {}

    def fake_verify(story, *, test_output, test_code, decisions="", **_):
        seen["decisions"] = decisions
        return QAVerdict(summary="ok", accepted=True, criteria=[criterion()])

    class Issues_(_QAIssues):
        def get(self, repo, number):
            return {"body": {6: EPIC, 7: STORY}.get(number, "")}

    monkeypatch.setattr(flow_qa, "verify_story", fake_verify)
    monkeypatch.setattr(flow_qa.workspace, "check", lambda w, sandbox=None: _green())
    monkeypatch.setattr(flow_qa, "collect_tests", lambda w: "def test_x(): pass")
    flow_qa.run_qa(
        _QABoard(),
        Issues_(comments=[]),
        EventSink(None),
        _QAWorkspace(tmp_path),
        None,
        cards=[card(status="QAing")],
        repo="sprint-metrics",
    )
    assert "Postgres, own schema" in seen["decisions"]
    assert "Counted where it merges" not in seen["decisions"]
