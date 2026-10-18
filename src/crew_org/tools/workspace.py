"""Applying an implementation to a worktree, and checking it.

Running the crew's output means executing model-generated code. The guards here
are bounded blast radius, not a sandbox: an isolated worktree, a hard timeout, a
capped output size, and no inherited credentials. Real isolation needs a
container — see docs/ways-of-working.md and the note in `check`.
"""

from __future__ import annotations

import ast
import os
import subprocess
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crew_org.crews.delivery_crew import FileWrite, TextEdit
from crew_org.rulebook import refuse
from crew_org.tools.sandbox import Mode, Sandbox, prepare_home

if TYPE_CHECKING:
    from crew_org.project import Design

# A runaway test must not hang the tick.
DEFAULT_TIMEOUT = 300
# What a command produced, kept whole. A head-and-tail slice of 3,000
# characters each was "enough to repair from" only if the defect happened to
# sit at one end: story #9's pytest run had thirteen failures, and the model
# repaired three times from a view with the middle cut out before it blocked —
# with 97% of a 262,144-token window unused.
#
# The ceiling below is a guard against a runaway command, not a prompt budget,
# and it keeps the end: a test run puts its summary and its last failure there.
MAX_FAILURE_REPORT_CHARS = 200_000

# Credentials must not be visible to code the model wrote.
STRIPPED_ENV = (
    "GITHUB_TOKEN",
    # The gh CLI's token: Claude's own GitHub account, when a tick runs from its session.
    "GH_TOKEN",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
    "ANTHROPIC_API_KEY",
    "HF_TOKEN",
)


@dataclass
class CommandResult:
    command: str
    code: int
    output: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


@dataclass
class CheckResult:
    """The verdict on an implementation, and the evidence for it."""

    results: list[CommandResult]
    # What the project's own fixers changed before the checks ran, for the
    # attempt's record (crew#758): the files the attempt touched that they fixed,
    # and the files outside it whose changes were undone.
    fixed: list[str] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)
    # Each allowed unsafe fix applied (`UNSAFE_FIXES`), as `CODE path:line`.
    unsafe: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failure_report(self) -> str:
        """What the Developer sees when repairing. Only the failures, whole.

        Bounded once, here, and only against a runaway command — never by the
        old per-command slice, which cut every report whether it needed it or
        not and cut it in the middle, where the failures are.
        """
        parts = []
        for result in self.results:
            if result.ok:
                continue
            reason = "timed out" if result.timed_out else f"exit {result.code}"
            parts.append(f"$ {result.command}\n({reason})\n{result.output}")
        report = "\n\n".join(parts)
        if len(report) > MAX_FAILURE_REPORT_CHARS:
            dropped = len(report) - MAX_FAILURE_REPORT_CHARS
            report = (
                f"… {dropped:,} characters dropped from the start of this report …\n\n"
                + report[-MAX_FAILURE_REPORT_CHARS:]
            )
        return report


