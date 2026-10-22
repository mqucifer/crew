"""A split's criteria checked together before any story exists (#428).

sprint-metrics#184's split gave #393 two criteria for the same request, a
`card_blocked` for a card never created: one expected 404, the other 400.
#394 expected cycle times the code defines as 0, and rejected a sprint range
a merged test pins as accepted. Design review caught one of these once and
missed it the next time; delivery would have spent rounds failing to prove
them. Each is visible in the criteria alone, before a design or a line of
code exists.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from crew_org.session import steps


class CriteriaConflict(BaseModel):
    story: str = Field(description="The title of the proposed story whose criterion can't be met")
    criterion: str = Field(description="That criterion, quoted")
    against: str = Field(
        description=(
            "What it contradicts, quoted: another criterion (name its story), or the "
            "merged code or test (name the file and the definition or test), or a row of "
            "the epic's conclusion (name its ID)"
        )
    )
    why: str = Field(description="Why no implementation can satisfy both")


class CriteriaCheck(BaseModel):
    conflicts: list[CriteriaConflict] = Field(
        default_factory=list,
        description=(
            "Every criterion that can't be met alongside what it contradicts. Empty if none"
        ),
    )


def opening(
    *,
    stories: str,
    siblings: str = "",
    planned: str = "",
    conclusion: str = "",
    goal: str = "",
    project_log: str = "",
) -> dict[str, str]:
    """QA's criteria check as a session's start (crew#807): its step first, then what the
    story's criteria are checked against. The code it was shown, focused on the story,
    is read through the tools."""
    from crew_org.crews.goal_look_crew import sponsors_words  # noqa: PLC0415

    return {
        "step": "## Your step: check one proposed story's criteria before it exists\n\n"
        "The Business Analyst proposed the stories below, and none is created yet. Report "
        "every criterion of the story under check that can't pass alongside:\n"
        "- another criterion, in the same story or another one, proposed or planned, that "
        "expects a different outcome from the same situation;\n"
        "- the project's code: a value the code defines or computes differently, or "
        "behaviour a merged test pins. A story that declares it changes those tests (its "
        "Existing tests line says a contract change naming them) is allowed to"
        + (
            ";\n- a row of the epic's record: a criterion that expects the opposite of what a "
            "row decides. A criterion that decides an open question (Q) the record leaves to "
            "whoever builds it is a conflict too, since no story settles it, unless the Goal, "
            "the project's decision log or a row of the record already says what the "
            "criterion states: restating a decision isn't deciding the question"
            if conclusion
            else ""
        )
        + ".\n\nQuote both sides. A criterion that is merely vague, large or worded "
        "differently from how you would write it is not a conflict. The stories and their "
        "repair are the Business Analyst's.",
        "goal": f"## The Goal, set by the Sponsor\n\n{sponsors_words(goal)}" if goal else "",
        "project_log": project_log,
        "conclusion": f"## The epic's record, decided before the split\n\n{conclusion}"
        if conclusion
        else "",
        "siblings": f"## The other stories in this split, with their criteria\n\n{siblings}"
        if siblings
        else "",
        "planned": f"## Stories other epics already plan, with their criteria\n\n{planned}"
        if planned
        else "",
        "stories": f"## The story under check\n\n{stories}",
    }


CHECK = steps.Step(
    "criteria check",
    "qa_engineer",
    CriteriaCheck,
    "every criterion of the story under check that can't be met, or none.",
    "the story",
    step="criteria_check",
)


def check_criteria(
    *,
    stories: str,
    siblings: str = "",
    planned: str = "",
    conclusion: str = "",
    goal: str = "",
    project_log: str = "",
    reading: steps.Reading | None = None,
    on_end: Any = None,
) -> CriteriaCheck:
    """QA's check of one proposed story's criteria, against the others, the code and the
    rows, in a session over the code (crew#807).

    The epic's record (crew#440, ADR 0023) holds every decision so far, the Product
    Owner's answers to story problems included: a criterion that goes against one of
    its rows can't be right, however well it reads.
    """
    found: CriteriaCheck = steps.run(
        CHECK,
        opening(
            stories=stories,
            siblings=siblings,
            planned=planned,
            conclusion=conclusion,
            goal=goal,
            project_log=project_log,
        ),
        reading,
        about=stories,
        on_end=on_end,
    )
    return found
