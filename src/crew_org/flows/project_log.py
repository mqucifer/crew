"""A project's own decision log, as the panel, the settle step and the split see it (crew#468).

Decisions that apply to a whole project (how a sprint counts a story, the telemetry
standard, the versioning rule) name no Goal, so the Goal-based collection rule never
finds them, and the panel replay missed the findings they settle. Each project keeps
them in its repository as ADRs, in `docs/decisions/`, the form the crew uses for itself
(ADR 0016).

Only Accepted entries are shown, and of each only its title and its Decision section:
the context and consequences are for a person reading the log, and the panel is given
what was decided. A superseded entry names its successor and is left out.
"""

from __future__ import annotations

import re
from typing import Any

DECISIONS_DIR = "docs/decisions"
_ENTRY = re.compile(r"^\d{4}-.+\.md$")
_TITLE = re.compile(r"^#\s+(.+)$", re.M)
_STATUS = re.compile(r"^-\s+\*\*Status:\*\*\s*(.+)$", re.M)
_DECISION = re.compile(r"^##\s+Decision\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)

HEADER = (
    "## The project's decision log\n\n"
    "Decided for every epic in this project. An epic, a row or a story follows these; "
    "it never contradicts one."
)


def accepted(text: str) -> tuple[str, str] | None:
    """An entry's title and Decision section, if it is Accepted. None otherwise."""
    status = _STATUS.search(text)
    if not status or not status.group(1).strip().lower().startswith("accepted"):
        return None
    title = _TITLE.search(text)
    decision = _DECISION.search(text)
    if not title or not decision or not decision.group(1).strip():
        return None
    return title.group(1).strip(), decision.group(1).strip()


def read_log(issues: Any, repo: str, ref: str = "main") -> str:
    """The project's Accepted decisions, rendered for a role. Empty if it keeps none.

    Context, never a reason to stop the work: a log that can't be read gives nothing.
    """
    try:
        names = sorted(n for n in issues.list_dir(repo, DECISIONS_DIR, ref) if _ENTRY.match(n))
        entries = []
        for name in names:
            found = accepted(issues.file_at(repo, f"{DECISIONS_DIR}/{name}", ref) or "")
            if found:
                entries.append(f"### {found[0]}\n\n{found[1]}")
    except Exception:  # noqa: BLE001
        return ""
    return f"{HEADER}\n\n" + "\n\n".join(entries) if entries else ""
