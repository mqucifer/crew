"""The Developer's tools for a session (crew#778, decision 2; Stage R).

See, find, change, check and finish, over a worktree at the story's base commit. Files
are read and written here, inside the worktree only; anything that runs the project's
code (`run`, the lint after an edit) runs in the held container. No shell, git or
network is offered.
"""

from __future__ import annotations

import ast
import difflib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from crew_org.flows.attempts import failing_tests, first_error, repair_report
from crew_org.tools.ast_edit import Edit, EditError, Operation, apply_edit, definitions
from crew_org.tools.repo_context import is_text

# Never read or written: the crew's own folders and what tools make.
SKIPPED = {
    ".git",
    ".crew-home",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
}
VIEW_LINES = 400
VIEW_CHARS = 40_000
SEARCH_HITS = 50
FIND_HITS = 40


@dataclass
class Reply:
    text: str
    kind: str  # "ok", "refused", "failed", "passed", "end"
    summary: str  # one line, for when it's older than the latest of its kind
    detail: dict[str, Any] = field(default_factory=dict)


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


S = {"type": "string"}
N = {"type": "integer"}

SPECS = [
    _spec(
        "view",
        "See part of a file, with line numbers: a range of lines, or for Python one definition "
        "by name (a function, a class, `Class.method`). Without a range, the first "
        f"{VIEW_LINES} lines.",
        {"path": S, "from_line": N, "to_line": N, "definition": S},
        ["path"],
    ),
    _spec(
        "search",
        "Find text across the repository, `.package/` included: each matching line as "
        f"path:line. At most {SEARCH_HITS} shown, with the total.",
        {"text": S},
        ["text"],
    ),
    _spec(
        "find",
        "Python only: where a definition is, and the lines that call or import it. For other "
        "languages, use `search`.",
        {"name": S},
        ["name"],
    ),
    _spec(
        "tests_for",
        "Python only: the merged tests that run a definition, from the coverage map at the "
        "story's base commit. Give it as `path::Name` or `Name`.",
        {"definition": S},
        ["definition"],
    ),
    _spec(
        "replace_text",
        "Replace text that occurs exactly once in a file, quoted as the file has it. Applied "
        "at once; the reply says whether the file still parses and lints.",
        {"path": S, "old": S, "new": S},
        ["path", "old", "new"],
    ),
    _spec(
        "replace_definition",
        "Python only: replace a top-level definition or a method, by name, with its whole new "
        "source. Applied at once; the reply says whether the file parses and lints.",
        {"path": S, "name": S, "source": S},
        ["path", "name", "source"],
    ),
    _spec(
        "write_file",
        "Write a file whole: a new file, or an existing one with `overwrite: true`. Applied "
        "at once; the reply says whether it parses and lints.",
        {"path": S, "content": S, "overwrite": {"type": "boolean"}},
        ["path", "content"],
    ),
    _spec(
        "run",
        "Run, in the project's container: `tests` (the test ids or files you name), `suite` "
        "(the project's whole checks), or `lint`. Feedback, not a verdict.",
        {
            "what": {"type": "string", "enum": ["tests", "suite", "lint"]},
            "tests": {"type": "array", "items": S},
        },
        ["what"],
    ),
    _spec(
        "done",
        "Finish: the story is done. The crew then checks your branch, and returns it if any "
        "of these fails: each acceptance criterion has a test that proves it and passes (a "
        "criterion about what a user doc says is proven by that doc); the docs that describe "
        "what you changed are updated on the same branch; each merged test you changed or "
        "removed is named here, with why; and the project's whole checks pass. Then review "
        "and QA follow.",
        {
            "criteria": {
                "type": "array",
                "items": {"type": "object", "properties": {"criterion": S, "test": S}},
            },
            "changed_tests": {
                "type": "array",
                "items": {"type": "object", "properties": {"test": S, "why": S}},
            },
            "docs": {
                "type": "string",
                "description": "The docs you updated, or why none describe this change.",
            },
            "summary": S,
        },
        ["criteria", "docs", "summary"],
    ),
    _spec(
        "blocked",
        "Finish without the work: say what's missing, a file, a fact the story doesn't give, "
        "or a choice no one has made.",
        {"missing": S, "kind": {"type": "string", "enum": ["file", "fact", "choice"]}},
        ["missing", "kind"],
    ),
]


