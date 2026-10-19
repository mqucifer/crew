"""The Developer's tools for a session (crew#778, decision 2; Stage R).

See, find, change, check and finish, over a worktree at the story's base commit. Files
are read and written here, inside the worktree only; anything that runs the project's
code (`run`, the lint after an edit) runs in the held container. No shell, git or
network is offered.

Pass 3's tools follow pass 1's findings and `var/notes/778/research-tool-design.md`:
`search` honours `path` and takes a pattern, text is told by content, `replace_text`
matches whole lines, an edit that stops a Python file parsing isn't applied (the
Sponsor, crew#778), edit replies show the edited lines, lint is the gate's and reports
only what an edit added, a failure leads with its first error, and every tool refuses
arguments it doesn't take.
"""

from __future__ import annotations

import ast
import difflib
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from crew_org.flows.attempts import failing_tests, first_error, repair_report
from crew_org.tools.ast_edit import Edit, EditError, apply_edit, definitions

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
SEARCH_FILES = 30
FIND_HITS = 40
# The lines either side of an edit its reply shows (SWE-agent's and OpenHands' editors).
EDIT_CONTEXT = 4
EDIT_LINES = 80
# The lines either side of the closest block shown when `old` isn't found (Aider).
NEAR_CONTEXT = 5
NEAR_RATIO = 0.6


def is_text(path: Path) -> bool:
    """Text by its content, not its name: no NUL in the first 8k bytes, and UTF-8.

    git's binary test. Pass 1 refused crew-presentation's one-line
    `site/css/design-system.version` for its extension.
    """
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)
    except OSError:
        return False
    if b"\0" in chunk:
        return False
    for cut in range(4):  # a read can end inside a multi-byte character
        try:
            chunk[: len(chunk) - cut].decode("utf-8")
            return True
        except UnicodeDecodeError:
            continue
    return False


def numbered(lines: list[str], lo: int, hi: int) -> list[str]:
    """Lines lo..hi (0-based, end excluded) with their 1-based numbers."""
    return [f"{n + 1:5}| {lines[n]}" for n in range(max(0, lo), min(len(lines), hi))]


def changed_spans(before: str, after: str) -> list[tuple[int, int, int, int]]:
    """Each changed region as (old_lo, old_hi, new_lo, new_hi), widened by `EDIT_CONTEXT`."""
    old, new = before.splitlines(), after.splitlines()
    spans: list[list[int]] = []
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        span = [
            max(0, i1 - EDIT_CONTEXT),
            min(len(old), max(i2, i1 + 1) + EDIT_CONTEXT),
            max(0, j1 - EDIT_CONTEXT),
            min(len(new), max(j2, j1 + 1) + EDIT_CONTEXT),
        ]
        if spans and span[2] <= spans[-1][3]:
            spans[-1][1], spans[-1][3] = max(spans[-1][1], span[1]), max(spans[-1][3], span[3])
        else:
            spans.append(span)
    return [(a, b, c, d) for a, b, c, d in spans]


