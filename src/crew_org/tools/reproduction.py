"""Does a bug's test reproduce it? It must fail against the code as merged (crew#407, B3).

A bug is fixed with a test that reproduces it: written to fail against the code
as it is, then made to pass by the fix (the regression-test-first practice of
test-driven development). The project's checks prove the second half. This proves
the first: the story's own criteria tests run against the merged code, with only
their test files changed, and at least one has to fail: that one reproduces the
bug. The others may pass, since a bug's criteria rightly guard edges that already
work (mqucifer/sprint-metrics#593's second criterion: an unknown sprint still
returns null). A skipped test never ran, so it reproduces nothing:
mqucifer/sprint-metrics#593's reproduction needs a database, which the sandbox
doesn't have (mqucifer/crew#614). So a project whose default branch declares a
workspace runs them there, with its services, as a session does (crew#839).

The run is in a copy of the worktree, with every changed file but the tests put
back as merged, set up and run the way the project's checks are. Git is only
read, in the worktree itself: a copied worktree shares its git metadata with the
original.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from crew_org.tools.regression import Merged
from crew_org.tools.sandbox import Sandbox
from crew_org.tools.workspace import DEFAULT_TIMEOUT, run, shown_test, toolchain

if TYPE_CHECKING:
    from crew_org.tools.services import Declared

REPORT = ".crew-reproduction.xml"


@dataclass
class Reproduction:
    """How each of the tests did against the merged code."""

    failed: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    # Why it couldn't be checked at all; the delivery goes on, saying so.
    unchecked: str = ""
    # The test a hang ended the run in, and where it waits: nothing after it ran.
    hung: tuple[str, str] | None = None

    def add(self, test: str, outcome: str, why: str) -> None:
        if outcome == "passed":
            self.passed.append(test)
        elif outcome == "skipped":
            self.skipped[test] = why or "no reason given"
        else:
            self.failed.append(test)

    def refusal(self) -> str:
        """Why the tests don't reproduce the bug, or "" when one fails as merged."""
        if self.hung is not None:
            test, where = self.hung
            return (
                f"`{test or 'an unnamed test'}` hung against the code as merged, waiting in "
                f"{where}, so the tests didn't all run. A hang shows nothing of the bug: a "
                "test has to fail against the code as it is, for the reason the bug describes"
            )
        if self.failed:
            return ""
        said = []
        if self.passed:
            said.append(
                f"{', '.join(self.passed)} pass{'es' if len(self.passed) == 1 else ''} "
                "against the code as merged, without your fix, so none of the tests "
                "reproduces the bug. At least one has to fail against the code as it is, "
                "for the reason the bug describes, and pass once it's fixed"
            )
        if self.skipped:
            said.append(
                "; ".join(f"{t} was skipped, not run: {why}" for t, why in self.skipped.items())
                + ". A skipped test can't show the bug either way"
            )
        return ". ".join(said)


def pytest_command(worktree: Path) -> list[str] | None:
    """The project's own pytest invocation, up to `pytest`, or None if it has none."""
    for command in toolchain(worktree).verify:
        if "pytest" in command:
            return command[: command.index("pytest") + 1]
    return None


def _changed(worktree: Path, sha: str) -> list[str]:
    """Every path the worktree changed since `sha`, committed or not, and new files."""
    diff = subprocess.run(
        ["git", "diff", "--name-only", sha], cwd=worktree, capture_output=True, text=True
    ).stdout.split()
    new = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        capture_output=True,
        text=True,
    ).stdout.split()
    return sorted(set(diff) | set(new))


