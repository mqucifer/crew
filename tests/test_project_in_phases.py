"""The phases read the project's record (#131).

Refinement and QA are shown it; delivery is shown it and held to it: a change
to a never-touch path, to the record itself, or to CI so that a design check
stops being enforced, sends the branch back from the gates at `done` (crew#778).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.flows import acceptance
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

RECORD = """
version: 3
intent:
  scope:
    serves: [{who: the Sponsor, needs: [how the crew performs]}]
    purpose: Report delivery metrics for the crew's own board.
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


def judged(changed: dict[str, str], record: str | None = RECORD) -> list[str]:
    """What the gates at `done` find in a branch that leaves `changed` as given.

    `.github/workflows/tests.yml` is merged as TESTS_YML.
    """
    before = {".github/workflows/tests.yml": TESTS_YML}
    after = dict(before)
    after.update({path: text for path, text in changed.items() if ci_guard.is_workflow(path)})
    return bounds.judged(
        list(changed),
        parse(record) if record is not None else None,
        workflows=(before, after),
        dockerfiles={},
    )


def edited(find: str, replace: str) -> dict[str, str]:
    """TESTS_YML on the branch with `find` replaced."""
    return {".github/workflows/tests.yml": TESTS_YML.replace(find, replace, 1)}


# --- reading the record --------------------------------------------------------------


def test_a_project_without_a_record_reads_as_none(tmp_path):
    assert read_record(tmp_path) is None


def test_an_unusable_record_raises_rather_than_reading_as_none(tmp_path):
    project(tmp_path, record="version: 3\nintent: {}\n")
    with pytest.raises(ProjectRecordError, match="missing"):
        read_record(tmp_path)


def test_the_brief_carries_purpose_scope_release_done_and_bounds(tmp_path):
    text = brief(read_record(project(tmp_path)))
    for expected in (
        "**Purpose:** Report delivery metrics",
        "- the Sponsor:\n  - how the crew performs",
        "- An MCP server, for now",
        "**A release here is:** the merge",
        "**Done means:** Its acceptance criteria are met",
        "- .github/workflows/board.yml",
        f"- {RECORD_PATH}",
        "`uv run pytest -q`",
    ):
        assert expected in text


# --- 1: refinement is shown purpose and scope ------------------------------------------


def test_refinement_is_shown_the_record_as_the_whole_projects(tmp_path):
    """A refinement step's start holds the record, said to be the project's, not the
    step's scope; the code is read in the session (crew#807)."""
    from crew_org.session import steps

    shown = steps.project_section(project(tmp_path))
    assert shown.startswith("## The project\n\nFrom `.crew/project.yaml`, the whole project's")
    assert "not this step's scope" in shown and "**Purpose:**" in shown
    assert "Work here is judged against them" not in shown


def test_refinement_goes_on_without_a_record_it_cannot_read(tmp_path):
    from crew_org.session import steps

    shown = steps.project_section(project(tmp_path, record="not: [a record"))
    assert shown == "## The project\n\n(no record)"


# --- 2: delivery and QA are shown done and what not to touch ------------------------------


def test_qa_is_shown_the_record(tmp_path):
    """Its session's start says it, as every step's does (crew#895); QA reads it first."""
    from crew_org.session import steps

    acceptance._read_project(project(tmp_path))
    assert "**Purpose:**" in steps.project_section(project(tmp_path))
    acceptance._read_project(tmp_path / "nothing")


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
    "path", [".github/workflows/board.yml", "docs/decisions/new.md", "docs/decisions/tool.py"]
)
def test_a_change_to_a_protected_path_is_refused(path):
    (reason,) = [r for r in judged({path: "x\n"}) if "protected" in r]
    assert "is protected by the project's record" in reason


def test_a_change_to_the_record_itself_is_refused():
    (reason,) = judged({RECORD_PATH: RECORD.replace("bar:", "bar: anything,")})
    assert "the record itself" in reason


def test_without_a_record_only_the_record_itself_is_protected():
    assert judged({"anything.md": "b"}, record=None) == []
    assert judged({RECORD_PATH: "b"}, record=None)


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
def test_a_workflow_that_stops_enforcing_a_check_is_refused(find, replace, why):
    reasons = judged(edited(find, replace))
    assert any("no longer enforce `uv run pytest -q`" in r for r in reasons), why


def test_a_workflow_may_change_while_every_check_still_runs():
    assert judged(edited("      - run: uv sync\n", "      - run: uv sync --extra dev\n")) == []


def test_a_check_may_move_to_another_workflow():
    moved = edited("      - run: uv run pytest -q\n", "")
    moved[".github/workflows/suite.yml"] = (
        "jobs:\n  suite:\n    steps:\n      - run: uv run pytest -q\n"
    )
    assert judged(moved) == []


def test_a_check_ci_never_ran_is_not_this_changes_doing():
    before = {"a.yml": "jobs:\n  j:\n    steps:\n      - run: echo hi\n"}
    assert ci_guard.weakened(["uv run pytest -q"], before, {}) == []


def test_without_design_checks_workflows_are_not_judged():
    no_design = RECORD.split("design:")[0]
    assert judged(edited("      - run: uv run pytest -q\n", ""), record=no_design) == []
