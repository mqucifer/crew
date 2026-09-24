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


class DoneAnswers(BaseModel):
    bar: str | None = Field(
        None, description="What must be true for a change to count as done, in words"
    )
    also: list[str] | None = Field(None, description="Anything else done requires, in words")
    never_touch: list[str] | None = Field(None, description="Paths agents must not change")


# No tools here, by design (#143): the language, build, sandbox, check commands
# and release mechanics are the project's Architect's to choose (#144). The
# interview has nowhere to record them, so it cannot ask the Sponsor to.
class Answers(BaseModel):
    scope: ScopeAnswers | None = None
    release: ReleaseAnswers | None = None
    done: DoneAnswers | None = None
    guidelines: list[str] | None = Field(
        None, description="This project's own rules, on top of the crew-wide ones"
    )
    priority: int | None = Field(None, description="Against other projects; 1 is first")


class Conflict(BaseModel):
    guideline: str = Field(description="The project's guideline, as the Sponsor gave it")
    crew_rule: str = Field(description="The crew-wide rule it would relax, by §19 number")
    why: str = Field(description="How it would relax that rule")


class Question(BaseModel):
    about: str = Field(
        description="The answer this is about, by its key, e.g. intent.scope.purpose"
    )
    question: str = Field(description="The question, specific to what is still unclear or missing")


class Turn(BaseModel):
    answers: Answers = Field(
        description=(
            "What this turn settled: what the Sponsor said or confirmed, written clearly "
            "enough for another role to act on, or what the project plainly shows. Leave "
            "everything else null."
        )
    )
    say: str = Field(
        description=(
            "What to tell the Sponsor: what you propose and why, what you understood, "
            "and what is still unclear"
        )
    )
    questions: list[Question] = Field(
        default_factory=list,
        description=(
            "Every answer still missing, unclear, or at odds with the project, one "
            "question each. Empty only when there is nothing left to ask."
        ),
    )
    conflicts: list[Conflict] = Field(
        default_factory=list,
        description=(
            "Each of the project's guidelines that would relax a crew-wide rule. A "
            "project can add to those rules, never relax one."
        ),
    )


def interview_turn(
    *,
    repository: str,
    intent: str,
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
            intent=intent,
            draft=draft,
            missing=missing,
            problems=problems,
            conversation=conversation,
        ),
        expected_output="The answers this turn settled, what to say, and what is left to ask.",
        agent=agent,
        output_pydantic=Turn,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic


def turn_description(
    *,
    repository: str,
    intent: str,
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
    asked = (
        "## What the project has been asked for\n\n"
        "Its open issues, goals included: the Sponsor's intent as written so far. The "
        "record's answers should agree with them, or say plainly where they part.\n\n"
        f"{intent}\n\n"
        if intent
        else "## What the project has been asked for\n\nNo open issues.\n\n"
    )
    still = (
        "## Required answers still missing\n\n"
        + "\n".join(f"- `{key}`: {words}" for key, words in missing.items())
        + "\n\n"
        if missing
        else "## Required answers still missing\n\nNone. Every required field holds "
        "something; whether each is clear enough is yours to judge.\n\n"
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
        + asked
        + f"## The record so far\n\n```yaml\n{draft}\n```\n\n"
        + still
        + wrong
        + f"## The conversation\n\n{conversation or '(it has not started)'}\n\n"
        "Take your next turn. Never invent an answer the Sponsor has not given and the "
        "project does not show."
    )
