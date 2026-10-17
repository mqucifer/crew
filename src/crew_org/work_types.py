"""The board's work types, and which of them the crew delivers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crew_org.tools.github_project import Card

GOAL = "Goal"
EPIC = "Epic"
STORY = "Story"
BUG = "Bug"

# Work the crew builds, reviews, proves and merges: a story, or a bug a
# collaborator filed (ADR 0027). A check that means "a story of an epic", such
# as the split's or the backlog order's, still names STORY.
DELIVERED = frozenset({STORY, BUG})


def open_in(cards: list[Card], status: str, *, repos: set[str] | None = None) -> list[Card]:
    """The open cards the crew delivers in a column, in the repositories it works.

    Which cards a phase takes: written into each phase until crew#756, where the
    copies could drift apart. A phase that takes more or fewer says so beside this.
    """
    return [
        c
        for c in cards
        if c.status == status
        and c.work_type in DELIVERED
        and c.state != "CLOSED"
        and (repos is None or c.repo in repos)
    ]
