"""How a split's stories follow the epic's record (crew#440).

The record sits at the end of the epic's body, under its own header; reading and
editing it is `flows/record.py`'s. The split is shown it apart from the epic's own
text, so it can't be missed or mistaken for part of what the Sponsor approved,
and each story names the rows it follows ("Follows the epic's R1, R4") so that
later steps can pull those rows alone.

Every binding row has to reach a story, or be named as one that only guides the
design note. A row no story follows is a decision the split dropped.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from crew_org.flows import record

FOLLOWS = "Follows the epic's"
# The line a story carries: `Follows the epic's R1, R4.`
_FOLLOWS_LINE = re.compile(rf"^{re.escape(FOLLOWS)} (R\d+(?:, R\d+)*)\.\s*$", re.M)


def follows_line(rows: list[str]) -> str:
    """The line a story carries naming the epic's rows it follows."""
    return f"{FOLLOWS} {', '.join(rows)}."


def parse_follows(story_body: str) -> list[str]:
    """The rows a story's issue says it follows, in the order it names them."""
    found = _FOLLOWS_LINE.search(story_body)
    return found.group(1).split(", ") if found else []


def story_rows(issues: Any, story: Any, default_repo: str) -> str:
    """The rows of the epic's record this story names, for the steps that build and judge it.

    "Whatever reads a story pulls only the rows it names": a long record doesn't
    bloat a story's context, and nothing the story relies on goes missing. A row
    replaced since the split comes with the row that replaced it. A story that
    names none, or an epic with no record, gives nothing.
    """
    if story is None or story.parent is None or story.number is None:
        return ""
    repo = story.repo or default_repo
    try:
        ids = parse_follows(issues.get(repo, story.number).get("body") or "")
        if not ids:
            return ""
        _, text = record.split(issues.get(repo, story.parent).get("body") or "")
    except Exception:  # noqa: BLE001 - context, never a reason to stop the work
        return ""
    return record.rows_for(text, ids)


def problems(proposal: Any, rows: list[str], tests: Sequence[str] = ()) -> list[str]:
    """What is wrong between a proposed split and the record's rows. Empty if nothing.

    Only a split that makes stories is checked: one that finds everything already
    delivered has nothing to follow the rows. `tests` are the merged tests the record
    says the epic changes (C rows): each is carried into a story's Existing tests
    line or dropped with why, as a superseded story is (#248, crew#583).
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
    # Only rows count: an open question (Q) or an infra item (I) listed here is harmless.
    left_out = {n.row for n in proposal.not_for_stories if n.row.startswith("R")}
    unknown_left = sorted(left_out - known)
    if unknown_left:
        found.append(f"not_for_stories names {', '.join(unknown_left)}, not rows of the epic.")
    unused = [r for r in rows if r not in followed and r not in left_out]
    if unused:
        found.append(
            f"No story follows {', '.join(unused)}. Name the story that does in its `follows`, "
            "or list the row in `not_for_stories` with why it is not for stories."
        )
    carried = " ".join(s.pinned_behaviour for s in proposal.stories)
    dropped = {d.test for d in getattr(proposal, "tests_dropped", [])}
    silent = [t for t in tests if t not in dropped and t not in carried]
    if silent:
        found.append(
            f"No story changes {', '.join(silent)}, which the record says this epic changes. "
            "Name each in the Existing tests line of the story that changes it, or list it in "
            "`tests_dropped` with why."
        )
    return found


def feedback(found: list[str]) -> str:
    """What the Business Analyst is told when its split doesn't fit the conclusion."""
    return (
        "## Your split doesn't fit the epic's conclusion\n\n"
        + "\n".join(f"- {p}" for p in found)
        + "\n\nThe conclusion's rows are settled: a story follows them, never contradicts them."
    )