def changed_lines(before: str, after: str, *, side: str = "new") -> str:
    """The lines an edit changed, numbered, with `EDIT_CONTEXT` either side."""
    lines = (after if side == "new" else before).splitlines()
    shown: list[str] = []
    for old_lo, old_hi, new_lo, new_hi in changed_spans(before, after):
        lo, hi = (new_lo, new_hi) if side == "new" else (old_lo, old_hi)
        if shown:
            shown.append("  ...")
        shown += numbered(lines, lo, hi)
    if len(shown) > EDIT_LINES:
        shown = [*shown[:EDIT_LINES], f"  ... ({len(shown) - EDIT_LINES} more lines: view them)"]
    return "\n".join(shown)


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
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
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
        "Find lines matching `pattern`, a regular expression (Python `re`; `(?i)` ignores "
        "case), across the repository, `.package/` included, or in one file or folder with "
        "`path`. A pattern that isn't a valid expression is searched as plain text. Up to "
        f"{SEARCH_HITS} lines are shown, grouped by file, `src/` and `tests/` first; past "
        "that, each file's count.",
        {"pattern": S, "path": S},
        ["pattern"],
    ),
    _spec(
        "find",
        "Python only: where a definition is, and the lines that call or import it, matched by "
        "its last name as a word. For other languages, use `search`.",
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
        "Replace one or more whole lines that occur exactly once in a file, quoted as the file "
        "has them, with enough lines to be unique. Applied at once, unless it would stop a "
        "Python file parsing; the reply shows the edited lines, numbered, and any lint the "
        "edit added.",
        {"path": S, "old": S, "new": S},
        ["path", "old", "new"],
    ),
    _spec(
        "replace_definition",
        "Python only: replace a top-level definition or a method, by name, with its whole new "
        "source. Applied at once, unless the file would stop parsing; the reply shows the "
        "edited lines, numbered, and any lint the edit added.",
        {"path": S, "name": S, "source": S},
        ["path", "name", "source"],
    ),
    _spec(
        "write_file",
        "Write a file whole: a new file, or an existing one with `overwrite: true`. Applied "
        "at once, unless an existing Python file would stop parsing; the reply shows the "
        "edited lines, numbered, and any lint the edit added.",
        {"path": S, "content": S, "overwrite": {"type": "boolean"}},
        ["path", "content"],
    ),
    _spec(
        "run",
        "Run, in the project's container: `tests` (the test ids or files you name), `suite` "
        "(the project's whole checks), or `lint` (the checks' own lint). Feedback, not a "
        "verdict.",
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
ACCEPTED = {s["function"]["name"]: s["function"]["parameters"] for s in SPECS}


def refused_arguments(name: str, args: dict[str, Any]) -> Reply | None:
    """A refusal for arguments the tool doesn't take, or a required one left out."""
    spec = ACCEPTED.get(name)
    if spec is None:
        return None
    takes = list(spec["properties"])
    unknown = [a for a in args if a not in takes]
    missing = [a for a in spec["required"] if a not in args]
    if not unknown and not missing:
        return None
    said = []
    if unknown:
        said.append(f"`{name}` doesn't take {', '.join(f'`{a}`' for a in unknown)}")
    if missing:
        said.append(f"`{name}` needs {', '.join(f'`{a}`' for a in missing)}")
    return Reply(
        "; ".join(said) + f". It takes: {', '.join(f'`{a}`' for a in takes)}. Nothing was done.",
        "refused",
        f"{name}: refused arguments {', '.join(unknown + missing)}",
    )


# The first error line of any form, when the crew's own patterns (lint codes, pytest,
# assert) find none: ruff format's `unformatted:` had no first error in pass 1.
_ANY_ERROR = re.compile(
    r"^.*\b(?:error|Error|ERROR|unformatted|would be reformatted|Traceback|FAILED|failed)\b.*$",
    re.MULTILINE,
)
# Lint findings in ruff's concise format: "path:line:col: CODE message".
_FINDING = re.compile(r"^[^:\n]+:\d+:\d+: (?P<rule>[A-Z]+\d+) (?P<msg>.*)$", re.MULTILINE)


def first_failure(output: str) -> str:
    """The first error of any form, falling back to the output's last line."""
    found = first_error(output)
    if found:
        return found
    match = _ANY_ERROR.search(output)
    if match:
        return match.group(0).strip()[:300]
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:300] if lines else "(no output)"


