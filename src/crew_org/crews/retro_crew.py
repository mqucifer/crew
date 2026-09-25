"""The retro: what the sprint actually did, and what the process should learn.

The Scrum Master reports; it does not decide. Its one job that needs judgment
is reading the escalation ledger honestly — a high escalation rate is a defect
in task design, and the retro's output is process defects, never a request for
a larger budget.
"""

from __future__ import annotations

from typing import Literal

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator


class ProcessDefect(BaseModel):
    """A defect the retro found. Filed as an issue where the thing it found lives."""

    subject: str = Field(description="The story, epic or rule the defect is about")
    problem: str = Field(description="What went wrong, as a fact about this sprint")
    change: str = Field(description="The specific change proposed")
    about: Literal["process", "product"] = Field(
        default="process",
        description="process: how the crew works. product: something the crew built",
    )
    repository: str | None = Field(
        default=None,
        description="For a product defect, the delivery repository holding what was found",
    )
    explained_by: int | None = Field(
        default=None,
        description="If an issue under 'Known issues' already explains this, its number. "
        "The defect is then cited as that issue, not filed again",
    )
    checked_against: list[int] = Field(
        default_factory=list,
        description="The known issues this finding was compared with and is not a duplicate of",
    )

    @field_validator("change")
    @classmethod
    def _is_a_change(cls, value: str) -> str:
        lowered = value.lower()
        if "budget" in lowered and (
            "increase" in lowered or "raise" in lowered or "more" in lowered
        ):
            raise ValueError(
                "a larger escalation budget is not a process improvement. Escalation "
                "rate is a symptom of task design; name the design change instead."
            )
        return value


class Retro(BaseModel):
    summary: str = Field(
        description="What the sprint delivered, plainly, including what went badly"
    )
    defects: list[ProcessDefect] = Field(
        default_factory=list, description="Process changes this sprint argues for"
    )


def write_retro(
    sprint: str,
    board_summary: str,
    escalations: str,
    *,
    delivery_repos: list[str] | None = None,
    standups: str = "",
    known: str = "",
    retries: str = "",
) -> Retro:
    agents_module = __import__("crew_org.agents", fromlist=["build_agents"])
    agents = agents_module.build_agents("scrum_master")
    task = Task(
        description=(
            f"Write the retro for sprint {sprint}.\n\n"
            f"## What the board says\n\n{board_summary}\n\n"
            f"## The escalation ledger\n\n{escalations or 'No escalations this sprint.'}\n\n"
            f"## The sprint's standups, one per tick\n\n{standups or 'None recorded.'}\n\n"
            "The board and the ledger say how the sprint ended; the standups say how it "
            "went. Read them for what they show over time: cards that stayed blocked or "
            "waiting across many ticks, long runs where nothing moved, work that stalled "
            "and restarted. Epics listed as awaiting approval are the Sponsor's queue at "
            "the gate, not stuck work: they are expected to wait until the Sponsor "
            "decides, and are not a defect.\n\n"
            + (
                "## Why work didn't land first time\n\n"
                f"{retries}\n\n"
                "A cause seen on more than one card is filed as a defect by the crew "
                "itself, with its count and cards as evidence; do not propose those again. "
                "Read the rest for what they say about how stories were written or "
                "delivered.\n\n"
                if retries
                else ""
            )
            + f"## Known issues on the crew repository, open now\n\n{known or 'None.'}\n\n"
            "These are defects and work already understood and filed. Before proposing a "
            "defect, check it against them. If one already explains what happened, cite it "
            "as the cause (write it as it is written above, e.g. crew#116) and set "
            "`explained_by` to its number rather than proposing it again: the symptom a "
            "sprint shows is often not the cause. For a finding that is new, list in "
            "`checked_against` the known issues you compared it with.\n\n"
            "Report what happened, including what went badly, for a Sponsor who was not "
            "present. Cite cards by number.\n"
            "Where escalation was needed, name the specific story that was too large or "
            "whose criteria were ambiguous, and propose the design change. A larger "
            "escalation budget is never the answer.\n\n"
            "Each defect is filed as an issue where the thing it found lives. Mark it "
            "`process` if it is about how the crew works, or `product` if it is about "
            "something the crew built, and for a product defect name the repository: "
            f"{', '.join(delivery_repos or []) or 'none configured'}. One observation can "
            "be both — a story that should have named its module is a process defect, the "
            "duplicate it produced is a product defect — so file both rather than choosing."
        ),
        expected_output="A summary and any process defects this sprint argues for.",
        agent=agents["scrum_master"],
        output_pydantic=Retro,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic
