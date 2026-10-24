"""Know-how a session reads when its work needs it: a skill (the Sponsor, 2026-10-23, crew#839).

A role's standing context (`config/agents.yaml`) is what it carries to every step. A
skill is narrower: how the crew does one kind of thing, offered only to the work that
does it, and read on demand with the `skill` tool. The start names it in one line, so
the rest of the start, and every other Task's, stays as it is.

The first is DevOps's `workspace`: mqucifer/sprint-metrics#739 spent its gate rounds
learning by trial how the crew runs a workspace. Its text is generated from the crew's
own constants and the container's own flags, so it can't drift from what the crew does.
The second is `ci`: how GitHub Actions runs a job's services, from GitHub's own pages.
Another skill is one more entry in `SKILLS` and one more condition in `for_task`.
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


def _first(argv: list[str], flag: str) -> str:
    """The first value `flag` has, or a word that says it has none: a flag taken out of
    the argv changes the text, never breaks the tool (the review of mqucifer/crew#880)."""
    return next(iter(_after(argv, flag)), "not set")


# Each flag the workspace skill reads, by the container it reads it from: a test holds
# each one in its argv, so the text says what runs.
READS = {
    "setup": ("--network", "--user", "--cap-drop", "--security-opt", "--read-only"),
    "held": ("--network",),
    "service": ("--user", "--cap-drop", "--tmpfs", "--read-only"),
}


def containers() -> dict[str, list[str]]:
    """The argv of setup's container, the session's, and a sample service's."""
    from crew_org.tools import container, services  # noqa: PLC0415
    from crew_org.tools.sandbox import Sandbox  # noqa: PLC0415

    root = Path("/work")
    sample = services.Service("db", "db@sha256:0", "999:999", tmpfs=["/data"])
    return {
        "setup": Sandbox().command(root, ["setup"], network=True),
        "held": Sandbox().command(root, ["sleep"], network=False, held="session"),
        "service": container.service_command("docker", sample, "db", "internal", []),
    }


def workspace() -> str:
    """How the crew runs a workspace, read from what it runs."""
    from crew_org.tools import container, devcontainer, services, workspace  # noqa: PLC0415
    from crew_org.tools.sandbox import HOME_DIR, OWN_ENV  # noqa: PLC0415

    def words(names: Any) -> str:
        return ", ".join(f"`{n}`" for n in names)

    argv = containers()
    setup, held, service = argv["setup"], argv["held"], argv["service"]
    owner = _first(service, "--user")
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
- It runs once the session's container has started, in a one-off container of its own \
with the network (`--network {_first(setup, "--network")}`). It shares only the \
worktree, so what it installs there is what the session sees. A failure is tried once \
more. It has {container.SETUP_TIMEOUT // 60} minutes.
- Once a setup has passed, it runs again before a `run`, the lint after an edit, or the \
gates, if a dependency file has changed since ({words(container.MANIFESTS)}). If the \
start's setup failed, nothing runs it again in the session: only `done`, which runs your \
branch's setup as it is then, with the network, in a container of its own.
- Setup's container runs as the host's user, never root (`--user`), \
{"with a read-only filesystem, " if read_only else ""}every capability dropped \
(`--cap-drop {dropped}`, `{secured}`). Only `/work` (the worktree), `/tmp` and the home \
(`/work/{HOME_DIR}`) can be written. So nothing installs into the image: no `apt-get`, \
no `sudo`. Name an image, pinned by digest, that already has what the project needs.
- A workspace file that names its own image replaces the crew's default setup \
(`{" ".join(workspace.SETUP)}`) and checks: name the setup it needs.
- `containerEnv` is the environment. The crew's own variables win: {words(sorted(OWN_ENV))}.

## The session
- After setup the session's container has no network \
(`--network {_first(held, "--network")}`, or its services' network, which has no \
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
`tmpfs` paths can be written, each owned by its user (for `user: "{owner}"`, \
`{(tmpfs or ["none:not set"])[-1].partition(":")[2]}`): a database \
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


_DOCS = "https://docs.github.com/en"
_SYNTAX = f"{_DOCS}/actions/reference/workflows-and-actions/workflow-syntax"
_SERVICES = f"{_DOCS}/actions/tutorials/use-containerized-services"


def ci() -> str:
    """How GitHub Actions runs a job's service containers, from GitHub's own pages.

    Each point says only what the page it links says (the operator, crew#839): what a
    card's own failure means is for its session to read in the log, not for the skill.
    """
    from crew_org.flows.ci import SERVICE_LOG_LINES  # noqa: PLC0415

    return f"""# How GitHub Actions runs a job's services

Each point is GitHub's, from the page it links.

## Reaching a service
- A job that runs on the runner, with no `container:`, reaches a service at \
`localhost:<port>` or `127.0.0.1:<port>`, and only through a port the service maps to \
the host in its `ports:`: a service doesn't expose its ports to the runner by default. \
([Communicating with Docker service containers]({_SERVICES}/use-docker-service-containers), \
"Running jobs on the runner machine")
- Only a job that runs in a container (`jobs.<job_id>.container`) reaches a service by \
its label: "The hostname of the service container is automatically mapped to the label \
name", over a bridge network the runner creates, and it needs no `ports:`. \
([the same page]({_SERVICES}/use-docker-service-containers), "Running jobs in a \
container"; [`jobs.<job_id>.services`]({_SYNTAX}#jobsjob_idservices))
- So the host a test connects to follows the job: `localhost` on the runner, the \
label in a container job. GitHub's PostgreSQL example sets `POSTGRES_HOST: localhost` \
for a job on the runner, with `5432:5432` in `ports:`, and `POSTGRES_HOST: postgres` for \
a container job. ([Creating PostgreSQL service containers]\
({_SERVICES}/create-postgresql-service-containers))
- A `ports:` entry with only the container's port maps it to a random free port on \
the host; the job reads it from `job.services.<service_id>.ports`. \
([Communicating with Docker service containers]({_SERVICES}/use-docker-service-containers), \
"Mapping Docker host and service container ports"; \
[`jobs.<job_id>.services.<service_id>.ports`]({_SYNTAX}#jobsjob_idservicesservice_idports))
- Service containers need a Linux runner: on GitHub-hosted runners, Ubuntu. \
([`jobs.<job_id>.services`]({_SYNTAX}#jobsjob_idservices))

## Health checks and `options`
- `options` takes "Additional Docker container resource options": the flags of \
`docker create`. "The `--network` option is not supported." \
([`jobs.<job_id>.services.<service_id>.options`]\
({_SYNTAX}#jobsjob_idservicesservice_idoptions))
- A health check is set there, with Docker's `--health-cmd`, `--health-interval`, \
`--health-timeout` and `--health-retries`. GitHub's PostgreSQL example uses them "to \
make sure the service is running". \
([Creating PostgreSQL service containers]({_SERVICES}/create-postgresql-service-containers))
- GitHub creates a fresh Docker container for each service configured in the \
workflow, and destroys it when the job completes. \
([Communicating with Docker service containers]({_SERVICES}/use-docker-service-containers))

## Logs
- In the run's page, failed steps are expanded; a step's log can be searched there, \
and the run's logs downloaded as an archive. \
([Using workflow run logs]({_DOCS}/actions/how-tos/monitor-workflows/use-workflow-run-logs))
- The REST API gives one job's whole log as a plain text file: \
`GET /repos/{{owner}}/{{repo}}/actions/jobs/{{job_id}}/logs`. \
([Download job logs for a workflow job]\
({_DOCS}/rest/actions/workflow-jobs#download-job-logs-for-a-workflow-job))
- A service container's ID is `job.services.<service_id>.id`, and its network's \
`job.services.<service_id>.network`, so a step can read its own log with \
`docker logs ${{{{ job.services.<service_id>.id }}}}`. \
([Contexts reference]({_DOCS}/actions/reference/workflows-and-actions/contexts#job-context))

## What the crew shows of a failed job
Not GitHub's: the crew's. The failing step, and before it, for each service the job \
started, its state as the runner last logged it (started, healthy, or what failed) and \
the last {SERVICE_LOG_LINES} lines of its own log, which the runner prints at the job's end.
"""


SKILLS: dict[str, Skill] = {
    "workspace": Skill(
        "workspace",
        "how the crew runs a workspace: setup, the network, services, and what the checks refuse",
        workspace,
    ),
    "ci": Skill(
        "ci",
        "how GitHub Actions runs a job's services: reaching them, health checks, and logs",
        ci,
    ),
}

# A workflow file: a Task whose branch changes one is offered `ci`.
WORKFLOWS = ".github/workflows/"


def for_task(role: Any, body: str | None, worktree: Path, paths: list[str]) -> list[Skill]:
    """The skills a Task's session is offered: by its role, and what its card says it is.

    DevOps's workspace Tasks get `workspace`: the first workspace file, the services or
    toolchain a Goal needs, a finding the gate filed on the workspace, or any Task whose
    branch already changes the workspace file. Its CI Tasks get `ci`: CI's services, the
    gate's finding that the workspace doesn't run what CI runs, or any Task whose branch
    changes a workflow (mqucifer/sprint-metrics#747 guessed how a job reaches its
    services, three times). Its release Tasks get neither.
    """
    from crew_org.flows import role_tasks  # noqa: PLC0415

    if role is not role_tasks.DEVOPS:
        return []
    found = role_tasks.marked(body)
    kind, key = (found.kind, found.key) if found is not None else ("", "")
    workspace_finding = kind == role_tasks.FINDING and key.startswith("workspace-")
    offered: list[Skill] = []
    if (
        kind in (role_tasks.FIRST_WORKSPACE, role_tasks.NEED)
        or workspace_finding
        or role_tasks.owns_workspace_file(role, worktree, paths)
    ):
        offered.append(SKILLS["workspace"])
    if (
        kind == role_tasks.CI_SERVICES
        or workspace_finding
        or any(p.startswith(WORKFLOWS) for p in paths)
    ):
        offered.append(SKILLS["ci"])
    return offered


def start_line(skills: list[Skill]) -> str:
    """The start's one line naming what can be read, or nothing."""
    if not skills:
        return ""
    named = "; ".join(f"`{s.name}`, {s.description}" for s in skills)
    return f"Skills for this Task: {named}. Read one with `skill` when you need it."