class Tools:
    def __init__(
        self,
        worktree: Path,
        held: Any,
        coverage: Any = None,
        notes: dict[str, str] | None = None,
        *,
        parse_guard: bool = True,
    ) -> None:
        self.root = worktree.resolve()
        # Read-only text the start names by path, which isn't in the repository.
        self.notes = notes or {}
        self.held = held
        self.coverage = coverage
        # An edit that stops a Python file parsing isn't applied: the Sponsor's decision
        # on crew#778 (SWE-agent's measured guardrail). Off only to compare.
        self.parse_guard = parse_guard
        self.edited: list[str] = []
        self.read: list[str] = []
        self.runs: list[dict[str, Any]] = []
        self.edits_since_run = 0
        tools = held.tools
        self.pytest = _prefix(tools.verify, "pytest")
        # Lint is the gate's: the project's checks that lint. Never its autofix, which the
        # gate runs with its outcome ignored (pass 1 told crew-presentation#83 lint failed
        # on its base's own formatting, from a check-only `ruff format --check`).
        self.lint_cmds = [list(c) for c in tools.verify if "ruff" in c or "lint" in " ".join(c)]
        self.ruff = _prefix(self.lint_cmds, "ruff")

    # --- paths ------------------------------------------------------------------------
    def _path(self, given: str, *, write: bool = False) -> tuple[Path | None, str]:
        rel = str(given or "").strip().replace("\\", "/").removeprefix("./").rstrip("/")
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
            if not is_text(target):
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
        body = "\n".join(numbered(lines, start - 1, end))
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

    def _files(self, under: str = "") -> list[Path]:
        """The text files under a path: `src/` and `tests/` first, then the rest, then
        `docs/`, then hidden folders like `.package/`."""

        def rank(p: Path) -> tuple[int, str]:
            top = p.relative_to(self.root).parts[0]
            order = {"src": 0, "tests": 1, "docs": 3}
            return (4 if top.startswith(".") else order.get(top, 2), str(p))

        out = []
        for p in self.root.rglob("*"):
            rel = p.relative_to(self.root).as_posix()
            if under and rel != under and not rel.startswith(f"{under}/"):
                continue
            if p.is_file() and not (SKIPPED & set(PurePosixPath(rel).parts)) and is_text(p):
                out.append(p)
        return sorted(out, key=rank)

    def _grep(self, files: list[Path], match: Any) -> list[tuple[str, int, str]]:
        hits = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if match(line):
                    hits.append((p.relative_to(self.root).as_posix(), n, line.strip()[:200]))
        return hits

    def search(self, pattern: str, path: str | None = None) -> Reply:
        if not pattern:
            return Reply("Give the pattern to find.", "refused", "search: empty")
        under = ""
        if path:
            target, under = self._path(path)
            if target is None or not target.exists():
                return Reply(
                    f"There's no file or folder `{path}`: the map lists every file.",
                    "refused",
                    f"search in {path}: no such path",
                )
        files = self._files(under)
        how = "as a pattern"
        try:
            hits = self._grep(files, re.compile(pattern).search)
            if not hits:
                plain = self._grep(files, lambda line: pattern in line)
                if plain:
                    hits, how = plain, "as plain text (as a pattern it found nothing)"
        except re.error:
            hits = self._grep(files, lambda line: pattern in line)
            how = "as plain text (it isn't a valid pattern)"
        where = f" in `{under}`" if under else ""
        head = f"{len(hits)} lines{where} match `{pattern}`, {how}"
        if len(hits) <= SEARCH_HITS:
            body: list[str] = []
            last = None
            for rel, n, line in hits:
                if rel != last:
                    body.append(f"{rel}:")
                    last = rel
                body.append(f"  {n}: {line}")
        else:
            counts = Counter(rel for rel, _, _ in hits)
            ordered = list(dict.fromkeys(rel for rel, _, _ in hits))
            body = [f"{rel}: {counts[rel]} lines" for rel in ordered[:SEARCH_FILES]]
            if len(counts) > SEARCH_FILES:
                body.append(f"... and {len(counts) - SEARCH_FILES} more files")
            head += (
                f", in {len(counts)} files. Too many to show: narrow it with `path` or a "
                "tighter pattern"
            )
        return Reply(
            f"{head}:\n" + "\n".join(body),
            "ok",
            f"searched `{pattern[:40]}`{where}: {len(hits)} lines",
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
    def _findings(self, rel: str, source: str) -> Counter[tuple[str, str]]:
        """The gate's lint findings for this source as the file `rel`, by rule and message.

        By rule and message, not line: an edit moves the lines below it.
        """
        if not self.ruff:
            return Counter()
        done = self.held.exec(
            [*self.ruff, "check", "--output-format", "concise", "--stdin-filename", rel, "-"],
            timeout=120,
            stdin=source,
        )
        return Counter((m["rule"], m["msg"]) for m in _FINDING.finditer(done.output))

    def _write(self, rel: str, target: Path, what: str, before: str | None, after: str) -> Reply:
        """Apply an edit, unless it stops a Python file parsing, and say what it did."""
        broken = ""
        if rel.endswith(".py"):
            try:
                ast.parse(after)
            except SyntaxError as exc:
                if self.parse_guard and before is not None and _parses(before):
                    return Reply(
                        f"Your changes have NOT been applied: `{rel}` would stop parsing "
                        f"({exc.msg}, line {exc.lineno}).\nThe lines as they would have been:\n"
                        f"{changed_lines(before, after)}\nThe lines as they are:\n"
                        f"{changed_lines(before, after, side='old')}",
                        "refused",
                        f"{what.split()[0].lower()} {rel}: not applied, it wouldn't parse "
                        f"(line {exc.lineno})",
                        {"path": rel},
                    )
                broken = f" It doesn't parse: {exc.msg} on line {exc.lineno}."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
        self.edited.append(rel)
        self.edits_since_run += 1
        notes = broken
        if rel.endswith(".py") and not broken and self.ruff:
            added = self._findings(rel, after) - self._findings(rel, before or "")
            notes += (
                " Lint finds nothing new."
                if not added
                else " Lint finds, new with this edit: "
                + "; ".join(f"{rule} {msg}" for (rule, msg) in added)
                + "."
            )
        return Reply(
            f"{what} `{rel}`.{notes}\nIt now reads:\n{changed_lines(before or '', after)}",
            "ok",
            f"{what.split()[0].lower()} {rel}",
            {"path": rel},
        )

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
        old, new = _trim(old), _trim(new)
        if not old.strip():
            return Reply("Quote the lines to replace.", "refused", f"replace_text {rel}: empty")
        if old == new:
            return Reply(
                "`old` and `new` are the same: nothing to change.",
                "refused",
                f"replace_text {rel}: no change",
            )
        lines = text.splitlines()
        want = old.split("\n")
        at = [i for i in range(len(lines) - len(want) + 1) if lines[i : i + len(want)] == want]
        if len(at) > 1:
            return Reply(
                f"Those lines occur {len(at)} times in `{rel}`, at lines "
                f"{', '.join(str(i + 1) for i in at)}: quote more lines around the one you mean.",
                "refused",
                f"replace_text {rel}: {len(at)} matches",
            )
        if not at:
            return Reply(
                self._not_found(rel, text, old, new), "refused", f"replace_text {rel}: not found"
            )
        i = at[0]
        changed = lines[:i] + (new.split("\n") if new else []) + lines[i + len(want) :]
        after = "\n".join(changed) + ("\n" if text.endswith("\n") else "")
        return self._write(rel, target, "Replaced lines in", text, after)

    def _not_found(self, rel: str, text: str, old: str, new: str) -> str:
        """Why `old` isn't there: part of a line, already replaced, or the closest block."""
        lines = text.splitlines()
        if old in text:
            where = [n for n, line in enumerate(lines) if old.split("\n")[0] in line]
            return (
                "`old` matches only part of a line, not whole lines: "
                + "; ".join(f"line {n + 1}: `{lines[n].strip()}`" for n in where[:3])
                + ". Quote whole lines."
            )
        if new and new in text:
            return f"`old` isn't in `{rel}`, and `new` already is: the change may be made."
        want = "\n".join(old.split("\n"))
        size = len(old.split("\n"))
        best, best_at = 0.0, -1
        for i in range(max(1, len(lines) - size + 1)):
            ratio = difflib.SequenceMatcher(
                a=want, b="\n".join(lines[i : i + size]), autojunk=False
            ).ratio()
            if ratio > best:
                best, best_at = ratio, i
        if best < NEAR_RATIO:
            return f"Those lines aren't in `{rel}`, and nothing close is: view the file first."
        shown = numbered(lines, best_at - NEAR_CONTEXT, best_at + size + NEAR_CONTEXT)
        return (
            f"Those lines aren't in `{rel}`. The closest block starts at line {best_at + 1}:\n"
            + "\n".join(shown)
        )

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
        before = target.read_text(encoding="utf-8")
        try:
            changed = apply_edit(before, Edit(name, source))
        except EditError as exc:
            names = ", ".join(list(definitions(before))[:40])
            return Reply(
                f"{exc}\n`{rel}` defines: {names}.",
                "refused",
                f"replace_definition {rel}::{name}: {str(exc)[:60]}",
            )
        return self._write(rel, target, f"Replaced `{name}` in", before, changed)

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
        before = target.read_text(encoding="utf-8") if target.exists() else None
        return self._write(rel, target, "Wrote", before, content)

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
            commands = self.lint_cmds
            if not commands:
                return Reply(
                    "This project's checks have no lint: the `suite` is what the crew runs.",
                    "refused",
                    "run lint: none",
                )
        else:
            commands = [list(c) for c in self.held.tools.verify]
        results = [self.held.exec(c, timeout=900) for c in commands]
        ok = all(r.ok for r in results)
        report = "\n\n".join(f"$ {r.command}\n(exit {r.code})\n{r.output}" for r in results)
        failing = failing_tests(report)
        failed = next((r for r in results if not r.ok), None)
        first = first_failure(failed.output) if failed else ""
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
        if ok:
            shown = "\n\n".join(
                f"$ {r.command}\n(exit 0)\n" + "\n".join(r.output.splitlines()[-5:])
                for r in results
            )
            return Reply(f"Passed.\n{shown}", "passed", f"run {what}: passed", record)
        command = failed.command if failed else ""
        return Reply(
            f"Failed: `{command}`. First error: {first}\n{repair_report(report)}",
            "failed",
            f"run {what}: `{command}` failed: {first[:120]}",
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
        refused = refused_arguments(name, args)
        if refused is not None:
            return refused
        try:
            if name == "view":
                return self.view(
                    args["path"], args.get("from_line"), args.get("to_line"), args.get("definition")
                )
            if name == "search":
                return self.search(args["pattern"], args.get("path"))
            if name == "find":
                return self.find(args["name"])
            if name == "tests_for":
                return self.tests_for(args["definition"])
            if name == "replace_text":
                return self.replace_text(args["path"], args["old"], args["new"])
            if name == "replace_definition":
                return self.replace_definition(args["path"], args["name"], args["source"])
            if name == "write_file":
                return self.write_file(args["path"], args["content"], bool(args.get("overwrite")))
            if name == "run":
                return self.run(args["what"], args.get("tests"))
        except Exception as exc:  # noqa: BLE001 - a tool's failure is its reply
            return Reply(
                f"The tool failed: {type(exc).__name__}: {exc}", "refused", f"{name}: failed"
            )
        return Reply(
            f"There's no tool `{name}`: use one of {', '.join(ACCEPTED)}.",
            "refused",
            f"{name}: no such tool",
        )


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def _trim(text: str) -> str:
    """One leading and one trailing newline off, so quoting whole lines is forgiving."""
    text = text[1:] if text.startswith("\n") else text
    return text[:-1] if text.endswith("\n") else text


def _prefix(commands: Any, word: str) -> list[str]:
    """The words of the first command that runs `word`, up to and including it."""
    for command in commands:
        if word in command:
            return list(command[: command.index(word) + 1])
    return []
