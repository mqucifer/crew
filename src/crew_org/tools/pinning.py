"""The merged tests that pin the behaviour an epic changes, in full, for the Business Analyst.

Roles that decide rather than edit see test files by name only (#230). But a
story that changes behaviour merged tests pin has to say whether that change
is opt-in or an expected contract change, and it can only say so if the role
writing it has read those tests (#189).

The tests come from the project's coverage map (crew#583, step C2, ADR 0024),
not from the epic's words. Matching words found 1 of the 3 merged tests
sprint-metrics#529 broke and 0 of the 5 #507 broke: those tests assert a whole
shape without naming the new keys. The map says which tests run the code the
epic names, a few hundred of them; of those, the ones that check a whole shape
are shown, because they are what a change to a shape breaks:

- a schema validation;
- an equality with a literal list, set or dict of three or more items, written
  inline or bound to a name first;
- a count;
- a file the test reads, asserted on.

A test asserts through its module's own helpers too, one call deep: the docs drift
check asserts in `_check_no_drift`. A test a story problem or the epic's record
names is always shown. A shape a test checks some other way is missed here; the
first failure in delivery is the Business Analyst's to rule on (crew#584).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

# A generous guard, not a budget (§16): it should not fire in normal work, and
# when it does, what was left out is named.
PINNING_CHAR_CEILING = 40_000
_TEST_ID = re.compile(r"([\w/.-]+\.py)::(\w+)")
_WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
_READS = {"read_text", "read_bytes"}


def named_tests(text: str) -> set[tuple[str, str]]:
    """`path::test` ids a story-problem report, or the epic's record, listed."""
    return set(_TEST_ID.findall(text or ""))


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Dict):
        return len(node.keys) >= 3
    return isinstance(node, ast.List | ast.Set | ast.Tuple) and len(node.elts) >= 3


def _checks_a_shape(function: ast.AST, helpers: dict[str, ast.AST], depth: int = 1) -> bool:
    """Whether a test, or a helper of its module it calls, asserts on a whole shape."""
    literals: set[str] = set()
    read: set[str] = set()
    assigned = [
        (node.targets[0].id, node.value)
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    literals = {name for name, value in assigned if _literal(value)}
    # What a file read, and anything made from it: `section` from `text` in the drift check.
    grew = True
    while grew:
        before = len(read)
        for name, value in assigned:
            for n in ast.walk(value):
                if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") in _READS) or (
                    isinstance(n, ast.Name) and n.id in read
                ):
                    read.add(name)
                    break
        grew = len(read) > before
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            called = str(getattr(node.func, "attr", None) or getattr(node.func, "id", ""))
            if "validate" in called:
                return True
            if depth and called in helpers and _checks_a_shape(helpers[called], {}, 0):
                return True
        if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)):
            continue
        for side in (node.test.left, *node.test.comparators):
            if _literal(side) or (isinstance(side, ast.Name) and side.id in literals | read):
                return True
            if isinstance(side, ast.Call) and (
                getattr(side.func, "id", "") == "len" or getattr(side.func, "attr", "") in _READS
            ):
                return True
    return False


class _Tests:
    """A repository's test functions and each module's own helpers, parsed once."""

    def __init__(self, clone: Path) -> None:
        self.clone = clone
        self._modules: dict[str, tuple[str, dict[str, ast.AST], dict[str, ast.AST]] | None] = {}

    def _module(self, path: str) -> tuple[str, dict[str, ast.AST], dict[str, ast.AST]] | None:
        if path not in self._modules:
            try:
                source = (self.clone / path).read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                self._modules[path] = None
                return None
            tests: dict[str, ast.AST] = {}
            helpers: dict[str, ast.AST] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    (tests if node.name.startswith("test") else helpers)[node.name] = node
            self._modules[path] = (source, tests, helpers)
        return self._modules[path]

    def _node(self, test: str) -> tuple[str, ast.AST, dict[str, ast.AST]] | None:
        path, _, name = test.partition("::")
        found = self._module(path)
        node = found[1].get(name.split("::")[-1]) if found else None
        return (found[0], node, found[2]) if found and node is not None else None

    def body(self, test: str) -> str:
        found = self._node(test)
        return (ast.get_source_segment(found[0], found[1]) or "") if found else ""

    def checks_a_shape(self, test: str) -> bool:
        found = self._node(test)
        return found is not None and _checks_a_shape(found[1], found[2])

    def restates(self, test: str, tables: list[set[str]]) -> bool:
        """Whether the test writes out most of one of `tables` as a literal of its own."""
        found = self._node(test)
        if found is None:
            return False
        for written in _string_literals(found[1]):
            for table in tables:
                shared = len(written & table)
                if shared >= 3 and shared >= 0.6 * len(written):
                    return True
        return False


def _string_literals(node: ast.AST) -> list[set[str]]:
    """The strings of each list, set, tuple or dict-key literal of three or more."""
    found = []
    for n in ast.walk(node):
        if isinstance(n, ast.List | ast.Set | ast.Tuple):
            items = n.elts
        elif isinstance(n, ast.Dict):
            items = [k for k in n.keys if k is not None]
        else:
            continue
        strings = {
            i.value for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)
        }
        if len(strings) >= 3:
            found.append(strings)
    return found


def _tables(clone: Path, files: list[str]) -> list[set[str]]:
    """The strings each module-level table in `files` holds, three or more to a table."""
    found = []
    for path in files:
        try:
            tree = ast.parse((clone / path).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                strings = {
                    n.value
                    for n in ast.walk(node.value)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                }
                if len(strings) >= 3:
                    found.append(strings)
    return found


def _named_code(clone: Path, text: str, coverage: Any) -> tuple[list[str], list[str]]:
    """The source files the epic names, and the definitions it names by their own name."""
    from crew_org.tools.repo_context import select_files  # noqa: PLC0415

    files = [
        f
        for f in select_files(clone, text)[0]
        if f.endswith(".py") and not Path(f).name.startswith("test_")
    ]
    words = set(_WORD.findall(text or ""))
    units = {u for found in coverage.tests.values() for u in found if "::" in u}
    names = sorted(u for u in units if u.rpartition("::")[2].split(".")[0] in words)
    return files, names


def pinning_tests(clone: Path, epic_text: str, evidence: str = "", *, coverage: Any = None) -> str:
    """The block the Business Analyst is shown: each pinning test in full, by file.

    `epic_text` is the epic with its record. Empty when nothing it names is pinned.
    """
    tests = _Tests(clone)
    wanted = [f"{p}::{t}" for p, t in sorted(named_tests(epic_text) | named_tests(evidence))]
    running = 0
    if coverage is not None:
        files, names = _named_code(clone, epic_text, coverage)
        candidates = coverage.covering(files=files, names=names)
        running = len(candidates)
        shapes = [t for t in candidates if tests.checks_a_shape(t)]
        # Under the ceiling, a test that spells out one of the named files' own
        # tables comes first: an addition to the table breaks exactly that test.
        # The static half of the map brings in every service test of a named file,
        # and sorted by name the three sprint-metrics#551 broke didn't fit (crew#612).
        tables = _tables(clone, files)
        wanted += sorted(shapes, key=lambda t: not tests.restates(t, tables))
    chosen = [(t, body) for t in dict.fromkeys(wanted) if (body := tests.body(t))]
    if not chosen:
        return ""

    lines = [
        "## Tests that pin behaviour this epic touches",
        "",
        "Merged tests that run the code this epic names and check a whole shape (a schema, "
        "an exact list or count, a file's content), and any a story problem or the record "
        "named. A story that changes what one of them asserts must say whether the change "
        "is opt-in, leaving them passing, or an expected contract change that updates them.",
        "",
    ]
    budget, left_out, current = PINNING_CHAR_CEILING, [], None
    for test, body in chosen:
        if len(body) > budget:
            left_out.append(test)
            continue
        budget -= len(body)
        path = test.partition("::")[0]
        if path != current:
            lines += [f"### `{path}`", ""]
            current = path
        lines += ["```python", body, "```", ""]
    if left_out:
        lines.append(
            "Also pinning it, and not shown because they did not fit: "
            + ", ".join(f"`{t}`" for t in left_out)
            + "."
        )
    if running:
        lines.append(
            f"{running} merged tests in all run the code this epic names; the rest don't "
            "check a whole shape."
        )
    return "\n".join(lines)
