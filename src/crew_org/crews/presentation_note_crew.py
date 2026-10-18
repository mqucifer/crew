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

from pydantic import BaseModel, Field, field_validator

from crew_org import calls
from crew_org.rulebook import refuse

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
            refuse("form.ReaderCriterion.stated")
        return value

    @field_validator("then")
    @classmethod
    def _observable(cls, value: str) -> str:
        if found := _JUDGEMENT.search(value):
            refuse("form.ReaderCriterion.observable", word=found.group(0))
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
            refuse("form.StoryCriteria.at_least_two")
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
            refuse("form.PresentationNote.has_a_sample")
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
        # A sample isn't checked against the criteria (on #62's live run it listed
        # two metrics twice), so it's illustration and the criteria decide (#377).
        "_An illustration. The criteria below decide; where the two differ, follow the criteria._",
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


def opening(*, goal: str, epic: str, stories: str, siblings: str = "") -> dict[str, str]:
    """The UX Designer's note as a session's start (crew#807). The repository it was
    shown whole, 376k characters for sprint-metrics#660, is read through the tools.

    The Goal's other epics come with their records: shown none, the note for
    crew-presentation#124 asked for "0.5 days" four seconds after its sibling's note
    asked for hours and minutes (`var/notes/refinement-path-analysis.md`).
    """
    from crew_org.crews.panel_crew import sponsors_words  # noqa: PLC0415

    return {
        "step": "## Your step: the presentation note\n\n"
        "This epic changes what a reader sees, and its stories are split. You write its "
        "presentation note: who reads the output and the question they bring to it, a sample "
        "of the finished output with realistic data, and for every story criteria about what "
        "the reader sees, in Given/When/Then, each asserting something a test can observe in "
        "the output. Your criteria are added to the stories' own: never restate, weaken or "
        "drop one. A story's scope is the Business Analyst's, and the tools and formats are "
        "the Architect's.",
        "goal": f"## The Goal this epic serves\n\n{sponsors_words(goal)}" if goal else "",
        "siblings": (
            f"## The other epics under this Goal, with their records\n\n{siblings}"
            if siblings
            else ""
        ),
        "epic": f"## The epic\n\n{epic}",
        "stories": f"## Its stories, in the order they are built\n\n{stories}",
    }


def write_note(
    *, goal: str, epic: str, stories: str, project: str, repository: str, feedback: str = ""
) -> PresentationNote:
    """The UX Designer's note for one epic."""
    refused = f"## Your last note was refused\n\n{feedback}\n\n" if feedback else ""
    return calls.ask(
        "ux_designer",
        [
            calls.Part(
                "task",
                (f"{project}\n\n" if project else "")
                + (f"## The Goal this epic serves\n\n{goal}\n\n" if goal else "")
                + f"## The epic\n\n{epic}\n\n## Its stories, in the order they are built\n\n"
                f"{stories}\n\n## The code as it stands\n\n{repository}\n\n{refused}"
                "Write the presentation note for this epic. Say who reads its output and the "
                "question they bring to it. Give a sample of the finished output with realistic "
                "data. For every story, add criteria about what the reader sees, in "
                "Given/When/Then, each asserting something a test can observe in the output.\n"
                "Add to the stories' criteria; never restate, weaken or drop one. A story's "
                "scope is the Business Analyst's, and the tools and formats are the Architect's.",
            )
        ],
        PresentationNote,
        expected="Who reads it, a sample of the output, and criteria per story.",
    )
