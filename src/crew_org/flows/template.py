"""The files every crew project starts with, from `mqucifer/project-template` (ADR 0030).

The same in every project, and nothing in them depends on the language: CI that
runs what the design names, the board automation, and a `.gitignore`. A new
repository is created from the template. `crew onboard` proposes them to one that
lacks them, as crew-presentation did: GitHub copies a template only once.

The crew's own files (`project.CREW_FILES`) are proposed whenever they differ, so
a project catches up when the template changes. The rest, such as `.gitignore`,
only when missing: a project adds its own lines to them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crew_org.project import CREW_FILES, is_protected

TEMPLATE = "project-template"
CHECKS_WORKFLOW = "checks.yml"

# The template's README describes the template, not the project.
NOT_COPIED = frozenset({"README.md"})


def files(issues: Any) -> dict[str, str]:
    """The template's files, by path, from its default branch."""
    branch = issues.repository(TEMPLATE).get("default_branch") or "main"
    found = {}
    for path in issues.tree(TEMPLATE, branch):
        if path in NOT_COPIED:
            continue
        text = issues.file_at(TEMPLATE, path, branch)
        if text is not None:
            found[path] = text
    return found


def wanted(template: dict[str, str], merged: Path) -> dict[str, str]:
    """What onboarding proposes from `template` to a project checked out at `merged`."""
    return {
        path: text
        for path, text in template.items()
        if is_protected(path, list(CREW_FILES)) or not (merged / path).exists()
    }
