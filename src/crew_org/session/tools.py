"""The Developer's tools in a session (crew#778, decision 2).

See, find, change, check and finish, over the story's worktree. Files are read and
written here, inside the worktree only; anything that runs the project's code (`run`,
the lint after an edit) runs in the workspace container. No shell, git or network is
offered.

These are the replay's pass 3 tools, which fixed what pass 1 found
(`var/notes/778/replay.md`, `research-tool-design.md`): `search` honours `path` and
takes a pattern, text is told by content, `replace_text` matches whole lines, an
edit that stops a Python file parsing isn't applied (the Sponsor, crew#778), edit
replies show the edited lines, lint is the checks' own and reports only what an edit
added, a failure leads with its first error, and every tool refuses arguments it
doesn't take. Each refusal is a declared rule (ADR 0031), named on its event.
"""

from __future__ import annotations

import ast
import contextlib
import difflib
import errno
import os
import re
import shlex
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from crew_org.flows.attempts import failing_tests, first_error, repair_report
from crew_org.rulebook import Refusal, refuse
from crew_org.session.container import GONE, Gone, Workspace
from crew_org.session.enclosing import Enclosing, enclosing
from crew_org.session.git import INJECTED, OUTPUT_CHARS, Git, parse, restore_args
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
# The tags `image_digest` shows at most.
TAG_LINES = 200
SEARCH_FILES = 30
# Past `SEARCH_HITS`, a file's count names the parts its lines are in when they're this
# few (crew#807).
SEARCH_PARTS = 3
FIND_HITS = 40
# The lines either side of an edit its reply shows (SWE-agent's and OpenHands' editors).
EDIT_CONTEXT = 4
EDIT_LINES = 80
# The lines either side of the closest block shown when `old` isn't found (Aider).
NEAR_CONTEXT = 5
NEAR_RATIO = 0.6
# A run's command, and the lint after an edit: the replay's limits.
RUN_TIMEOUT = 900
LINT_TIMEOUT = 120
# How much of a failed setup's output a run's reply shows: its end, as the start does.
SETUP_TAIL = 1500


def is_text(path: Path) -> bool:
    """Text by its content, not its name: no NUL in the first 8k bytes, and UTF-8.

    git's binary test. Pass 1 refused crew-presentation's one-line
    `site/css/design-system.version` for its extension.
    """
    try:
        with path.open("rb") as f:
            return is_text_bytes(f.read(8192))
    except OSError:
        return False


def is_text_bytes(content: bytes) -> bool:
    """`is_text`, of content already read."""
    chunk = content[:8192]
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


def hit_lines(
    hits: list[tuple[str, int, str]],
    named: Callable[[str], Enclosing | None],
    *,
    counted: bool = False,
) -> list[str]:
    """`search`'s hits under their files, each line named by what encloses it (crew#807).

    `named` gives a file's names (`enclosing`). A line is `  27 [jobs.docker.services.
    postgres]: image: postgres:16`, and lines in a row that share a name are put under
    it once. A file that doesn't parse says so, and its lines are shown as they are.
    `counted`, past `SEARCH_HITS`, each of the first `SEARCH_FILES` files gives its
    count, with the parts its lines are in when there are at most `SEARCH_PARTS`.
    """
    by_file: dict[str, list[tuple[int, str]]] = {}
    for rel, n, line in hits:
        by_file.setdefault(rel, []).append((n, line))
    body: list[str] = []
    for rel, found in list(by_file.items())[: SEARCH_FILES if counted else None]:
        where = named(rel)
        names = [where.name(n) if where else "" for n, _ in found]
        if counted:
            parts = [f"[{name}]" for name in dict.fromkeys(names) if name]
            inside = f", in {', '.join(parts)}" if 0 < len(parts) <= SEARCH_PARTS else ""
            body.append(f"{rel}: {len(found)} lines{inside}")
            continue
        if where and where.broken:
            body.append(
                f"{rel} (it doesn't parse as {where.broken}, so what each line is in isn't shown):"
            )
        else:
            body.append(f"{rel}:")
        for name, run in groupby(zip(names, found, strict=True), key=lambda pair: pair[0]):
            lines = [hit for _, hit in run]
            if not name:
                body += [f"  {n}: {line}" for n, line in lines]
            elif len(lines) == 1:
                body.append(f"  {lines[0][0]} [{name}]: {lines[0][1]}")
            else:
                body += [f"  [{name}]", *(f"    {n}: {line}" for n, line in lines)]
    if counted and len(by_file) > SEARCH_FILES:
        body.append(f"... and {len(by_file) - SEARCH_FILES} more files")
    return body


