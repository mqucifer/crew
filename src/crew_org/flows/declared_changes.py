"""A release declares the answers it changes, and api_version moves once, at release
(crew#1019).

On 2026-11-03 sprint-metrics' stories took the responses' api_version from "10" to "16"
in a day, one story at a time, so the stories planned to set "12" and "13" couldn't be
met. Its additive check, that every answer the previous image gave keeps its value, was
loosened for good to let one planned change through (mqucifer/sprint-metrics#971).

**The declaration** is `declared-changes.json` at the project's root, the same for a
Python project and a JavaScript one:

    {"api_version": "16",
     "changes": [{"response": "GET /range", "path": "$.sprints[*].working_time",
                  "why": "..."}]}

`api_version` is the version the responses carry now. Each change names an answer a
story added, changed or removed on purpose, by its RFC 9535 JSONPath in the response.
The project's additive check accepts exactly those, and the project's tests hold its
code's api_version to the file's.

**The pattern** is Changesets' and towncrier's: each change writes what it changes for
the release, and the release consumes it. A story adds its change and never touches the
version. The release, DevOps's Task, moves `api_version` once when anything is declared,
by one, and empties `changes`: what it declared ships in that release only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

FILE = "declared-changes.json"
_FIELDS = ("response", "path", "why")

# The release Task's step and its line under Done when, both release Tasks' (crew#1019).
RELEASE_STEP = (
    f"**Answers:** where `{FILE}` lists changes, move its `api_version` up by one, and the "
    "code's with it, then empty `changes`. With none listed, leave both."
)
RELEASE_DONE = f"`{FILE}`, where the project keeps one, has no changes left"


@dataclass(frozen=True)
class Declared:
    api_version: str
    changes: tuple[dict[str, Any], ...] = ()


def read(text: str | None) -> Declared | None:
    """The declaration in `text`, or None for no file. Raises ValueError, saying what's
    wrong, for a file that isn't one."""
    if text is None:
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"it isn't JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("it isn't a JSON object")
    version, changes = raw.get("api_version"), raw.get("changes")
    if not isinstance(version, str) or not version:
        raise ValueError('its "api_version" isn\'t a string')
    if not isinstance(changes, list):
        raise ValueError('its "changes" isn\'t a list')
    for change in changes:
        if not isinstance(change, dict) or not all(
            isinstance(change.get(f), str) and change[f] for f in _FIELDS
        ):
            raise ValueError('each of its "changes" is an object with "response", "path" and "why"')
    return Declared(version, tuple(changes))


def next_version(version: str) -> str:
    """The version a release moves a numbered api_version to, or "" for any other."""
    return str(int(version) + 1) if version.isdigit() else ""
