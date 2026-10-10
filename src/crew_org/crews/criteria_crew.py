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

from pydantic import BaseModel, Field

from crew_org import calls


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


def parts(
    *,
    stories: str,
    repository: str = "",
    planned: str = "",
    conclusion: str = "",
    goal: str = "",
    project_log: str = "",
    decided: str = "",
) -> list[calls.Part]:
    """What the check is shown, part by part, in the order it reads them."""
    return [
        calls.Part(
            "code", f"## The project's code as it stands\n\n{repository}" if repository else ""
        ),
        calls.Part(
            "planned",
            f"## Stories other epics already plan, with their criteria\n\n{planned}"
            if planned
            else "",
        ),
        # What is already decided, so restating it isn't taken for deciding it: on
        # sprint-metrics#406 two criteria naming the five event types, decided on the
        # Goal and in the project's log, were refused as settling an open question.
        calls.Part("goal", f"## The Goal, set by the Sponsor\n\n{goal}" if goal else ""),
        calls.Part("project_log", project_log),
        # The Product Owner's answer to the story problem the epic was sent back
        # with (#189): the re-split follows it. Without it, sprint-metrics#468's
        # field names, decided there, were refused as settling an open question
        # 17 times (2026-10-09).
        calls.Part(
            "decided",
            f"## Decided on this epic since it was sent back\n\n{decided}" if decided else "",
        ),
        calls.Part(
            "conclusion",
            f"## The epic's conclusion, decided before the split\n\n{conclusion}"
            if conclusion
            else "",
        ),
        calls.Part("stories", f"## The proposed stories\n\n{stories}"),
        calls.Part(
            "task",
            "Report every criterion of the proposed stories that can't pass alongside:\n"
            "- another criterion, in the same story or another one, proposed or planned, "
            "that expects a different outcome from the same situation;\n"
            "- the project's code: a value the code defines or computes differently, or "
            "behaviour a merged test pins. A story that declares it changes those tests "
            "(its Existing tests line says a contract change naming them) is allowed to"
            + (
                ";\n- a row of the epic's conclusion above: a criterion that expects the "
                "opposite of what a row decides. A criterion that decides an open question "
                "(Q) the record leaves to whoever builds it is a conflict too, since no "
                "story settles it, unless the Goal, the project's decision log, what was "
                "decided on this epic or a decided row already says what the criterion "
                "states: restating a decision isn't deciding the question"
                if conclusion
                else ""
            )
            + ".\n\nQuote both sides. A criterion that is merely vague, large or worded "
            "differently from how you would write it is not a conflict.",
        ),
    ]


def check_criteria(
    *,
    stories: str,
    repository: str = "",
    planned: str = "",
    conclusion: str = "",
    goal: str = "",
    project_log: str = "",
    decided: str = "",
) -> CriteriaCheck:
    """QA's check of a proposed split's criteria, against each other, the code and the rows.

    The epic's conclusion (crew#440) is decided before the split: a criterion that
    goes against one of its rows can't be right, however well it reads. The first
    step on the crew's own call layer (ADR 0025): a refused answer is asked for
    again with the reason.
    """
    shown = parts(
        stories=stories,
        repository=repository,
        planned=planned,
        conclusion=conclusion,
        goal=goal,
        project_log=project_log,
        decided=decided,
    )
    return calls.ask(
        "qa_engineer",
        shown,
        CriteriaCheck,
        step="criteria_check",
        expected="Every criterion that can't be met, or none.",
    )