class Tools:
    def __init__(
        self, worktree: Path, held: Any, coverage: Any = None, notes: dict[str, str] | None = None
    ) -> None:
        self.root = worktree.resolve()
        # Read-only text the start names by path, which isn't in the repository.
        self.notes = notes or {}
        self.held = held
        self.coverage = coverage
        self.edited: list[str] = []
        self.read: list[str] = []
        self.runs: list[dict[str, Any]] = []
        self.edits_since_run = 0
        tools = held.tools
        self.pytest = _prefix(tools.verify, "pytest")
        self.lint_cmds = [
            c
            for c in (*tools.verify, *tools.autofix)
            if "ruff" in c and "--fix" not in " ".join(c) and c[-2:] != ["format", "."]
        ]
        ruff = _prefix((*tools.verify, *tools.autofix), "ruff")
        self.lint_file = (lambda p: [*ruff, "check", p]) if ruff else None

    # --- paths ------------------------------------------------------------------------
    def _path(self, given: str, *, write: bool = False) -> tuple[Path | None, str]:
        rel = str(given or "").strip().replace("\\", "/").removeprefix("./")
        pure = PurePosixPath(rel)
        if not rel or pure.is_absolute() or ".." in pure.parts:
            return None, f"`{given}` isn't a path in the repository: give it relative to its root."
        if SKIPPED & set(pure.parts):
            return (
                None,
                f"`{given}` is in a folder the session doesn't {'write' if write else 'read'}.",
            )
        return self.root / rel, rel

    # --- see and find -------------------------------------------------------------------
    def view(
        self,
        path: str,
        from_line: int | None = None,
        to_line: int | None = None,
        definition: str | None = None,
    ) -> Reply:
        if path.strip() in self.notes:
            rel = path.strip()
            lines = self.notes[rel].splitlines()
            definition = None
        else:
            target, rel = self._path(path)
            if target is None:
                return Reply(rel, "refused", f"view {path}: refused")
            if not target.is_file():
                return Reply(
                    f"There's no file `{rel}`. The map lists every file; `search` finds text.",
                    "refused",
                    f"view {rel}: no such file",
                )
            if not is_text(target) and target.suffix != ".py":
                return Reply(f"`{rel}` isn't text.", "refused", f"view {rel}: not text")
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if definition:
            node = definitions("\n".join(lines)).get(definition) if rel.endswith(".py") else None
            if node is None:
                names = (
                    ", ".join(list(definitions("\n".join(lines)))[:40])
                    if rel.endswith(".py")
                    else ""
                )
                return Reply(
                    f"`{rel}` defines no `{definition}`."
                    + (f" It defines: {names}." if names else ""),
                    "refused",
                    f"view {rel}::{definition}: not defined",
                )
            start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
            end = node.end_lineno or node.lineno
        else:
            start = max(1, from_line or 1)
            end = min(len(lines), to_line or start + VIEW_LINES - 1)
        shown = [f"{n:5}| {lines[n - 1]}" for n in range(start, end + 1)] if lines else []
        body = "\n".join(shown)
        cut = ""
        if len(body) > VIEW_CHARS:
            body = body[:VIEW_CHARS]
            cut = " (cut at the character limit: view a smaller range)"
        more = f" The file has {len(lines)} lines." if end < len(lines) or start > 1 else ""
        self.read.append(f"{rel}::{definition}" if definition else f"{rel}:{start}-{end}")
        what = f"{rel}::{definition}" if definition else f"{rel} lines {start}-{end}"
        return Reply(
            f"`{what}`{more}{cut}\n{body}",
            "ok",
            f"viewed {what}",
            {"path": rel, "definition": definition, "lines": [start, end]},
        )

    def _files(self) -> list[Path]:
        out = []
        for p in sorted(self.root.rglob("*")):
            parts = p.relative_to(self.root).parts
            if p.is_file() and not (SKIPPED & set(parts)) and (p.suffix == ".py" or is_text(p)):
                out.append(p)
        return out

    def search(self, text: str) -> Reply:
        if not text:
            return Reply("Give the text to find.", "refused", "search: empty")
        hits = []
        for p in self._files():
            try:
                for n, line in enumerate(
                    p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                ):
                    if text in line:
                        hits.append(f"{p.relative_to(self.root)}:{n}: {line.strip()[:200]}")
            except OSError:
                continue
        shown = "\n".join(hits[:SEARCH_HITS])
        return Reply(
            f"{len(hits)} lines contain `{text}`"
            + (f", the first {SEARCH_HITS}" if len(hits) > SEARCH_HITS else "")
            + f":\n{shown}",
            "ok",
            f"searched `{text[:40]}`: {len(hits)} lines",
            {"hits": len(hits)},
        )

    def find(self, name: str) -> Reply:
        short = name.split(".")[-1].split("::")[-1]
        defined, uses = [], []
        word = re.compile(rf"\b{re.escape(short)}\b")
        for p in self._files():
            if p.suffix != ".py":
                continue
            rel = str(p.relative_to(self.root))
            source = p.read_text(encoding="utf-8", errors="ignore")
            for qual, node in definitions(source).items():
                if qual == name or qual.split(".")[-1] == short:
                    defined.append(f"{rel}:{node.lineno} defines `{qual}`")
            for n, line in enumerate(source.splitlines(), 1):
                if word.search(line) and not re.match(
                    rf"\s*(async\s+)?(def|class)\s+{re.escape(short)}\b", line
                ):
                    uses.append(f"{rel}:{n}: {line.strip()[:160]}")
        body = "\n".join(defined) or f"No Python definition named `{name}`."
        body += (
            f"\n{len(uses)} lines call or import it"
            + (f", the first {FIND_HITS}" if len(uses) > FIND_HITS else "")
            + ":\n"
            + "\n".join(uses[:FIND_HITS])
        )
        return Reply(body, "ok", f"found `{name}`: {len(defined)} definitions, {len(uses)} uses")

    def tests_for(self, definition: str) -> Reply:
        if self.coverage is None:
            return Reply(
                "There's no coverage map for this project (it's Python only).",
                "refused",
                "tests_for: no map",
            )
        path, _, name = definition.rpartition("::")
        if path:
            found = self.coverage.covering(names=[definition])
        else:
            found = sorted(
                {
                    t
                    for t, units in self.coverage.tests.items()
                    for u in units
                    if u.split("::")[-1].split(".")[-1] == name or u.endswith(f"::{name}")
                }
            )
        body = "\n".join(found[:FIND_HITS]) or "No merged test runs it."
        return Reply(
            f"{len(found)} merged tests run `{definition}`:\n{body}",
            "ok",
            f"tests_for `{definition}`: {len(found)}",
        )

    # --- change -------------------------------------------------------------------------
    def _checked(self, rel: str, target: Path, what: str) -> Reply:
        self.edited.append(rel)
        self.edits_since_run += 1
        notes = []
        if rel.endswith(".py"):
            try:
                ast.parse(target.read_text(encoding="utf-8"))
                notes.append("It parses.")
            except SyntaxError as exc:
                notes.append(f"It doesn't parse: {exc.msg} on line {exc.lineno}.")
            if self.lint_file is not None:
                lint = self.held.exec(self.lint_file(rel), timeout=120)
                notes.append("Lint is clean." if lint.ok else f"Lint:\n{lint.output[-1500:]}")
        return Reply(f"{what} `{rel}`. " + " ".join(notes), "ok", f"{what} {rel}", {"path": rel})

    def replace_text(self, path: str, old: str, new: str) -> Reply:
        target, rel = self._path(path, write=True)
        if target is None:
            return Reply(rel, "refused", f"replace_text {path}: refused")
        if not target.is_file():
            return Reply(
                f"There's no file `{rel}`: `write_file` makes a new one.",
                "refused",
                f"replace_text {rel}: no such file",
            )
        text = target.read_text(encoding="utf-8")
        count = text.count(old) if old else 0
        if count != 1:
            near = difflib.get_close_matches(
                old.strip().splitlines()[0] if old.strip() else "",
                text.splitlines(),
                n=3,
                cutoff=0.6,
            )
            hint = ("\nClosest lines:\n" + "\n".join(near)) if near and count == 0 else ""
            why = (
                "isn't in the file" if count == 0 else f"occurs {count} times: quote more around it"
            )
            return Reply(
                f"The text to replace in `{rel}` {why}.{hint}",
                "refused",
                f"replace_text {rel}: {why}",
            )
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return self._checked(rel, target, "Replaced text in")

    def replace_definition(self, path: str, name: str, source: str) -> Reply:
        target, rel = self._path(path, write=True)
        if target is None:
            return Reply(rel, "refused", f"replace_definition {path}: refused")
        if not rel.endswith(".py") or not target.is_file():
            return Reply(
                f"`{rel}` isn't an existing Python file: use `replace_text` or `write_file`.",
                "refused",
                f"replace_definition {rel}: not Python",
            )
        try:
            changed = apply_edit(
                target.read_text(encoding="utf-8"), Edit(Operation.REPLACE, name, source)
            )
        except EditError as exc:
            return Reply(str(exc), "refused", f"replace_definition {rel}::{name}: {str(exc)[:60]}")
        target.write_text(changed, encoding="utf-8")
        return self._checked(rel, target, f"Replaced `{name}` in")

    def write_file(self, path: str, content: str, overwrite: bool = False) -> Reply:
        target, rel = self._path(path, write=True)
        if target is None:
            return Reply(rel, "refused", f"write_file {path}: refused")
        if target.exists() and not overwrite:
            return Reply(
                f"`{rel}` exists. Change it with `replace_text` or `replace_definition`, "
                "or rewrite it whole with `overwrite: true`.",
                "refused",
                f"write_file {rel}: exists",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self._checked(rel, target, "Wrote")

    # --- check --------------------------------------------------------------------------
    def run(self, what: str, tests: list[str] | None = None) -> Reply:
        began = time.monotonic()
        if what == "tests":
            if not tests:
                return Reply(
                    "Name the tests (ids or files), or run the `suite`.",
                    "refused",
                    "run tests: none named",
                )
            if not self.pytest:
                return Reply(
                    "This project's checks name no pytest command: run the `suite`.",
                    "refused",
                    "run tests: no pytest",
                )
            commands = [[*self.pytest, "-q", *tests]]
        elif what == "lint":
            commands = self.lint_cmds or []
            if not commands:
                return Reply("This project's checks have no lint.", "refused", "run lint: none")
        else:
            commands = [list(c) for c in self.held.tools.verify]
        results = [self.held.exec(c, timeout=900) for c in commands]
        ok = all(r.ok for r in results)
        report = "\n\n".join(f"$ {r.command}\n(exit {r.code})\n{r.output}" for r in results)
        shown = report if ok else repair_report(report)
        if ok:
            shown = "\n\n".join(
                f"$ {r.command}\n(exit 0)\n" + "\n".join(r.output.splitlines()[-5:])
                for r in results
            )
        failing = failing_tests(report)
        first = first_error(report) if not ok else ""
        record = {
            "what": what,
            "tests": tests or [],
            "ok": ok,
            "first_error": first,
            "failing": [f["id"] for f in failing][:20],
            "seconds": round(time.monotonic() - began, 1),
            "edits_before": self.edits_since_run,
        }
        self.runs.append(record)
        self.edits_since_run = 0
        head = "Passed." if ok else f"Failed. First error: {first}"
        return Reply(
            f"{head}\n{shown}",
            "passed" if ok else "failed",
            f"run {what}: {'passed' if ok else 'failed: ' + first[:80]}",
            record,
        )

    def no_progress(self) -> bool:
        """The same first error in the same test after 3 runs with edits between (decision 3)."""
        failed = [r for r in self.runs if not r["ok"]][-3:]
        if len(failed) < 3 or self.runs[-1]["ok"]:
            return False
        same = {(r["first_error"], (r["failing"] or [""])[0]) for r in failed}
        return len(same) == 1 and all(r["edits_before"] > 0 for r in failed[1:])

    def call(self, name: str, args: dict[str, Any]) -> Reply:
        try:
            if name == "view":
                return self.view(
                    args.get("path", ""),
                    args.get("from_line"),
                    args.get("to_line"),
                    args.get("definition"),
                )
            if name == "search":
                return self.search(args.get("text", ""))
            if name == "find":
                return self.find(args.get("name", ""))
            if name == "tests_for":
                return self.tests_for(args.get("definition", ""))
            if name == "replace_text":
                return self.replace_text(
                    args.get("path", ""), args.get("old", ""), args.get("new", "")
                )
            if name == "replace_definition":
                return self.replace_definition(
                    args.get("path", ""), args.get("name", ""), args.get("source", "")
                )
            if name == "write_file":
                return self.write_file(
                    args.get("path", ""), args.get("content", ""), bool(args.get("overwrite"))
                )
            if name == "run":
                return self.run(args.get("what", "suite"), args.get("tests"))
        except Exception as exc:  # noqa: BLE001 - a tool's failure is its reply
            return Reply(
                f"The tool failed: {type(exc).__name__}: {exc}", "refused", f"{name}: failed"
            )
        return Reply(f"There's no tool `{name}`.", "refused", f"{name}: no such tool")


def _prefix(commands: Any, word: str) -> list[str]:
    """The words of the first command that runs `word`, up to and including it."""
    for command in commands:
        if word in command:
            return list(command[: command.index(word) + 1])
    return []
