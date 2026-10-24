"""Merged tests that use what a story's criteria remove, found before any session (crew#839).

On sprint-metrics#753 the criteria check found merged tests pinning what a criterion
removes, not named on the story's Existing tests line (tests/test_report.py's
escalation column, on the /range story). It found them by reading, in sessions of
about 14 minutes; code can list them.

A criterion's Then is cut into clauses, and a clause that says something is gone,
removed, deleted, retired, renamed or no longer there gives the code names before
that word, and one that removes, deletes, retires, renames or drops something the
names after it: a backticked name, a snake_case, UPPER_CASE or dotted name, a file
path, or an endpoint ("X is gone from report.py, `import sprint_metrics` succeeds"
gives X alone). A name right after "from", "in", "of", "to" or "into" is where the removal
happens or what replaces it, not what goes ("gone from report.py"). In any clause, a
name right after "no", "without" or "nor", or joined to one by "or" ("has no
escalation_rate_percent or escalation_count key"), is one the story says isn't there.

A merged test uses a name when its code has it as a name, an attribute or an import,
or a string holds it whole; an import at a module's top uses it for every test in
the file.

Whether the Existing tests line names a test is the gate's own question, asked with
its code (`gates.unnamed_by_story`), so the split and the gate read the line alike.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.crews.refinement_crew import Story
from crew_org.rulebook import refuse
from crew_org.tools.ast_edit import definitions

# What's gone, said of a name before it ("X is gone") or after it ("deletes X").
_GONE = re.compile(
    r"\b(gone|removed|deleted|retired|renamed|dropped|no longer|not there|absent)\b", re.I
)
_GOES = re.compile(r"\b(removes?|deletes?|retires?|renames?|drops?)\b", re.I)
# Where a clause ends: a semicolon, a dash, a sentence, or a joined clause.
_CLAUSE = re.compile(r";|\s[—–]\s|\.\s|,\s(?:and|while|but|so)\s|\(|\)")
_BACKTICKED = re.compile(r"`([^`\s]+)`")
_NAME = re.compile(
    r"(?<![\w./-])("
    r"[A-Za-z_][\w-]*(?:/[\w.-]+)+"  # a path: schemas/single-sprint.json
    r"|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+"  # dotted: report.py, sprint_metrics.report
    r"|[A-Za-z]\w*_\w*[A-Za-z0-9]"  # snake_case or UPPER_CASE
    r"|[\w-]+\.(?:json|py|md|yaml|yml|toml|csv|sql)"  # a file: single-sprint.json
    r")(?![\w/-])"
)
# An endpoint, as the criteria name it: GET /sprint.
_ROUTE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}-]*\w)")
_PLACE = re.compile(r"\b(?:from|in|of|to|into)\s+(?:the\s+)?$", re.IGNORECASE)
_NOT_THERE = re.compile(
    r"\b(?:no|without|nor)\s+(?:[`'\"]?[\w./-]+[`'\"]?\s+(?:or|nor)\s+)*[`'\"]?$", re.IGNORECASE
)


def removed_names(story: Story) -> list[str]:
    """The code names the story's criteria say are removed, once each, in order."""
    found: dict[str, None] = {}
    for ac in story.acceptance_criteria:
        for clause in _CLAUSE.split(ac.then):
            gone = [m.start() for m in _GONE.finditer(clause)]
            goes = [m.end() for m in _GOES.finditer(clause)]
            for name, at in _names_in(clause):
                if name.startswith("tests/") or name.endswith("/"):
                    continue
                said = any(at < g for g in gone) or any(at > g for g in goes)
                if _NOT_THERE.search(clause[:at]) or said and not _PLACE.search(clause[:at]):
                    found.setdefault(name, None)
    return list(found)


def _names_in(clause: str) -> list[tuple[str, int]]:
    # A backticked name is found at its backtick, so what's before it reads as before.
    ticked = [(m.group(1).strip("()"), m.start()) for m in _BACKTICKED.finditer(clause)]
    rest = _BACKTICKED.sub(lambda m: " " * len(m.group(0)), clause)
    bare = [(m.group(1), m.start()) for m in _NAME.finditer(rest)]
    routes = [(m.group(1), m.start()) for m in _ROUTE.finditer(rest)]
    return [(n, at) for n, at in ticked + bare + routes if n]


