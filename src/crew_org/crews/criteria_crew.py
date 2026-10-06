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

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field

from crew_org.agents import build_agent
from crew_org.permissions import load_agents


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


def check_criteria(
    *, stories: str, repository: str = "", planned: str = "", conclusion: str = ""
) -> CriteriaCheck:
    """QA's check of a proposed split's criteria, against each other, the code and the rows.

    The epic's conclusion (crew#440) is decided before the split: a criterion that
    goes against one of its rows can't be right, however well it reads.
    """
    spec = load_agents()["qa_engineer"]
    checker = build_agent("qa_engineer", {**spec, **spec["criteria_check"]})
    task = Task(
        description=(
            (f"## The project's code as it stands\n\n{repository}\n\n" if repository else "")
            + (
                f"## Stories other epics already plan, with their criteria\n\n{planned}\n\n"
                if planned
                else ""
            )
            + (
                f"## The epic's conclusion, decided before the split\n\n{conclusion}\n\n"
                if conclusion
                else ""
            )
            + "## The proposed stories\n\n"
            f"{stories}\n\n"
            "Report every criterion of the proposed stories that can't pass alongside:\n"
            "- another criterion, in the same story or another one, proposed or planned, "
            "that expects a different outcome from the same situation;\n"
            "- the project's code: a value the code defines or computes differently, or "
            "behaviour a merged test pins. A story that declares it changes those tests "
            "(its Existing tests line says a contract change naming them) is allowed to"
            + (
                ";\n- a row of the epic's conclusion above: a criterion that expects the "
                "opposite of what a row decides. A criterion that decides an open question "
                "(Q) the row table leaves for the design note is a conflict too, since no "
                "story settles it"
                if conclusion
                else ""
            )
            + ".\n\nQuote both sides. A criterion that is merely vague, large or worded "
            "differently from how you would write it is not a conflict."
        ),
        expected_output="Every criterion that can't be met, or none.",
        agent=checker,
        output_pydantic=CriteriaCheck,
    )
    crew = Crew(agents=[checker], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic
