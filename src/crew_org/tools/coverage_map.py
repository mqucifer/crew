"""Which merged tests execute which code: a project's coverage map (crew#583, ADR 0024).

A story that changes a response shape breaks tests that assert the whole shape
without naming the new keys, so matching the epic's words against test bodies
can't find them: 1 of 3 for sprint-metrics#529, 0 of 5 for sprint-metrics#507
(crew#584). Which tests *run* the code a change touches is a lookup instead. This
is test impact analysis, also called regression test selection (Rothermel and
Harrold; pytest-testmon and Ekstazi do it in practice).

The project's tests run once per base commit in the sandbox, under coverage with
each test as its own context. Every covered line is then put under the innermost
definition that holds it, so the map answers "which tests execute
`schema.py::single_sprint_schema`", not only "which execute schema.py". A line
outside any definition runs at import, under no test, and maps to the file.

Python only, through pytest-cov: a project with its own sandbox image (crew#376's
profiles) has no map until its toolchain gets one (the plan's Q4).
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crew_org.tools import workspace
from crew_org.tools.ast_edit import _definitions, definitions
from crew_org.tools.regression import _modules, _resolve
from crew_org.tools.sandbox import Sandbox

COVERAGE_DIR = Path("var/coverage")
# Inside the clone while the tests run, removed after: the sandbox writes only to
# the worktree it is given.
WORK = ".crew-coverage"
# Installed beside the project's own dev dependencies, with the network the setup
# step already has. The project's lockfile is not changed.
INSTALL = ["uv", "pip", "install", "--quiet", "pytest-cov"]
RC = f"""\
[run]
omit =
    .venv/*
    tests/*
    {WORK}/*
relative_files = True
data_file = {WORK}/data

[json]
show_contexts = True
"""
MEASURE = [
    "uv",
    "run",
    "--no-sync",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "--cov",
    "--cov-context=test",
    f"--cov-config={WORK}/rc",
    f"--cov-report=json:{WORK}/coverage.json",
]
# The whole suite under coverage, which is slower than a plain run.
TIMEOUT = 900

_PHASE = re.compile(r"\|(setup|run|teardown)$")
_PARAMS = re.compile(r"\[.*\]$")


class CoverageUnavailable(RuntimeError):
    """No map for this project or commit: why, for the event log."""


@dataclass(frozen=True)
class CoverageMap:
    repo: str
    commit: str
    # Test id (`tests/test_x.py::test_y`) to what it executes or reads:
    # `path::Qualified.name` for a definition or a module-level name, `path` for
    # lines outside any.
    tests: dict[str, frozenset[str]]

    def covering(self, files: Iterable[str] = (), names: Iterable[str] = ()) -> list[str]:
        """The tests that execute or read any of these files or definitions.

        A definition named `path::Name` also matches what is inside it, such as
        `path::Name.method`.
        """
        wanted_files = set(files)
        wanted = list(names)
        found = []
        for test, units in self.tests.items():
            for unit in units:
                if unit.partition("::")[0] in wanted_files or any(
                    unit == w or unit.startswith(f"{w}.") for w in wanted
                ):
                    found.append(test)
                    break
        return sorted(found)

    def executed_by(self, test: str) -> frozenset[str]:
        return self.tests.get(test, frozenset())

    def to_json(self) -> str:
        return json.dumps(
            {
                "repo": self.repo,
                "commit": self.commit,
                "tests": {t: sorted(u) for t, u in sorted(self.tests.items())},
            },
            indent=1,
        )

    @classmethod
    def from_json(cls, text: str) -> CoverageMap:
        data = json.loads(text)
        return cls(
            data["repo"],
            data["commit"],
            {t: frozenset(u) for t, u in data["tests"].items()},
        )


def test_id(context: str) -> str | None:
    """`tests/test_x.py::test_y[a]|run` as the test it names, or None for no test.

    Parameters are folded into the one test function: the rows that cite a test
    name the function, and every case of it runs the same code paths or near it.
    """
    if not context:
        return None
    return _PARAMS.sub("", _PHASE.sub("", context)) or None


def _spans(source: str) -> list[tuple[int, int, str]]:
    """Each definition's lines, 1-based and inclusive, decorators included."""
    spans = []
    for name, node in definitions(source).items():
        decorators = getattr(node, "decorator_list", None) or []
        start = decorators[0].lineno if decorators else node.lineno
        spans.append((start, node.end_lineno or node.lineno, name))
    return spans


def _unit_of(spans: list[tuple[int, int, str]], line: int) -> str | None:
    """The innermost definition holding this line, by the narrowest span."""
    holding = [(end - start, name) for start, end, name in spans if start <= line <= end]
    return min(holding)[1] if holding else None


def _module_names(tree: ast.Module) -> set[str]:
    """The names a module assigns at its top level: its constants and tables."""
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            found |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
    return found


class _Reads:
    """Which of the project's module-level names a piece of code reads, as `path::NAME`.

    Coverage sees a constant's lines run once, at import, under no test. A test
    that asserts the whole of a schema table, or runs a function that builds from
    one, reads it all the same: sprint-metrics' `test_schema_gen` reads
    `SINGLE_SPRINT_SCHEMA` through `_generate_schema_files`, and a story that
    added keys to it broke the test (crew#584). Names are read where they are
    used, in the same file or imported from another of the project's files.
    """

    def __init__(self, clone: Path) -> None:
        self.trees = _modules(clone)
        self.files = set(self.trees)
        self.names = {path: _module_names(tree) for path, tree in self.trees.items()}
        self._bound: dict[str, dict[str, tuple[str, str]]] = {}
        self._defined: dict[str, dict[str, ast.stmt]] = {}

    def _imports(self, path: str) -> dict[str, tuple[str, str]]:
        """Each name `path` imports from one of the project's files: (that file, its name)."""
        if path not in self._bound:
            bound: dict[str, tuple[str, str]] = {}
            for node in ast.walk(self.trees[path]):
                if isinstance(node, ast.ImportFrom):
                    target = _resolve(path, node.module, node.level, self.files)
                    if target is not None:
                        for alias in node.names:
                            bound[alias.asname or alias.name] = (target, alias.name)
            self._bound[path] = bound
        return self._bound[path]

    def of(self, path: str, node: ast.AST) -> set[str]:
        if path not in self.trees:
            return set()
        own, bound = self.names[path], self._imports(path)
        found: set[str] = set()
        for name in ast.walk(node):
            if not (isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)):
                continue
            if name.id in own:
                found.add(f"{path}::{name.id}")
            elif name.id in bound:
                target, original = bound[name.id]
                if original in self.names.get(target, ()):
                    found.add(f"{target}::{original}")
        return found

    def of_definition(self, path: str, name: str) -> set[str]:
        if path not in self.trees:
            return set()
        if path not in self._defined:
            self._defined[path] = _definitions(self.trees[path])
        node = self._defined[path].get(name)
        return self.of(path, node) if node is not None else set()


def from_report(repo: str, commit: str, clone: Path, report: dict[str, Any]) -> CoverageMap:
    """The map, from coverage's JSON report with contexts shown.

    Each test holds what it executed, and the module-level names that code reads
    and that the test's own body reads.
    """
    executed: dict[str, set[str]] = {}
    for path, data in report.get("files", {}).items():
        try:
            source = (clone / path).read_text(encoding="utf-8")
        except OSError:
            continue
        spans = _spans(source)
        for line, contexts in (data.get("contexts") or {}).items():
            name = _unit_of(spans, int(line))
            unit = f"{path}::{name}" if name else path
            for context in contexts:
                test = test_id(context)
                if test:
                    executed.setdefault(test, set()).add(unit)
    reads = _Reads(clone)
    tests: dict[str, frozenset[str]] = {}
    for test, units in executed.items():
        found = set(units)
        for unit in units:
            path, _, name = unit.partition("::")
            if name:
                found |= reads.of_definition(path, name)
        path, _, name = test.partition("::")
        found |= reads.of_definition(path, name.replace("::", "."))
        tests[test] = frozenset(found)
    return CoverageMap(repo, commit, tests)


def _head(clone: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True, check=True
    ).stdout.strip()


def stored(repo: str, commit: str, *, store: Path = COVERAGE_DIR) -> Path:
    return store / repo / f"{commit}.json"


def build(
    clone: Path,
    repo: str,
    *,
    sandbox: Sandbox | None = None,
    store: Path = COVERAGE_DIR,
    run: Callable[..., workspace.CommandResult] = workspace.run,
) -> CoverageMap:
    """The map for the clone's commit: read from the store, or measured and stored.

    Raises `CoverageUnavailable`, with why, when the project has no map.
    """
    commit = _head(clone)
    path = stored(repo, commit, store=store)
    if path.exists():
        return CoverageMap.from_json(path.read_text())
    tools = workspace.toolchain(clone)
    if tools.image:
        raise CoverageUnavailable(f"{repo} has its own sandbox image; the map is Python only")
    if tools.problem:
        raise CoverageUnavailable(tools.problem)
    sandbox = sandbox or Sandbox()
    work = clone / WORK
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    (work / "rc").write_text(RC)
    try:
        for command in [c for c in (tools.setup, INSTALL) if c]:
            done = run(clone, command, sandbox=sandbox, network=True)
            if not done.ok:
                raise CoverageUnavailable(f"`{done.command}` failed: {done.output[-300:]}")
        # The tests' own verdict doesn't matter here: a failing test still ran
        # its code, and the base branch is what it is.
        done = run(clone, MEASURE, sandbox=sandbox, network=False, timeout=TIMEOUT)
        report = work / "coverage.json"
        if not report.exists():
            raise CoverageUnavailable(f"no coverage report: {done.output[-300:]}")
        found = from_report(repo, commit, clone, json.loads(report.read_text()))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not found.tests:
        raise CoverageUnavailable(f"{repo}@{commit[:7]}: no test executed any measured code")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(found.to_json())
    return found
