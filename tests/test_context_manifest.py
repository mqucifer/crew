"""What each step is shown, checked against the code (crew#583, D7, step A7).

`docs/reference/context.md` lists each step's parts and where each comes from. This
reads it and checks every function, class and method it names still exists, so the
table and the code can't drift apart unnoticed. A drift check, not a gate (the
Sponsor, 2026-10-10): it fails a change that renames or removes what the table
names, and nothing else.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import crew_org

MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "reference" / "context.md"
_NAMED = re.compile(r"`([A-Za-z_][\w]*\.[A-Za-z_][\w.]*)`")
# Things the table names that aren't code: files and paths.
_NOT_CODE = re.compile(r"\.(md|yaml|yml|py|json)$|/")


def _modules() -> dict[str, object]:
    found: dict[str, object] = {}
    for info in pkgutil.walk_packages(crew_org.__path__, "crew_org."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001, S112 - a module that can't import names nothing
            continue
        found.setdefault(info.name.rsplit(".", 1)[-1], module)
    return found


MODULES = _modules()


def _resolves(name: str) -> bool:
    """`module.attr` by a module's short name, or `Class.attr` by a class in any module."""
    head, *rest = name.split(".")
    starts = [MODULES[head]] if head in MODULES else []
    starts += [getattr(m, head) for m in MODULES.values() if hasattr(m, head)]
    for start in starts:
        found = start
        for part in rest:
            found = getattr(found, part, None)
            if found is None:
                break
        else:
            return True
    return False


def _named() -> list[tuple[str, str]]:
    section = ""
    names: list[tuple[str, str]] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:]
        names += [(section, n) for n in _NAMED.findall(line) if not _NOT_CODE.search(n)]
    return names


def test_the_manifest_names_something():
    assert len(_named()) > 10


@pytest.mark.parametrize(("section", "name"), _named())
def test_everything_the_manifest_names_exists_in_the_code(section, name):
    assert _resolves(name), f"{section}: `{name}` isn't in the code; update the table with it"


def test_each_step_the_plan_names_has_its_entry():
    """Every step of the plan's table \"What each step reads after Part A\" (crew#583, D7)."""
    sections = {line[3:] for line in MANIFEST.read_text().splitlines() if line.startswith("## ")}
    for step in (
        "Architect, settling the design questions (before the split)",
        "Architect, the design note (after the split)",
        "Product Owner, answering a story problem",
        "Product Owner, placing the Sponsor's words",
        "Business Analyst, the split",
        "Business Analyst, ruling on a story's contract in delivery",
        "The refinement panel (four members)",
        "Product Owner, settling the panel's notes",
        "QA, the criteria check",
        "Developer, delivering a story",
        "Code Reviewer, judging a diff",
        "QA, accepting a story",
        "Scrum Master, the retro",
    ):
        assert step in sections
