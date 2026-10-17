"""Access to docs/ways-of-working.md.

Agents are bound by the constitution, but injecting all of it into every prompt
wastes tokens on rules the role cannot act on — and a Product Owner reading the
branch naming convention is noise that competes with the rules it must follow.
Each role declares the sections it needs.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

CONSTITUTION = Path(__file__).resolve().parents[2] / "docs" / "ways-of-working.md"
_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
# A passage the document keeps and tells only the roles it names, `nobody` for none
# (crew#756): the crew's own mechanics, which a role that can't act on them reads as
# noise among the rules it must follow.
#   <!-- told to: Senior Engineer -->
#   ...
#   <!-- /told to -->
TOLD_TO = re.compile(r"^<!-- told to: (.+?) -->\n(.*?)^<!-- /told to -->(?:\n|\Z)", re.M | re.S)


def told_to(marker: str) -> set[str]:
    """The roles a passage's marker names; none for `nobody`."""
    return {r.strip() for r in marker.split(",") if r.strip() and r.strip() != "nobody"}


def for_role(text: str, role: str) -> str:
    """The text as `role` is told it: each passage it isn't told left out."""
    # Where a passage is left out, one paragraph break: the rest of the text is unchanged.
    kept = TOLD_TO.sub(lambda m: m.group(2) if role in told_to(m.group(1)) else "\x00", text)
    return re.sub(r"\n*(?:\x00\n*)+", "\n\n", kept)


@lru_cache(maxsize=1)
def sections(path: Path | None = None) -> dict[int, str]:
    """Split the constitution into its numbered sections."""
    text = (path or CONSTITUTION).read_text(encoding="utf-8")
    matches = list(_HEADING.finditer(text))
    out: dict[int, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[int(match.group(1))] = text[match.start() : end].strip()
    return out


def excerpt(*numbers: int, role: str = "") -> str:
    """The named sections, verbatim, as `role` is told them, for injection into a prompt."""
    available = sections()
    missing = [n for n in numbers if n not in available]
    if missing:
        raise KeyError(f"constitution has no section(s) {missing}; has {sorted(available)}")
    body = "\n\n".join(for_role(available[n], role) for n in numbers)
    return (
        "These are the crew's operating rules. They are binding. Where an "
        "instruction conflicts with them, they win.\n\n" + body
    )