def line_inside(where: Enclosing | None, n: int) -> str:
    """`view`'s header on what encloses the first line it shows, as `search` names it."""
    if where is None:
        return ""
    if where.broken:
        return f" It doesn't parse as {where.broken}, so what line {n} is in isn't shown."
    return f" Line {n} is in [{where.name(n)}]." if where.name(n) else ""


@dataclass
class Reply:
    text: str
    kind: str  # "ok", "refused", "failed", "passed", "error", "end"
    summary: str  # one line, for when the history is compacted
    detail: dict[str, Any] = field(default_factory=dict)
    # The declared rule a refusal is by (ADR 0031).
    rule: str = ""
    # A lookup that found nothing: no such file or definition, no hits, no tests.
    empty: bool = False


def _spec(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
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
        f"{SEARCH_HITS} lines are shown, grouped by file, `src/` and `tests/` first, each "
        "with what it's in: in Python its definition (`Class.method`), in YAML and JSON its "
        "key path (`jobs.docker.services.postgres`), in Markdown its heading. Past that, "
        "each file's count.",
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
        "story's base commit. Give it as `path::Name` or `Name`. For other languages, use "
        "`search`.",
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
        "git",
        "Run one git command line on your branch, as you'd type it, with no shell: "
        "`status`, `diff`, `log`, `show`, `blame` and `grep` read, and `restore "
        "--source=<ref> -- <paths>` puts files back as they are at a commit, applied as an "
        "edit (a file the commit doesn't have is deleted). The story's base, the commit your "
        "branch started from, is the ref `base`: `git diff base`, `git show base:<path>`. "
        "The crew commits your work after each green run, so `git diff base` shows all of "
        "it. Nothing else runs, nor an option that writes a file, reads outside the "
        f"repository or runs a program. Output past {OUTPUT_CHARS} characters is cut.",
        {"command": S},
        ["command"],
    ),
    _spec(
        "image_digest",
        "The digest a registry holds now for an image's tag, to pin it by (`name@sha256:...`): "
        "Docker Hub or GHCR, read by the crew, since your container has no network. `image` "
        "as you'd pull it (`postgres`, `ghcr.io/owner/name`), `tag` as given (`latest` if "
        "none). With `tags: true`, the image's tags instead, those starting with `tag`, up "
        f"to {TAG_LINES}.",
        {"image": S, "tag": S, "tags": {"type": "boolean"}},
        ["image"],
    ),
    _spec(
        "done",
        "Finish: the story is done. First `done` checks that each test you name for a "
        "criterion exists; if one doesn't, the reply says so, and you write it or name the "
        "right one, then call `done` again. Then the crew checks your branch, and returns "
        "it if any "
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
# The tools that end the session, which the loop answers, not `Tools`.
ENDINGS = ("done", "blocked")
EDITS = ("replace_text", "replace_definition", "write_file")


def edited(name: str, reply: Reply) -> list[str]:
    """The files a call's edit changed: an edit tool's, or `git restore`'s (crew#778)."""
    if name in EDITS or reply.detail.get("edit"):
        return list(reply.detail.get("paths") or [str(reply.detail.get("path", ""))])
    return []


class Missing(Refusal):
    """A lookup refused for what isn't there: a file, a folder, a definition, a map."""


def check_arguments(name: str, args: Any, accepted: dict[str, Any] | None = None) -> None:
    """Refuse a call whose arguments aren't an object, or that the tool doesn't take.

    `accepted` is what each of the session's tools takes, the Developer's by default.
    """
    accepted = ACCEPTED if accepted is None else accepted
    spec = accepted.get(name)
    if spec is None:
        refuse("session.arguments", "no_tool", tool=name, tools=", ".join(accepted))
    if not isinstance(args, dict):
        refuse("session.arguments", "not_an_object", tool=name)
    takes = ", ".join(f"`{a}`" for a in spec["properties"])
    unknown = [a for a in args if a not in spec["properties"]]
    if unknown:
        refuse(
            "session.arguments",
            "unknown",
            tool=name,
            unknown=", ".join(f"`{a}`" for a in unknown),
            takes=takes,
        )
    # A form whose every field has a default lists none (crew#807: the settle's).
    missing = [a for a in spec.get("required", []) if a not in args]
    if missing:
        refuse(
            "session.arguments",
            "missing",
            tool=name,
            missing=", ".join(f"`{a}`" for a in missing),
            takes=takes,
        )


def target_of(args: Any) -> str:
    """What a call is about, for its record: a path, or a name looked for."""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "name", "definition", "command"):
        if isinstance(args.get(key), str) and args[key]:
            return str(args[key])
    return ""


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
    """The tools over one worktree, its workspace container and the project's checks.

    `toolchain` is the project's (`workspace.toolchain`): its checks are what `run`
    runs. `notes` is read-only text the start names by path, which isn't in the
    repository.
    """

    def __init__(
        self,
        worktree: Path,
        workspace: Workspace,
        toolchain: Any,
        coverage: Any = None,
        notes: dict[str, str] | None = None,
        *,
        parse_guard: bool = True,
    ) -> None:
        self.root = worktree.resolve()
        self.notes = notes or {}
        self.workspace = workspace
        self.coverage = coverage
        self.verify = [list(c) for c in toolchain.verify]
        # An edit that stops a Python file parsing isn't applied: the Sponsor's decision
        # on crew#778 (SWE-agent's measured guardrail). Off only to compare.
        self.parse_guard = parse_guard
        # Whether a search with no path searches the notes too (crew#807).
        self.search_notes = False
        self.edited: list[str] = []
        self.read: list[str] = []
        self.runs: list[dict[str, Any]] = []
        self.edits_since_run = 0
        # Whether each setup a run sent round again passed (crew#814).
        self.resetups: list[bool] = []
        self.pytest = _prefix(self.verify, "pytest")
        # Lint is the checks': the project's checks that lint. Never its autofix, which the
        # gate runs with its outcome ignored (pass 1 told crew-presentation#83 lint failed
        # on its base's own formatting, from a check-only `ruff format --check`).
        self.lint_cmds = [c for c in self.verify if "ruff" in c or "lint" in " ".join(c)]
        self.ruff = _prefix(self.lint_cmds, "ruff")
        self.repo = Git(self.root)

    def _exec(self, command: list[str], timeout: int, stdin: str | None = None) -> Any:
        done = self.workspace.exec(command, timeout=timeout, stdin=stdin)
        if done.code == GONE:
            raise Gone(done.output[-300:])
        return done

    # --- paths ------------------------------------------------------------------------
    def _path(self, given: str, *, write: bool = False) -> tuple[Path, str]:
        rel = str(given or "").strip().replace("\\", "/").removeprefix("./").rstrip("/")
        pure = PurePosixPath(rel)
        if not rel or pure.is_absolute() or ".." in pure.parts:
            refuse("session.path", "outside", path=given)
        if SKIPPED & set(pure.parts):
            refuse("session.path", "skipped", path=given, verb="write" if write else "read")
        # The tools run on the host, so a symlink in the worktree, the project's or one a
        # process in the container made, mustn't take them out of it (crew#778). What's
        # used is the resolved path, opened without following a link (`_open`).
        target = self.root / rel
        real = target.resolve()
        if not real.is_relative_to(self.root):
            refuse("session.path", "outside", path=given)
        if write and target.is_symlink():
            refuse("session.path", "link", path=given)
        return real, rel

    def _open(self, real: Path, flags: int, given: str) -> int:
        """`real`, a resolved path in the worktree, opened from the worktree's root one
        part at a time with no symlink followed, so a link swapped in after `_path`
        checked it fails here rather than leading out. Creating, the folders are made."""
        parts = real.relative_to(self.root).parts
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                if flags & os.O_CREAT:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, dir_fd=fd)
                inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = inner
            return os.open(parts[-1], flags | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                refuse("session.path", "outside", path=given)
            raise
        finally:
            os.close(fd)

    def _read(self, real: Path, given: str) -> bytes:
        with os.fdopen(self._open(real, os.O_RDONLY, given), "rb") as f:
            return f.read()

    def _text(self, real: Path, given: str) -> str:
        return self._read(real, given).decode("utf-8", errors="replace")

    def _enclosing(self, rel: str) -> Enclosing | None:
        """What encloses each line of a file `search` found, read as `_grep` read it."""
        if rel in self.notes:
            return enclosing(rel, self.notes[rel])
        try:
            return enclosing(rel, self._text(self.root / rel, rel))
        except (OSError, Refusal):
            return None

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
            if not target.is_file():
                refuse("session.view", "no_file", kind=Missing, path=rel)
            content = self._read(target, path)
            if not is_text_bytes(content):
                refuse("session.view", "not_text", path=rel)
            lines = content.decode("utf-8", errors="replace").splitlines()
        if definition:
            node = definitions("\n".join(lines)).get(definition) if rel.endswith(".py") else None
            if node is None:
                names = (
                    ", ".join(list(definitions("\n".join(lines)))[:40])
                    if rel.endswith(".py")
                    else ""
                )
                refuse(
                    "session.view",
                    "no_definition",
                    kind=Missing,
                    path=rel,
                    name=definition,
                    names=f" It defines: {names}." if names else "",
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
        inside = "" if definition else line_inside(enclosing(rel, "\n".join(lines)), start)
        return Reply(
            f"`{what}`{more}{inside}{cut}\n{body}",
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
        # No symlink is followed, to a folder or a file: one could lead out (crew#778).
        for here, folders, files in os.walk(self.root, followlinks=False):
            folders[:] = [f for f in folders if f not in SKIPPED]
            for name in files:
                p = Path(here) / name
                rel = p.relative_to(self.root).as_posix()
                if under and rel != under and not rel.startswith(f"{under}/"):
                    continue
                if not p.is_symlink() and p.is_file() and is_text(p):
                    out.append(p)
        return sorted(out, key=rank)

    def _grep(self, files: list[Path], match: Any) -> list[tuple[str, int, str]]:
        hits = []
        for p in files:
            try:
                text = self._text(p, p.relative_to(self.root).as_posix())
            except (OSError, Refusal):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if match(line):
                    hits.append((p.relative_to(self.root).as_posix(), n, line.strip()[:200]))
        return hits

    def search(self, pattern: str, path: str | None = None) -> Reply:
        if not pattern:
            refuse("session.search", "empty")
        note = path.strip() if path and path.strip() in self.notes else ""
        under = ""
        if path and not note:
            target, under = self._path(path)
            if not target.exists():
                refuse("session.search", "no_path", kind=Missing, path=path)
        files = [] if note else self._files(under)
        # The notes the start names: one that `path` names, or all of them in a session
        # that searches its notes with the repository, as a refinement step does (crew#807).
        notes = (
            {note: self.notes[note]}
            if note
            else self.notes
            if self.search_notes and not under
            else {}
        )

        def grep(match: Any) -> list[tuple[str, int, str]]:
            return self._grep(files, match) + _grep_notes(notes, match)

        how = "as a pattern"
        try:
            hits = grep(re.compile(pattern).search)
            if not hits:
                plain = grep(lambda line: pattern in line)
                if plain:
                    hits, how = plain, "as plain text (as a pattern it found nothing)"
        except re.error:
            hits = grep(lambda line: pattern in line)
            how = "as plain text (it isn't a valid pattern)"
        where = f" in `{under or note}`" if under or note else ""
        head = f"{len(hits)} lines{where} match `{pattern}`, {how}"
        if len(hits) <= SEARCH_HITS:
            body = hit_lines(hits, self._enclosing)
        else:
            counts = Counter(rel for rel, _, _ in hits)
            body = hit_lines(hits, self._enclosing, counted=True)
            head += (
                f", in {len(counts)} files. Too many to show: narrow it with `path` or a "
                "tighter pattern"
            )
        return Reply(
            f"{head}:\n" + "\n".join(body),
            "ok",
            f"searched `{pattern[:40]}`{where}: {len(hits)} lines",
            {"hits": len(hits)},
            empty=not hits,
        )

    def find(self, name: str) -> Reply:
        short = name.split(".")[-1].split("::")[-1]
        defined, uses = [], []
        word = re.compile(rf"\b{re.escape(short)}\b")
        for p in self._files():
            if p.suffix != ".py":
                continue
            rel = str(p.relative_to(self.root))
            try:
                source = self._text(p, rel)
            except (OSError, Refusal):
                continue
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
        return Reply(
            body,
            "ok",
            f"found `{name}`: {len(defined)} definitions, {len(uses)} uses",
            {"definitions": len(defined), "uses": len(uses)},
            empty=not defined,
        )

    def tests_for(self, definition: str) -> Reply:
        if self.coverage is None:
            refuse("session.tests_for", "no_map", kind=Missing)
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
            {"tests": len(found)},
            empty=not found,
        )

    # --- change -------------------------------------------------------------------------
    def _findings(self, rel: str, source: str) -> Counter[tuple[str, str]]:
        """The checks' lint findings for this source as the file `rel`, by rule and message.

        By rule and message, not line: an edit moves the lines below it.
        """
        if not self.ruff:
            return Counter()
        done = self._exec(
            [*self.ruff, "check", "--output-format", "concise", "--stdin-filename", rel, "-"],
            timeout=LINT_TIMEOUT,
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
                    refuse(
                        "session.parse_guard",
                        path=rel,
                        error=exc.msg,
                        line=exc.lineno,
                        after=changed_lines(before, after),
                        before=changed_lines(before, after, side="old"),
                    )
                broken = f" It doesn't parse: {exc.msg} on line {exc.lineno}."
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with os.fdopen(self._open(target, flags, rel), "w", encoding="utf-8") as f:
            f.write(after)
        self.edited.append(rel)
        self.edits_since_run += 1
        notes = broken
        failed = ""
        if rel.endswith(".py") and not broken and self.ruff:
            # The lint is `uv run ruff`, which meets a stale lockfile as a run does (crew#814).
            setup = self._resync()
            if setup is not None and setup.code != 0:
                notes += " Not linted: setup failed, as below."
                failed = "\n\n" + self._setup_said(setup, "the lint")
            else:
                if setup is not None:
                    notes += f" {self._setup_ran(setup)}"
                added = self._findings(rel, after) - self._findings(rel, before or "")
                notes += (
                    " Lint finds nothing new."
                    if not added
                    else " Lint finds, new with this edit: "
                    + "; ".join(f"{rule} {msg}" for (rule, msg) in added)
                    + "."
                )
        return Reply(
            f"{what} `{rel}`.{notes}\nIt now reads:\n{changed_lines(before or '', after)}{failed}",
            "ok",
            f"{what.split()[0].lower()} {rel}",
            {"path": rel},
        )

    def replace_text(self, path: str, old: str, new: str) -> Reply:
        target, rel = self._path(path, write=True)
        if not target.is_file():
            refuse("session.replace_text", "no_file", path=rel)
        text = self._text(target, path)
        old, new = _trim(old), _trim(new)
        if not old.strip():
            refuse("session.replace_text", "empty")
        if old == new:
            refuse("session.replace_text", "same")
        lines = text.splitlines()
        want = old.split("\n")
        at = [i for i in range(len(lines) - len(want) + 1) if lines[i : i + len(want)] == want]
        if len(at) > 1:
            refuse(
                "session.replace_text",
                "twice",
                count=len(at),
                path=rel,
                lines=", ".join(str(i + 1) for i in at),
            )
        if not at:
            self._not_found(rel, text, old, new)
        i = at[0]
        changed = lines[:i] + (new.split("\n") if new else []) + lines[i + len(want) :]
        after = "\n".join(changed) + ("\n" if text.endswith("\n") else "")
        return self._write(rel, target, "Replaced lines in", text, after)

    def _not_found(self, rel: str, text: str, old: str, new: str) -> NoReturn:
        """Refuse `old` for why it isn't there: part of a line, already replaced, or the
        closest block."""
        lines = text.splitlines()
        if old in text:
            where = [n for n, line in enumerate(lines) if old.split("\n")[0] in line]
            refuse(
                "session.replace_text",
                "part_of_a_line",
                where="; ".join(f"line {n + 1}: `{lines[n].strip()}`" for n in where[:3]),
            )
        if new and new in text:
            refuse("session.replace_text", "already", path=rel)
        size = len(old.split("\n"))
        best, best_at = 0.0, -1
        for i in range(max(1, len(lines) - size + 1)):
            ratio = difflib.SequenceMatcher(
                a=old, b="\n".join(lines[i : i + size]), autojunk=False
            ).ratio()
            if ratio > best:
                best, best_at = ratio, i
        if best < NEAR_RATIO:
            refuse("session.replace_text", "not_found", path=rel)
        shown = numbered(lines, best_at - NEAR_CONTEXT, best_at + size + NEAR_CONTEXT)
        refuse(
            "session.replace_text", "closest", path=rel, line=best_at + 1, shown="\n".join(shown)
        )

    def replace_definition(self, path: str, name: str, source: str) -> Reply:
        target, rel = self._path(path, write=True)
        if not rel.endswith(".py") or not target.is_file():
            refuse("session.replace_definition", "not_python", path=rel)
        before = self._text(target, path)
        try:
            changed = apply_edit(before, Edit(name, source))
        except EditError as exc:
            # The edit's own rule, with what the file defines.
            names = ", ".join(list(definitions(before))[:40])
            raise EditError(f"{exc}\n`{rel}` defines: {names}.", rule=exc.rule) from exc
        return self._write(rel, target, f"Replaced `{name}` in", before, changed)

    def write_file(self, path: str, content: str, overwrite: bool = False) -> Reply:
        target, rel = self._path(path, write=True)
        if target.exists() and not overwrite:
            refuse("session.write_file", "exists", path=rel)
        before = self._text(target, path) if target.exists() else None
        return self._write(rel, target, "Wrote", before, content)

    # --- check --------------------------------------------------------------------------
    def run(self, what: str, tests: list[str] | None = None) -> Reply:
        began = time.monotonic()
        if what == "tests":
            if not tests:
                refuse("session.run", "no_tests")
            if not self.pytest:
                refuse("session.run", "no_pytest")
            commands = [[*self.pytest, "-q", *tests]]
        elif what == "lint":
            commands = self.lint_cmds
            if not commands:
                refuse("session.run", "no_lint")
        else:
            commands = self.verify
        setup = self._resync()
        if setup is not None and setup.code != 0:
            return self._setup_failed(what, tests, setup, began)
        results = [self._exec(c, timeout=RUN_TIMEOUT) for c in commands]
        ok = all(r.code == 0 for r in results)
        report = "\n\n".join(f"$ {r.command}\n(exit {r.code})\n{r.output}" for r in results)
        failing = failing_tests(report)
        failed = next((r for r in results if r.code != 0), None)
        first = first_failure(failed.output) if failed else ""
        record = {
            "what": what,
            "tests": tests or [],
            "ok": ok,
            "commands": [
                {"command": r.command, "code": r.code, "seconds": r.seconds}
                for r in ([setup] if setup else []) + results
            ],
            "first_error": first,
            "failing": [f["id"] for f in failing][:20],
            "seconds": round(time.monotonic() - began, 1),
            "edits_before": self.edits_since_run,
        }
        self.runs.append(record)
        self.edits_since_run = 0
        again = f"\n\n{self._setup_ran(setup)}" if setup else ""
        if ok:
            shown = "\n\n".join(
                f"$ {r.command}\n(exit 0)\n" + "\n".join(r.output.splitlines()[-5:])
                for r in results
            )
            return Reply(f"Passed.\n{shown}{again}", "passed", f"run {what}: passed", record)
        command = failed.command if failed else ""
        return Reply(
            f"Failed: `{command}`. First error: {first}\n{repair_report(report)}{again}",
            "failed",
            f"run {what}: `{command}` failed: {first[:120]}",
            record,
        )

    def _setup_failed(self, what: str, tests: list[str] | None, setup: Any, began: float) -> Reply:
        """A run whose setup, sent round again by changed dependency files, failed. Nothing
        else ran, and the reply is setup's own command, exit code and output's end."""
        first = first_failure(setup.output)
        record = {
            "what": what,
            "tests": tests or [],
            "ok": False,
            "commands": [{"command": setup.command, "code": setup.code, "seconds": setup.seconds}],
            "first_error": first,
            "failing": [],
            "seconds": round(time.monotonic() - began, 1),
            "edits_before": self.edits_since_run,
        }
        self.runs.append(record)
        self.edits_since_run = 0
        return Reply(
            "Failed: setup, so nothing else ran. " + self._setup_said(setup, "the checks"),
            "failed",
            f"run {what}: setup failed: {first[:120]}",
            record,
        )

    def _resync(self) -> Any:
        """Setup again first, if the dependency files changed since it last passed: the
        container has no network, and `uv run` re-locks a stale lockfile, which needs one
        (crew#814). Counted, with no output."""
        setup = self.workspace.resync()
        if setup is not None:
            self.resetups.append(setup.code == 0)
        return setup

    @staticmethod
    def _setup_ran(setup: Any) -> str:
        return (
            f"Setup ran first, with the network, as `{setup.command}`: the dependency files "
            "had changed since it last passed."
        )

    @staticmethod
    def _setup_said(setup: Any, before: str) -> str:
        """A failed setup: why it ran, and its command, exit code and output's end."""
        return (
            f"The dependency files changed since it last passed, so it ran again before "
            f"{before}, with the network, and failed. It runs again before the next lint or "
            f"`run`.\n$ {setup.command}\n(exit {setup.code})\n{setup.output[-SETUP_TAIL:]}"
        )

    # --- history ------------------------------------------------------------------------
    def git(self, command: str) -> Reply:
        """One git command line: a read's output, or `restore` applied as an edit."""
        sub, args = parse(command)
        restoring = restore_args(args) if sub == "restore" else None
        self.repo.set_base()
        if restoring:
            return self._restore(*restoring)
        ran = self.repo.run([sub, *INJECTED[sub], *args], limit=OUTPUT_CHARS)
        text = ran.output.decode("utf-8", errors="replace")
        # grep finding nothing, and diff's `--exit-code` finding a change, exit 1.
        ok = ran.code == 0 or (ran.code == 1 and sub in ("grep", "diff"))
        if ran.cut:
            text = (
                text[:OUTPUT_CHARS] + f"\n(cut at {OUTPUT_CHARS} characters: narrow it with a "
                "path, `-n`, `--stat` or `--name-only`)"
            )
        body = (
            text.rstrip() or ran.errors or ("No lines match." if sub == "grep" else "(no output)")
        )
        shown = shlex.join([sub, *args])
        lines = len(text.splitlines())
        return Reply(
            f"$ git {shown}\n" + ("" if ok else f"(exit {ran.code})\n") + body,
            "ok" if ok else "failed",
            f"git {shown[:80]}: " + (f"{lines} lines" if ok else f"exit {ran.code}"),
            {"git": sub, "exit": ran.code, "lines": lines},
            empty=ok and not text.strip() and sub in ("grep", "log"),
        )

    def _restore(self, ref: str, paths: list[str]) -> Reply:
        """`git restore --source=<ref>`: each file as it is at `ref`, written as an edit is,
        or deleted where `ref` doesn't have it. Read with `ls-tree` and `cat-file`, never
        by running `git restore`, so it gets an edit's reply and parse guard. Every file
        is checked before any is written."""
        planned: list[tuple[str, Path, str, bytes | None]] = []
        for given in paths:
            target, rel = self._path(given, write=True)
            if target.is_dir():
                refuse("session.restore", "folder", path=rel, ref=ref)
            listed = self.repo.run(["ls-tree", "-z", ref, "--", rel])
            if listed.code != 0:
                refuse("session.restore", "no_ref", ref=ref, said=listed.errors.strip()[:300])
            entry = next(
                (
                    e
                    for e in listed.output.decode("utf-8", "replace").split("\0")
                    if e.endswith(f"\t{rel}")
                ),
                "",
            )
            mode, kind, sha = entry.partition("\t")[0].split() if entry else ("", "", "")
            if kind == "tree":
                refuse("session.restore", "folder", path=rel, ref=ref)
            if kind not in ("", "blob") or mode == "120000":
                refuse("session.restore", "link", path=rel, ref=ref)
            content = self.repo.run(["cat-file", "blob", sha]).output if sha else None
            if content is not None and not is_text_bytes(content):
                refuse("session.restore", "not_text", path=rel, ref=ref)
            if content is None and not target.is_file():
                refuse("session.restore", "neither", kind=Missing, path=rel, ref=ref)
            planned.append((rel, target, mode, content))
        said: list[str] = []
        applied: list[str] = []
        refused: Refusal | None = None
        for rel, target, mode, content in planned:
            before = self._text(target, rel) if target.is_file() else None
            if content is None:
                self._remove(target, rel)
                self.edited.append(rel)
                self.edits_since_run += 1
                said.append(f"Deleted `{rel}`: `{ref}` doesn't have it.")
                applied.append(rel)
                continue
            after = content.decode("utf-8")
            if after == before:
                said.append(f"`{rel}` is already as it is at `{ref}`: nothing to change.")
                continue
            try:
                written = self._write(rel, target, f"Restored as at `{ref}`:", before, after)
            except Refusal as exc:
                refused = refused or exc
                said.append(str(exc))
                continue
            self._executable(target, rel, mode == "100755")
            said.append(written.text)
            applied.append(rel)
        if refused is not None and not applied:
            raise refused
        return Reply(
            "\n\n".join(said),
            "ok",
            f"restored {', '.join(applied) or 'nothing'} as at {ref}"[:160],
            {"path": (applied or [planned[0][0]])[0], "paths": applied, "edit": bool(applied)},
        )

    def _remove(self, real: Path, given: str) -> None:
        """Delete `real`, a file in the worktree, with no symlink followed on the way."""
        if real.parent == self.root:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        else:
            fd = self._open(real.parent, os.O_RDONLY | os.O_DIRECTORY, given)
        try:
            os.unlink(real.name, dir_fd=fd)
        finally:
            os.close(fd)

    def _executable(self, real: Path, given: str, executable: bool) -> None:
        """Give a restored file the executable bit it has at the ref, as git would."""
        fd = self._open(real, os.O_RDONLY, given)
        try:
            mode = os.fstat(fd).st_mode & 0o777
            wanted = mode | 0o111 if executable else mode & ~0o111
            if wanted != mode:
                os.fchmod(fd, wanted)
        finally:
            os.close(fd)

    def no_progress(self) -> bool:
        """The same first error in the same test after 3 runs with edits between (decision 3)."""
        failed = [r for r in self.runs if not r["ok"]][-3:]
        if len(failed) < 3 or self.runs[-1]["ok"]:
            return False
        same = {(r["first_error"], (r["failing"] or [""])[0]) for r in failed}
        return len(same) == 1 and all(r["edits_before"] > 0 for r in failed[1:])

    def call(self, name: str, args: Any) -> Reply:
        """One tool call's reply. A refusal is a reply that names its rule; only a
        workspace that's gone raises."""
        try:
            check_arguments(name, args)
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
            if name == "git":
                return self.git(args["command"])
            if name == "image_digest":
                return image_digest(args["image"], args.get("tag"), bool(args.get("tags")))
            refuse("session.arguments", "no_tool", tool=name, tools=", ".join(ACCEPTED))
        except Refusal as exc:
            text = str(exc)
            return Reply(
                text,
                "refused",
                f"{name}: refused, {text.splitlines()[0][:100]}",
                rule=exc.rule,
                empty=isinstance(exc, Missing),
            )
        except Gone:
            raise
        except Exception as exc:  # noqa: BLE001 - a tool's failure is its reply
            return Reply(
                f"The tool failed: {type(exc).__name__}: {exc}", "error", f"{name}: failed"
            )


def image_digest(image: str, tag: str | None = None, tags: bool = False) -> Reply:
    """What a registry holds now for an image (crew#839): read by the crew on the host,
    anonymously, so nothing the model wrote runs and its container stays offline."""
    from crew_org.tools import base_images  # noqa: PLC0415

    given = image.strip()
    if "@" in given or not given or given.startswith("-") or any(c.isspace() for c in given):
        refuse("session.image_digest", "not_a_name", image=image)
    ref = base_images.parse(f"{given}:{tag.strip()}" if tag and tag.strip() else given)
    if ref is None:
        refuse("session.image_digest", "a_variable", image=image)
    try:
        if tags:
            found, more = base_images.TAGS(ref.host, ref.name)
            prefix = (tag or "").strip()
            matching = sorted(t for t in found if t.startswith(prefix))
            shown = matching[:TAG_LINES]
            cut = len(matching) > TAG_LINES or more
            text = (
                f"{ref.shown.rsplit(':', 1)[0]}'s tags"
                + (f" starting with `{prefix}`" if prefix else "")
                + f": {', '.join(shown) if shown else 'none'}"
                + (
                    "\n(not all of them: narrow it with `tag`, the start of the ones you want)"
                    if cut
                    else ""
                )
            )
            return Reply(
                text,
                "ok",
                f"image_digest {ref.name} tags: {len(shown)}",
                {"image": ref.name, "tags": len(shown)},
                empty=not shown,
            )
        digest = base_images.lookup(ref.host, ref.name, ref.tag)
    except base_images.Unreadable as exc:
        return Reply(f"The registry couldn't be read: {exc}", "failed", "image_digest: unreadable")
    if digest is None:
        return Reply(
            f"The registry has no `{ref.shown}`. `tags: true` lists the tags it has.",
            "ok",
            f"image_digest {ref.shown}: none",
            {"image": ref.name},
            empty=True,
        )
    return Reply(
        f"`{ref.shown}` is `{digest}` now. Pin it as `{ref.shown}@{digest}`.",
        "ok",
        f"image_digest {ref.shown}",
        {"image": ref.name},
    )


def _grep_notes(notes: dict[str, str], match: Any) -> list[tuple[str, int, str]]:
    """`_grep`'s hits, in the notes a start names, by their names."""
    return [
        (name, n, line.strip()[:200])
        for name, text in notes.items()
        for n, line in enumerate(text.splitlines(), 1)
        if match(line)
    ]


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
