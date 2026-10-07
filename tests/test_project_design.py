"""`crew design`: the Architect proposes a project's design section (#144).

The Architect and the Code Reviewer are scripted: what is tested is when the
flow accepts a proposal, when it refuses one, and what the pull request says.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import Implementation, TextEdit
from crew_org.crews.design_crew import (
    Change,
    Choice,
    Conflict,
    DesignProposal,
    DesignReview,
    PartChoice,
)
from crew_org.flows.design import (
    design,
    needs_an_image,
    open_design_pr,
    to_design,
    undeclared,
    workflows,
)
from crew_org.project import RECORD_PATH, parse, render
from crew_org.tools import bounds
from tests.test_onboard import FakeIssues, FakeWorkspace

RECORD = parse(
    """
version: 2
intent:
  scope:
    purpose: Report delivery metrics for the crew's own board.
  release:
    deploys: false
  done:
    bar: Its acceptance criteria are met and CI is green.
  guidelines: [No network calls at import time.]
"""
)

CI = {
    ".github/workflows/tests.yml": (
        "jobs:\n  tests:\n    steps:\n"
        "      - run: uv run ruff check .\n      - run: uv run pytest -q\n"
    )
}


def proposal(*checks: str, changes=(), sandbox="uv with Python 3.12") -> DesignProposal:
    return DesignProposal(
        language=Choice(value="Python 3.12", basis=".python-version"),
        sandbox=Choice(value=sandbox, basis="tests.yml uses setup-uv"),
        checks=[Choice(value=c, basis="tests.yml") for c in checks],
        release_how=Choice(value="Tag vX.Y.Z on main", basis="the record: a version tag"),
        changes=list(changes),
        summary="Python 3.12 under uv; ruff and pytest enforce done.",
    )


class Architect:
    def __init__(self, *proposals: DesignProposal) -> None:
        self.proposals, self.calls = list(proposals), []

    def __call__(self, **context) -> DesignProposal:
        self.calls.append(context)
        return self.proposals.pop(0)


class Reviewer:
    def __init__(self, *reviews: DesignReview) -> None:
        self.reviews, self.calls = list(reviews), []

    def __call__(self, **context) -> DesignReview:
        self.calls.append(context)
        return self.reviews.pop(0) if self.reviews else DesignReview()


def run(architect, reviewer=None, *, ci=CI, record=RECORD, reason=""):
    return design(
        record,
        repository="code",
        ci=ci,
        propose_design=architect,
        review_design=reviewer or Reviewer(),
        reason=reason,
    )


# --- 1: a proposal, with its basis ----------------------------------------------------------


def test_a_design_that_records_what_exists_is_accepted():
    ended = run(Architect(proposal("uv run ruff check .", "uv run pytest -q")))

    assert ended.record.design.checks == ["uv run ruff check .", "uv run pytest -q"]
    assert ended.record.design.release_how == "Tag vX.Y.Z on main"
    assert ended.record.intent == RECORD.intent, "the Sponsor's intent is untouched"
    assert parse(render(ended.record)) == ended.record


def test_the_architect_is_shown_the_intent_and_the_code():
    architect = Architect(proposal("uv run pytest -q"))
    run(architect)
    shown = architect.calls[0]
    assert "**Purpose:** Report delivery metrics" in shown["project"]
    assert "No network calls at import time." in shown["project"]
    assert shown["repository"] == "code"


def test_a_design_names_at_least_one_check():
    with pytest.raises(ValidationError, match="at least one check"):
        proposal()


def test_the_pull_request_carries_each_choice_its_basis_and_the_changes(tmp_path: Path):
    ended = run(Architect(proposal("uv run ruff check .", "uv run pytest -q")))
    issues = FakeIssues()

    url = open_design_pr(FakeWorkspace(tmp_path), issues, "sprint-metrics", ended, base="main")

    assert url.endswith("/pull/8")
    body = issues.pulls[0]["body"]
    assert "Closes #7" in body and issues.pulls[0]["head"] == "chore/7-project-design"
    assert "**Check:** uv run pytest -q" in body and "_based on: tests.yml_" in body
    assert "None. This records what the project already does." in body
    written = parse((tmp_path / RECORD_PATH).read_text())
    assert written.design.checks == ["uv run ruff check .", "uv run pytest -q"]


# --- 2: a design that contradicts a guideline is refused before it is opened --------------------


def test_a_conflict_is_retried_with_the_guideline_named_then_accepted():
    reviewer = Reviewer(
        DesignReview(
            conflicts=[
                Conflict(
                    guideline="§19 rule 1",
                    choice="sandbox: none, tests run on the host",
                    why="generated code always runs sandboxed",
                )
            ]
        )
    )
    architect = Architect(
        proposal("uv run pytest -q", sandbox="none, tests run on the host"),
        proposal("uv run pytest -q"),
    )

    ended = run(architect, reviewer)

    assert "§19 rule 1" in architect.calls[1]["feedback"]
    assert ended.record is not None and ended.attempts == 2


def test_a_design_still_in_conflict_is_refused_with_no_record():
    conflict = DesignReview(
        conflicts=[Conflict(guideline="the project's guideline", choice="x", why="y")]
    )
    ended = run(
        Architect(proposal("uv run pytest -q"), proposal("uv run pytest -q")),
        Reviewer(conflict, conflict),
    )
    assert ended.record is None
    assert ended.refused == ["x contradicts the project's guideline: y"]


# --- 3: an existing toolchain is recorded; a difference is stated as a change


def test_a_check_ci_does_not_run_must_be_stated_as_a_change():
    reviewer = Reviewer()
    ended = run(
        Architect(
            proposal("uv run pytest -q", "uv run mypy src"),
            proposal("uv run pytest -q", "uv run mypy src"),
        ),
        reviewer,
    )
    assert ended.record is None
    assert "`uv run mypy src` isn't a check CI runs today" in ended.refused[0]
    assert reviewer.calls == [], "refused mechanically, before any review"


# crew#513: crew-presentation's first proposal was refused mechanically, its second by
# the review, and the run ended without the Architect seeing the review's findings.
MYPY = Change(what="add `uv run mypy src` to the checks", was="no type check", why="types")
CONFLICT = DesignReview(conflicts=[Conflict(guideline="§19 rule 1", choice="x", why="y")])


def test_a_mechanical_refusal_leaves_the_review_its_own_retry():
    architect = Architect(
        proposal("uv run pytest -q", "uv run mypy src"),
        proposal("uv run pytest -q", "uv run mypy src", changes=[MYPY]),
        proposal("uv run pytest -q", "uv run mypy src", changes=[MYPY]),
    )
    reviewer = Reviewer(CONFLICT)

    ended = run(architect, reviewer)

    assert "§19 rule 1" in architect.calls[2]["feedback"], "shown the review's findings"
    assert ended.record is not None and ended.attempts == 3


def test_each_check_still_ends_the_run_on_its_second_refusal():
    unstated = proposal("uv run pytest -q", "uv run mypy src")
    stated = proposal("uv run pytest -q", "uv run mypy src", changes=[MYPY])

    ended = run(Architect(unstated, stated, unstated), Reviewer(CONFLICT))

    assert ended.record is None and ended.attempts == 3
    assert "`uv run mypy src` isn't a check CI runs today" in ended.refused[0]


def test_a_stated_change_is_accepted_and_shown_in_the_pull_request(tmp_path: Path):
    declared = Change(
        what="add `uv run mypy src` to the checks", was="no type check", why="catch type drift"
    )
    ended = run(Architect(proposal("uv run pytest -q", "uv run mypy src", changes=[declared])))
    issues = FakeIssues()

    open_design_pr(FakeWorkspace(tmp_path), issues, "r", ended, base="main")

    assert ended.record.design.checks == ["uv run pytest -q", "uv run mypy src"]
    assert "**add `uv run mypy src` to the checks**: was no type check." in issues.pulls[0]["body"]


def test_a_project_without_ci_has_nothing_existing_to_keep():
    assert undeclared(proposal("uv run pytest -q"), {}) == []


# crew#516: crew-presentation had only the board automation onboarding installs, and
# was held to a toolchain it didn't have.
def test_the_board_automation_is_not_ci(tmp_path: Path):
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "board.yml").write_text("on: issues\n")
    assert workflows(tmp_path) == {}

    (folder / "tests.yml").write_text(CI[".github/workflows/tests.yml"])
    assert list(workflows(tmp_path)) == [".github/workflows/tests.yml"]


# crew#516: a crew-presentation proposal named `npx playwright test` and a JavaScript
# part, and no image: the crew's default sandbox has no Node.
def node_design(*checks: str, parts=(), image=None) -> DesignProposal:
    design = proposal(*checks)
    design.parts = [PartChoice(path=p, language="javascript", basis="b") for p in parts]
    design.sandbox_image = Choice(value=image, basis="b") if image else None
    return design


def test_a_check_run_by_node_needs_an_image():
    (reason,) = needs_an_image(node_design("uv run pytest -q", "npx playwright test"))
    assert "the check `npx playwright test`" in reason and "sandbox_image" in reason


def test_a_part_that_isnt_python_needs_an_image():
    (reason,) = needs_an_image(node_design("uv run pytest -q", parts=["static/"]))
    assert "the javascript part `static/`" in reason


def test_a_named_image_or_a_python_design_needs_nothing_more():
    named = node_design("npx playwright test", parts=["static/"], image="node:22")
    assert needs_an_image(named) == []
    assert needs_an_image(proposal("uv run pytest -q")) == []


def test_a_design_needing_node_with_no_image_is_refused_before_review():
    reviewer = Reviewer()
    needs_node = node_design("npx playwright test")
    ended = run(Architect(needs_node, needs_node), reviewer, ci={})
    assert ended.record is None and "only Python and uv" in ended.refused[0]
    assert reviewer.calls == []


# --- 4: a later change comes through the Architect, never delivery


def test_a_revision_is_shown_the_current_design_and_why():
    record = RECORD.model_copy(update={"design": to_design(proposal("uv run pytest -q"))})
    architect = Architect(proposal("uv run pytest -q"))

    run(architect, record=record, reason="the tool now needs a type check")

    assert "uv run pytest -q" in architect.calls[0]["current"]
    assert architect.calls[0]["reason"] == "the tool now needs a type check"
    # The design comes separately, as what is being revisited, not as settled intent.
    assert "The Architect's design" not in architect.calls[0]["project"]


def test_delivery_cannot_change_the_design(tmp_path: Path):
    record = RECORD.model_copy(update={"design": to_design(proposal("uv run pytest -q"))})
    change = Implementation(
        summary="s",
        text_edits=[TextEdit(path=RECORD_PATH, find="uv run pytest -q", replace="true")],
    )
    (reason,) = bounds.out_of_bounds(tmp_path, change, record)
    assert "the record itself" in reason


# --- where the user docs live (#306) --------------------------------------
#
# Sprint 8: `docs` was in the record and not in what the Architect proposes,
# so every sprint-metrics documentation story wrote to one README.


def test_the_architect_can_say_where_the_user_docs_live():
    proposal = DesignProposal(
        checks=[Choice(value="uv run pytest -q", basis="tests.yml")],
        docs=Choice(
            value="README.md is the front page; docs/usage.md covers the flags, "
            "docs/metrics.md the six metrics",
            basis="parallel docs stories collided in the README",
        ),
        summary="Docs split by topic.",
    )
    assert to_design(proposal).docs == ["README.md", "docs/usage.md", "docs/metrics.md"]


def test_no_docs_answer_leaves_the_readme_default():
    proposal = DesignProposal(
        checks=[Choice(value="uv run pytest -q", basis="tests.yml")], summary="x"
    )
    assert to_design(proposal).docs == []


def test_the_docs_choice_carries_the_parallel_stories_goal():
    """#306's first wording asked only which files. The Architect split the one
    line that had collided and left two shared files."""
    description = DesignProposal.model_fields["docs"].description
    assert "the way `structure` divides the code" in description
    assert "parallel stories land in different files" in description
