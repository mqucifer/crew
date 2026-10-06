"""An epic's conclusion, as the split reads it (crew#440).

The conclusion sits at the end of the epic's body, under its own header
(`flows/settle.py` writes it). The split is shown it apart from the epic's own
text, so it can't be missed or mistaken for part of what the Sponsor approved,
and each story names the rows it follows ("Follows the epic's R1, R4") so that
later steps can pull those rows alone.

Every settled row has to reach a story, or be named as one that only guides the
design note. A row no story follows is a decision the split dropped.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.flows.settle import CONCLUSION_HEADER

# A decision row of the conclusion's table: `| R3 | accepted | ...`.
_ROW = re.compile(r"^\|\s*(R\d+)\s*\|", re.M)

FOLLOWS = "Follows the epic's"


def split_conclusion(body: str) -> tuple[str, str]:
    """The epic's own text, and its conclusion (empty if it has none)."""
    head, found, tail = body.partition(CONCLUSION_HEADER)
    if not found:
        return body, ""
    return head.rstrip(), f"{CONCLUSION_HEADER}{tail}".strip()


def row_ids(conclusion: str) -> list[str]:
    """The IDs of the decisions, in the order the table has them."""
    return _ROW.findall(conclusion)


def follows_line(rows: list[str]) -> str:
    """The line a story carries naming the epic's rows it follows."""
    return f"{FOLLOWS} {', '.join(rows)}."


def problems(proposal: Any, rows: list[str]) -> list[str]:
    """What is wrong between a proposed split and the conclusion's rows. Empty if nothing.

    Only a split that makes stories is checked: one that finds everything already
    delivered has nothing to follow the rows.
    """
    if not proposal.stories:
        return []
    known = set(rows)
    found: list[str] = []
    followed: set[str] = set()
    for story in proposal.stories:
        unknown = sorted(set(story.follows) - known)
        if unknown:
            found.append(
                f"Story '{story.title}' follows {', '.join(unknown)}, not rows of the epic."
            )
        followed.update(story.follows)
    left_out = {n.row for n in proposal.not_for_stories}
    unknown_left = sorted(left_out - known)
    if unknown_left:
        found.append(f"not_for_stories names {', '.join(unknown_left)}, not rows of the epic.")
    unused = [r for r in rows if r not in followed and r not in left_out]
    if unused:
        found.append(
            f"No story follows {', '.join(unused)}. Name the story that does in its `follows`, "
            "or list the row in `not_for_stories` with why it is not for stories."
        )
    return found


def feedback(found: list[str]) -> str:
    """What the Business Analyst is told when its split doesn't fit the conclusion."""
    return (
        "## Your split doesn't fit the epic's conclusion\n\n"
        + "\n".join(f"- {p}" for p in found)
        + "\n\nThe conclusion's rows are settled: a story follows them, never contradicts them."
    )
