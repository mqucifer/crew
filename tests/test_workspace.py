"""Applying and checking an implementation. The guards matter because this code
writes files the model chose and then executes them."""

from __future__ import annotations

import pytest

from crew_org.crews.delivery_crew import FileWrite
from crew_org.tools import workspace
from crew_org.tools.sandbox import Mode, Sandbox
from crew_org.tools.workspace import CheckResult, CommandResult, apply, check

# These exercise the runner itself — argument handling, timeouts, output caps —
# so they run on the host deliberately. Spinning up a container per assertion
# would make the suite slow and dependent on a running daemon while testing
# nothing about the container. The sandbox has its own tests.
HOST = Sandbox(mode=Mode.OFF)


def run(worktree, command, **kwargs):
    return workspace.run(worktree, command, sandbox=HOST, **kwargs)


def test_files_are_written_into_the_worktree(tmp_path):
    written = apply(tmp_path, [FileWrite(path="src/pkg/mod.py", content="x = 1\n")])
    assert written == ["src/pkg/mod.py"]
    assert (tmp_path / "src/pkg/mod.py").read_text() == "x = 1\n"


def test_nested_directories_are_created(tmp_path):
    apply(tmp_path, [FileWrite(path="a/b/c/d.py", content="y\n")])
    assert (tmp_path / "a/b/c/d.py").exists()


def test_an_existing_file_is_replaced_in_full(tmp_path):
    (tmp_path / "f.py").write_text("old and much longer\n")
    apply(tmp_path, [FileWrite(path="f.py", content="new\n")])
    assert (tmp_path / "f.py").read_text() == "new\n"


def test_a_symlinked_escape_is_refused(tmp_path):
    """The schema blocks '..' in a path, but a symlink can still point outside.
    Containment is re-checked after resolution because the cost of being wrong
    is writing outside the repository."""
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the worktree"):
        apply(tmp_path, [FileWrite(path="link/escaped.py", content="x\n")])


# --- running -------------------------------------------------------------


def test_a_successful_command_is_ok(tmp_path):
    result = run(tmp_path, ["python3", "-c", "print('hello')"])
    assert result.ok and "hello" in result.output


def test_a_failing_command_carries_its_output(tmp_path):
    result = run(tmp_path, ["python3", "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"])
    assert not result.ok
    assert result.code == 2
    assert "boom" in result.output


def test_a_hanging_command_is_killed(tmp_path):
    """A runaway test must not hang the tick."""
    result = run(tmp_path, ["python3", "-c", "import time; time.sleep(30)"], timeout=1)
    assert result.timed_out and not result.ok


def test_a_missing_command_is_reported_not_raised(tmp_path):
    assert run(tmp_path, ["definitely-not-a-command"]).code == 127


def test_credentials_are_stripped_even_on_the_host_path(tmp_path, monkeypatch):
    """Host execution is an explicit opt-in, not a safe one — it must still
    refuse to hand a repository token to code the model wrote."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_secret")
    monkeypatch.setenv("GH_TOKEN", "github_pat_secret")
    monkeypatch.setenv("HARMLESS", "visible")
    result = run(
        tmp_path,
        [
            "python3",
            "-c",
            "import os; e = os.environ; "
            "print(e.get('GITHUB_TOKEN'), e.get('GH_TOKEN'), e.get('HARMLESS'))",
        ],
    )
    assert "ghs_secret" not in result.output
    assert "github_pat_secret" not in result.output
    assert "None None visible" in result.output


def test_command_output_is_kept_whole(tmp_path):
    """A 3,000-character head and tail was enough to repair from only if the
    defect sat at one end. Story #9's pytest run had thirteen failures and the
    model repaired three times from a view with the middle cut out."""
    result = run(
        tmp_path, ["python3", "-c", "print('A'*50_000); print('MIDDLE'); print('B'*50_000)"]
    )

    assert "MIDDLE" in result.output
    assert len(result.output) > 100_000


def test_a_runaway_report_keeps_the_end(tmp_path):
    """The guard is against a command that will not stop, not against size. A
    test run puts its summary and its last failure at the end."""
    from crew_org.tools.workspace import CheckResult, CommandResult

    huge = "x" * (workspace.MAX_FAILURE_REPORT_CHARS + 10_000)
    check = CheckResult(
        results=[CommandResult(command="pytest -q", code=1, output=huge + "\nFAILED test_last")]
    )

    report = check.failure_report
    assert report.endswith("FAILED test_last")
    assert "dropped from the start" in report
    assert len(report) < workspace.MAX_FAILURE_REPORT_CHARS * 2


# --- verdicts ------------------------------------------------------------


def ok(cmd: str) -> CommandResult:
    return CommandResult(command=cmd, code=0, output="")


def bad(cmd: str, output: str = "failed") -> CommandResult:
    return CommandResult(command=cmd, code=1, output=output)


def test_all_green_is_ok():
    assert CheckResult(results=[ok("ruff"), ok("pytest")]).ok


def test_any_failure_fails_the_check():
    assert not CheckResult(results=[ok("ruff"), bad("pytest")]).ok


def test_the_failure_report_carries_only_what_broke():
    report = CheckResult(results=[ok("ruff"), bad("pytest", "2 failed")]).failure_report
    assert "pytest" in report and "2 failed" in report
    assert "ruff" not in report


def test_a_timeout_is_named_as_such_in_the_report():
    timed = CommandResult(command="pytest", code=-1, output="no output", timed_out=True)
    assert "timed out" in CheckResult(results=[timed]).failure_report


# --- the autofix pass ----------------------------------------------------


@pytest.fixture
def recorded(monkeypatch):
    """Record what check() runs, and let each command's exit code be chosen."""
    calls: list[list[str]] = []
    codes: dict[str, int] = {}

    def fake_run(worktree, command, **_kwargs):
        calls.append(command)
        key = " ".join(command)
        return CommandResult(
            command=key, code=codes.get(key, 0), output=codes.get(key, 0) and "x" or ""
        )

    monkeypatch.setattr(workspace, "run", fake_run)
    return calls, codes


