"""Running untrusted code.

Everything the crew generates is untrusted: an LLM wrote it, and testing it
means executing it. The default is therefore a container with no network, no
capabilities, a non-root user and hard resource limits.

When no container engine is available the sandbox **fails** rather than falling
back to the host. A silent fallback is worse than no sandbox at all, because it
looks protected while running arbitrary code against the user's own account.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"

# A host directory rather than a named volume: a named volume is created
# root-owned, and the container runs as the invoking user, so uv cannot write
# its cache into one. This directory is created by us and therefore ours.
CACHE_DIR = Path("var/uv-cache")

# Each command is its own container, and only the worktree carries over. A home
# inside it keeps what the dependency step installs for the checks after it, in
# any language: under /tmp, pip's user installs were gone before the checks ran
# (crew#690, ADR 0030). It goes when the worktree does, and ignores itself, so
# nothing in it is ever committed.
HOME_DIR = ".crew-home"

# How a project's commands run, for the roles that write them: the Architect
# choosing setup and checks, and the Developer making them pass. Told none of it,
# crew-presentation's design called the image's own `python` while its setup
# installed into a uv environment, and its Developer saw only "No module named
# pytest" (crew#690). Its lock then took playwright 1.64.0 against the image's
# v1.52 browsers, which can't launch them. Facts about this sandbox, kept beside
# the code they describe.
HOW_IT_RUNS = (
    "Each command, the setup and then each check, runs in its own container from "
    "the sandbox image. The image's filesystem is read-only: only the repository's "
    "checkout and an empty `/tmp` are writable, and only the checkout carries over from "
    f"one command to the next. HOME is `{HOME_DIR}` inside the checkout, so what setup installs "
    "under HOME (a user install, `pip install --user`, or a tool's own files) is "
    "still there for the checks, and `$HOME/.local/bin` comes first on PATH. Only "
    "the setup has a network. A check runs exactly its command: a bare `python` is "
    "the image's own, which doesn't see a virtual environment the setup made unless "
    "the command runs through it, as `uv run` does. A package that drives a program "
    "the image ships, such as Playwright and its browsers, must be the image's version "
    "of it: another version looks for builds the image doesn't have, and the checks "
    "have no network to fetch them. CI runs each command the same way."
)

DEFAULT_TIMEOUT = 300
PROBE_TIMEOUT = 15
MEMORY = "2g"
CPUS = "2"
PIDS = "512"


class Mode(StrEnum):
    REQUIRED = "required"
    """Container or nothing. The default."""

    OFF = "off"
    """Run on the host. Only ever an explicit, named choice."""


@dataclass(frozen=True)
class Sandbox:
    mode: Mode = Mode.REQUIRED
    engine: str = "docker"
    image: str = IMAGE

    @classmethod
    def from_config(cls, org: dict) -> Sandbox:
        section = org.get("sandbox") or {}
        return cls(
            mode=Mode(section.get("mode", Mode.REQUIRED)),
            engine=section.get("engine", "docker"),
            image=section.get("image", IMAGE),
        )

    @property
    def available(self) -> bool:
        return shutil.which(self.engine) is not None

    def unavailable_reason(self) -> str | None:
        """Why this sandbox cannot run, if it cannot."""
        if self.mode is Mode.OFF:
            return None
        if not self.available:
            return (
                f"{self.engine!r} is not on PATH, and the sandbox is {self.mode}. "
                "Generated code is not run on the host by default — start the engine, "
                "or set sandbox.mode to 'off' in org.yaml to accept that risk explicitly."
            )
        # `docker info` is slow enough on Docker Desktop to time out; asking for
        # the server version is the cheap way to learn whether a daemon answers.
        try:
            probe = subprocess.run(
                [self.engine, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"{self.engine} did not respond within {PROBE_TIMEOUT}s. Is the engine started?"
        if probe.returncode != 0:
            return f"{self.engine} is installed but no daemon is answering. Start it."
        return None

    @property
    def cache_dir(self) -> Path:
        path = CACHE_DIR.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def search_path(self) -> str | None:
        """The image's own PATH, or None until the image has been pulled."""
        return _image_path(self.engine, self.image)

    def command(
        self, worktree: Path, inner: list[str], *, network: bool, path: str | None = None
    ) -> list[str]:
        """Build the container invocation for one command.

        `path` is the image's own PATH. Given it, what setup installed under the
        home (`.local/bin`, as pip's user installs put it) is found first.
        """
        home = f"/work/{HOME_DIR}"
        found = ["-e", f"PATH={home}/.local/bin:{path}"] if path else []
        return [
            self.engine,
            "run",
            "--rm",
            # No network for anything but dependency resolution.
            "--network",
            "bridge" if network else "none",
            # Files stay owned by the user, and nothing runs as root.
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            # The container filesystem is immutable; only the worktree and
            # /tmp are writable.
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,size=512m",
            "--memory",
            MEMORY,
            "--memory-swap",
            MEMORY,
            "--pids-limit",
            PIDS,
            "--cpus",
            CPUS,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{worktree.resolve()}:/work",
            "-v",
            f"{self.cache_dir}:/cache",
            "-w",
            "/work",
            "-e",
            "UV_CACHE_DIR=/cache",
            "-e",
            f"HOME={home}",
            "-e",
            "XDG_CACHE_HOME=/cache",
            *found,
            self.image,
            *inner,
        ]


def prepare_home(worktree: Path) -> None:
    """The worktree's own home, made before a command runs in it."""
    home = worktree / HOME_DIR
    home.mkdir(exist_ok=True)
    ignore = home / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")


# Only an answer is kept: an image not yet pulled has none, and is asked again.
_PATHS: dict[tuple[str, str], str] = {}


def _image_path(engine: str, image: str) -> str | None:
    if (engine, image) in _PATHS:
        return _PATHS[(engine, image)]
    try:
        done = subprocess.run(
            [engine, "image", "inspect", "--format", "{{json .Config.Env}}", image],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        env = json.loads(done.stdout) or []
    except ValueError:
        return None
    path = next((e.split("=", 1)[1] for e in env if e.startswith("PATH=")), None)
    if path:
        _PATHS[(engine, image)] = path
    return path