def apply_implementation(
    worktree: Path, implementation, *, loose: list[dict[str, Any]] | None = None
) -> list[str]:
    """Write new files and apply edits to existing ones.

    New files and edits are separate on purpose. A new file is written whole;
    an existing file is only ever changed by name, so nothing the model did not
    name can be touched. A quote matched only loosely is added to `loose`.
    """
    from crew_org.tools.ast_edit import Edit, EditError, Operation, apply_edits  # noqa: PLC0415

    root = worktree.resolve()
    written = apply(worktree, implementation.new_files)
    # A quote is of the file as the Developer was shown it, so changes by quoting
    # come before anything else changes it. crew-presentation#73 quoted its whole
    # placeholder and added definitions by name, and the adds, landing before its
    # `__main__` block, split the quote (crew#734). Quoted deletions come last,
    # where one a named edit already made is done.
    changes = [t for t in implementation.text_edits if t.replace]
    deletions = [t for t in implementation.text_edits if not t.replace]
    written += apply_text_edits(worktree, changes, loose=loose)
    # Moves before edits: an edit to the new module can then address what moved in.
    moves = getattr(implementation, "moves", [])
    if moves:
        from crew_org.tools.move_code import apply_moves  # noqa: PLC0415

        written += apply_moves(root, moves)

    by_path: dict[str, list[Edit]] = {}
    tests = getattr(implementation, "criteria_edits", set())
    for item in implementation.all_edits:
        # A criterion's test the file already has is replaced, not added twice:
        # sprint-metrics#93's repair sent its first attempt's tests again (#183).
        # Judged as it's added: crew-presentation#79 wrote its test by renaming a
        # placeholder's, and #119 named one test for four criteria (crew#735).
        by_path.setdefault(item.path, []).append(
            Edit(
                operation=item.operation,
                target=item.target,
                source=item.source,
                replaces_existing=(item.path, item.target) in tests,
            )
        )

    # As each file read before its named edits: a quoted deletion of text a
    # named delete already removed is already done (sprint-metrics#125).
    before = {
        t.path: (root / t.path).read_text(encoding="utf-8")
        for t in deletions
        if (root / t.path).is_file()
    }
    for path, edits in by_path.items():
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            refuse("edit.workspace.apply_implementation", "outside", path=repr(path))
        if not target.exists():
            refuse(
                "edit.workspace.apply_implementation", "missing", kind=EditError, path=repr(path)
            )
        target.write_text(apply_edits(target.read_text(encoding="utf-8"), edits), encoding="utf-8")
        written.append(path)

    written += apply_text_edits(worktree, deletions, before=before, loose=loose)
    # Retiring a test removes it: sprint-metrics#132 named three tests as
    # retired and sent no delete for them, twice, and the one still importing
    # the deleted module was one of them. What's retired is gone.
    for retired in getattr(implementation, "retired_tests", []):
        target = (root / retired.path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue  # its file deleted outright, or never there: nothing to remove
        if _defines(target, retired.test):
            text = target.read_text(encoding="utf-8")
            target.write_text(
                apply_edits(text, [Edit(operation=Operation.DELETE, target=retired.test)]),
                encoding="utf-8",
            )
            if retired.path not in written:
                written.append(retired.path)
    for path in getattr(implementation, "deleted_files", []):
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            refuse("edit.workspace.apply_implementation", "outside", path=repr(path))
        if not target.is_file():
            refuse(
                "edit.workspace.apply_implementation", "not_a_file", kind=EditError, path=repr(path)
            )
        target.unlink()
        written.append(path)
    return written


def _defines(path: Path, name: str) -> bool:
    """Does the file at `path` already define a top-level function called `name`?"""
    import re  # noqa: PLC0415

    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return re.search(rf"^(?:async\s+)?def {re.escape(name)}\b", text, re.MULTILINE) is not None


def apply_text_edits(
    worktree: Path,
    text_edits: list,
    *,
    before: dict[str, str] | None = None,
    loose: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Apply quoted replacements to existing non-Python files (#140).

    Every edit is checked before any file is written, so a quote that doesn't
    match leaves every file as it was rather than half-changed. A quote must
    occur exactly once: zero means it was not copied from the file, and more
    than one means the edit doesn't say which it meant.
    """
    changed = plan_text_edits(worktree, text_edits, before=before, loose=loose)
    root = worktree.resolve()
    for path, text in changed.items():
        (root / path).write_text(text, encoding="utf-8")
    return list(changed)


def plan_text_edits(
    worktree: Path,
    text_edits: list,
    *,
    before: dict[str, str] | None = None,
    loose: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """What each file quoted by `text_edits` would hold afterwards. Writes nothing.

    Raises `EditError` for a quote that isn't in the file, or occurs twice.
    `before` is each file as it was before the named edits: a deletion quoting
    text that was there and that they already removed is skipped, not failed.
    A quote matched only loosely (`_loose_match`) is added to `loose`, for the
    attempt's record.
    """
    before = before or {}
    from crew_org.flows.skeleton import marked  # noqa: PLC0415
    from crew_org.tools.ast_edit import EditError  # noqa: PLC0415

    root = worktree.resolve()
    changed: dict[str, str] = {}
    placeholders: set[str] = set()
    rewritten: set[str] = set()
    for item in text_edits:
        target = (root / item.path).resolve()
        if not target.is_relative_to(root):
            refuse("edit.workspace.plan_text_edits", "outside", path=repr(item.path))
        if item.path not in changed:
            if not target.exists():
                refuse(
                    "edit.workspace.plan_text_edits",
                    "missing",
                    kind=EditError,
                    path=repr(item.path),
                )
            changed[item.path] = target.read_text(encoding="utf-8")
            if marked(changed[item.path]):
                placeholders.add(item.path)
        text = changed[item.path]
        found = text.count(item.find) if item.find else 0
        removed = not item.replace and item.find in before.get(item.path, "")
        if item.find and found == 0 and not removed:
            matched = _loose_match(text, item)
            if matched is not None:
                item, how = matched
                found = 1
                line = text.count("\n", 0, text.index(item.find)) + 1
                if loose is not None:
                    loose.append({"fallback": how, "path": item.path, "line": line})
        if item.path.endswith(".py"):
            try:
                _outside_definitions(item, text)
            except EditError:
                # A walking skeleton's placeholder has no definitions anyone relies
                # on: the story replacing it rewrites it (ADR 0030). crew-presentation
                # stories 73 and 84 rewrote theirs by quoting, and were refused seven
                # times for bringing in definitions (crew#731).
                if item.path not in placeholders:
                    raise
                rewritten.add(item.path)
        if not item.find:
            joiner = "" if not text or text.endswith("\n") else "\n"
            changed[item.path] = text + joiner + item.replace
            continue
        if found == 0 and not item.replace and item.find in before.get(item.path, ""):
            continue  # removing what a named edit already removed
        if found == 0:
            refuse(
                "edit.workspace.plan_text_edits",
                "not_found",
                kind=EditError,
                path=repr(item.path),
                quote=item.find[:300],
            )
        if found > 1:
            refuse(
                "edit.workspace.plan_text_edits",
                "ambiguous",
                kind=EditError,
                count=found,
                path=repr(item.path),
                quote=item.find[:300],
            )
        changed[item.path] = text.replace(item.find, item.replace, 1)
    for path in sorted(rewritten):
        # Still marked, the real file it became could be rewritten by every later story.
        if marked(changed[path]):
            refuse("edit.workspace.plan_text_edits", "placeholder", kind=EditError, path=repr(path))
    for path, text in changed.items():
        if path.endswith(".py"):
            try:
                ast.parse(text)
            except SyntaxError as exc:
                refuse(
                    "edit.workspace.plan_text_edits",
                    "invalid",
                    kind=EditError,
                    path=repr(path),
                    error=exc.msg,
                    line=exc.lineno,
                )
    return changed


def _loose_match(text: str, item: TextEdit) -> tuple[TextEdit, str] | None:
    """The quote as the file has it, when only a known slip keeps it from matching.

    Each fallback is taken only when it finds exactly one place, and the caller
    records which one matched, so a looser match is never silent (crew#734):

    - **quote.final_newline:** a block always ends with a newline (ADR 0026), so a quote
      of part of a line came back with one the file doesn't have there.
      crew-presentation stories 79 and 120 and sprint-metrics stories 589 and 657
      were refused for it.
    - **quote.indent:** the quote's lines, all at one other indent, in spaces.
      sprint-metrics#604 quoted a schema's lines eight spaces deeper than they
      are. The replacement moves by the same amount, its own indentation kept.
    """
    if item.find.endswith("\n") and text.count(item.find[:-1]) == 1:
        replace = item.replace[:-1] if item.replace.endswith("\n") else item.replace
        trimmed = item.model_copy(update={"find": item.find[:-1], "replace": replace})
        return trimmed, "quote.final_newline"
    shifted = _shifted(text, item.find, item.replace)
    if shifted is not None:
        find, replace = shifted
        return item.model_copy(update={"find": find, "replace": replace}), "quote.indent"
    return None


def _shifted(text: str, find: str, replace: str) -> tuple[str, str] | None:
    """`find` and `replace` moved to the one indent where the file has `find`'s lines."""
    if "\t" in find or "\t" in replace:
        return None
    trailing = find.endswith("\n")
    wanted = find[:-1].split("\n") if trailing else find.split("\n")
    if not any(line.strip() for line in wanted):
        return None
    lines = text.split("\n")
    found: list[tuple[int, int]] = []
    for start in range(len(lines) - len(wanted) + 1):
        shift = _one_shift(lines[start : start + len(wanted)], wanted)
        if shift:
            found.append((start, shift))
    if len(found) != 1:
        return None
    start, shift = found[0]
    moved = []
    for line in replace.split("\n"):
        if not line.strip():
            moved.append(line)
        elif shift > 0:
            moved.append(" " * shift + line)
        elif line.startswith(" " * -shift):
            moved.append(line[-shift:])
        else:
            return None  # its own indentation can't be kept
    window = "\n".join(lines[start : start + len(wanted)]) + ("\n" if trailing else "")
    return window, "\n".join(moved)


def _one_shift(lines: list[str], wanted: list[str]) -> int:
    """How many spaces `lines` sit deeper than `wanted`, if every line agrees. 0 if not."""
    shift: int | None = None
    for line, want in zip(lines, wanted, strict=True):
        if not want.strip():
            if line.strip():
                return 0
            continue
        if "\t" in line or line.lstrip(" ") != want.lstrip(" "):
            return 0
        this = (len(line) - len(line.lstrip(" "))) - (len(want) - len(want.lstrip(" ")))
        if shift is None:
            shift = this
        elif this != shift:
            return 0
    return shift or 0


def _outside_definitions(item, text: str) -> None:
    """A text edit to Python may change module-level lines only (#204).

    Imports, an `if __name__ == "__main__":` block, a docstring: a file's lines
    outside any function or class, which have no name for `edits` to address.
    sprint-metrics#133 could not repoint one import in `__main__.py` any other
    way. A definition is still changed by name, where the contract check (§15)
    sees it, so an edit reaching into one, or bringing one in, is refused.
    """
    from crew_org.tools.ast_edit import EditError  # noqa: PLC0415

    how = (
        f"Text edits to a Python file ({item.path!r}) are for its module-level lines: "
        "imports, an `if __name__` block, the docstring. Change or add a function or "
        "class with `edits`, by name."
    )
    try:
        added = ast.parse(textwrap.dedent(item.replace)) if item.replace.strip() else None
    except SyntaxError:
        added = None  # a fragment; the whole file is parsed once every edit is in
    if added is not None and any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for node in ast.walk(added)
    ):
        refuse("edit.workspace.outside_definitions", "brings_in", kind=EditError, how=how)
    # An empty `find` appends, so imports would land after the code that uses them:
    # crew-presentation#73 did it three times and failed each on a NameError at
    # line 10, with the imports at the end of the file (crew#690).
    if (
        not item.find
        and added is not None
        and any(isinstance(node, ast.Import | ast.ImportFrom) for node in added.body)
    ):
        refuse(
            "edit.workspace.outside_definitions",
            "appends_imports",
            kind=EditError,
            path=repr(item.path),
        )
    if not item.find or item.find not in text:
        return  # an append, or a quote the caller reports as not in the file
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return  # nothing to locate definitions in; the file is refused as a whole later
    start = text.index(item.find)
    first = text.count("\n", 0, start) + 1
    # The quote's last line with text: a trailing newline ends a line, it doesn't
    # reach into the next one (an import directly above a function was refused).
    last = first + item.find.rstrip("\n").count("\n")
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        top = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        if first <= (node.end_lineno or node.lineno) and top <= last:
            refuse(
                "edit.workspace.outside_definitions",
                "reaches_into",
                kind=EditError,
                name=node.name,
                how=how,
                replace=_as_a_replace(text, node, item, first, last),
            )


# The most of a definition a refusal shows back, ready to send as a `replace`.
REPLACE_SHOWN_CHARS = 12_000


def _as_a_replace(text: str, node: ast.stmt, item: TextEdit, first: int, last: int) -> str:
    """The definition the quote reaches into, with the quoted change made, to send by name.

    mqucifer/sprint-metrics#585 (2026-10-11) was blocked after three tries at changing
    one cell of a large expected table inside a test with a quoted edit: the refusal
    said what not to do, and copying out the whole test was what it kept avoiding. A
    quote wholly inside one definition (a method, if it's inside one) comes back as
    that definition with the change made; otherwise as it stands.
    """
    lines = text.splitlines()
    target, name = node, node.name
    if isinstance(node, ast.ClassDef):
        for child in node.body:
            if (
                isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                and min([child.lineno, *(d.lineno for d in child.decorator_list)]) <= first
                and last <= (child.end_lineno or child.lineno)
            ):
                target, name = child, f"{node.name}.{child.name}"
    top = min([target.lineno, *(d.lineno for d in getattr(target, "decorator_list", []))])
    end = target.end_lineno or target.lineno
    source = "\n".join(lines[top - 1 : end])
    inside = top <= first and last <= end and source.count(item.find) == 1
    if inside:
        source = source.replace(item.find, item.replace)
    if len(source) > REPLACE_SHOWN_CHARS:
        return f" Send `{name}` whole as a `replace` edit, with your change made in it."
    said = "with your change made" if inside else "as it stands; make your change in it"
    return (
        f" Here is `{name}` {said}: send it back whole as a `replace` edit of `{name}`."
        f"\n\n```python\n{source}\n```"
    )


def apply(worktree: Path, files: list[FileWrite]) -> list[str]:
    """Write an implementation into the worktree. Returns the paths written.

    Paths were validated at the schema boundary; this re-checks containment
    because the cost of being wrong is writing outside the repository.
    """
    written: list[str] = []
    root = worktree.resolve()
    for item in files:
        target = (root / item.path).resolve()
        if not target.is_relative_to(root):
            refuse("edit.workspace.apply", path=repr(item.path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.content, encoding="utf-8")
        written.append(item.path)
    return written


def run(
    worktree: Path,
    command: list[str],
    *,
    sandbox: Sandbox | None = None,
    network: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run one command against the worktree.

    With a sandbox in `required` mode the command runs inside a container; the
    host is never a fallback. Credentials are stripped either way, because the
    host path exists only as an explicit opt-in and still must not hand a
    repository token to code the model wrote. `env` is the project's own
    environment, given to the container.
    """
    sandbox = sandbox or Sandbox()
    printable = " ".join(command)

    if sandbox.mode is Mode.REQUIRED:
        blocked = sandbox.unavailable_reason()
        if blocked:
            return CommandResult(command=printable, code=126, output=blocked)
        prepare_home(worktree)
        argv = sandbox.command(
            worktree, command, network=network, path=sandbox.search_path(), env=env
        )
        cwd = None
    else:
        argv = command
        cwd = worktree

    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    try:
        completed = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            command=printable, code=-1, output=f"no output within {timeout}s", timed_out=True
        )
    except FileNotFoundError as exc:
        return CommandResult(command=printable, code=127, output=str(exc))

    output = (completed.stdout + completed.stderr).strip()
    return CommandResult(command=printable, code=completed.returncode, output=output)


# What a developer's editor does on save. Deterministic, so there is no reason
# to spend a model round-trip on any of it.
AUTOFIX = (
    # Safe fixes only, whatever the project's config says: an unsafe one may not
    # keep what the code meant, and that is the Developer's call (crew#758).
    ["uv", "run", "ruff", "check", "--fix-only", "--no-unsafe-fixes", "."],
    ["uv", "run", "ruff", "format", "."],
)

# The unsafe fixes the default autofix also applies, each with why it may (the
# Sponsor's decision on crew#758, 2026-10-17). An unsafe fix is one ruff says may
# not keep what the code meant; any not named here stays the Developer's. Each
# was chosen from the event log, for a VERIFY failure it alone caused and a
# repair call it cost. Only a finding the project's own config reports, in a file
# the attempt touched, is fixed, and each is on the attempt's record.
UNSAFE_FIXES: dict[str, str] = {
    # sprint-metrics#563, 2026-10-11: its only finding, and one repair call.
    "SIM102": "Joining nested `if`s with `and` runs the same code in the same order. "
    "Ruff calls it unsafe for the comments it can drop, which review sees in the diff.",
    # sprint-metrics#453, 2026-10-07, beside an F841.
    "B905": "`strict=False` is what `zip()` does without it, so behaviour is unchanged. "
    "Ruff calls it unsafe because `strict=True` may have been meant: the tests decide.",
}

VERIFY = (
    ["uv", "run", "ruff", "check", "."],
    ["uv", "run", "pytest", "-q"],
)


# The dependency step when a project's design names none: today's, for Python.
SETUP = ["uv", "sync", "--extra", "dev", "--quiet"]


def shell_operators(command: str) -> list[str]:
    """The shell operators in a design's command, which the crew never runs in a shell.

    Each command is run as its words, so `pip install -r requirements.txt && python
    x.py` reached pip with `&&` as a package name (crew#690). A shell would need
    `shell=True`, which §19.4 rules out. Quoted text is an argument, not an operator.
    """
    import shlex  # noqa: PLC0415

    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return []
    found = [w for w in words if w and set(w) <= set("();<>|&")]
    found += [w for w in words if "$(" in w or "`" in w]
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class Toolchain:
    """What `check` runs for one project, from its design (#403)."""

    image: str | None
    setup: list[str] | None
    autofix: tuple[list[str], ...]
    verify: tuple[list[str], ...]
    problem: str | None = None
    # Whether any part is Python's: the coverage map follows the language, not
    # the image (crew#690, ADR 0030). No parts named means the whole project is.
    python: bool = True
    # The project's own environment, from its devcontainer.json.
    env: dict[str, str] = field(default_factory=dict)


def toolchain(worktree: Path) -> Toolchain:
    """The project's own sandbox image, setup, autofix and checks, or today's defaults.

    The image, setup and environment come from the project's devcontainer.json,
    their one source (`tools/devcontainer.py`), and from its design when it has
    no file. A project that names its own image gets only what it names: the
    Python defaults (uv, ruff) would fail in any other image. One that names none
    keeps them, so sprint-metrics is checked exactly as before.
    """
    import shlex  # noqa: PLC0415

    from crew_org.project import ProjectRecordError, read_record  # noqa: PLC0415
    from crew_org.tools import devcontainer  # noqa: PLC0415

    try:
        record = read_record(worktree)
    except ProjectRecordError:
        record = None
    design = record.design if record is not None else None
    defined = devcontainer.read(worktree)
    if defined is not None:
        image, named = defined.image, f"image in `{defined.path}`"
    else:
        image = design.sandbox_image if design is not None else None
        named = "design's sandbox image"
    own = bool(image)
    verify = (
        tuple(shlex.split(c) for c in design.checks)
        if design is not None and design.checks
        else (() if own else VERIFY)
    )
    if defined is not None:
        setup = defined.setup
    elif design is not None and design.setup:
        setup = shlex.split(design.setup)
    else:
        setup = None if own else SETUP
    if design is not None and design.autofix:
        autofix = tuple(shlex.split(c) for c in design.autofix)
    else:
        autofix = () if own else AUTOFIX
    problem = None
    if defined is not None and defined.problems:
        problem = "; ".join(defined.problems)
    elif own and "@sha256:" not in image:
        # The image rule (§19): what runs is pinned, never a tag that can move.
        problem = f"the {named} `{image}` isn't pinned by digest (name@sha256:...)"
    elif own and not verify:
        problem = (
            f"`{defined.path}` names its own image, but the design names no checks to run in it"
            if defined is not None
            else "the design names its own sandbox image but no checks to run in it"
        )
    elif design is not None and (shelled := _shelled(design)):
        problem = shelled
    parts = design.parts if design is not None else []
    python = not parts or any(p.language.strip().lower() == "python" for p in parts)
    return Toolchain(
        image=image,
        setup=setup,
        autofix=autofix,
        verify=verify,
        problem=problem,
        python=python,
        env=dict(defined.env) if defined is not None else {},
    )


def _shelled(design: Design) -> str | None:
    """Why a design's commands can't run as written, or None (crew#690)."""
    commands = [design.setup or "", *design.checks, *design.autofix]
    for command in commands:
        if found := shell_operators(command):
            return (
                f"the design's command `{command}` uses {', '.join(f'`{o}`' for o in found)}, "
                "but the crew runs each command without a shell (§19.4). The Architect "
                "names one command, a script if it needs several steps"
            )
    return None


def check(
    worktree: Path, *, sandbox: Sandbox | None = None, timeout: int = DEFAULT_TIMEOUT
) -> CheckResult:
    """Format what a formatter owns, then lint and test what is left.

    The autofix pass is not part of the verdict, and deliberately so. An unused
    import and a missed blank line are not judgement calls — they have exactly
    one correct resolution, which a tool applies in milliseconds. Sending them
    to the model instead spends a repair attempt, and the repair attempts are
    the budget reserved for failures that actually need thinking. A card was
    blocked having spent every attempt on an unused variable and a long line,
    never once reaching the question of whether its logic was right.

    So the fixable is fixed, and the model is asked only about the rest. What
    survives a formatter is, by construction, something a formatter could not
    decide.

    Only the dependency step is given a network. Everything after it runs with
    none at all, so generated code cannot reach anything while it executes.
    """
    sandbox = sandbox or Sandbox()
    tools = toolchain(worktree)
    if tools.problem:
        # Refused before anything runs: a design that can't be checked safely is
        # the Architect's to fix, and the card says so.
        return CheckResult(results=[CommandResult(command="design", code=2, output=tools.problem)])
    if tools.image:
        sandbox = replace(sandbox, image=tools.image)
    given: dict[str, Any] = {"timeout": timeout, "env": tools.env}
    results = []
    if tools.setup:
        results.append(run(worktree, tools.setup, sandbox=sandbox, network=True, **given))
    fixed: list[str] = []
    reverted: list[str] = []
    unsafe: list[str] = []
    if all(r.ok for r in results):
        touched = _changed(worktree)
        kept = {p: _read(worktree / p) for p in touched or ()}
        if touched is not None and tools.autofix == AUTOFIX:
            # Before the formatter, which then formats what the fix wrote.
            unsafe = _unsafe_fixes(worktree, touched, sandbox=sandbox, timeout=timeout)
        for command in tools.autofix:
            # Outcome ignored on purpose: whatever the autofix could not fix is
            # reported by the lint that follows, which is the verdict.
            run(worktree, command, sandbox=sandbox, network=False, **given)
        if touched is not None:
            fixed = [p for p, text in kept.items() if _read(worktree / p) != text]
            # A fixer runs over the whole project, but the attempt answers for what
            # it changed: a fix elsewhere would land unreviewed in its pull request.
            # Undone, its finding is reported by the checks like any other.
            reverted = sorted((_changed(worktree) or set()) - touched)
            if reverted:
                _git(worktree, "checkout", "--", *reverted)
        for command in tools.verify:
            results.append(run(worktree, command, sandbox=sandbox, network=False, **given))
    return CheckResult(results=results, fixed=sorted(fixed), reverted=reverted, unsafe=unsafe)


def _unsafe_fixes(
    worktree: Path, touched: set[str], *, sandbox: Sandbox, timeout: int
) -> list[str]:
    """Apply the allowed unsafe fixes (`UNSAFE_FIXES`) the project's lint reports.

    Only in the files the attempt touched, and only for findings the project's
    own ruff config reports: `--select` names those codes alone, so nothing it
    doesn't enable is fixed. Returns each applied as `CODE path:line`.
    """
    import json  # noqa: PLC0415

    files = sorted(p for p in touched if p.endswith(".py") and (worktree / p).is_file())
    if not files:
        return []
    found = run(
        worktree,
        ["uv", "run", "ruff", "check", "--output-format", "json", *files],
        sandbox=sandbox,
        network=False,
        timeout=timeout,
    )
    listed = found.output[found.output.find("[") : found.output.rfind("]") + 1]
    try:
        findings = json.loads(listed) if listed else []
    except json.JSONDecodeError:
        return []
    applied: list[str] = []
    for finding in findings:
        fix = finding.get("fix") or {}
        if finding.get("code") not in UNSAFE_FIXES or fix.get("applicability") != "unsafe":
            continue
        # Reported where the check ran, which may be a container's path.
        name = str(finding.get("filename", ""))
        path = next((f for f in files if name == f or name.endswith("/" + f)), None)
        if path is not None:
            line = (finding.get("location") or {}).get("row")
            applied.append(f"{finding['code']} {path}:{line}")
    if not applied:
        return []
    codes = sorted({a.split()[0] for a in applied})
    paths = sorted({a.split()[1].rsplit(":", 1)[0] for a in applied})
    was = {p: _read(worktree / p) for p in paths}
    run(
        worktree,
        ["uv", "run", "ruff", "check", "--fix-only", "--unsafe-fixes"]
        + ["--select", ",".join(codes), *paths],
        sandbox=sandbox,
        network=False,
        timeout=timeout,
    )
    # On the record only where the file did change: a fix that didn't run isn't one.
    changed = {p for p in paths if _read(worktree / p) != was[p]}
    return [a for a in applied if a.split()[1].rsplit(":", 1)[0] in changed]


def _changed(worktree: Path) -> set[str] | None:
    """The files changed or new in the worktree, as git sees it. None outside git."""
    listed = _git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if listed is None:
        return None
    paths: set[str] = set()
    entries = iter(listed.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC":
            next(entries, None)  # a rename's old path follows it
    return paths


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _git(worktree: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None
