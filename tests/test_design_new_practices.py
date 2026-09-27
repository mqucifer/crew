"""An Architect's new practice reaches the Sponsor as a declared change (#154).

sprint-metrics' first design (PR #82) said "The version in pyproject.toml is
updated to match the tag before it is cut", a practice the project didn't
have, under "Changes: None". Only a check command was ever held to being
declared; the rest could arrive looking like a record of existing fact.
Declared changes already reach the PR and become technical epics once it
merges (#192); this makes an undeclared one come back to be declared.
"""

from __future__ import annotations

import contextlib

from crew_org.crews import design_crew
from crew_org.crews.design_crew import Change, DesignReview, Undeclared
from tests.test_project_design import Architect, proposal, run

CHECKS = ("uv run ruff check .", "uv run pytest -q")
BUMP = Undeclared(
    choice="The version in pyproject.toml is updated to match the tag before it is cut",
    today="version is 0.0.0 and nothing has been tagged",
)
DECLARED = Change(
    what="Update the version in pyproject.toml to match each tag",
    was="version 0.0.0, never tagged",
    why="a released version should say what it is",
    needs_work=True,
    work="Set pyproject.toml's version to the tag's before each release is cut",
)


class Reviewer:
    def __init__(self, *reviews: DesignReview) -> None:
        self.reviews, self.calls = list(reviews), []

    def __call__(self, **context) -> DesignReview:
        self.calls.append(context)
        return self.reviews.pop(0) if self.reviews else DesignReview()


def test_the_reviewer_is_shown_the_code_and_the_declared_changes():
    reviewer = Reviewer()
    run(Architect(proposal(*CHECKS, changes=[DECLARED])), reviewer)
    (call,) = reviewer.calls
    assert call["repository"] == "code"
    assert "Update the version in pyproject.toml to match each tag" in call["changes"]


def test_an_undeclared_practice_goes_back_to_be_declared():
    """#154, criterion 1: the one-retry path a guideline conflict takes."""
    architect = Architect(proposal(*CHECKS), proposal(*CHECKS, changes=[DECLARED]))
    ended = run(architect, Reviewer(DesignReview(undeclared=[BUMP])))

    feedback = architect.calls[1]["feedback"]
    assert "is a new practice" in feedback and "version is 0.0.0" in feedback
    assert "List it in `changes`" in feedback
    assert ended.record is not None and ended.proposal.changes == [DECLARED]


def test_one_still_undeclared_is_refused():
    ended = run(
        Architect(proposal(*CHECKS), proposal(*CHECKS)),
        Reviewer(DesignReview(undeclared=[BUMP]), DesignReview(undeclared=[BUMP])),
    )
    assert ended.record is None
    assert any("is a new practice" in r for r in ended.refused)


def captured(monkeypatch, **kwargs) -> str:
    seen: dict = {}

    class Stop(Exception):
        pass

    def crew(**_):
        raise Stop

    monkeypatch.setattr(design_crew, "_role", lambda *a: None)
    monkeypatch.setattr(design_crew, "Task", lambda **k: seen.update(k))
    monkeypatch.setattr(design_crew, "Crew", crew)
    with contextlib.suppress(Stop):
        design_crew.review_design(project="p", design="d", **kwargs)
    return seen["description"]


def test_the_reviewer_is_asked_about_new_practices_only_for_a_projects_design(monkeypatch):
    project = captured(monkeypatch, repository="README says 0.0.0", changes="- none")
    assert "report, in `undeclared`, every choice that introduces a practice" in project
    assert "README says 0.0.0" in project
    note = captured(monkeypatch, stories="#145 its criteria")
    assert "undeclared" not in note, "a design note isn't a project's design"
