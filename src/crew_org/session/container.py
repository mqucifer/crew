"""What a session needs of its workspace container (crew#778, decision 4b).

The container is held for one story, from the project's image, with the crew's
sandbox hardening and no network: Stage B builds it, keeping the replay's `Held`
interface. The session only runs commands in it. Who starts it, sets it up and
stops it is the caller: delivery runs its gates in the same container after `done`.
"""

from __future__ import annotations

from typing import Protocol

# A container that's gone answers every command with this code: Docker's own for "the
# engine failed, not the command", which the held container also returns when it
# finds its container missing. It ends the session as interrupted, not blocked (P3).
GONE = 125


class Ran(Protocol):
    """One command's result: what ran, its exit code, its output and how long it took."""

    @property
    def command(self) -> str: ...
    @property
    def code(self) -> int: ...
    @property
    def output(self) -> str: ...
    @property
    def seconds(self) -> float: ...


class Workspace(Protocol):
    """The held container, as the replay's `Held` and Stage B's have it."""

    def start(self) -> Ran: ...
    def setup(self, timeout: int = ...) -> Ran | None: ...
    def exec(self, command: list[str], timeout: int = ..., stdin: str | None = None) -> Ran: ...
    def stop(self) -> None: ...


class Gone(RuntimeError):
    """The workspace container is gone: nothing more can run in this session."""
