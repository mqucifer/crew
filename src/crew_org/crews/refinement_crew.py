"""Refinement: a Sponsor goal becomes epics, and an epic becomes stories.

The constitution's rules for what counts as a well-formed story are expressed
here as validators rather than as prose in a prompt. A story with one acceptance
criterion, or an estimate off the scale, is rejected by the schema — which makes
it a SCHEMA failure the repair loop already handles, instead of something a
reviewer has to notice later.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agents

# Modified Fibonacci, per constitution §6. Nothing larger enters a sprint.
POINT_SCALE = (1, 2, 3, 5, 8)
MIN_CRITERIA = 2

# A Sponsor goal that decomposes into one epic has not been decomposed — there
# is nothing to sequence and nothing to deliver early. Observed in practice: the
# same goal produced two epics on one run and one on the next, so the judgement
# is encoded here rather than left to the Sponsor to catch each sprint.
MIN_EPICS = 2
MAX_EPICS = 5

# A justification shorter than this is a restatement, not an argument.
MIN_JUSTIFICATION = 40


class AcceptanceCriterion(BaseModel):
    """One Given/When/Then scenario, per constitution §3."""

    given: str = Field(description="The precondition")
    when: str = Field(description="The action taken")
    then: str = Field(description="The observable outcome a test can assert")


class Story(BaseModel):
    title: str = Field(description="Short imperative title")
    as_a: str = Field(description="The role who wants this")
    i_want: str = Field(description="The capability")
    so_that: str = Field(description="The benefit")
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        description=f"At least {MIN_CRITERIA}; one must cover a failure or edge case"
    )
    points: int = Field(description=f"One of {POINT_SCALE}")

    @field_validator("points")
    @classmethod
    def _on_the_scale(cls, value: int) -> int:
        if value not in POINT_SCALE:
            raise ValueError(
                f"{value} is not on the estimation scale {POINT_SCALE}. "
                "Anything larger than 8 must be split, not admitted."
            )
        return value

    @field_validator("acceptance_criteria")
    @classmethod
    def _enough_criteria(cls, value: list[AcceptanceCriterion]) -> list[AcceptanceCriterion]:
        if len(value) < MIN_CRITERIA:
            raise ValueError(
                f"a story needs at least {MIN_CRITERIA} acceptance criteria, "
                "at least one of them a failure or edge case"
            )
        return value


class Epic(BaseModel):
    title: str = Field(description="Short imperative title")
    outcome: str = Field(description="The user-visible outcome this delivers")
    rationale: str = Field(description="Why this slice is worth doing, and why now")
    separately_deliverable: str = Field(
        description=(
            "Why this epic can ship on its own and still be worth having, without "
            "the others. Name what the user could do with only this."
        )
    )

    @field_validator("separately_deliverable")
    @classmethod
    def _is_an_argument(cls, value: str) -> str:
        if len(value.strip()) < MIN_JUSTIFICATION:
            raise ValueError(
                "state specifically what a user could do with this epic alone. "
                "If nothing, it is not a separate epic — fold it into another."
            )
        return value

    @model_validator(mode="after")
    def _not_a_restatement(self) -> Epic:
        if self.separately_deliverable.strip().lower() == self.outcome.strip().lower():
            raise ValueError(
                "separately_deliverable repeats the outcome. It must argue why this "
                "slice stands alone, which is a different question."
            )
        return self


class EpicProposal(BaseModel):
    """What the Product Owner proposes for a goal. Awaits Sponsor approval."""

    epics: list[Epic] = Field(description="The smallest set that covers the goal")
    ordering_rationale: str = Field(description="Why this order delivers value soonest")

    @field_validator("epics")
    @classmethod
    def _decomposed(cls, value: list[Epic]) -> list[Epic]:
        if len(value) < MIN_EPICS:
            raise ValueError(
                f"a goal must decompose into at least {MIN_EPICS} epics, got {len(value)}. "
                "One epic is not a decomposition: there is nothing to sequence and "
                "nothing deliverable early. Split by workflow step, by business rule, "
                "or by happy-path-then-edge-cases — never by architectural layer."
            )
        if len(value) > MAX_EPICS:
            raise ValueError(
                f"{len(value)} epics is too many for one goal (limit {MAX_EPICS}). "
                "Group the smaller slices into coherent outcomes."
            )
        titles = [e.title.strip().lower() for e in value]
        if len(set(titles)) != len(titles):
            raise ValueError("two epics share a title; each must be a distinct slice")
        return value


class AlreadyDelivered(BaseModel):
    """A story this epic would need that the project has already delivered (#220)."""

    title: str = Field(description="The story you would otherwise have written")
    by: int = Field(description="The delivered story that already does it, by number")
    why: str = Field(description="Which of its outcomes covers this story's criteria")


class StoryProposal(BaseModel):
    """What the Business Analyst proposes for one epic."""

    epic_title: str
    stories: list[Story] = Field(default_factory=list)
    already_delivered: list[AlreadyDelivered] = Field(
        default_factory=list,
        description=(
            "Stories this epic needs that the project has already delivered, each with "
            "the delivered story that did it. Not written again."
        ),
    )

    @model_validator(mode="after")
    def _not_empty(self) -> StoryProposal:
        if not self.stories and not self.already_delivered:
            raise ValueError("an epic must decompose into at least one story")
        return self


class ProductAnswer(BaseModel):
    """The Product Owner's answer to the question a story problem raised (#189).

    Either a decision grounded in what the project already has, or one question
    for the Sponsor when nothing written down answers it. Never both.
    """

    answer: str = Field(
        default="",
        description="What the stories should do, when the project already answers it",
    )
    based_on: list[str] = Field(
        default_factory=list,
        description=(
            "What the answer follows: the Goal, the project's record, or a delivered story "
            "by number, e.g. '#100 made threshold flags opt-in via --thresholds'"
        ),
    )
    question: str = Field(
        default="",
        description=(
            "When nothing written down answers it: one clear question for the Sponsor, "
            "answerable in a sentence"
        ),
    )

    @model_validator(mode="after")
    def _one_or_the_other(self) -> ProductAnswer:
        if bool(self.answer.strip()) == bool(self.question.strip()):
            raise ValueError("give an answer grounded in the project, or one question; not both")
        if self.answer.strip() and not self.based_on:
            raise ValueError("an answer names what it follows: the Goal, the record, or a story")
        return self


def answer_story_problem(
    *, epic: str, goal: str, evidence: str, project: str = "", delivered: str = ""
) -> ProductAnswer:
    """Product Owner only: answer the question a story problem raised, or ask the Sponsor.

    Answering the team's questions about product intent is the Product Owner's
    job. sprint-metrics#97's question, opt-in or default flags, was answerable
    from merged work (#100 and #102 already flagged opt-in via --thresholds),
    and took the Sponsor to settle.
    """
    agents = build_agents("product_owner")
    task = Task(
        description=(
            (f"{project}\n\n" if project else "")
            + _delivered_block(delivered, "a choice that contradicts what these delivered")
            + f"## The Goal\n\n{goal}\n\n## The epic\n\n{epic}\n\n"
            f"## Why it came back\n\n{evidence}\n\n"
            "A story of this epic kept breaking merged tests, because the epic doesn't "
            "say what the product should do here. Decide it, if the Goal, the project's "
            "record or what's already delivered answers it, and name what you followed. "
            "If none of them does, ask the Sponsor one question instead: the one whose "
            "answer settles it."
        ),
        expected_output="A grounded answer with what it follows, or one question.",
        agent=agents["product_owner"],
        output_pydantic=ProductAnswer,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic


REWORK = (
    "\n\n## The Sponsor sent your last answer back\n\n"
    "{feedback}\n\n"
    "This replaces what you proposed before; the cards from it have been closed. "
    "Answer the objection rather than repeating yourself.\n"
)


def propose_epics(
    goal: str, *, repository: str = "", feedback: str = "", delivered: str = ""
) -> EpicProposal:
    """Product Owner only: a goal becomes a set of epics.

    `repository` is the code the goal is about. A role deciding what should
    exist works better for knowing what already does — and the alternative is
    a decomposition of a product the model is imagining.
    """
    agents = build_agents("product_owner")
    repo_block = f"## The repository as it stands\n\n{repository}\n\n" if repository else ""
    sent_back = REWORK.format(feedback=feedback) if feedback else ""
    task = Task(
        description=(
            repo_block
            + _delivered_block(delivered, "an epic whose outcome these already deliver")
            + f"The Product Sponsor has set this goal:\n\n{goal}\n\n"
            + sent_back
            + f"Propose between {MIN_EPICS} and {MAX_EPICS} epics that together deliver it. "
            "Decompose by outcome, never by architectural layer. "
            "Order them so the most valuable is deliverable first.\n\n"
            "For each epic, state what a user could do with that epic alone, without "
            "the others. If you cannot say, it is not a separate epic — merge it. "
            "A goal that comes back as a single epic has not been decomposed."
        ),
        expected_output="A set of epics, each with a title, outcome and rationale.",
        agent=agents["product_owner"],
        output_pydantic=EpicProposal,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic


def check_delivered(proposal: StoryProposal, numbers: set[int] | None) -> None:
    """Refuse an `already_delivered` naming a story the project hasn't delivered.

    Mechanical, because it can be: a split that leaves work out on the strength
    of a story that doesn't exist, or isn't done, loses that work silently.
    """
    if numbers is None:
        return
    unknown = sorted({d.by for d in proposal.already_delivered} - numbers)
    if unknown:
        raise ValueError(
            "already_delivered names "
            + ", ".join(f"#{n}" for n in unknown)
            + ", which the project has not delivered. Name a delivered story, or write "
            "the story."
        )


def _delivered_block(delivered: str, instead: str) -> str:
    if not delivered:
        return ""
    return (
        "## What this project has already delivered\n\n"
        "Each story, and the outcomes its acceptance criteria promised:\n\n"
        f"{delivered}\n\nDon't propose {instead}.\n\n"
    )


def split_epic(
    title: str,
    context: str = "",
    *,
    repository: str = "",
    feedback: str = "",
    delivered: str = "",
    delivered_numbers: set[int] | None = None,
) -> StoryProposal:
    """Business Analyst only: an epic becomes INVEST-sized stories.

    Takes the epic's title and whatever context the card carries, rather than an
    Epic model. By the time an epic is being split the Sponsor has approved it,
    so the proposal-time validators — which argue for why a slice deserves to
    exist — no longer apply and reconstructing one just to satisfy them would be
    inventing data.
    """
    agents = build_agents("business_analyst")
    # Stable first: the repository is identical between every split in a pass,
    # where the epic is not, so it goes ahead of it and the cached prefix holds.
    repo_block = f"## The repository as it stands\n\n{repository}\n\n" if repository else ""
    sent_back = REWORK.format(feedback=feedback) if feedback else ""
    task = Task(
        description=(
            repo_block
            + _delivered_block(
                delivered,
                "a story these already deliver: list it in `already_delivered`, with the "
                "story that did it",
            )
            + sent_back
            + "Split this epic into stories.\n\n"
            f"Epic: {title}\n\n{context}\n\n"
            "Each story must satisfy INVEST and carry acceptance criteria a test can be "
            "written from directly. Split by workflow step, by business rule, or by "
            "happy-path-then-edge-cases — never by layer.\n\n"
            "Order them so that each story can be built on the ones before it. Your "
            "order is the order they will be delivered in, one at a time: a story "
            "that extends another must come after it, or it will be written against "
            "a codebase that does not contain the thing it extends.\n\n"
            "Write criteria against what the code above actually has. A criterion "
            "asking for data the product does not carry cannot be satisfied by any "
            "implementation, and the story will be delivered as something that passes "
            "its tests and does nothing."
        ),
        expected_output="Stories with acceptance criteria and estimates.",
        agent=agents["business_analyst"],
        output_pydantic=StoryProposal,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    proposal = crew.kickoff().pydantic
    check_delivered(proposal, delivered_numbers)
    return proposal