def test_the_fixable_is_fixed_before_anything_is_judged(tmp_path, recorded):
    calls, _ = recorded
    check(tmp_path, sandbox=HOST)
    joined = [" ".join(c) for c in calls]
    fix = joined.index("uv run ruff check --fix-only --no-unsafe-fixes .")
    lint = joined.index("uv run ruff check .")
    assert fix < lint, "autofix must run before the lint that judges"
    assert joined.index("uv run ruff format .") < lint


def test_a_failing_autofix_is_not_a_failing_check(tmp_path, recorded):
    """`ruff check --fix-only` exits non-zero when something is left unfixed.
    That is the lint's verdict to deliver, not the autofix's — otherwise every
    unfixable finding would be reported twice and counted once too often."""
    _, codes = recorded
    codes["uv run ruff check --fix-only --no-unsafe-fixes ."] = 1
    assert check(tmp_path, sandbox=HOST).ok


def _repo(path):
    import subprocess

    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "touched.py").write_text("x = 1\n")
    (path / "elsewhere.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)
    return path


@pytest.fixture
def fixer(monkeypatch):
    """A fixer that rewrites every Python file in the project, as `ruff --fix .` would."""

    def fake_run(worktree, command, **_kwargs):
        if "--fix-only" in command:
            for path in worktree.glob("*.py"):
                path.write_text(path.read_text() + "# fixed\n")
        return CommandResult(command=" ".join(command), code=0, output="")

    monkeypatch.setattr(workspace, "run", fake_run)


def test_what_the_fixer_changed_is_recorded_and_kept_where_the_attempt_was(tmp_path, fixer):
    """crew#758: the fix is part of the attempt, and on its record."""
    repo = _repo(tmp_path)
    (repo / "touched.py").write_text("x = 2\n")
    (repo / "new.py").write_text("z = 1\n")
    result = check(repo, sandbox=HOST)
    assert result.fixed == ["new.py", "touched.py"]
    assert (repo / "touched.py").read_text() == "x = 2\n# fixed\n"


def test_a_fix_outside_what_the_attempt_touched_is_undone(tmp_path, fixer):
    repo = _repo(tmp_path)
    (repo / "touched.py").write_text("x = 2\n")
    result = check(repo, sandbox=HOST)
    assert result.reverted == ["elsewhere.py"]
    assert (repo / "elsewhere.py").read_text() == "y = 1\n"


def test_outside_git_the_fixer_runs_as_before_with_nothing_recorded(tmp_path, fixer):
    (tmp_path / "a.py").write_text("x = 1\n")
    result = check(tmp_path, sandbox=HOST)
    assert (result.fixed, result.reverted) == ([], [])
    assert (tmp_path / "a.py").read_text() == "x = 1\n# fixed\n"


def test_the_default_fixer_takes_only_safe_fixes():
    assert "--no-unsafe-fixes" in workspace.AUTOFIX[0]


def test_the_verdict_is_still_the_lint_and_the_tests(tmp_path, recorded):
    _, codes = recorded
    codes["uv run ruff check ."] = 1
    assert not check(tmp_path, sandbox=HOST).ok


def test_nothing_runs_if_dependencies_do_not_resolve(tmp_path, recorded):
    """Autofixing with no toolchain installed would fail confusingly."""
    calls, codes = recorded
    codes["uv sync --extra dev --quiet"] = 1
    check(tmp_path, sandbox=HOST)
    assert len(calls) == 1


def test_a_failed_sync_stops_before_linting(tmp_path, monkeypatch):
    """Running tests against unresolved dependencies produces noise, not signal."""
    calls: list[list[str]] = []

    def fake_run(worktree, command, *, sandbox=None, network=False, timeout=0, env=None):
        calls.append(command)
        return CommandResult(command=" ".join(command), code=1, output="lock conflict")

    monkeypatch.setattr(workspace, "run", fake_run)
    result = check(tmp_path, sandbox=HOST)
    assert len(calls) == 1
    assert not result.ok


def test_the_sandbox_is_required_unless_explicitly_turned_off(tmp_path, monkeypatch):
    """The default path refuses to run when no engine is available, rather than
    quietly executing generated code on the host."""
    monkeypatch.setattr("crew_org.tools.sandbox.shutil.which", lambda _: None)
    result = workspace.run(tmp_path, ["echo", "hi"])
    assert not result.ok
    assert result.code == 126
    assert "not on PATH" in result.output


def test_dependency_resolution_is_the_only_networked_step(tmp_path, monkeypatch):
    """Generated code must not reach anything while it executes."""
    seen: list[tuple[list[str], bool]] = []

    def fake_run(worktree, command, *, sandbox=None, network=False, timeout=0, env=None):
        seen.append((command, network))
        return CommandResult(command=" ".join(command), code=0, output="")

    monkeypatch.setattr(workspace, "run", fake_run)
    check(tmp_path, sandbox=HOST)
    assert seen[0][1] is True and "sync" in seen[0][0]
    assert all(networked is False for _cmd, networked in seen[1:])


NESTED = """def check(event):
    if "because" in event:
        if event["because"] != "x":
            return 1
    unused = 2
    return 0
"""


def test_an_allowed_unsafe_fix_is_applied_and_recorded_and_no_other(tmp_path, monkeypatch):
    """sprint-metrics#563's only finding was SIM102, whose fix ruff calls unsafe (crew#758).

    Runs the real ruff: F841's unsafe fix isn't on the list, so it stays for VERIFY.
    """
    import subprocess
    import sys

    real = workspace.run

    def ruff_here(worktree, command, **kwargs):
        if command[:3] != ["uv", "run", "ruff"]:
            return CommandResult(command=" ".join(command), code=0, output="")  # setup, tests
        return real(worktree, [sys.executable, "-m", "ruff", *command[3:]], **kwargs)

    monkeypatch.setattr(workspace, "run", ruff_here)
    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["F", "SIM"]\n')
    (repo / "elsewhere.py").write_text(NESTED)
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "merged"], check=True)
    (repo / "touched.py").write_text(NESTED)
    result = check(repo, sandbox=HOST)
    assert result.unsafe == ["SIM102 touched.py:2"]
    text = (repo / "touched.py").read_text()
    assert 'if "because" in event and event["because"] != "x":' in text
    assert "unused = 2" in text and not result.ok
    # The merged file's finding is left as it was, for VERIFY to report.
    assert "--> touched.py:2" not in result.failure_report
    assert "--> elsewhere.py:2" in result.failure_report and "F841" in result.failure_report
    assert (repo / "elsewhere.py").read_text() == NESTED


def test_every_allowed_unsafe_fix_says_why():
    assert workspace.UNSAFE_FIXES and all(len(why) > 40 for why in workspace.UNSAFE_FIXES.values())


def test_the_attempts_record_and_pull_request_name_what_the_fixers_changed():
    from crew_org.events import EventKind, EventSink
    from crew_org.flows.delivery import DeliveryOutcome, _pr_body, _record_autofix
    from tests.test_delivery_flow import IMPL, story

    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    outcome = DeliveryOutcome(card=6)
    fixed = CheckResult(
        results=[], fixed=["src/a.py"], reverted=["src/b.py"], unsafe=["SIM102 src/a.py:4"]
    )
    _record_autofix(fixed, outcome, sink, 6)
    (event,) = [e for e in seen if e.kind is EventKind.AUTOFIX_APPLIED]
    assert event.detail["rules"] == ["SIM102"] and event.detail["reverted"] == ["src/b.py"]
    body = _pr_body(story(), IMPL, outcome)
    assert "## Fixed by the project's own tools before the checks" in body
    assert "- `src/a.py`" in body and "unsafe fix `SIM102` at `src/a.py:4`" in body