def on_the_default_branch(
    worktree: Path, merged: Merged, tests: list[str], name: str
) -> dict[str, str] | str:
    """How each test, `path::Class.name`, does in the default branch's own workspace:
    "passed", "skipped", "failed" or "not run"; or why that couldn't be told (crew#839).

    The review of mqucifer/crew#877: a workspace Task hands the Developer only what its
    workspace turned on, and a test that passed on the default branch was turned off by
    the branch instead (its image, `containerEnv` or lockfile). So the tests run as
    merged, every changed file put back, in a container with the default branch's own
    services and setup: what a story's session runs.
    """
    from crew_org.tools.services import at_main  # noqa: PLC0415

    holder = Path(tempfile.mkdtemp(prefix="crew-main-"))
    copy = holder / worktree.name
    try:
        _copy(worktree, copy, merged, keep=set())
        command = pytest_command(copy)
        if command is None:
            return "the default branch's checks don't run pytest"
        ran = _in_workspace(copy, name, at_main(worktree), command, map(_node_id, tests))
        if ran.problem:
            return ran.problem
        if ran.hung is not None:
            return f"`{ran.hung[0] or 'an unnamed test'}` hung, waiting in {ran.hung[1]}"
        return {t: ran.cases.get(t, ("not run", ""))[0] for t in tests}
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def on_the_branch(worktree: Path, entries: list[str], name: str) -> dict[str, str] | str:
    """Why each of `entries` doesn't pass on the branch, in the default branch's workspace:
    none when all do, or why they couldn't be run (crew#839).

    The entries are a turned-on bug's record: a test, `path::Class.name`, or a whole file
    that hung, whose other tests weren't all run. A test passes when it runs and passes; a
    file when it runs to its end without hanging and each of its tests passes.
    """
    from crew_org.tools.services import at_main  # noqa: PLC0415

    holder = Path(tempfile.mkdtemp(prefix="crew-branch-"))
    copy = holder / worktree.name
    try:
        _copy(worktree, copy, None, keep=set())
        command = pytest_command(copy)
        if command is None:
            return "the project's checks don't run pytest"
        targets = [_node_id(e) if "::" in e else e for e in entries]
        ran = _in_workspace(copy, name, at_main(worktree), command, targets)
        if ran.problem:
            return ran.problem
        if ran.hung is not None:
            test, where = ran.hung
            test = shown_test(test) if test else "an unnamed test"
            entry = next((e for e in entries if e in (test, test.partition("::")[0])), test)
            return {
                entry: f"`{test}` hung, waiting in {where}: the run stopped there, so "
                "the tests after it didn't run"
            }
        wrong: dict[str, str] = {}
        for entry in entries:
            if "::" in entry:
                outcome = ran.cases.get(entry, ("not run", ""))[0]
                if outcome != "passed":
                    wrong[entry] = outcome
                continue
            cases = {t: said[0] for t, said in ran.cases.items() if t.startswith(f"{entry}::")}
            if not cases:
                wrong[entry] = "none of its tests ran"
            elif bad := {t: o for t, o in cases.items() if o != "passed"}:
                wrong[entry] = ", ".join(f"`{t}` {o}" for t, o in sorted(bad.items()))
        return wrong
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def _copy(worktree: Path, copy: Path, merged: Merged | None, keep: set[str]) -> None:
    """The worktree copied to `copy`, with each changed file but `keep` put back as
    merged; with none put back when there's no `merged`."""
    shutil.copytree(worktree, copy, symlinks=True)
    if merged is None:
        return
    for path in _changed(worktree, merged.sha):
        if path in keep:
            continue
        before = merged.text(path)
        target = copy / path
        if before is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(before, encoding="utf-8")


@dataclass
class _Ran:
    """A pytest run in a held workspace: each test case by `path::Class.name`, with its
    outcome and message; or the test a hang ended it in, and where it waits; or why it
    didn't run."""

    cases: dict[str, tuple[str, str]] = field(default_factory=dict)
    hung: tuple[str, str] | None = None
    problem: str = ""


def _in_workspace(
    copy: Path, name: str, declared: Declared, command: list[str], targets: Iterable[str]
) -> _Ran:
    """Run pytest on `targets` in a container with `declared`'s services and the project's
    setup, as a story's session runs (crew#614): what a database test needs to run."""
    from crew_org.tools.container import Held  # noqa: PLC0415
    from crew_org.tools.workspace import REPORT as HELD_REPORT  # noqa: PLC0415
    from crew_org.tools.workspace import _test_id, hung_test  # noqa: PLC0415

    held = Held(copy, name, declared=declared)
    try:
        if not (started := held.start()).ok:
            return _Ran(problem=f"its workspace didn't start: {started.output[-200:]}")
        setup = held.setup()
        if setup is not None and not setup.ok:
            setup = held.setup()
        if setup is not None and not setup.ok:
            return _Ran(problem=f"its setup failed: {setup.output[-200:]}")
        (copy / HELD_REPORT).unlink(missing_ok=True)
        done = held.exec([*command, "-p", "no:cacheprovider", *targets])
        # A hang ends the run before pytest writes its report (`with_report`).
        if (hang := hung_test(done.output, copy)) is not None:
            return _Ran(hung=hang)
        try:
            cases = ET.parse(copy / HELD_REPORT).getroot().iter("testcase")
        except (OSError, ET.ParseError):
            return _Ran(problem="pytest wrote no report")
        ran = _Ran()
        for case in cases:
            named = (case.get("file"), case.get("classname") or "", case.get("name") or "")
            if (skip := case.find("skipped")) is not None:
                said = ("skipped", skip.get("message", ""))
            elif case.find("failure") is not None or case.find("error") is not None:
                said = ("failed", "")
            else:
                said = ("passed", "")
            ran.cases[_test_id(copy, *named)] = said
        return ran
    finally:
        held.stop()


