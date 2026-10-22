"""A re-split replaces only the stories a story problem affects (crew#859).

The rework gate closed every unstarted story of an epic and split it again from
scratch, and most of Sprint 31's discarded stories went that way. Which stories a
problem affects is found from the links the crew already records, an impact
analysis by traceability:

- the stories the problem names, as references in its comment;
- the stories that follow a row of the record since replaced (their Follows line);
- the stories that build on those. Within an epic that's its order: a story waits
  for every earlier sibling (`siblings.py`), so every later one builds on it. No
  field records which sibling a story needs, and none is invented here.

The rest stay as they are. The replacements are numbered after every kept story,
so the order delivery reads stays true.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from crew_org.flows.conclusion import parse_follows


@dataclass(frozen=True)
class Plan:
    """Which of an epic's open stories a re-split replaces, and which it keeps."""

    named: tuple[int, ...]
    replaced: tuple[int, ...]
    kept: tuple[int, ...]
    # Affected, but work on them has started: the re-split waits, discarding nothing.
    started: tuple[int, ...]
    # Each named story's place among the epic's open stories, 1 first: the evidence
    # for whether links between an epic's stories are worth recording.
    positions: Mapping[int, int]


def named_in(text: str, *, owner: str, repo: str, among: Iterable[int]) -> set[int]:
    """The stories of `among` that `text` references: `#12`, `repo#12`, `owner/repo#12`
    or a link. Another repository's `crew#12` is not one of them."""
    here = rf"(?<![\w/-])(?:{re.escape(owner)}/)?{re.escape(repo)}#"
    link = rf"/{re.escape(owner)}/{re.escape(repo)}/issues/"
    found = re.findall(rf"(?:(?<![\w/])#|{here}|{link})(\d+)\b", text)
    return {int(n) for n in found} & set(among)


def affected(
    stories: Mapping[int, str],
    *,
    named: set[int],
    replaced_rows: set[str],
    started: set[int],
) -> Plan:
    """What a re-split replaces of `stories`, the epic's open stories by number and body.

    A problem that names none of them, and follows no replaced row, affects them all:
    nothing says which it's about.
    """
    order = sorted(stories)
    followers = {n for n in order if set(parse_follows(stories[n])) & replaced_rows}
    first = min(named | followers, default=None)
    replaced = tuple(n for n in order if first is None or n >= first)
    return Plan(
        named=tuple(sorted(named)),
        replaced=replaced,
        kept=tuple(n for n in order if n not in replaced),
        started=tuple(n for n in replaced if n in started),
        positions={n: order.index(n) + 1 for n in sorted(named)},
    )
