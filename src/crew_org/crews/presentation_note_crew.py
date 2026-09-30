"""An epic's presentation note: what its reader sees, as criteria a test can check (#377).

An epic that changes what someone reads (a report, a page, an output format)
had nobody who owned the reader. The Business Analyst writes criteria about
behaviour, and nothing gave it the presentation intent; asking it to "keep the
reader in mind" is a request, not a guarantee (§15), and extra duties in one
prompt helped 0 of 3 times in the panel test (#359).

So the UX Designer writes one note per epic labelled `needs:ux`, after its
stories are split: who reads the output and what they need from it, a sample of
the finished output, and for each story criteria about what the reader sees.
Those criteria join the story's own, so QA proves them with tests like any
other. A criterion that needs judgement ("clear", "at a glance") is refused
here, in code: whether it reads well stays with the Sponsor at review.
"""

from __future__ import annotations

import re

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator

from crew_org.agents import build_agent
from crew_org.permissions import load_agents

# Words that ask a reader to judge rather than a test to observe. Whole words,
# so "clearly" is caught and "nuclear" isn't.
JUDGEMENT = (
    "clear",
    "clearly",
    "readable",
    "readability",
    "easy",
    "easily",
    "intuitive",
    "user-friendly",
    "friendly",
    "nice",
    "nicely",
    "clean",
    "cleanly",
    "appropriate",
    "appropriately",
    "properly",
    "glance",
    "understandable",
    "obvious",
    "obviously",
    "simple",
    "concise",
    "helpful",
    "meaningful",
    "sensible",
    "reasonable",
    "attractive",
    "prominent",
    "prominently",
    "scannable",
)
_JUDGEMENT = re.compile(r"\b(" + "|".join(re.escape(w) for w in JUDGEMENT) + r")\b", re.I)


class ReaderCriterion(BaseModel):
    given: str = Field(description="The output's input or state, concretely")
    when: str = Field(description="What produces the output")
    then: str = Field(
        description=(
            "What a test can observe in the output: what comes first, the order of "
            "sections, exact labels or text, which items appear and which don't, a "
            "count, the format of a value"
        )
    )

    @field_validator("given", "when", "then")
    @classmethod
    def _stated(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("every part of a criterion says something")
        return value

    @field_validator("then")
    @classmethod
    def _observable(cls, value: str) -> str:
        if found := _JUDGEMENT.search(value):
            raise ValueError(
                f"'{found.group(0)}' asks for judgement, which a test can't check. State "
                "what the output contains: an order, a position, exact text, a count, a "
                "format. Whether it reads well is the Sponsor's to judge at review."
            )
        return value


class StoryCriteria(BaseModel):
    story: int = Field(description="The story's issue number")
    criteria: list[ReaderCriterion] = Field(
        description="At least two criteria about what the reader sees from this story"
    )

    @field_validator("criteria")
    @classmethod
    def _at_least_two(cls, value: list[ReaderCriterion]) -> list[ReaderCriterion]:
        if len(value) < 2:
            raise ValueError("each story gets at least two criteria about what the reader sees")
        return value


class PresentationNote(BaseModel):
    reader: str = Field(description="Who reads this output, and the question they bring to it")
    sample: str = Field(
        description=(
            "The finished output with realistic data, exactly as it would appear to the "
            "reader. A sample, not a template: real-looking values, no placeholders."
        )
    )
    stories: list[StoryCriteria] = Field(description="One entry for every story still to build")
    looked_at: list[str] = Field(description="What you read to write this: files, issues")
    beyond_reach: str | None = Field(
        None,
        description=(
            "Only if the epic can't be written for its reader: name specifically what "
            "is missing. Never 'this is hard'."
        ),
    )

    @field_validator("sample")
    @classmethod
    def _has_a_sample(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the note carries a sample of the finished output")
        return value


def criterion_lines(number: int, c: ReaderCriterion) -> list[str]:
    """One criterion in the Business Analyst's own form, so every reader parses it alike."""
    return [
        f"{number}. **Given** {c.given}",
        f"   **When** {c.when}",
        f"   **Then** {c.then}",
        "",
    ]


def render(note: PresentationNote) -> str:
    """The note as the epic's comment reads, and as the Developer and Reviewer are shown it."""
    lines = [
        "## Presentation note",
        "",
        f"**Who reads it:** {note.reader}",
        "",
        "**The finished output, as it should read**",
        "",
        "~~~",
        note.sample.strip("\n"),
        "~~~",
        "",
        "**What each story adds for the reader** (added to each story's criteria)",
        "",
    ]
    for s in note.stories:
        lines.append(f"- #{s.story}")
        lines += [
            f"  - **Given** {c.given} **When** {c.when} **Then** {c.then}" for c in s.criteria
        ]
    lines += ["", "_Looked at: " + ", ".join(note.looked_at) + "_"]
    return "\n".join(lines)


def write_note(
    *, goal: str, epic: str, stories: str, project: str, repository: str, feedback: str = ""
) -> PresentationNote:
    """The UX Designer's note for one epic."""
    spec = load_agents()["ux_designer"]
    designer = build_agent("ux_designer", spec)
    refused = f"## Your last note was refused\n\n{feedback}\n\n" if feedback else ""
    task = Task(
        description=(
            (f"{project}\n\n" if project else "")
            + (f"## The Goal this epic serves\n\n{goal}\n\n" if goal else "")
            + f"## The epic\n\n{epic}\n\n## Its stories, in the order they are built\n\n"
            f"{stories}\n\n## The code as it stands\n\n{repository}\n\n{refused}"
            "Write the presentation note for this epic. Say who reads its output and the "
            "question they bring to it. Give a sample of the finished output with realistic "
            "data. For every story, add criteria about what the reader sees, in "
            "Given/When/Then, each asserting something a test can observe in the output.\n"
            "Add to the stories' criteria; never restate, weaken or drop one. A story's "
            "scope is the Business Analyst's, and the tools and formats are the Architect's."
        ),
        expected_output="Who reads it, a sample of the output, and criteria per story.",
        agent=designer,
        output_pydantic=PresentationNote,
    )
    crew = Crew(agents=[designer], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic
