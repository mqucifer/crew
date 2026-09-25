"""When a project's design is under strain: the same files colliding (#192).

sprint-metrics is one module. Every Sprint 6 story touched it, and four of them
(#73, #95, #100, #101) had to be rebuilt from main because their branches
conflicted in it. Each rebuild is a whole attempt thrown away and every gate
run again. Nothing counted them, so the module's shape was nobody's decision.

This counts them. A rebuild records the files its branch conflicted in. When
enough different stories of one sprint collide in the same file, that file is
evidence the Architect should look at the design. The number starts a look,
not a refactor: the Architect may find nothing to change.

Only rebuilds after the Architect last looked count, so evidence it has
already weighed doesn't trigger it again. Per sprint, so three collisions
spread over months don't add up to strain.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# How many different stories of one sprint must collide in the same file
# before the Architect revisits the design. org.yaml's `design.revisit_conflicts`
# overrides it.
REVISIT_CONFLICTS = 3

# Logged before rebuilds carried their detail: the summary is all there is.
_REBUILT = re.compile(r"^#(\d+) rebuilt from main: its branch conflicted in (.+)$")


@dataclass(frozen=True)
class Rebuild:
    """A story rebuilt from main because its branch conflicted."""

    card: int
    paths: tuple[str, ...]
    at: str
    # None for rebuilds logged before the repository was recorded.
    repo: str | None = None


@dataclass
class Strain:
    """One file that too many of one sprint's stories collided in."""

    sprint: str
    path: str
    cards: list[int] = field(default_factory=list)


def read_rebuilds(events_dir: Path | None) -> list[Rebuild]:
    """Every rebuild the event log recorded, oldest first."""
    if events_dir is None or not events_dir.exists():
        return []
    found: list[Rebuild] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            rebuild = _rebuild(event)
            if rebuild is not None:
                found.append(rebuild)
    return sorted(found, key=lambda r: r.at)


def _rebuild(event: dict[str, Any]) -> Rebuild | None:
    if event.get("kind") != "note":
        return None
    detail = event.get("detail") or {}
    at = event.get("at") or ""
    if detail.get("rebuilt"):
        paths = tuple(p for p in detail.get("paths") or [] if p)
        if detail.get("card") is None or not paths:
            return None
        return Rebuild(card=int(detail["card"]), paths=paths, at=at, repo=detail.get("repo"))
    match = _REBUILT.match(event.get("summary") or "")
    if match is None:
        return None
    paths = tuple(p.strip() for p in match.group(2).split(",") if p.strip())
    if not paths or paths == ("unknown files",):
        return None
    return Rebuild(card=int(match.group(1)), paths=paths, at=at)


def strained(
    rebuilds: Iterable[Rebuild],
    cards: Iterable[Any],
    repo: str,
    *,
    since: str = "",
    threshold: int = REVISIT_CONFLICTS,
    repos: set[str] | None = None,
) -> list[Strain]:
    """The files in `repo` that `threshold` stories of one sprint collided in.

    A rebuild's sprint is its card's, read off the board. Only rebuilds after
    `since` (ISO 8601) count. A rebuild logged without its repository counts
    only when its card number belongs to `repo` alone among `repos`: card
    numbers repeat across repositories, and a guess would be someone else's
    evidence.
    """
    cards = list(cards)
    sprint_of = {c.number: c.sprint for c in cards if (c.repo or repo) == repo and c.sprint}
    elsewhere = {
        c.number for c in cards if c.repo and c.repo != repo and (repos is None or c.repo in repos)
    }
    collided: dict[tuple[str, str], list[int]] = {}
    for rebuild in rebuilds:
        if since and _when(rebuild.at) <= _when(since):
            continue
        if rebuild.repo is not None and rebuild.repo != repo:
            continue
        if rebuild.repo is None and rebuild.card in elsewhere:
            continue
        sprint = sprint_of.get(rebuild.card)
        if sprint is None:
            continue
        for path in rebuild.paths:
            stories = collided.setdefault((sprint, path), [])
            if rebuild.card not in stories:
                stories.append(rebuild.card)
    return [
        Strain(sprint=sprint, path=path, cards=sorted(stories))
        for (sprint, path), stories in sorted(collided.items())
        if len(stories) >= threshold
    ]


def _when(stamp: str) -> datetime:
    """The log writes microseconds and GitHub doesn't: compare times, not strings."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def evidence(strains: list[Strain]) -> str:
    """The strain, as the reason the Architect is given and the Sponsor reads."""
    return "\n".join(
        f"- In {s.sprint}, {len(s.cards)} stories had to be rebuilt from main because "
        f"their branches conflicted in `{s.path}`: " + ", ".join(f"#{n}" for n in s.cards)
        for s in strains
    )
