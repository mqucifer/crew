"""The project record: `.crew/project.yaml` (#111, story #129)."""

from __future__ import annotations

from datetime import date

import pytest

from crew_org.project import (
    ProjectRecord,
    ProjectRecordError,
    parse,
    render,
)

SPRINT_METRICS = """
intent:
  scope:
    purpose: Report how the crew is performing, for the Sponsor.
    in_scope: [cycle time, lead time, throughput]
    out_of_scope: [a web UI]
  release:
    deploys: false
  done:
    bar: Its tests and lint pass, and CI can't be weakened to make them.
    never_touch: [.github/workflows/board.yml]
"""


def problems(text: str) -> list[str]:
    with pytest.raises(ProjectRecordError) as raised:
        parse(text)
    return raised.value.problems


def test_a_complete_record_loads():
    """Criterion 1."""
    record = parse(SPRINT_METRICS)
    assert isinstance(record, ProjectRecord)
    assert record.intent.scope.purpose.startswith("Report how the crew")
    assert record.intent.done.bar.startswith("Its tests and lint pass")
    assert record.intent.done.never_touch == [".github/workflows/board.yml"]
    assert record.design is None and record.intent.priority is None


def test_every_missing_answer_is_named_at_once():
    """Criterion 2: not one validation error per attempt."""
    assert problems("") == [
        "missing: what the project is for (purpose)",
        "missing: whether it deploys, or the merge is the release",
        "missing: what must be true for a change to count as done",
    ]


def test_an_empty_answer_is_a_missing_one():
    text = SPRINT_METRICS.replace(
        "purpose: Report how the crew is performing, for the Sponsor.", "purpose: '  '"
    ).replace("bar: Its tests and lint pass, and CI can't be weakened to make them.", "bar: ''")
    assert problems(text) == [
        "missing: what the project is for (purpose)",
        "missing: what must be true for a change to count as done",
    ]


def test_a_project_that_does_not_deploy_releases_on_merge():
    """Criterion 3: stated rather than implied. Answers #90 for this project."""
    assert parse(SPRINT_METRICS).release_is == "the merge"


def test_a_project_that_deploys_must_say_where():
    text = SPRINT_METRICS.replace("deploys: false", "deploys: true")
    assert problems(text) == ["missing: where it is deployed, since it deploys"]
    deployed = parse(
        text.replace("deploys: true", "deploys: true\n    where: staging on the Spark")
    )
    assert deployed.release_is == "a deployment"


def test_the_crew_has_its_own_section_for_what_it_learns():
    """Criterion 4: #112's entries sit beside the Sponsor's intent."""
    text = SPRINT_METRICS + (
        "learned:\n"
        "  - fact: Stories must name the module a metric belongs in.\n"
        "    evidence: mqucifer/crew#123\n"
        "    found: 2026-09-24\n"
    )
    record = parse(text)
    assert record.learned[0].found == date(2026, 9, 24)
    assert record.intent.scope.purpose, "learning adds to the record, it does not touch intent"


def test_a_written_record_reads_back_the_same():
    record = parse(SPRINT_METRICS)
    assert parse(render(record)) == record
    assert render(record).startswith("# The project's onboarding record")


def test_what_is_not_a_record_says_so():
    assert "not valid YAML" in problems("intent: [unclosed")[0]
    assert "must be a mapping" in problems("- a list")[0]


def test_a_wrong_type_names_where_it_is():
    text = SPRINT_METRICS.replace("deploys: false", "deploys: sometimes")
    assert any(p.startswith("intent.release.deploys:") for p in problems(text))


# --- #143: the Sponsor's intent and the Architect's design ---------------------------

DESIGN = """
design:
  language: Python 3.12
  sandbox: uv
  checks: ["uv run pytest -q", "uv run ruff check ."]
  release_how: Tag vX.Y.Z on main after the release's work merges.
"""


def test_the_architects_choices_have_their_own_section():
    record = parse(SPRINT_METRICS + DESIGN)
    assert record.design.checks == ["uv run pytest -q", "uv run ruff check ."]
    assert record.design.release_how.startswith("Tag vX.Y.Z")
    assert record.checks == record.design.checks


def test_a_project_is_complete_without_a_design():
    """Criterion 1: check commands are no longer a Sponsor answer."""
    assert parse(SPRINT_METRICS).intent.done.bar


def test_without_a_design_no_checks_are_defined_rather_than_guessed():
    """Criterion 3."""
    with pytest.raises(ProjectRecordError, match="no checks are defined"):
        _ = parse(SPRINT_METRICS).checks
    with pytest.raises(ProjectRecordError, match="no checks are defined"):
        _ = parse(SPRINT_METRICS + "design:\n  language: Python\n").checks


def test_the_sponsor_can_add_guidelines():
    text = SPRINT_METRICS + "  guidelines: [No network calls at import time.]\n"
    assert parse(text).intent.guidelines == ["No network calls at import time."]


@pytest.mark.parametrize(
    "where",
    [
        "intent.done.checks",  # a version 1 field, now the Architect's
        "intent.build",
        "intent.release.how",
    ],
)
def test_a_field_in_the_wrong_place_is_named_not_dropped(where):
    section, *rest = where.split(".")[1:]
    text = SPRINT_METRICS.replace(
        {"done": "  done:\n", "build": "  done:\n", "release": "  release:\n"}[section],
        {
            "done": "  done:\n    checks: [pytest]\n",
            "build": "  build:\n    language: Python\n  done:\n",
            "release": "  release:\n    how: tag it\n",
        }[section],
    )
    assert any(p.startswith(where) and "Extra inputs" in p for p in problems(text))


def test_a_version_1_record_is_refused_and_told_why():
    text = "version: 1\n" + SPRINT_METRICS
    (problem,) = problems(text)
    assert "format version 1" in problem and "`design`" in problem
