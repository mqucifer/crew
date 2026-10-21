"""A refinement step's tools: the Developer's, less what edits, and its role's own.

`view`, `search`, `find`, `tests_for` and `git` over one checkout of the project's
default branch, on the host (crew#807). `git` is the Developer's tool
(mqucifer/crew#808) with `restore` refused, and `search` reads the notes the start
names along with the repository.

A role's question can need more (ADR 0035, crew#846): its own tools that read
(`role_tools`), and for QA the Developer's `run`, in the Developer's workspace
container (ADR 0006, ADR 0032) over a clone of the checkout, so nothing it runs
touches the checkout the other steps read. The container starts on the first run
and goes when the session ends; a step that never runs starts none.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crew_org.rulebook import Refusal, refuse
from crew_org.session import role_tools
from crew_org.session.git import parse
from crew_org.session.tools import Reply, Tools

OFFERED = ("view", "search", "find", "tests_for", "git")
ALLOWED = "`status`, `diff`, `log`, `show`, `blame` and `grep`, which read"
_NO_CHECKS = SimpleNamespace(verify=())


class _Nowhere:
    """The workspace of a step that runs nothing: no tool it's offered reaches it."""

    def start(self) -> Any:
        raise RuntimeError("a reading step runs nothing")

    def setup(self, timeout: int = 0) -> Any:
        return None

    def resync(self, timeout: int = 0) -> Any:
        return None

    def exec(self, command: list[str], timeout: int = 0, stdin: str | None = None) -> Any:
        raise RuntimeError("a reading step runs nothing")

    def stop(self) -> None:
        return None


class RunsAtMain:
    """The Developer's workspace container over a clone of the checkout a step reads,
    started on the step's first `run` (crew#846), as `handover.setup_leaves` runs one."""

    def __init__(self, root: Path, name: str) -> None:
        self.root = root
        self.name = name
        self.held: Any = None
        self.copy: Path | None = None
        self.unready: Any = None

    def _ready(self) -> Any:
        """The started container, or the result that says why it isn't."""
        from crew_org.tools.container import Held, Result  # noqa: PLC0415

        if self.held is None and self.unready is None:
            self.copy = Path(tempfile.mkdtemp(prefix="reading-run-", dir=self.root.parent))
            head = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
            subprocess.run(["git", "clone", "--quiet", str(self.root), str(self.copy)], check=True)
            subprocess.run(["git", "-C", str(self.copy), "checkout", "--quiet", head], check=True)
            held = Held(self.copy, self.name)
            started = held.start()
            if not started.ok:
                self.unready = started
                return started
            done = held.setup()
            if done is not None and not done.ok:
                # Once more: a fetch can fail for a moment.
                done = held.setup()
            if done is not None and not done.ok:
                held.stop()
                self.unready = Result(done.command, done.code, f"setup failed: {done.output}")
                return self.unready
            self.held = held
        return self.held or self.unready

    def start(self) -> Any:
        return None

    def setup(self, timeout: int = 0) -> Any:
        return None

    def resync(self, timeout: int = 0) -> Any:
        return self.held.resync() if self.held is not None else None

    def exec(self, command: list[str], timeout: int = 0, stdin: str | None = None) -> Any:
        ready = self._ready()
        if ready is self.unready:
            return ready
        return ready.exec(command, timeout=timeout, stdin=stdin)

    def stop(self) -> None:
        if self.held is not None:
            self.held.stop()
        if self.copy is not None:
            shutil.rmtree(self.copy, ignore_errors=True)


class ReadTools(Tools):
    """The read tools over a checkout at the default branch, the start's notes, and the
    tools the step's role is given (`role_tools`), with `run` where it's QA's."""

    def __init__(
        self,
        root: Path,
        coverage: Any = None,
        notes: dict[str, str] | None = None,
        *,
        given: tuple[str, ...] = (),
        context: role_tools.Context | None = None,
        runs: RunsAtMain | None = None,
    ):
        from crew_org.tools.workspace import toolchain  # noqa: PLC0415

        super().__init__(root, runs or _Nowhere(), toolchain(root) if runs else _NO_CHECKS)
        self.coverage = coverage
        self.notes = notes or {}
        self.search_notes = True
        self.given = {n: role_tools.TOOLS[n] for n in given}
        self.context = context or role_tools.Context(root, "")
        self.offered = (*OFFERED, *(("run",) if runs else ()), *self.given)

    def call(self, name: str, args: Any) -> Reply:
        if name not in self.offered:
            try:
                refuse("session.arguments", "no_tool", tool=name, tools=", ".join(self.offered))
            except Refusal as exc:
                return Reply(str(exc), "refused", f"{name}: refused, no such tool", rule=exc.rule)
        if name in self.given:
            try:
                text = self.given[name].call(self.context, args or {})
            except Exception as exc:  # noqa: BLE001 - a tool's failure is its reply
                return Reply(
                    f"The tool failed: {type(exc).__name__}: {exc}", "error", f"{name}: failed"
                )
            return Reply(text, "ok", f"{name}: {len(text)} characters")
        return super().call(name, args)

    def stop(self) -> None:
        """The run container, if one started, and its clone; and what `look` fetched."""
        self.workspace.stop()
        self.context.looked.close()

    def git(self, command: str) -> Reply:
        """A git command that reads. `restore`, which the Developer's git takes, writes."""
        sub, _ = parse(command)
        if sub == "restore":
            refuse("session.git", "read_only", command=sub, allowed=ALLOWED)
        return super().git(command)
