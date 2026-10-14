"""A producer's usage notes, followed from a record's `uses` (ADR 0021; crew#690).

Records point to the source's notes and never copy a shape. crew-presentation's
record said "used as the crew's README says", and nothing followed it: the
Architect guessed the package's layout, and a story's gate would have refused
every real package. Each section is read from the producer's default branch, once
per run, so the roles see what it says today.

Read without a token: every producer the crew uses is public. A private one
would need the crew's token here.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from crew_org.project import Use

RAW = "https://raw.githubusercontent.com/{repo}/HEAD/{file}"
TIMEOUT = 15

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@lru_cache(maxsize=32)
def _file(repo: str, file: str) -> str:
    response = httpx.get(RAW.format(repo=repo, file=file), timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.text


def section(markdown: str, heading: str) -> str | None:
    """The text under `heading`, to the next heading at its level or above."""
    wanted = heading.strip().casefold()
    lines = markdown.splitlines()
    level, start, fenced = 0, None, False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else _HEADING.match(line)
        if not found:
            continue
        depth = len(found.group(1))
        if start is None:
            if found.group(2).strip().casefold() == wanted:
                level, start = depth, i + 1
        elif depth <= level:
            return "\n".join(lines[start:i]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else None


def notes(use: Use) -> str:
    """What `use` points to, or why it couldn't be read."""
    try:
        text = _file(use.repo, use.file)
    except httpx.HTTPError as exc:
        return f"(Couldn't read `{use.file}` from {use.repo}: {exc}.)"
    found = section(text, use.section)
    if found is None:
        return f'({use.repo}\'s `{use.file}` has no section "{use.section}".)'
    return found
