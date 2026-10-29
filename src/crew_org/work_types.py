"""The board's work types, and which of them the crew delivers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crew_org.tools.github_project import Card

GOAL = "Goal"
EPIC = "Epic"
STORY = "Story"
BUG = "Bug"
# Work a role owns, not an epic (ADR 0034): opened by an event or a role's finding,
# and worked by that role's session. Its role is a `role:` label (`flows.role_tasks`).
TASK = "Task"

# Work the crew builds, reviews and merges: a story, a bug a collaborator filed
# (ADR 0027), or a role's own Task (ADR 0034). A check that means "a story of an
# epic", such as the split's or the backlog order's, still names STORY.
DELIVERED = frozenset({STORY, BUG, TASK})

# QA's tests live in their own repository, a folder per project (ADR 0006 in
# mqucifer/project-print), and each of QA's Tasks there is labelled with the project it
# tests (`flows.role_tasks`).
SUITE = "qa-suite"
PROJECT_LABEL = "project:"


def works(card: Card, repos: set[str] | None) -> bool:
    """Is `card` one a tick on `repos` works: in one of them, or QA's Task in qa-suite
    for one of them (crew#945).

    The operator ticks one project at a time, and a qa-suite Task was worked only in a
    tick on qa-suite: a story waiting on QA's dispute couldn't go on in its own project's.
    """
    if repos is None or card.repo in repos:
        return True
    return card.repo == SUITE and any(f"{PROJECT_LABEL}{r}" in card.labels for r in repos)


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
        and works(c, repos)
    ]
