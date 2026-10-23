"""Checking a worktree: the project's lint and tests, in its sandbox.

Running the crew's output means executing model-generated code. The guards here
are bounded blast radius, not a sandbox: an isolated worktree, a hard timeout, a
capped output size, and no inherited credentials. Real isolation needs a
container — see docs/ways-of-working.md and the note in `check`.
"""

from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crew_org.tools.sandbox import HOME_DIR, Mode, Sandbox, prepare_home

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
    # In a held container: whether setup ran again first, because the dependency files
    # had changed, and passed (crew#814). None when it didn't run.
    resetup: bool | None = None
    # In a held container: each service the project declares that isn't running, with
    # why (crew#860). Its tests would skip or fail for the workspace's reason, not the
    # story's.
    services_down: dict[str, str] = field(default_factory=dict)
    # In a held container: each test the checks skipped, by `path::Class.name`, with its
    # reason, from pytest's report (crew#860). None when there's no report: not pytest.
    skipped: dict[str, str] | None = None
    # In a held container: each test that failed or errored, by `path::Class.name`, with
    # its first line, and the commands whose report named them (crew#839).
    failed: dict[str, str] = field(default_factory=dict)
    reported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def only_tests_failed(self) -> bool:
        """Every failing command is a pytest run that finished and named what failed in its
        report: exit 1, which is tests failing, never an interrupted or broken run."""
        bad = [r for r in self.results if not r.ok]
        return (
            bool(bad)
            and bool(self.failed)
            and all(r.code == 1 and not r.timed_out and r.command in self.reported for r in bad)
        )

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
    worktree: Path,
    *,
    sandbox: Sandbox | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    held: Any = None,
    since: str | None = None,
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

    `held` is a session's workspace container (`tools/container.py`): its setup
    ran when it started, so the fixers and checks run in it (crew#778). Setup runs
    again first, as the container's, only when the dependency files changed since
    it last passed (crew#814). `since` is the commit the work started from: a session
    commits its checkpoints, so what it touched is read against that, not HEAD.
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

    def do(command: list[str], network: bool = False) -> CommandResult:
        if held is None:
            return run(worktree, command, sandbox=sandbox, network=network, **given)
        done = held.exec(command, timeout=timeout)
        return CommandResult(command=done.command, code=done.code, output=done.output)

    results = []
    resetup = None
    if tools.setup and held is None:
        results.append(do(tools.setup, network=True))
    elif held is not None and (again := held.resync()) is not None:
        resetup = again.code == 0
        results.append(CommandResult(command=again.command, code=again.code, output=again.output))
    fixed: list[str] = []
    reverted: list[str] = []
    unsafe: list[str] = []
    skipped: dict[str, str] | None = None
    failed: dict[str, str] = {}
    reported: list[str] = []
    if all(r.ok for r in results):
        touched = _changed(worktree, since)
        kept = {p: _read(worktree / p) for p in touched or ()}
        if touched is not None and tools.autofix == AUTOFIX:
            # Before the formatter, which then formats what the fix wrote.
            unsafe = _unsafe_fixes(worktree, touched, do)
        for command in tools.autofix:
            # Outcome ignored on purpose: whatever the autofix could not fix is
            # reported by the lint that follows, which is the verdict.
            do(command)
        if touched is not None:
            fixed = [p for p, text in kept.items() if _read(worktree / p) != text]
            # A fixer runs over the whole project, but the attempt answers for what
            # it changed: a fix elsewhere would land unreviewed in its pull request.
            # Undone, its finding is reported by the checks like any other.
            reverted = sorted((_changed(worktree, since) or set()) - touched)
            if reverted:
                _git(worktree, "checkout", "--", *reverted)
        if held is not None:
            (worktree / REPORT).unlink(missing_ok=True)
        for command in tools.verify:
            results.append(do(command))
            # Each pytest command writes the one report: read after each, so a project
            # with several (crew-presentation runs three) is read whole.
            if held is not None and (found := skipped_tests(worktree)) is not None:
                skipped = {**(skipped or {}), **found}
                failed.update(failed_tests(worktree) or {})
                reported.append(results[-1].command)
                (worktree / REPORT).unlink(missing_ok=True)
    return CheckResult(
        results=results,
        fixed=sorted(fixed),
        reverted=reverted,
        unsafe=unsafe,
        resetup=resetup,
        services_down=dict(getattr(held, "down", None) or {}),
        skipped=skipped,
        failed=failed,
        reported=reported,
    )


# The tests' report, written by every pytest run in a held workspace into the worktree's
# own home, which ignores itself, so it's never committed (crew#860). The checks remove
# it first, so what's read after them is theirs.
REPORT = f"{HOME_DIR}/junit.xml"


def with_report(env: dict[str, str]) -> dict[str, str]:
    """A held workspace's environment, with pytest told to write its report."""
    given = env.get("PYTEST_ADDOPTS", "")
    # The root pinned to the worktree, and xunit1, whose test cases name their file: a
    # test is then named by its path, whatever dots its directories have.
    ours = f"--rootdir=/work --junitxml=/work/{REPORT} -o junit_family=xunit1"
    return {**env, "PYTEST_ADDOPTS": f"{given} {ours}".strip()}


def skipped_tests(worktree: Path) -> dict[str, str] | None:
    """Each test the last pytest run skipped, by `path::Class.name`, with its reason.

    None when there's no report, or it doesn't parse: the checks weren't pytest's.
    """
    try:
        root = ET.parse(worktree / REPORT).getroot()
    except (OSError, ET.ParseError):
        return None
    found: dict[str, str] = {}
    for case in root.iter("testcase"):
        skip = case.find("skipped")
        # An expected failure is reported as skipped, but it ran.
        if skip is None or skip.get("type") == "pytest.xfail":
            continue
        reason = (skip.get("message") or skip.text or "").strip()
        named = (case.get("file"), case.get("classname") or "", case.get("name") or "")
        found[_test_id(worktree, *named)] = reason
    return found


def failed_tests(worktree: Path) -> dict[str, str] | None:
    """Each test the last pytest run failed or errored on, by `path::Class.name`, with its
    message's first line. None when there's no report, as `skipped_tests`."""
    try:
        root = ET.parse(worktree / REPORT).getroot()
    except (OSError, ET.ParseError):
        return None
    found: dict[str, str] = {}
    for case in root.iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        said = (bad.get("message") or bad.text or "").strip().splitlines()
        named = (case.get("file"), case.get("classname") or "", case.get("name") or "")
        found[_test_id(worktree, *named)] = (said[0] if said else "")[:200]
    return found


def _test_id(worktree: Path, file: str | None, classname: str, name: str) -> str:
    """A test case as `path::Class.name`: its file, as xunit1 names it, with the classes
    after the module in its dotted `classname`. Without the file, the classname is read
    as one. One that names no file in the worktree is left as pytest gave it."""
    if file and file.endswith(".py") and (worktree / file).is_file():
        module = file[: -len(".py")].replace("/", ".")
        rest = classname[len(module) + 1 :] if classname.startswith(f"{module}.") else ""
        return f"{file}::" + ".".join([*filter(None, [rest]), name])
    parts = classname.split(".")
    for at in range(len(parts), 0, -1):
        path = "/".join(parts[:at]) + ".py"
        if (worktree / path).is_file():
            return f"{path}::" + ".".join([*parts[at:], name])
    return f"{classname}::{name}"


def _unsafe_fixes(
    worktree: Path, touched: set[str], do: Callable[[list[str]], CommandResult]
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
    found = do(["uv", "run", "ruff", "check", "--output-format", "json", *files])
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
    do(
        ["uv", "run", "ruff", "check", "--fix-only", "--unsafe-fixes"]
        + ["--select", ",".join(codes), *paths]
    )
    # On the record only where the file did change: a fix that didn't run isn't one.
    changed = {p for p in paths if _read(worktree / p) != was[p]}
    return [a for a in applied if a.split()[1].rsplit(":", 1)[0] in changed]


def _changed(worktree: Path, since: str | None = None) -> set[str] | None:
    """The files changed or new in the worktree, as git sees it. None outside git.

    `since` counts what's committed after that commit as changed too.
    """
    if since is not None:
        diffed = _git(worktree, "diff", "--name-only", "-z", since)
        new = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
        if diffed is None or new is None:
            return None
        return {p for p in (diffed + new).split("\0") if p}
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
