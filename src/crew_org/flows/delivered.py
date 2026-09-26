"""What a project has already delivered, for the roles deciding what to build next (#220).

Epic sprint-metrics#60 was split into seven stories, and six of them repeated
work Sprint 6 had already delivered (#73, #70, #75, #76, #91, #92). The
Business Analyst was shown the code, and a CLI flag in the code doesn't read
as "this criterion is met". What was delivered, and what it promised, does.

Each delivered story is its title and the outcomes its criteria promised (the
**Then** lines), kept short: the project's history grows with every sprint,
and the whole of every story would crowd out the code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from crew_org.tools.github_project import Card

# Every closed issue since this, which is all of them.
SINCE = "2000-01-01T00:00:00Z"
STORY_TYPE = "Story"
_THEN = re.compile(r"^\s*\**Then\**\s+(.+?)\s*$", re.MULTILINE)
MAX_THEN = 160


@dataclass(frozen=True)
class DeliveredStory:
    number: int
    title: str
    outcomes: tuple[str, ...]


@dataclass
class Delivered:
    stories: list[DeliveredStory] = field(default_factory=list)

    @property
    def numbers(self) -> set[int]:
        return {s.number for s in self.stories}

    def render(self) -> str:
        """The block a role is shown. Empty when nothing is delivered yet."""
        if not self.stories:
            return ""
        lines = []
        for story in self.stories:
            lines.append(f"- #{story.number} {story.title}")
            lines += [f"  - then {outcome}" for outcome in story.outcomes]
        return "\n".join(lines)


def delivered(issues: Any, cards: list[Card], repo: str) -> Delivered:
    """The project's stories closed as completed, oldest first.

    Only stories: the board knows which issues are, and a closed Goal, epic or
    standup isn't a promise a new story could repeat. Only completed: a story
    closed as not planned, a duplicate among them, delivered nothing.
    """
    stories = {
        c.number
        for c in cards
        if (c.repo or repo) == repo and c.work_type == STORY_TYPE and c.number is not None
    }
    found = [
        DeliveredStory(
            number=issue["number"],
            title=issue.get("title") or "",
            outcomes=tuple(then[:MAX_THEN] for then in _THEN.findall(issue.get("body") or "")),
        )
        for issue in issues.closed_since(repo, SINCE)
        if issue.get("state_reason") == "completed" and issue.get("number") in stories
    ]
    return Delivered(sorted(found, key=lambda s: s.number))
