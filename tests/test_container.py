"""A session's workspace container: hardened, without a network, and cleaned up
however the session ends (crew#778, decision 4b).

Each was run against Docker on 2026-10-18 before these pin it: setup reached the
network and the session's container didn't; `.git` and the root were read-only;
`timeout` stopped a command inside; a second start under the same name failed; the
sweep removed only a container whose crew had gone; and one past its lifetime
ended and was removed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from crew_org.session.tools import Tools
from crew_org.tools import container
from crew_org.tools.container import LABEL, OWNER, Held, Result, sweep
from crew_org.tools.sandbox import Mode, Sandbox

IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:" + "e" * 64


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n")
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer/devcontainer.json").write_text(
        f'{{"image": "{IMAGE}", "postCreateCommand": ["uv", "sync"],'
        ' "containerEnv": {"LANG": "C.UTF-8"}}'
    )
    (tmp_path / ".crew").mkdir()
    (tmp_path / ".crew/project.yaml").write_text(
        "version: 2\nintent:\n  scope:\n    purpose: A site.\n  release:\n    deploys: false\n"
        "  done:\n    bar: CI is green.\ndesign:\n  checks:\n  - uv run pytest -q\n"
    )
    return tmp_path


@pytest.fixture
def ran(monkeypatch) -> list[list[str]]:
    """Every command the workspace runs, each answered as docker would."""
    seen: list[list[str]] = []

    def run(argv, **kw):
        seen.append(argv)
        out = "/usr/local/bin:/usr/bin" if argv[1:3] == ["image", "inspect"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(container.subprocess, "run", run)
    monkeypatch.setattr(Sandbox, "unavailable_reason", lambda self: None)
    monkeypatch.setattr(Sandbox, "search_path", lambda self: "/usr/local/bin:/usr/bin")
    return seen


def started(worktree: Path, ran: list[list[str]], **kw) -> list[str]:
    held = Held(worktree, "sprint-metrics#662", **kw)
    assert held.start().ok
    return next(a for a in ran if a[1] == "run")


def test_the_session_s_container_never_has_a_network(worktree, ran):
    argv = started(worktree, ran)
    assert argv[argv.index("--network") + 1] == "none"


def test_it_keeps_the_sandbox_s_hardening(worktree, ran):
    argv = started(worktree, ran)
    assert "--read-only" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in argv and "--pids-limit" in argv
    assert f"{os.getuid()}:{os.getgid()}" in argv


def test_it_is_held_named_labelled_and_reaped(worktree, ran):
    argv = started(worktree, ran)
    assert argv[argv.index("--name") + 1] == "crew-workspace-sprint-metrics-662"
    assert "-d" in argv and "--rm" in argv and "--init" in argv
    assert f"{LABEL}=1" in argv and f"{OWNER}={os.getpid()}" in argv


def test_a_forgotten_container_ends_itself(worktree, ran):
    argv = started(worktree, ran, lifetime=600)
    assert argv[-2:] == ["sleep", "600"]
    assert container.LIFETIME > 2 * 60 * 60, "past the session's 2-hour backstop"


def test_git_is_read_only_inside(worktree, ran):
    argv = started(worktree, ran)
    assert f"type=bind,src={worktree.resolve() / '.git'},dst=/work/.git,readonly" in argv


def test_it_runs_the_project_s_image_with_its_environment(worktree, ran):
    argv = started(worktree, ran)
    assert IMAGE in argv and "LANG=C.UTF-8" in argv


def test_a_session_never_runs_on_the_host(worktree, ran):
    held = Held(worktree, "x", sandbox=Sandbox(mode=Mode.OFF))
    assert held.sandbox.mode is Mode.REQUIRED


def test_setup_runs_in_a_one_off_container_with_the_network(worktree, ran):
    Held(worktree, "x").setup(timeout=60)
    argv = next(a for a in ran if a[1] == "run")
    assert argv[argv.index("--network") + 1] == "bridge"
    assert "-d" not in argv and "--name" not in argv and "--rm" in argv
    assert argv[-6:] == ["timeout", "-k", "10", "60", "uv", "sync"]


def test_each_command_runs_in_the_held_container_under_a_timeout(worktree, ran):
    held = Held(worktree, "x")
    held.exec(["uv", "run", "pytest", "-q"], timeout=30)
    assert ran[-1] == [
        "docker",
        "exec",
        "-w",
        "/work",
        "crew-workspace-x",
        "timeout",
        "-k",
        "10",
        "30",
        "uv",
        "run",
        "pytest",
        "-q",
    ]


def test_input_is_given_on_stdin(worktree, ran):
    Held(worktree, "x").exec(["ruff", "check", "-"], stdin="x = 1\n")
    assert ran[-1][:3] == ["docker", "exec", "-i"]


def test_a_command_stopped_by_its_timeout_says_so(worktree, monkeypatch):
    monkeypatch.setattr(
        container, "_run", lambda argv, command, timeout, stdin=None: Result(command, 124, "...")
    )
    done = Held(worktree, "x").exec(["sleep", "30"], timeout=2)
    assert done.code == 124 and done.output == "...\n(stopped: still running after 2s)"


def test_a_container_that_has_gone_is_said_to_be(worktree, monkeypatch):
    """A session whose container went (Docker restarted) is the infrastructure's failure."""
    monkeypatch.setattr(
        container,
        "_run",
        lambda argv, command, timeout, stdin=None: Result(
            command, 1, "Error response from daemon: No such container: crew-workspace-x"
        ),
    )
    held = Held(worktree, "x")
    done = held.exec(["true"])
    assert done.code == 125 and "the workspace container is gone" in done.output and held.lost


