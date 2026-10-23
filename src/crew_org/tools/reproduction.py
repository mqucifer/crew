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
doesn't have (mqucifer/crew#614).

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
from dataclasses import dataclass, field, replace
from pathlib import Path

from crew_org.tools.regression import Merged
from crew_org.tools.sandbox import Sandbox
from crew_org.tools.workspace import DEFAULT_TIMEOUT, run, toolchain

REPORT = ".crew-reproduction.xml"


@dataclass
class Reproduction:
    """How each of the tests did against the merged code."""

    failed: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    # Why it couldn't be checked at all; the delivery goes on, saying so.
    unchecked: str = ""

    def refusal(self) -> str:
        """Why the tests don't reproduce the bug, or "" when one fails as merged."""
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
                + ". A test the sandbox skips can't show the bug either way"
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
    from crew_org.tools.container import Held  # noqa: PLC0415
    from crew_org.tools.services import at_main  # noqa: PLC0415
    from crew_org.tools.workspace import REPORT as HELD_REPORT  # noqa: PLC0415
    from crew_org.tools.workspace import _test_id  # noqa: PLC0415

    holder = Path(tempfile.mkdtemp(prefix="crew-main-"))
    copy = holder / worktree.name
    try:
        shutil.copytree(worktree, copy, symlinks=True)
        for path in _changed(worktree, merged.sha):
            before = merged.text(path)
            target = copy / path
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(before, encoding="utf-8")
        command = pytest_command(copy)
        if command is None:
            return "the default branch's checks don't run pytest"
        held = Held(copy, name, declared=at_main(worktree))
        try:
            if not (started := held.start()).ok:
                return f"its workspace didn't start: {started.output[-200:]}"
            setup = held.setup()
            if setup is not None and not setup.ok:
                setup = held.setup()
            if setup is not None and not setup.ok:
                return f"its setup failed: {setup.output[-200:]}"
            (copy / HELD_REPORT).unlink(missing_ok=True)
            held.exec([*command, "-p", "no:cacheprovider", *map(_node_id, tests)])
            try:
                cases = ET.parse(copy / HELD_REPORT).getroot().iter("testcase")
            except (OSError, ET.ParseError):
                return "pytest wrote no report"
            seen: dict[str, str] = {}
            for case in cases:
                named = (case.get("file"), case.get("classname") or "", case.get("name") or "")
                if case.find("skipped") is not None:
                    outcome = "skipped"
                elif case.find("failure") is not None or case.find("error") is not None:
                    outcome = "failed"
                else:
                    outcome = "passed"
                seen[_test_id(copy, *named)] = outcome
            return {t: seen.get(t, "not run") for t in tests}
        finally:
            held.stop()
    finally:
        shutil.rmtree(holder, ignore_errors=True)


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
    sandbox: Sandbox | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Reproduction:
    """Run `tests`, (path, name), against the merged code with only their files changed."""
    found = Reproduction()
    command = pytest_command(worktree)
    if command is None:
        found.unchecked = "the project's checks don't run pytest"
        return found
    keep = {path for path, _ in tests}
    holder = Path(tempfile.mkdtemp(prefix="crew-repro-"))
    copy = holder / worktree.name
    try:
        shutil.copytree(worktree, copy, symlinks=True)
        for path in _changed(worktree, merged.sha):
            if path in keep:
                continue
            before = merged.text(path)
            target = copy / path
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(before, encoding="utf-8")
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
        ids = [f"{path}::{name}" for path, name in tests]
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
        outcomes = _outcomes(report)
        for path, name in tests:
            # A test that never reached the report failed to load: its module
            # couldn't import what the fix adds, which is failing as merged.
            outcome, why = outcomes.get(name.rsplit("::", 1)[-1].rsplit(".", 1)[-1], ("failed", ""))
            test = f"{path}::{name}"
            if outcome == "passed":
                found.passed.append(test)
            elif outcome == "skipped":
                found.skipped[test] = why or "no reason given"
            else:
                found.failed.append(test)
        return found
    finally:
        shutil.rmtree(holder, ignore_errors=True)
