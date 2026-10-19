"""A workspace container held for one session (crew#778, decision 4b; ADR 0006).

A session runs its tests and searches in one container for the whole story, not
one container per command. It starts from the project's image (its
devcontainer.json, or its design) with the sandbox's hardening, and it never has
a network. Setup runs before that, in a one-off container that has one, and only
the worktree carries over, as between the sandbox's commands. So nothing setup
started is still running, and there's no disconnect step to fail
(`var/notes/778/research-workspace.md`). It runs again, the same way, when the
dependency files have changed since it last passed (`resync`, crew#814).

Clean-up has three layers, because a session can end in a crash:
- `stop()` at the session's end;
- `sweep()` at a tick's start, of the containers whose crew process has gone;
- the container's own keep-alive ends after its lifetime, and `--rm` removes it.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

from crew_org.tools import workspace
from crew_org.tools.sandbox import Mode, Sandbox, prepare_home

# On every workspace container, with the process id of the crew that started it,
# so a sweep can tell which are left behind.
LABEL = "crew.workspace"
OWNER = "crew.pid"
# How long a container keeps itself alive. A delivery's backstop is 3 hours of
# model time, and its runs, setup and gates take time besides, so it can't end
# under a live session (the Sponsor, crew#778). A container the crew forgets ends
# after this, and `--rm` removes it.
LIFETIME = 4 * 60 * 60
# Setup fetches and installs, so it gets longer than a test run.
SETUP_TIMEOUT = 900
# How long a command has to stop after its timeout, before it's killed.
GRACE = 10
# The files whose change sends setup round again, at the project's root where setup
# reads them: the devcontainer `updateContentCommand` (crew#814). sprint-metrics#671
# bumped its version, `uv run` re-locked the stale `uv.lock`, and with no network
# every run and gate failed.
MANIFESTS = (
    "pyproject.toml",
    "uv.lock",
    "requirements*.txt",
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
)


@dataclass
class Result:
    command: str
    code: int
    output: str
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.code == 0


def manifests(worktree: Path) -> dict[str, str]:
    """The dependency files at the worktree's root (`MANIFESTS`), each by name to its digest."""
    found = {p for pattern in MANIFESTS for p in worktree.glob(pattern) if p.is_file()}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(found)}