def test_a_project_whose_workspace_can_t_be_used_starts_nothing(tmp_path, ran):
    (tmp_path / ".devcontainer.json").write_text('{"image": "node:22"}')
    done = Held(tmp_path, "x").start()
    assert done.code == 2 and "isn't pinned by digest" in done.output
    assert ran == []


def test_stop_removes_only_what_it_started(worktree, ran):
    held = Held(worktree, "x")
    held.stop()
    assert ran == []
    held.start()
    held.stop()
    assert ran[-1] == ["docker", "rm", "-f", "crew-workspace-x"]


# --- setup again when the dependency files change (crew#814) ----------------------------


def setups(ran: list[list[str]]) -> list[int]:
    """Where setup ran among the docker calls: a one-off `run`, not the held start."""
    return [i for i, a in enumerate(ran) if a[1] == "run" and "-d" not in a]


def test_a_changed_dependency_file_runs_setup_again_once_before_the_run(worktree, ran):
    """sprint-metrics#671 bumped its version, and every run then needed the network."""
    (worktree / "pyproject.toml").write_text('[project]\nversion = "1.3.1"\n')
    held = Held(worktree, "x")
    assert held.start().ok and held.setup().ok
    tools = Tools(worktree, held, held.tools)
    ran.clear()
    assert tools.run("suite").kind == "passed"
    assert setups(ran) == [], "nothing changed, so no setup"
    (worktree / "pyproject.toml").write_text('[project]\nversion = "1.3.2"\n')
    ran.clear()
    assert tools.run("suite").kind == "passed"
    (setup,) = setups(ran)
    argv = ran[setup]
    assert argv[argv.index("--network") + 1] == "bridge" and argv[-2:] == ["uv", "sync"]
    assert setup < next(i for i, a in enumerate(ran) if a[1] == "exec"), "then the run"
    ran.clear()
    tools.run("suite")
    assert setups(ran) == [], "once"
    assert tools.resetups == [True]


def test_each_dependency_file_counts_and_nothing_else_does(worktree, ran):
    held = Held(worktree, "x")
    held.setup()
    (worktree / "README.md").write_text("changed\n")
    assert held.resync() is None
    (worktree / "requirements-dev.txt").write_text("pytest\n")
    assert held.resync() is not None
    assert set(container.manifests(worktree)) == {"requirements-dev.txt"}


def test_the_lockfile_setup_writes_doesnt_send_it_round_again(worktree, monkeypatch, ran):
    """`uv sync` re-locks a stale `uv.lock` itself: what's recorded is what it left."""
    answer = container.subprocess.run

    def run(argv, **kw):
        if argv[-2:] == ["uv", "sync"]:
            (worktree / "uv.lock").write_text(f"locked {len(ran)}\n")
        return answer(argv, **kw)

    monkeypatch.setattr(container.subprocess, "run", run)
    held = Held(worktree, "x")
    held.setup()
    assert held.resync() is None
    (worktree / "pyproject.toml").write_text('[project]\nversion = "2"\n')
    assert held.resync().ok and held.resync() is None


def test_a_failed_setup_again_is_tried_once_more_and_again_at_the_next_call(worktree, monkeypatch):
    """As at the start: a fetch can fail for a moment. A failure leaves the files changed."""
    codes = iter([0, 1, 1, 1, 0])
    tried: list[str] = []

    def run(argv, command, timeout, stdin=None):
        if command == "uv sync":
            tried.append(command)
            return Result(command, next(codes), "error: Failed to fetch")
        return Result(command, 0, "")

    monkeypatch.setattr(container, "_run", run)
    monkeypatch.setattr(Sandbox, "unavailable_reason", lambda self: None)
    monkeypatch.setattr(Sandbox, "search_path", lambda self: "/usr/local/bin:/usr/bin")
    held = Held(worktree, "x")
    assert held.setup().ok
    (worktree / "uv.lock").write_text("stale\n")
    failed = held.resync()
    assert not failed.ok and failed.output == "error: Failed to fetch" and len(tried) == 3
    assert held.resync().ok and len(tried) == 5, "the next call runs it again"
    assert held.resync() is None


def test_the_sweep_removes_only_containers_whose_crew_has_gone(monkeypatch):
    """Two ticks can run at once: one's sweep mustn't remove the other's sessions."""
    listed = f"crew-workspace-mine\t{os.getpid()}\ncrew-workspace-dead\t999999\n"
    removed: list[list[str]] = []

    def run(argv, **kw):
        if argv[1] == "rm":
            removed.append(argv)
        return subprocess.CompletedProcess(argv, 0, listed if argv[1] == "ps" else "", "")

    monkeypatch.setattr(container.subprocess, "run", run)
    monkeypatch.setattr(container, "_alive", lambda pid: pid == str(os.getpid()))
    assert sweep() == ["crew-workspace-dead"]
    assert removed == [["docker", "rm", "-f", "crew-workspace-dead"]]


def test_the_sweep_without_an_engine_removes_nothing(monkeypatch):
    def missing(argv, **kw):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(container.subprocess, "run", missing)
    assert sweep("podman") == []
