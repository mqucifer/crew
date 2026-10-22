"""A refinement step's tools: the Developer's, less what edits or runs, and its role's own.

`view`, `search`, `find`, `tests_for` and `git` over one checkout of the project's
default branch, on the host. Nothing runs, so there's no workspace container (the
Sponsor, crew#807). `git` is the Developer's tool (mqucifer/crew#808) with `restore`
refused, and `search` reads the notes the start names along with the repository.

A role's question can need more (ADR 0035, crew#846): its own tools that read
(`role_tools`), such as the Architect's `contracts` and the UX Designer's `look`.
"""

from __future__ import annotations

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


class ReadTools(Tools):
    """The read tools over a checkout at the default branch, the start's notes, and the
    tools the step's role is given (`role_tools`)."""

    def __init__(
        self,
        root: Path,
        coverage: Any = None,
        notes: dict[str, str] | None = None,
        *,
        given: tuple[str, ...] = (),
        context: role_tools.Context | None = None,
    ):
        super().__init__(root, _Nowhere(), _NO_CHECKS, coverage, notes)
        self.search_notes = True
        self.context = context or role_tools.Context(root, "")
        tools = {n: role_tools.TOOLS[n] for n in given}
        self.given = {n: t for n, t in tools.items() if t.offered(self.context)}
        self.offered = (*OFFERED, *self.given)

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
        """What `look` fetched, which goes when the session ends."""
        self.context.looked.close()

    def git(self, command: str) -> Reply:
        """A git command that reads. `restore`, which the Developer's git takes, writes."""
        sub, _ = parse(command)
        if sub == "restore":
            refuse("session.git", "read_only", command=sub, allowed=ALLOWED)
        return super().git(command)