def sweep(engine: str = "docker") -> list[str]:
    """Remove the workspace containers whose crew process has gone. Returns their names.

    Only those: nothing stops two ticks running at once, and one tick's sweep
    mustn't remove another's sessions.
    """
    try:
        listed = subprocess.run(
            [engine, "ps", "-a", "--filter", f"label={LABEL}"]
            + ["--format", f'{{{{.Names}}}}\t{{{{.Label "{OWNER}"}}}}'],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    left = [
        name
        for name, _, pid in (line.partition("\t") for line in listed.stdout.splitlines())
        if name and not _alive(pid)
    ]
    if left:
        subprocess.run([engine, "rm", "-f", *left], capture_output=True, timeout=120)
    return left


def _alive(pid: str) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True  # someone else's process, alive
    return True


class Held:
    """One container for one session, with the worktree at /work."""

    def __init__(
        self,
        worktree: Path,
        name: str,
        sandbox: Sandbox | None = None,
        lifetime: int = LIFETIME,
    ) -> None:
        self.worktree = worktree
        self.tools = workspace.toolchain(worktree)
        # Never the host, whatever the sandbox's mode: a session only runs held.
        sandbox = replace(sandbox or Sandbox(), mode=Mode.REQUIRED)
        if self.tools.image:
            sandbox = replace(sandbox, image=self.tools.image)
        self.sandbox = sandbox
        # One name per session, so a second start fails loudly instead of running
        # beside a leftover.
        self.name = "crew-workspace-" + re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-.")
        self.lifetime = lifetime
        self.started = False
        self.lost = False
        # The dependency files as the last setup that passed left them: setup can
        # write the lockfile itself, so they're read after it (crew#814).
        self.synced: dict[str, str] | None = None

    def start(self) -> Result:
        """Start the session's container, with no network."""
        refused = self._refused("start")
        if refused:
            return refused
        prepare_home(self.worktree)
        argv = self.sandbox.command(
            self.worktree,
            ["sleep", str(self.lifetime)],
            network=False,
            path=self.sandbox.search_path(),
            env=self.tools.env,
            held=self.name,
            labels={LABEL: "1", OWNER: str(os.getpid())},
        )
        done = _run(argv, "start", timeout=120)
        self.started = done.ok
        return done

    def setup(self, timeout: int = SETUP_TIMEOUT) -> Result | None:
        """The project's setup, in a one-off container with the network.

        Before or after `start`: only the worktree carries over, and the session's
        container sees it as setup leaves it.
        """
        if not self.tools.setup:
            return None
        refused = self._refused(" ".join(self.tools.setup))
        if refused:
            return refused
        prepare_home(self.worktree)
        argv = self.sandbox.command(
            self.worktree,
            _bounded(self.tools.setup, timeout),
            network=True,
            path=self.sandbox.search_path(),
            env=self.tools.env,
        )
        done = _said(_run(argv, " ".join(self.tools.setup), timeout + GRACE + 30), timeout)
        if done.ok:
            self.synced = manifests(self.worktree)
        return done

    def resync(self, timeout: int = SETUP_TIMEOUT) -> Result | None:
        """Setup again, the same way, if the dependency files changed since it last passed.

        None when they haven't, or when it never passed. Once more on a failure, as at
        the start: a fetch can fail for a moment. A failure leaves them as changed, so
        the next call runs it again.
        """
        if self.synced is None or manifests(self.worktree) == self.synced:
            return None
        done = self.setup(timeout)
        if done is not None and not done.ok:
            done = self.setup(timeout)
        return done

    def exec(
        self, command: list[str], timeout: int = workspace.DEFAULT_TIMEOUT, stdin: str | None = None
    ) -> Result:
        """Run one command in the session's container, from /work, with `stdin` if given.

        Bounded inside the container as well: killing the `docker exec` client
        hasn't always stopped what it started (moby/moby, 4b84a33).
        """
        argv = [
            self.sandbox.engine,
            "exec",
            *(["-i"] if stdin is not None else []),
            "-w",
            "/work",
            self.name,
            *_bounded(command, timeout),
        ]
        done = _said(_run(argv, " ".join(command), timeout + GRACE + 30, stdin), timeout)
        if not done.ok and "Error response from daemon" in done.output and not self._running():
            # The session ends as the infrastructure's failure, not the Developer's:
            # 125 is docker's code for a command it couldn't run.
            self.lost = True
            return Result(
                done.command, 125, f"the workspace container is gone: {done.output}", done.seconds
            )
        return done

    def stop(self) -> None:
        if self.started:
            subprocess.run([self.sandbox.engine, "rm", "-f", self.name], capture_output=True)
            self.started = False

    def _refused(self, command: str) -> Result | None:
        """Why nothing can run yet, or None. Pulls the image the first time."""
        if self.tools.problem:
            return Result(command, 2, self.tools.problem)
        reason = self.sandbox.unavailable_reason()
        if reason:
            return Result(command, 126, reason)
        engine, image = self.sandbox.engine, self.sandbox.image
        if _run([engine, "image", "inspect", image], "inspect", timeout=60).ok:
            return None
        pulled = _run([engine, "pull", "-q", image], f"pull {image}", timeout=SETUP_TIMEOUT)
        return None if pulled.ok else pulled

    def _running(self) -> bool:
        found = _run(
            [self.sandbox.engine, "container", "inspect", "-f", "{{.State.Running}}", self.name],
            "inspect",
            timeout=60,
        )
        return found.ok and found.output == "true"


def _bounded(command: list[str], timeout: int) -> list[str]:
    return ["timeout", "-k", str(GRACE), str(timeout), *command]


def _said(result: Result, timeout: int) -> Result:
    """A command `timeout` stopped, saying so after what it printed."""
    if result.code != 124:  # `timeout`'s code for a command it stopped
        return result
    output = f"{result.output}\n(stopped: still running after {timeout}s)".strip()
    return replace(result, output=output)


def _run(argv: list[str], command: str, timeout: float, stdin: str | None = None) -> Result:
    # The docker client runs on the host, so credentials are stripped from it too.
    env = {k: v for k, v in os.environ.items() if k not in workspace.STRIPPED_ENV}
    began = time.monotonic()
    try:
        done = subprocess.run(
            argv, input=stdin, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        return Result(command, -1, f"no output within {timeout:.0f}s", float(timeout))
    except FileNotFoundError as exc:
        return Result(command, 127, str(exc))
    output = (done.stdout + done.stderr).strip()
    return Result(command, done.returncode, output, round(time.monotonic() - began, 1))
