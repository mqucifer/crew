"""The design and presentation notes of epics split before the Goal's look (crew#858).

The notes went with the per-epic panel: an epic's approach and what its reader needs
are set in the Goal's look now, before the split, and the stories are built and
judged against the Goal's record and target diagram. An epic split before then keeps
its notes, and the Developer and the Code Reviewer are shown them, read from their
marked comments, until it closes. Nothing writes a note any more; this only reads.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.tools.github_project import Card

DESIGN_NOTE_MARKER = "<!-- crew:design-note -->"
PRESENTATION_NOTE_MARKER = "<!-- crew:presentation-note -->"
# The split's comment, which a note followed: a note before the latest split is stale.
_SPLIT_MARKER = "<!-- crew:story-split -->"


def note_for(issues: Any, repo: str, epic: int, marker: str) -> str:
    """The epic's note of this kind for its current stories, or '' if it has none.

    Only a note posted since the epic was last split: a note is written against
    the stories, and a re-split makes it stale. sprint-metrics#59 was re-split
    after its note told the reviewer to reject what its criterion required
    (#145), and the old note would have told the new stories the same (#244).
    """
    try:
        comments = issues.comments(repo, epic)
    except Exception:  # noqa: BLE001
        return ""
    bodies = [c.get("body") or "" for c in comments]
    splits = [i for i, b in enumerate(bodies) if _SPLIT_MARKER in b]
    since = bodies[splits[-1] + 1 :] if splits else bodies
    notes = [b for b in since if marker in b]
    return notes[-1] if notes else ""


def story_notes(issues: Any, card: Card, default_repo: str) -> str:
    """The notes a story is built and reviewed against, if its epic has them: its
    design and presentation, cut to this story (lean-prompts plan, L2)."""
    if card.parent is None:
        return ""
    repo = card.repo or default_repo
    notes = [
        note_for(issues, repo, card.parent, m)
        for m in (DESIGN_NOTE_MARKER, PRESENTATION_NOTE_MARKER)
    ]
    return for_story("\n\n".join(n for n in notes if n), card.number or 0)


# Each other story's direction, as much as says what it covers.
OTHER_STORY_CHARS = 200
_DIVIDE = "**How the stories divide the work**"
_PER_STORY = "**What each story adds for the reader**"


def for_story(notes: str, number: int) -> str:
    """An epic's notes as one story needs them (lean-prompts plan, L2).

    The design note's shared parts stay whole; this story's direction stays whole and
    each other story's shrinks to its first sentence, which says what it covers so this
    one doesn't build it. The presentation note's per-story criteria go: this story's
    are already among its own acceptance criteria, and the others' aren't its work. The
    "Looked at" lines go. On mqucifer/sprint-metrics#567 the two notes were 29.4k
    characters, 15.8k of it per-story text for other stories or repeated.
    """
    if not number:
        return notes
    mine = re.compile(rf"(?<!\d)#{number}(?!\d)|/issues/{number}\)")
    out: list[str] = []
    section = ""
    skipping = False
    for line in notes.splitlines():
        if line.startswith(("## ", "**")) and not line.startswith("**Given**"):
            section = line
            skipping = line.startswith(_PER_STORY)
            if skipping:
                out.append("_Each story's reader criteria are among its own acceptance criteria._")
                continue
        if line.startswith("_Looked at"):
            skipping = False
            continue
        if skipping:
            continue
        if section.startswith(_DIVIDE) and line.startswith("- ") and not mine.search(line):
            head, _, rest = line.partition(": ")
            first = re.split(r"(?<=[.;])\s", rest, maxsplit=1)[0]
            line = f"{head}: {first[:OTHER_STORY_CHARS]}" + (
                "…" if len(first) > OTHER_STORY_CHARS or first != rest else ""
            )
        out.append(line)
    return "\n".join(out)
