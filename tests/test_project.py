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
    checks: ["uv run pytest -q", "uv run ruff check ."]
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
    assert record.intent.done.checks == ["uv run pytest -q", "uv run ruff check ."]
    assert record.intent.done.never_touch == [".github/workflows/board.yml"]
    assert record.intent.build is None and record.intent.priority is None


def test_every_missing_answer_is_named_at_once():
    """Criterion 2: not one validation error per attempt."""
    assert problems("") == [
        "missing: what the project is for (purpose)",
        "missing: whether it deploys, or the merge is the release",
        "missing: the checks a change must pass to be done",
    ]


def test_an_empty_answer_is_a_missing_one():
    text = SPRINT_METRICS.replace(
        "purpose: Report how the crew is performing, for the Sponsor.", "purpose: '  '"
    ).replace('checks: ["uv run pytest -q", "uv run ruff check ."]', "checks: []")
    assert problems(text) == [
        "missing: what the project is for (purpose)",
        "missing: the checks a change must pass to be done",
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