def _node_id(test: str) -> str:
    """`path::Class.name[param]` as pytest's node id, `path::Class::name[param]`."""
    path, _, rest = test.partition("::")
    named, bracket, param = rest.partition("[")
    return f"{path}::{named.replace('.', '::')}{bracket}{param}"


def _outcomes(report: Path) -> dict[str, tuple[str, str]]:
    """Each test case's name, as pytest's JUnit report has it: (outcome, message)."""
    found: dict[str, tuple[str, str]] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        name = case.get("name", "")
        if case.find("skipped") is not None:
            found[name] = ("skipped", case.find("skipped").get("message", ""))  # type: ignore[union-attr]
        elif case.find("failure") is not None or case.find("error") is not None:
            found[name] = ("failed", "")
        else:
            found[name] = ("passed", "")
    return found


def reproduces(
    worktree: Path,
    merged: Merged,
    tests: list[tuple[str, str]],
    *,
    name: str = "",
    sandbox: Sandbox | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Reproduction:
    """Run `tests`, (path, name), against the merged code with only their files changed.

    In the default branch's workspace, with its services, when it declares one and a
    container `name` is given: in the sandbox a database test only ever skipped, so it
    could never reproduce a bug (mqucifer/sprint-metrics#742, crew#839). A project with
    no workspace runs in the sandbox, as its checks do.
    """
    from crew_org.tools.services import at_main  # noqa: PLC0415

    found = Reproduction()
    command = pytest_command(worktree)
    if command is None:
        found.unchecked = "the project's checks don't run pytest"
        return found
    declared = at_main(worktree) if name else None
    holder = Path(tempfile.mkdtemp(prefix="crew-repro-"))
    copy = holder / worktree.name
    try:
        _copy(worktree, copy, merged, keep={path for path, _ in tests})
        ids = [f"{path}::{test}" for path, test in tests]
        if declared is not None and declared.path is not None:
            ran = _in_workspace(copy, name, declared, command, ids)
            if ran.problem:
                found.unchecked = f"the workspace couldn't run them: {ran.problem}"
                return found
            if ran.hung is not None:
                found.hung = ran.hung
                return found
            # Matched by file and name, as the sandbox's report is read below.
            outcomes = {
                (key.partition("::")[0], _last(key.partition("::")[2])): said
                for key, said in ran.cases.items()
            }
            for path, test in tests:
                said = outcomes.get((path, _last(test)), ("failed", ""))
                found.add(f"{path}::{test}", *said)
            return found
        # As the checks run: the project's setup with a network, then the tests with
        # none. Without the setup the tests had no dependencies to import (a real
        # run on sprint-metrics, 2026-10-12).
        tools = toolchain(copy)
        if tools.image:
            sandbox = replace(sandbox or Sandbox(), image=tools.image)
        if tools.setup:
            setup = run(copy, tools.setup, sandbox=sandbox, network=True, timeout=timeout)
            if not setup.ok:
                found.unchecked = f"the project's setup failed: {setup.output[-200:]}"
                return found
        result = run(
            copy,
            [*command, "-p", "no:cacheprovider", f"--junitxml={REPORT}", *ids],
            sandbox=sandbox,
            network=False,
            timeout=timeout,
        )
        report = copy / REPORT
        if not report.exists():
            found.unchecked = f"pytest wrote no report (exit {result.code})"
            return found
        reported = _outcomes(report)
        for path, test in tests:
            # A test that never reached the report failed to load: its module
            # couldn't import what the fix adds, which is failing as merged.
            found.add(f"{path}::{test}", *reported.get(_last(test), ("failed", "")))
        return found
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def _last(test: str) -> str:
    """A test's own name, without its classes."""
    return test.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
