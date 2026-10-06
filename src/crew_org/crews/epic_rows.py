"""The epic's decisions a story follows, as the steps that judge it are shown them (crew#440).

Only the rows the story names, with what each step is to do with them. The epic's whole
conclusion isn't passed down: a long one would bloat every story's context, and a row
the story doesn't follow isn't its concern.
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
