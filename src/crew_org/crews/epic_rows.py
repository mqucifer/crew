"""The decisions a story follows, as the steps that build and judge it are shown them (crew#440).

Only the rows the story names, with what each step is to do with them. The whole record
isn't passed down: a long one would bloat every story's context, and a row the story
doesn't follow isn't its concern. With them, the Goal's target diagram (crew#858): what
the system will be once the Goal is delivered.
"""

from __future__ import annotations


def block(rows: str, judge: str, *, level: int = 2) -> str:
    """The rows under their heading with the step's own instruction, or nothing if none.

    `level` is the heading's depth: the Developer's context is built from `#` headings,
    the other steps' prompts from `##`.
    """
    if not rows:
        return ""
    heading = "#" * level
    return f"{heading} What the epic decided that this story follows\n\n{rows}\n\n{judge}\n\n"


def diagram(source: str, use: str, *, level: int = 2) -> str:
    """The Goal's target diagram with the step's own use for it, or nothing if none."""
    if not source:
        return ""
    heading = "#" * level
    return f"{heading} The Goal's target diagram\n\n```mermaid\n{source}\n```\n\n{use}\n\n"
