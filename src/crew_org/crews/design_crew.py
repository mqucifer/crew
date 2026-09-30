"""A project's design: the Architect proposes it, the Code Reviewer checks it (#144).

The Sponsor sets what a project is for and the rules it works within (the
record's `intent`, and the constitution's §19). The project's toolchain is the
Architect's to choose: language, dependencies, sandbox needs, the commands
that enforce done, and how a release happens.

Two roles, on purpose. Whether a design contradicts a guideline is a
judgement, and a proposer grading its own proposal is the weakest possible
check. The Code Reviewer is already bound to §19 and already judges work
against it, so it judges this too.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agent
from crew_org.permissions import load_agents


class Choice(BaseModel):
    """One design field, with what it is based on."""

    value: str = Field(description="The choice, as it should read in the record")
    basis: str = Field(
        description="What it is based on: a file, the CI, the record's intent, a guideline"
    )


class Change(BaseModel):
    """A choice that differs from what the project does today."""

    what: str = Field(description="What changes, e.g. 'add a type-check to the checks'")
    was: str = Field(description="What the project does today, or 'nothing'")
    why: str = Field(description="Why it should change")
    needs_work: bool = Field(
        False,
        description=(
            "True when making it so means changing the project's code, tests or "
            "configuration, not only this record"
        ),
    )
    # What becomes the technical epic (#309). `what` says what changes in the
    # record, which the design pull request itself does: an epic titled with
    # it was answered "already delivered".
    work: str | None = Field(
        None,
        description=(
            "When needs_work: the change to the project's code, tests, docs or "
            "configuration that makes it match this design, as one sentence of work "
            "to be done. Not the edit to this record"
        ),
    )

    @model_validator(mode="after")
    def _work_is_said(self) -> Change:
        if self.needs_work and not (self.work or "").strip():
            raise ValueError(
                f"'{self.what}' needs work on the project: say what that work is in "
                "`work`, as distinct from the edit to the design record"
            )
        return self


class PartChoice(BaseModel):
    """One part of a project, by path, in its own language (#404)."""

    path: str = Field(description="Where it lives, e.g. `site/`; empty for the whole project")
    language: str = Field(description="Its language, e.g. python, javascript")
    tests: list[str] = Field(
        default_factory=list,
        description="Its test files, as globs, e.g. `site/tests/**/*.spec.js`",
    )
    basis: str = Field(description="What it's based on: the files, the build, the record")


class DesignProposal(BaseModel):
    language: Choice | None = None
    dependencies: Choice | None = None
    sandbox: Choice | None = Field(
        None,
        description=(
            "What the crew's sandbox must provide to build and test this project, e.g. "
            "uv and Python 3.12. Not what the program itself does when it runs"
        ),
    )
    checks: list[Choice] = Field(
        description=(
            "The commands that enforce the definition of done, one per command. Each is "
            "run as it reads, in the crew's sandbox, on every change"
        )
    )
    # The sandbox itself (#403). Left empty, a project is built and tested in the
    # crew's Python image with uv, ruff and pytest, as sprint-metrics is.
    sandbox_image: Choice | None = Field(
        None,
        description=(
            "Only for a project that isn't Python built with uv: the container image its "
            "checks run in, with every tool they need: the reference alone, "
            "registry/name:tag. The crew pins it to the digest the tag names today. "
            "Empty for a Python project"
        ),
    )
    setup: Choice | None = Field(
        None,
        description=(
            "With `sandbox_image` only: the one command that installs dependencies, the "
            "only step given a network, e.g. `npm ci`"
        ),
    )
    autofix: list[Choice] = Field(
        default_factory=list,
        description="With `sandbox_image` only: formatters run before the checks",
    )
    # A project's parts, each read by its own language's tools (#404). Empty: the
    # whole project is Python's, as sprint-metrics is.
    parts: list[PartChoice] = Field(
        default_factory=list,
        description=(
            "Only for a project that isn't all Python: its parts by path, each with its "
            "language and test files. Empty for a Python project"
        ),
    )
    # What only CI can prove (#335): an image built and run as deployed isn't a
    # command the sandbox can run, and written into `checks` it would be run as one.
    ci_checks: list[Choice] = Field(
        default_factory=list,
        description=(
            "What CI proves on every pull request that the sandbox can't, such as an "
            "image built and run the way it's deployed (constitution §19.8). In words, "
            "one per proof, naming the CI job. Not commands: those are `checks`"
        ),
    )
    release_how: Choice | None = Field(None, description="How a release happens")
    structure: Choice | None = Field(
        None, description="How the code is divided into modules, and what each module owns"
    )
    # Where a user reads how to use it (#306). Unasked, every project's user
    # docs were the README, and parallel stories all wrote to one file. The goal
    # was left out of the first wording, and the Architect split only the one
    # line that had collided, leaving two shared files.
    docs: Choice | None = Field(
        None,
        description=(
            "The files a user reads to use the project, as paths separated by commas, "
            "and what each covers. Divided the way `structure` divides the code, so "
            "that parallel stories land in different files"
        ),
    )
    changes: list[Change] = Field(
        default_factory=list,
        description=(
            "Every choice that differs from what the project does today, with why. "
            "Empty when the design records what already exists."
        ),
    )
    summary: str = Field(description="The design in two or three sentences, for the PR")

    @field_validator("checks")
    @classmethod
    def _enforces_something(cls, value: list[Choice]) -> list[Choice]:
        if not value:
            raise ValueError(
                "a design names at least one check: without one, nothing enforces the "
                "definition of done"
            )
        return value


class Conflict(BaseModel):
    guideline: str = Field(description="The guideline it contradicts: §19 rule N, or the project's")
    choice: str = Field(description="The design choice that contradicts it")
    why: str


class Undeclared(BaseModel):
    """A practice the design introduces without saying so (#154)."""

    choice: str = Field(description="The design choice, as it reads")
    today: str = Field(description="What the project actually does today, from its code")


class DesignReview(BaseModel):
    conflicts: list[Conflict] = Field(
        default_factory=list,
        description="Every design choice that contradicts a guideline. Empty if none does.",
    )
    undeclared: list[Undeclared] = Field(
        default_factory=list,
        description=(
            "Every choice that introduces a practice the project doesn't follow today and "
            "that isn't among the declared changes. Empty if none does."
        ),
    )


def _role(key: str, section: str):
    spec = load_agents()[key]
    return build_agent(key, {**spec, **spec[section]})


def propose_design(
    *, project: str, repository: str, current: str = "", reason: str = "", feedback: str = ""
) -> DesignProposal:
    """The Architect's design for a project, from its record and its code.

    `current` is the design already in the record, when this is a revision, and
    `reason` is why it is being revisited. `feedback` is a previous proposal's
    refusal, when this is a retry.
    """
    architect = _role("architect", "project_design")
    revising = (
        f"## The design in the record now\n\n```yaml\n{current}\n```\n\n"
        f"It is being revisited because: {reason or '(no reason given)'}\n\n"
        "Change only what that reason calls for, and list each change.\n\n"
        if current
        else ""
    )
    refused = f"## Your last proposal was refused\n\n{feedback}\n\n" if feedback else ""
    task = Task(
        description=(
            f"{project}\n\n## The project's code\n\n{repository or '(the repository is empty)'}"
            f"\n\n{revising}{refused}"
            "Propose this project's design: language, dependencies, what the sandbox must "
            "provide, the commands that enforce its definition of done, how a release "
            "happens, how its code is divided into modules, and which files its user "
            "documentation lives in. The crew's tools differ by language: Python gets edits "
            "by definition name, a guard that refuses a change removing an existing "
            "definition, and a map of what every file defines; any other language gets "
            "whole-file and find-and-replace edits, tests found by title in its declared test "
            "files, and no such guard. Weigh that where the language is a choice. Give the "
            "basis of each choice. Where the project already has an answer (its CI, its "
            "lockfile, its "
            "build, its modules), record it; anything that differs from what it does today "
            "goes in `changes`, with why, and whether making it so needs work on the code."
        ),
        expected_output="The design, each choice with its basis, and any changes with why.",
        agent=architect,
        output_pydantic=DesignProposal,
    )
    crew = Crew(agents=[architect], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic


def review_design(
    *, project: str, design: str, stories: str = "", repository: str = "", changes: str = ""
) -> DesignReview:
    """The Code Reviewer's check of a proposed design against the guidelines.

    With `stories`, a design note is also checked against the criteria of the
    stories it directs (#258). Epic sprint-metrics#59's note told #145's test to
    leave out the assertion #145's criterion required, and nothing at design
    review could see it: the reviewer was shown the note, never the stories.

    With `repository`, a project's design is also checked for new practices it
    doesn't declare (#154). sprint-metrics' first design added "the version in
    pyproject.toml is updated to match the tag" under "Changes: None", and a
    new practice arrived looking like a record of existing fact. The role that
    proposes doesn't grade itself.
    """
    reviewer = _role("code_reviewer", "design_review")
    against = (
        "## The stories this note directs, with their acceptance criteria\n\n"
        f"{stories}\n\n"
        "The acceptance criteria are the product's decisions. Also report, as a conflict "
        "whose `guideline` names the story and quotes its criterion, every place the note "
        "has a story drop, weaken or defer what that story's own criterion requires.\n\n"
        if stories
        else ""
    )
    new = (
        f"## The project's code as it stands\n\n{repository}\n\n"
        f"## The changes the design declares\n\n{changes or '(none)'}\n\n"
        "Also report, in `undeclared`, every choice that introduces a practice the code "
        "above doesn't show and the declared changes don't list, with what the project "
        "does today. Recording what already exists is not a change.\n\n"
        if repository
        else ""
    )
    task = Task(
        description=(
            f"{project}\n\n## The proposed design\n\n```yaml\n{design}\n```\n\n"
            + against
            + new
            + "Check each choice against the crew-wide guidelines (§19, in your rules) and "
            "the project's own guidelines above. Report every choice that contradicts one, "
            "naming the guideline. A choice that is merely different from what you would "
            "pick is not a conflict."
        ),
        expected_output="Every conflict with a guideline, or none.",
        agent=reviewer,
        output_pydantic=DesignReview,
    )
    crew = Crew(agents=[reviewer], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic
