"""A design can say what only CI proves, and a design that changes nothing still files its work.

sprint-metrics' revision for §19.8 (2026-09-29, #335) hit both. The design had
nowhere to put "CI builds the image and runs it as deployed", so the Architect
wrote it into `checks`, the commands the sandbox runs, and was refused twice.
In the run before, it left the record as it was and declared five changes that
needed work; GitHub refused a pull request with no commits, and the work went
with it.
"""

from __future__ import annotations

from crew_org.crews.design_crew import Change, Choice
from crew_org.flows.design import to_design, undeclared
from crew_org.flows.review import release_brief
from crew_org.project import parse, render
from tests.test_revisit import (  # noqa: F401 - fixtures
    REPO,
    SPLIT,
    SPRINT6,
    Architect,
    Issues,
    clone,
    proposal,
    revisit,
    sprint6,
)

SMOKE = "CI's docker-smoke job starts the image as its docs say and reaches /metrics through -p"
ENTRYPOINT = Change(
    what="Give the image an ENTRYPOINT",
    was="docker-smoke repeats the program's name",
    why="constitution §19.8",
    needs_work=True,
    work="Add ENTRYPOINT to the Dockerfile and run docker-smoke without the program's name",
)


def with_proof(*changes, proofs=(SMOKE,)):
    p = proposal(structure=None, changes=list(changes))
    p.ci_checks = [Choice(value=v, basis="§19.8") for v in proofs]
    return p


# --- what only CI proves -------------------------------------------------------------------


def test_a_ci_proof_is_recorded_apart_from_the_commands_the_sandbox_runs():
    design = to_design(with_proof(ENTRYPOINT))
    assert design.ci_checks == [SMOKE]
    assert design.checks == ["uv run pytest -q"], "the sandbox never runs the proof as a command"


def test_it_survives_the_record_round_trip():
    record = parse(
        "version: 2\nintent:\n  scope:\n    purpose: metrics\n  release:\n    deploys: false\n"
        f"  done:\n    bar: CI passes\ndesign:\n  checks: [uv run pytest -q]\n"
        f'  ci_checks: ["{SMOKE}"]\n'
    )
    assert parse(render(record)).design.ci_checks == [SMOKE]


def test_a_new_ci_proof_needs_the_work_that_makes_ci_prove_it():
    [why] = undeclared(with_proof(), ci={})
    assert "new CI proof" in why, "otherwise the record claims CI proves something it doesn't"
    assert undeclared(with_proof(ENTRYPOINT), ci={}) == []


def test_a_proof_the_record_already_has_needs_no_change():
    assert undeclared(with_proof(), ci={}, ci_checks_now=[SMOKE]) == []


def test_the_design_pull_request_shows_what_ci_proves(clone, sprint6):  # noqa: F811
    issues = Issues()
    revisit(issues, clone, SPRINT6, Architect(with_proof(ENTRYPOINT)), events_dir=sprint6)
    (pull,) = issues.pulls
    assert f"**CI proves:** {SMOKE}" in pull["body"]


def test_the_deploy_review_is_told_what_ci_proves():
    class Records:
        def file_at(self, repo, path, ref):
            return (
                "version: 2\nintent:\n  scope:\n    purpose: metrics\n  release:\n"
                "    publishes: true\n    where: GHCR\n  done:\n    bar: CI passes\n"
                f'design:\n  checks: [uv run pytest -q]\n  ci_checks: ["{SMOKE}"]\n'
            )

    assert f"- CI proves:\n  - {SMOKE}" in release_brief(Records(), REPO, "c0ffee")


# --- a design that leaves the record as it was ----------------------------------------------


def test_work_declared_against_an_unchanged_record_is_filed_without_a_pull_request(clone, sprint6):  # noqa: F811
    issues = Issues()
    result = revisit(
        issues,
        clone,
        SPRINT6,
        Architect(proposal(structure=None, changes=[SPLIT])),
        events_dir=sprint6,
    )

    assert issues.pulls == [], "a pull request with no commits is refused"
    epic, look = issues.created
    assert epic["title"] == SPLIT.work and "the design already says this" in epic["body"]
    assert "already says it" in look["title"] and f"#{epic['number']}" in look["body"]
    assert result.epics == [(REPO, epic["number"])]


def test_work_already_open_as_an_epic_is_not_filed_twice(clone, sprint6):  # noqa: F811
    class Filed(Issues):
        def open_issues(self, repo):
            return [{"number": 150, "title": SPLIT.work}]

    issues = Filed()
    result = revisit(
        issues,
        clone,
        SPRINT6,
        Architect(proposal(structure=None, changes=[SPLIT])),
        events_dir=sprint6,
    )

    [look] = issues.created
    assert "already filed" in look["body"] and result.epics == []
