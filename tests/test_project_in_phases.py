"""The phases read the project's record (#131).

Refinement and QA are shown it; delivery is shown it and held to it: a change
to a never-touch path, to the record itself, or to CI so that a design check
stops being enforced, is refused before anything is written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import FileEdit, FileWrite, Implementation, TextEdit
from crew_org.events import EventSink
from crew_org.flows import acceptance
from crew_org.flows.board_flow import RepoContext
from crew_org.git_ops import branch_name
from crew_org.project import (
    RECORD_PATH,
    ProjectRecordError,
    brief,
    is_protected,
    parse,
    protected,
    read_record,
)
from crew_org.tools import bounds, ci_guard
from tests.test_delivery_flow import IMPL, green, harness  # noqa: F401

RECORD = """
version: 2
intent:
  scope:
    purpose: Report delivery metrics for the crew's own board.
    in_scope: [cycle time]
    out_of_scope: ["An MCP server, for now"]
  release:
    deploys: false
  done:
    bar: Its acceptance criteria are met and CI is green.
    never_touch: [.github/workflows/board.yml, docs/decisions/, "*.lock"]
design:
  checks: ["uv run ruff check .", "uv run pytest -q"]
"""

TESTS_YML = """name: tests
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run pytest -q
"""


def project(tmp_path: Path, record: str = RECORD) -> Path:
    (tmp_path / ".crew").mkdir(parents=True, exist_ok=True)
    (tmp_path / RECORD_PATH).write_text(record)
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "tests.yml").write_text(TESTS_YML)
    return tmp_path


def text_edit(path: str, find: str, replace: str) -> Implementation:
    return Implementation(summary="s", text_edits=[TextEdit(path=path, find=find, replace=replace)])


# --- reading the record --------------------------------------------------------------


def test_a_project_without_a_record_reads_as_none(tmp_path):
    assert read_record(tmp_path) is None


def test_an_unusable_record_raises_rather_than_reading_as_none(tmp_path):
    project(tmp_path, record="version: 2\nintent: {}\n")
    with pytest.raises(ProjectRecordError, match="missing"):
        read_record(tmp_path)


def test_the_brief_carries_purpose_scope_release_done_and_bounds(tmp_path):
    text = brief(read_record(project(tmp_path)))
    for expected in (
        "**Purpose:** Report delivery metrics",
        "- cycle time",
        "- An MCP server, for now",
        "**A release here is:** the merge",
        "**Done means:** Its acceptance criteria are met",
        "- .github/workflows/board.yml",
        f"- {RECORD_PATH}",
        "`uv run pytest -q`",
    ):
        assert expected in text


# --- 1: refinement is shown purpose and scope ------------------------------------------


def test_refinement_is_shown_the_record_before_the_code(tmp_path):
    class Clone:
        def for_repo(self, repo):
            return self

        def current(self):
            return project(tmp_path)

    shown = RepoContext(Clone(), EventSink(None)).for_repo("sprint-metrics")
    assert shown.startswith("## What this project is for")
    assert shown.index("**Purpose:**") < shown.index("### Files and what they define")


def test_refinement_goes_on_without_a_record_it_cannot_read(tmp_path):
    notes = []
    sink = EventSink(None)
    sink.subscribe(notes.append)

    class Clone:
        def for_repo(self, repo):
            return self

        def current(self):
            return project(tmp_path, record="not: [a record")

    shown = RepoContext(Clone(), sink).for_repo("sprint-metrics")
    assert "### Files and what they define" in shown and "**Purpose:**" not in shown
    assert any("without its record" in n.summary for n in notes)


# --- 2: delivery and QA are shown done and what not to touch ------------------------------


def record_in_worktree(tmp_path: Path, record: str = RECORD) -> None:
    project(tmp_path / branch_name(6, "Show metric 6").replace("/", "__"), record)


def test_the_developer_is_shown_the_record(harness, tmp_path):  # noqa: F811
    record_in_worktree(tmp_path)
    _, _, _, _, calls, _ = harness(checks=[green()])
    assert calls["context"][0].startswith("## What this project is for")
    assert "**Done means:**" in calls["context"][0]


def test_a_record_that_cannot_be_read_stops_the_card(harness, tmp_path):  # noqa: F811
    record_in_worktree(tmp_path, record="version: 2\nintent: {}\n")
    result, _, _, _, calls, _ = harness(checks=[green()])
    assert calls["implement"] == 0
    assert "the project's record can't be read" in result.blocked[0].blocked_reason


def test_qa_is_shown_the_record(tmp_path):
    assert acceptance._project_brief(project(tmp_path)).startswith("## What this project is for")
    assert acceptance._project_brief(tmp_path / "nothing") == ""


# --- 3: what a release is comes from the record ----------------------------------------------


def test_what_a_release_is_comes_from_the_record():
    assert parse(RECORD).release_is == "the merge"
    deployed = RECORD.replace("deploys: false", "deploys: true\n    where: a version tag")
    assert "**A release here is:** a deployment, to a version tag" in brief(parse(deployed))


# --- 4: never-touch paths, and the record itself, are refused -----------------------------------


@pytest.mark.parametrize(
    "path, rule",
    [
        (".github/workflows/board.yml", ".github/workflows/board.yml"),
        ("docs/decisions/0001.md", "docs/decisions/"),
        ("uv.lock", "*.lock"),
        (RECORD_PATH, RECORD_PATH),
    ],
)
def test_a_protected_path_names_its_rule(path, rule):
    assert is_protected(path, protected(parse(RECORD))) == rule


def test_an_unprotected_path_is_free():
    assert is_protected("src/metrics.py", protected(parse(RECORD))) is None
    assert is_protected(".github/workflows/tests.yml", protected(parse(RECORD))) is None


@pytest.mark.parametrize(
    "implementation",
    [
        text_edit(".github/workflows/board.yml", "a", "b"),
        Implementation(
            summary="s", new_files=[FileWrite(path="docs/decisions/new.md", content="x")]
        ),
        Implementation(
            summary="s",
            edits=[
                FileEdit(
                    path="docs/decisions/tool.py",
                    operation="add",
                    target="f",
                    source="def f(): ...",
                )
            ],
        ),
        text_edit(RECORD_PATH, "bar:", "bar: anything,"),
    ],
)
def test_a_change_to_a_protected_path_is_refused(tmp_path, implementation):
    (reason,) = bounds.out_of_bounds(project(tmp_path), implementation, parse(RECORD))
    assert "is protected by the project's record" in reason


def test_without_a_record_only_the_record_itself_is_protected(tmp_path):
    assert bounds.out_of_bounds(tmp_path, text_edit("anything.md", "a", "b"), None) == []
    assert bounds.out_of_bounds(tmp_path, text_edit(RECORD_PATH, "a", "b"), None)


def test_delivery_refuses_a_protected_change_before_writing_and_says_why(harness, tmp_path):  # noqa: F811
    record_in_worktree(tmp_path)
    board_yml = text_edit(".github/workflows/board.yml", "a", "b")
    applied = []

    result, _, _, _, calls, _ = harness(
        checks=[green()],
        implement=lambda n: board_yml if n == 1 else IMPL,
        apply=lambda w, impl: applied.append(impl) or [],
    )

    assert applied == [IMPL], "the protected change was never applied"
    assert ".github/workflows/board.yml" in calls["feedback"][1]
    assert len(result.delivered) == 1


# --- 5: CI may change, never so a design check stops being enforced -------------------------------


@pytest.mark.parametrize(
    "find, replace, why",
    [
        ("      - run: uv run pytest -q\n", "", "dropped"),
        ("uv run pytest -q", "uv run pytest -q || true", "swallowed"),
        (
            "      - run: uv run pytest -q",
            "      - run: uv run pytest -q\n        continue-on-error: true",
            "step may fail",
        ),
        (
            "    runs-on: ubuntu-latest",
            "    runs-on: ubuntu-latest\n    continue-on-error: true",
            "job may fail",
        ),
    ],
)
def test_a_workflow_that_stops_enforcing_a_check_is_refused(tmp_path, find, replace, why):
    change = text_edit(".github/workflows/tests.yml", find, replace)
    reasons = bounds.out_of_bounds(project(tmp_path), change, parse(RECORD))
    assert any("no longer enforce `uv run pytest -q`" in r for r in reasons), why


def test_a_workflow_may_change_while_every_check_still_runs(tmp_path):
    change = text_edit(
        ".github/workflows/tests.yml",
        "      - run: uv sync\n",
        "      - run: uv sync --extra dev\n",
    )
    assert bounds.out_of_bounds(project(tmp_path), change, parse(RECORD)) == []


def test_a_check_may_move_to_another_workflow(tmp_path):
    root = project(tmp_path)
    moved = Implementation(
        summary="s",
        text_edits=[
            TextEdit(
                path=".github/workflows/tests.yml",
                find="      - run: uv run pytest -q\n",
                replace="",
            )
        ],
        new_files=[
            FileWrite(
                path=".github/workflows/suite.yml",
                content="jobs:\n  suite:\n    steps:\n      - run: uv run pytest -q\n",
            )
        ],
    )
    assert bounds.out_of_bounds(root, moved, parse(RECORD)) == []


def test_a_check_ci_never_ran_is_not_this_changes_doing():
    before = {"a.yml": "jobs:\n  j:\n    steps:\n      - run: echo hi\n"}
    assert ci_guard.weakened(["uv run pytest -q"], before, {}) == []


def test_without_design_checks_workflows_are_not_judged(tmp_path):
    no_design = RECORD.split("design:")[0]
    change = text_edit(".github/workflows/tests.yml", "      - run: uv run pytest -q\n", "")
    assert bounds.out_of_bounds(project(tmp_path), change, parse(no_design)) == []