@dataclass
class Index:
    """The merged tests of a checkout, each with the names and strings its code uses."""

    tests: dict[str, set[str]] = field(default_factory=dict)
    strings: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def of(cls, root: Path) -> Index:
        index = cls()
        for path in sorted(root.glob("**/*.py")):
            rel = path.relative_to(root).as_posix()
            if not _a_test_file(rel):
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError):
                continue
            # A name imported at the top is used by every test in the file.
            top = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
            if top:
                index._add(rel, top)
            for name, node in definitions(source).items():
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and name.rpartition(
                    "."
                )[2].startswith("test"):
                    index._add(f"{rel}::{name}", [node])
        return index

    def _add(self, key: str, nodes: Sequence[ast.stmt]) -> None:
        names: set[str] = set()
        strings: list[str] = []
        for node in (n for top in nodes for n in ast.walk(top)):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.update({node.name, *node.name.split("."), node.asname or ""})
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.update({node.module, *node.module.split(".")})
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)
        self.tests[key] = names
        self.strings[key] = strings

    def using(self, names: list[str]) -> dict[str, list[str]]:
        """Each test (or whole file, for its imports) that uses any of `names`, with which."""
        found: dict[str, list[str]] = {}
        for key, used in self.tests.items():
            hits = [n for n in names if _uses(n, used, self.strings[key])]
            if hits:
                found[key] = hits
        return found


def _a_test_file(rel: str) -> bool:
    parts = rel.split("/")
    if any(p.startswith(".") or p in {"node_modules", "venv", "site-packages"} for p in parts):
        return False
    name = parts[-1]
    return "tests" in parts[:-1] or name.startswith("test_") or name.endswith("_test.py")


_FILE = re.compile(r"\.(?:json|md|yaml|yml|toml|csv|sql)$")


def _uses(name: str, used: set[str], strings: list[str]) -> bool:
    # A module named as a file is used as its import: report.py is `report`. Another
    # file, or an endpoint, is only ever a string.
    if name.endswith(".py"):
        if name[:-3].rpartition("/")[2] in used:
            return True
    elif not (_FILE.search(name) or name.startswith("/")) and name.rpartition(".")[2] in used:
        return True
    whole = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")
    return any(whole.search(s) for s in strings)


def undeclared(index: Index, story: Story) -> dict[str, list[str]]:
    """The merged tests using what the story removes that its Existing tests line doesn't
    name, each with the names it uses. A test in a file the line names whole is named."""
    from crew_org.flows.board_flow import existing_tests_line  # noqa: PLC0415
    from crew_org.flows.gates import unnamed_by_story  # noqa: PLC0415

    names = removed_names(story)
    if not names:
        return {}
    using = index.using(names)
    line = existing_tests_line(story.pinned_behaviour)
    return {key: using[key] for key in unnamed_by_story(sorted(using), line)}


def described(found: dict[str, list[str]]) -> str:
    """The pre-check's finding as the criteria check reads it: facts, one test a line."""
    if not found:
        return ""
    return (
        "## Merged tests that use what this story removes, not on its Existing tests line\n\n"
        "Found in code before your session, by the names the criteria say are removed. "
        "One may use a name without pinning what the story removes.\n\n"
        + "\n".join(
            f"- `{key}` uses {', '.join(f'`{n}`' for n in names)}" for key, names in found.items()
        )
    )


class Once:
    """The split's `done` refused once for the merged tests `undeclared` finds (crew#839).

    The earliest place to catch them: the Business Analyst names each on its story's
    Existing tests line, or calls `done` again when one doesn't pin what's removed. A
    match by names can be wrong, so the same tests aren't refused twice, and what the
    second `done` leaves goes to the criteria check as facts. `outcome` counts which
    way each went, so the matching can be tuned from evidence.
    """

    def __init__(self, index: Index) -> None:
        self.index = index
        self.refused: dict[str, set[str]] = {}

    def outcome(self, proposal: Any) -> dict[str, int]:
        """Of the tests refused: how many the split then declared, and how many it passed."""
        left = {
            (s.title, k)
            for s in proposal.stories
            for k in undeclared(self.index, s)
            if k in self.refused.get(s.title, set())
        }
        refused = sum(len(v) for v in self.refused.values())
        return {"refused": refused, "declared": refused - len(left), "passed": len(left)}


def refuse_once(once: Once, proposal: Any) -> None:
    """Refuse the split's `done` for each story's undeclared tests not refused before."""
    new = {
        s.title: {k: v for k, v in found.items() if k not in once.refused.get(s.title, set())}
        for s in proposal.stories
        if (found := undeclared(once.index, s))
    }
    new = {title: found for title, found in new.items() if found}
    if not new:
        return
    for title, found in new.items():
        once.refused.setdefault(title, set()).update(found)
    refuse(
        "check.removed_tests.undeclared",
        tests="\n".join(
            f"- *{title}*: "
            + "; ".join(f"`{k}` uses {', '.join(f'`{n}`' for n in v)}" for k, v in found.items())
            for title, found in new.items()
        ),
    )
