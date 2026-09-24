"""Onboarding: the Product Owner interviews the Sponsor about a project (#130).

One call is one turn of the interview. The Product Owner is shown the project
(or told there is nothing to show), the answers so far, which required ones are
still missing, and the conversation. It returns what it has learned and what to
say next. Whether the interview is finished is not its call: the record's own
loader decides that (#129), so a model that believes it is done cannot end an
interview with a required answer unsettled.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field

from crew_org.agents import build_agent
from crew_org.permissions import load_agents

# Every field optional: a turn reports only what it learned. The flow merges it
# into the answers so far, so an answer settled three turns ago is not lost
# because the model left it out of this one.


class ScopeAnswers(BaseModel):
    purpose: str | None = Field(None, description="What the project is for, and for whom")
    in_scope: list[str] | None = Field(None, description="What it does")
    out_of_scope: list[str] | None = Field(None, description="What it deliberately doesn't")


class ReleaseAnswers(BaseModel):
    deploys: bool | None = Field(
        None, description="True if anything is deployed; false if the merge is the release"
    )
    where: str | None = Field(None, description="Where it is deployed, if it is")
    how: str | None = Field(None, description="How a release happens, if it deploys")


class DoneAnswers(BaseModel):
    checks: list[str] | None = Field(
        None, description="Commands that must pass for a change to be done"
    )
    also: list[str] | None = Field(None, description="Anything else done requires, in words")
    never_touch: list[str] | None = Field(None, description="Paths agents must not change")


class BuildAnswers(BaseModel):
    language: str | None = None
    dependencies: str | None = None
    sandbox: str | None = Field(None, description="Anything the sandbox must provide")


class Answers(BaseModel):
    scope: ScopeAnswers | None = None
    release: ReleaseAnswers | None = None
    done: DoneAnswers | None = None
    build: BuildAnswers | None = None
    priority: int | None = Field(None, description="Against other projects; 1 is first")


class Question(BaseModel):
    about: str = Field(description="The missing answer this asks for, by its key, as listed")
    question: str = Field(description="The question, specific to what is still unsettled")


class Turn(BaseModel):
    answers: Answers = Field(
        description=(
            "What this turn settled or proposes. Only what the Sponsor said, or what the "
            "repository shows. Leave everything else null."
        )
    )
    say: str = Field(
        description="What to tell the Sponsor: what you propose and why, or what you understood"
    )
    questions: list[Question] = Field(
        default_factory=list, description="One question for each answer still missing"
    )


def interview_turn(
    *,
    repository: str,
    draft: str,
    missing: dict[str, str],
    problems: list[str],
    conversation: str,
) -> Turn:
    """The Product Owner's next turn in an onboarding interview."""
    spec = load_agents()["product_owner"]
    agent = build_agent("product_owner", {**spec, **spec["onboarding"]})
    task = Task(
        description=turn_description(
            repository=repository,
            draft=draft,
            missing=missing,
            problems=problems,
            conversation=conversation,
        ),
        expected_output="The answers this turn settled, what to say, and the next questions.",
        agent=agent,
        output_pydantic=Turn,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic


def turn_description(
    *,
    repository: str,
    draft: str,
    missing: dict[str, str],
    problems: list[str],
    conversation: str,
) -> str:
    """What the Product Owner is told for one turn: propose from the project, or ask."""
    if repository:
        seen = (
            "## The project as it stands\n\n"
            f"{repository}\n\n"
            "The project has content. Read it and **propose** answers from what it shows, "
            "saying what each proposal is based on, for the Sponsor to confirm or correct.\n\n"
        )
    else:
        seen = (
            "## The project as it stands\n\n"
            "The repository is empty: no README, no code, nothing to read. There is nothing "
            "to propose from, so **ask**. Probe for what the Sponsor has in mind.\n\n"
        )
    still = (
        "## Still missing\n\n"
        + "\n".join(f"- `{key}`: {words}" for key, words in missing.items())
        + "\n\nAsk about each of these, specifically.\n\n"
        if missing
        else "## Still missing\n\nNothing required. Confirm the optional answers if useful.\n\n"
    )
    wrong = (
        "## Answers that do not fit the record\n\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\nAsk the Sponsor to settle these.\n\n"
        if problems
        else ""
    )
    return (
        seen
        + f"## The record so far\n\n```yaml\n{draft}\n```\n\n"
        + still
        + wrong
        + f"## The conversation\n\n{conversation or '(it has not started)'}\n\n"
        "Take your next turn. Record in `answers` only what the Sponsor has said or "
        "confirmed, or what the project plainly shows. Never invent an answer."
    )
