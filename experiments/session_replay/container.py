"""A workspace container held for one session (crew#778, decision 4b; Stage R).

Started from the project's image with today's sandbox hardening, built by the crew's
own `Sandbox.command`: not root, no capabilities, no new privileges, a read-only root
with /tmp writable, memory, CPU and process caps, and only the worktree mounted.
Setup runs once inside it with the network; then the network is disconnected, so
everything the session runs after that has none. Removed at the end, and any left
by a crash is swept up by its label.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from crew_org.tools import workspace
from crew_org.tools.sandbox import Sandbox, prepare_home

LABEL = "crew-replay"


@dataclass
class Result:
    command: str
    code: int
    output: str
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.code == 0


def sweep(engine: str = "docker") -> list[str]:
    """Remove every replay container left behind, by its label."""
    found = subprocess.run(
        [engine, "ps", "-aq", "--filter", f"label={LABEL}"], capture_output=True, text=True
    ).stdout.split()
    if found:
        subprocess.run([engine, "rm", "-f", *found], capture_output=True)
    return found


class Held:
    """One container for one session, with the worktree mounted at /work."""

    def __init__(self, worktree: Path, case: str) -> None:
        self.worktree = worktree
        self.tools = workspace.toolchain(worktree)
        sandbox = Sandbox()
        if self.tools.image:
            sandbox = replace(sandbox, image=self.tools.image)
        self.sandbox = sandbox
        self.name = f"{LABEL}-{case}-{uuid.uuid4().hex[:6]}"
        self.started = False
        self.network = True

    def start(self) -> Result:
        """Start it with the network, for setup only."""
        reason = self.sandbox.unavailable_reason()
        if reason:
            return Result("start", 126, reason)
        subprocess.run([self.sandbox.engine, "pull", "-q", self.sandbox.image], capture_output=True)
        prepare_home(self.worktree)
        argv = self.sandbox.command(
            self.worktree, ["sleep", "infinity"], network=True, path=self.sandbox.search_path()
        )
        # The crew's one-command form, held: detached and named instead of removed at exit.
        at = argv.index("--rm")
        argv[at : at + 1] = ["-d", "--name", self.name, "--label", f"{LABEL}=1"]
        done = subprocess.run(argv, capture_output=True, text=True)
        self.started = done.returncode == 0
        return Result("start", done.returncode, (done.stdout + done.stderr).strip())

    def setup(self, timeout: int = 900) -> Result | None:
        """The project's setup, once, while the network is still connected."""
        if not self.tools.setup:
            return None
        return self.exec(self.tools.setup, timeout=timeout)

    def cut_network(self) -> Result:
        done = subprocess.run(
            [self.sandbox.engine, "network", "disconnect", "bridge", self.name],
            capture_output=True,
            text=True,
        )
        self.network = done.returncode != 0
        return Result("network disconnect", done.returncode, (done.stdout + done.stderr).strip())

    def exec(
        self, command: list[str], timeout: int = workspace.DEFAULT_TIMEOUT, stdin: str | None = None
    ) -> Result:
        """Run one command inside, as the worktree's owner, from /work, with `stdin` if given."""
        import time  # noqa: PLC0415

        argv = [
            self.sandbox.engine,
            "exec",
            *(["-i"] if stdin is not None else []),
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-w",
            "/work",
            self.name,
            *command,
        ]
        began = time.monotonic()
        try:
            done = subprocess.run(
                argv, input=stdin, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return Result(" ".join(command), -1, f"no output within {timeout}s", timeout)
        return Result(
            " ".join(command),
            done.returncode,
            (done.stdout + done.stderr).strip(),
            round(time.monotonic() - began, 1),
        )

    def stop(self) -> None:
        if self.started:
            subprocess.run([self.sandbox.engine, "rm", "-f", self.name], capture_output=True)
            self.started = False
