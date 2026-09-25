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
from pydantic import BaseModel, Field, field_validator

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
        description="The commands that enforce the definition of done, one per command"
    )
    release_how: Choice | None = Field(None, description="How a release happens")
    structure: Choice | None = Field(
        None, description="How the code is divided into modules, and what each module owns"
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


class DesignReview(BaseModel):
    conflicts: list[Conflict] = Field(
        default_factory=list,
        description="Every design choice that contradicts a guideline. Empty if none does.",
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
            "happens, and how its code is divided into modules. Give the basis of each "
            "choice. Where the project already has an answer (its CI, its lockfile, its "
            "build, its modules), record it; anything that differs from what it does today "
            "goes in `changes`, with why, and whether making it so needs work on the code."
        ),
        expected_output="The design, each choice with its basis, and any changes with why.",
        agent=architect,
        output_pydantic=DesignProposal,
    )
    crew = Crew(agents=[architect], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic


def review_design(*, project: str, design: str) -> DesignReview:
    """The Code Reviewer's check of a proposed design against the guidelines."""
    reviewer = _role("code_reviewer", "design_review")
    task = Task(
        description=(
            f"{project}\n\n## The proposed design\n\n```yaml\n{design}\n```\n\n"
            "Check each choice against the crew-wide guidelines (§19, in your rules) and "
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
