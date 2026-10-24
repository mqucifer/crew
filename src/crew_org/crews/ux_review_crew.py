"""The UX Designer's release review: its step, its form and what it's told (crew#830).

The review brings user-experience expertise to what was built, not a comparison with a
mock alone (the Sponsor, 2026-10-17). It judges by two established standards, and each
correction names the one it breaks:
- Nielsen's 10 usability heuristics (Nielsen Norman Group, 1994, revised 2020);
- WCAG 2.2's success criteria that `look` can see: reflow, alternative text, headings
  and labels, reading order and page titles. Contrast isn't measured by `look`, so the
  review doesn't claim it; the project's own checks do.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from crew_org.rulebook import refuse
from crew_org.session import steps

Standard = Literal[
    "Nielsen 1: Visibility of system status",
    "Nielsen 2: Match between the system and the real world",
    "Nielsen 3: User control and freedom",
    "Nielsen 4: Consistency and standards",
    "Nielsen 5: Error prevention",
    "Nielsen 6: Recognition rather than recall",
    "Nielsen 7: Flexibility and efficiency of use",
    "Nielsen 8: Aesthetic and minimalist design",
    "Nielsen 9: Help users recognize, diagnose and recover from errors",
    "Nielsen 10: Help and documentation",
    "WCAG 2.2 1.1.1 Non-text Content",
    "WCAG 2.2 1.3.1 Info and Relationships",
    "WCAG 2.2 1.3.2 Meaningful Sequence",
    "WCAG 2.2 1.4.10 Reflow",
    "WCAG 2.2 2.4.2 Page Titled",
    "WCAG 2.2 2.4.6 Headings and Labels",
]
STANDARDS: tuple[str, ...] = get_args(Standard)
# The UX Designer's own review can't change a file, so no correction is its own.
Owner = Literal["bug", "devops", "architect", "qa"]


class Correction(BaseModel):
    page: str = Field(
        description="The published page, as you gave it to `look`. You looked at it there, "
        "at this width: that look's screenshot is filed with the correction"
    )
    width: int = Field(description="The width you saw it at, in px, one you gave `look`")
    saw: str = Field(description="What the reader meets there, as you saw it")
    instead: str = Field(description="What the reader should meet instead")
    standard: Standard = Field(description="The heuristic or success criterion it breaks")
    owner: Owner = Field(
        description=(
            "Whose work it is. bug: the page breaks what the project accepted (a "
            "criterion, the Goal's intent, a standard it holds); devops: how the site is "
            "built or published; architect: how its parts connect; qa: tests across pages"
        )
    )


class ReleaseReview(BaseModel):
    readers_meet: str = Field(
        description="What each reader meets on the pages, in a few lines, the good with the bad"
    )
    corrections: list[Correction] = Field(
        description="What to correct, one per problem, each on the page and width it's "
        "worst at. Empty when there is nothing"
    )


STEP = steps.Step(
    "ux release review",
    "ux_designer",
    ReleaseReview,
    "what the readers meet, and each correction with its page, width and standard.",
    "the release",
    step="release_review",
    tools=("look",),
)


def section(version: str, task: str) -> str:
    """The step's own part of its start: what a release review is, and the Task."""
    standards = "\n".join(f"- {s}" for s in STANDARDS)
    return (
        f"## Your step: review `{version}` as its readers meet it\n\n"
        "A release review looks at what was built, as its readers meet it: the published "
        "pages, at the widths the Goals name (375, 768 and 1280 when they name none), "
        "against what each reader needs and the UX criteria. Use `look` with `source` "
        "`published`: that's what readers are served, and each width's screenshot is "
        "kept for the correction that cites it. Read the repository where it helps you "
        "find a page or understand what a reader came for.\n\n"
        "Each correction names the standard it breaks, one of these:\n\n"
        f"{standards}\n\n"
        "`look` doesn't measure colour contrast, so leave contrast out. Nothing you file "
        "blocks a merge: each correction is filed as a bug or a Task for its owner.\n\n"
        f"## The Task\n\n{task.strip()}"
    )


def check_seen(review: ReleaseReview, shots: dict[tuple[str, int], Any]) -> None:
    """`done` refuses a correction on a page and width `look` didn't show on the
    published site: it would be filed with no screenshot."""
    unseen = [
        f"`{c.page}` at {c.width}px" for c in review.corrections if (c.page, c.width) not in shots
    ]
    if unseen:
        refuse("check.ux_review.seen_on_the_published_site", unseen=", ".join(unseen))
