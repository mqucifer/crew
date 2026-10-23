"""Know-how a session reads when its work needs it: a skill (the Sponsor, 2026-10-23, crew#839).

A role's standing context (`config/agents.yaml`) is what it carries to every step. A
skill is narrower: how the crew does one kind of thing, offered only to the work that
does it, and read on demand with the `skill` tool. The start names it in one line, so
the rest of the start, and every other Task's, stays as it is.

The first is DevOps's `workspace`: mqucifer/sprint-metrics#739 spent its gate rounds
learning by trial how the crew runs a workspace. Its text is generated from the crew's
own constants and the container's own flags, so it can't drift from what the crew does.
A second skill is one more entry in `SKILLS` and one more condition in `for_task`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skill:
    name: str
    # What the start says it's for, in one line.
    description: str
    body: Callable[[], str]


def _after(argv: list[str], flag: str) -> list[str]:
    """Every value a container's argv gives `flag`."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


def workspace() -> str:
    """How the crew runs a workspace, read from what it runs."""
    from crew_org.tools import container, devcontainer, services, workspace  # noqa: PLC0415
    from crew_org.tools.sandbox import HOME_DIR, OWN_ENV, Sandbox  # noqa: PLC0415

    def words(names: Any) -> str:
        return ", ".join(f"`{n}`" for n in names)

    root = Path("/work")
    setup = Sandbox().command(root, ["setup"], network=True)
    held = Sandbox().command(root, ["sleep"], network=False, held="session")
    sample = services.Service("db", "db@sha256:0", "999:999", tmpfs=["/data"])
    service = container.service_command("docker", sample, "db", "internal", [])
    mounted = [*_after(service, "-v"), *_after(service, "--mount")]
    tmpfs = _after(service, "--tmpfs")
    dropped = " ".join(_after(setup, "--cap-drop"))
    secured = " ".join(_after(setup, "--security-opt"))
    read_only = "--read-only" in setup and "--read-only" in service
    not_honoured = "\n".join(f"- `{n}`: {why}" for n, why in devcontainer.NOT_HONOURED.items())
    return f"""# How the crew runs a workspace

The crew reads `{devcontainer.PATHS[0]}` (or `{devcontainer.PATHS[1]}`) and its compose \
file, and starts every container itself. Nothing in them can loosen a container.

## Setup
- One setup command, from exactly one of {words(devcontainer.SETUP_FIELDS)}, as an array \
of words: a string would run in a shell, which the crew never does. For several steps, \
one script. A field by any other name is read as nothing.
- It runs at the start, in a one-off container with the network \
(`--network {_after(setup, "--network")[0]}`), before the session's container starts; \
again at `done`; and again when a dependency file changes ({words(container.MANIFESTS)}). \
It has {container.SETUP_TIMEOUT // 60} minutes.
- That container runs as the crew's own user (`--user {_after(setup, "--user")[0]}`), \
never root, \
{"with a read-only filesystem, " if read_only else ""}every capability dropped \
(`--cap-drop {dropped}`, `{secured}`). Only `/work` (the worktree), `/tmp` and the home \
(`/work/{HOME_DIR}`) can be written. So nothing installs into the image: no `apt-get`, \
no `sudo`. Name an image, pinned by digest, that already has what the project needs.
- A workspace file that names its own image replaces the crew's default setup \
(`{" ".join(workspace.SETUP)}`) and checks: name the setup it needs.
- `containerEnv` is the environment. The crew's own variables win: {words(sorted(OWN_ENV))}.

## The session
- After setup the session's container has no network \
(`--network {_after(held, "--network")[0]}`, or its services' network, which has no \
route out). Whatever a command would fetch then fails: a `uv run` that resolves a \
package, a test that downloads. Setup has to leave it installed.

## Services
- `dockerComposeFile` names the compose file, `service` the workspace's own service in \
it, and `runServices`, optionally, which others start. Of the workspace's service only \
`image` is read: its environment is `containerEnv`.
- Of each other service the crew reads {words(services.HONOURED)}. Its image is pinned \
by digest; its `user` isn't root; its `healthcheck.test` is `["CMD", ...]`, with no shell.
- Each starts as its own `user`, {"read-only, " if "--read-only" in service else ""}\
`--cap-drop {" ".join(_after(service, "--cap-drop"))}`, \
{"with no mounts" if not mounted else "with only the crew's mounts"}, on an internal \
network the workspace shares, reached by the service's name. Only `/tmp` and its \
`tmpfs` paths can be written, each owned by its user (for `user: "{sample.user}"`, \
`{tmpfs[-1].partition(":")[2]}`): a database \
needs its data and socket directories there (Postgres: `PGDATA`'s directory and \
`/var/run/postgresql`).

## Not honoured
Named by `workspace_check`, and ignored:
{not_honoured}
Any compose service field besides those read: {services.NOT_HONOURED}.

## What the checks refuse
- `workspace_check`, before `done`, without starting anything: the image pinned by \
digest, setup as words, one setup field, a field no specification defines, each \
compose image pinned to a digest its registry has, and each service as the crew starts \
one.
- At `done`, the gate also starts your branch's services and runs your setup, with the \
network, in a container of its own, then the project's whole checks.
"""


SKILLS: dict[str, Skill] = {
    "workspace": Skill(
        "workspace",
        "how the crew runs a workspace: setup, the network, services, and what the checks refuse",
        workspace,
    ),
}


def for_task(role: Any, body: str | None, worktree: Path, paths: list[str]) -> list[Skill]:
    """The skills a Task's session is offered: by its role, and what its card says it is.

    DevOps's workspace Tasks get `workspace`: the first workspace file, the services or
    toolchain a Goal needs, a finding the gate filed on the workspace, or any Task whose
    branch already changes the workspace file. Its CI and release Tasks don't.
    """
    from crew_org.flows import role_tasks  # noqa: PLC0415

    if role is not role_tasks.DEVOPS:
        return []
    found = role_tasks.marked(body)
    kind, key = (found.kind, found.key) if found is not None else ("", "")
    if (
        kind in (role_tasks.FIRST_WORKSPACE, role_tasks.NEED)
        or (kind == role_tasks.FINDING and key.startswith("workspace-"))
        or role_tasks.changes_workspace(role, worktree, paths)
    ):
        return [SKILLS["workspace"]]
    return []


def start_line(skills: list[Skill]) -> str:
    """The start's one line naming what can be read, or nothing."""
    if not skills:
        return ""
    named = "; ".join(f"`{s.name}`, {s.description}" for s in skills)
    return f"Skills for this Task: {named}. Read one with `skill` when you need it."
