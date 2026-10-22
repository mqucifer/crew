"""The services a project's tests need, beside its workspace (crew#814 part 4, crew#860).

A project declares them as the Development Container specification does: its
`devcontainer.json` names a compose file (`dockerComposeFile`), the workspace's own
service in it (`service`), and optionally the ones to start (`runServices`). The
rest of the compose file's services are the workspace's services: sprint-metrics'
Postgres, for its database tests.

The crew never runs `docker compose`. It reads a subset of each service and starts
it itself, so nothing in the file can loosen the sandbox (ADR 0006): no ports, no
mounts, no capabilities, never root, read-only but for the tmpfs paths it names, on
a network with no route out that only the workspace shares. What it doesn't honour
is named, as `devcontainer.py` names what it doesn't.

They're read from the default branch, never the story's worktree: otherwise a
story's generated code would choose what the host pulls and runs (the operator,
crew#860). A change to them takes effect once it's reviewed and merged.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from crew_org.tools import devcontainer

# What the crew reads of a compose service. Everything else is named as not honoured.
HONOURED = ("image", "environment", "command", "healthcheck", "tmpfs", "user")
NOT_HONOURED = "the crew starts the service itself, with the sandbox's hardening"
ROOT_NAMES = {"root"}
# A service's name becomes a container name and a network alias: never an option.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class Service:
    """One service as the crew starts it."""

    name: str
    image: str
    user: str
    env: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    # The health check's command, in the service's container: no shell (§19.4).
    health: list[str] | None = None
    tmpfs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Declared:
    """The services a project declares, from its default branch."""

    services: list[Service] = field(default_factory=list)
    # Each service that can't be started as written, by name, with why. Not started:
    # each is a finding at `done` for DevOps, never a silent skip.
    problems: dict[str, str] = field(default_factory=dict)
    # Each field not honoured, as `service.field`, with why.
    not_honoured: dict[str, str] = field(default_factory=dict)
    # The workspace definition the services came from, if any.
    path: str | None = None
    # Skip reasons the project declares CI makes too (`customizations.crew.ciOnlySkips`):
    # a test that skips at `done` for one of these isn't one CI runs (crew#860).
    ci_only: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.services or self.problems)


def at_main(worktree: Path) -> Declared:
    """The services declared on the default branch the worktree's branch left."""
    from crew_org.tools.regression import merged_base  # noqa: PLC0415

    merged = merged_base(worktree)
    return declared(merged.text) if merged is not None else Declared()


def declared(read: Callable[[str], str | None]) -> Declared:
    """The services a project declares, with `read` giving a file's text by its path."""
    found = next(((p, t) for p in devcontainer.PATHS if (t := read(p)) is not None), None)
    if found is None:
        return Declared()
    path, text = found
    try:
        raw = json.loads(devcontainer.strip_comments(text))
    except ValueError:
        return Declared()  # `devcontainer.read` says it doesn't parse
    if not isinstance(raw, dict):
        return Declared(path=path)
    return replace(_composed(raw, path, read), ci_only=ci_only(raw))


def ci_only(raw: dict[str, object]) -> list[str]:
    """The skip reasons the project declares CI-only, in the spec's slot for a tool's own
    settings (`customizations`)."""
    crew = (raw.get("customizations") or {}) if isinstance(raw.get("customizations"), dict) else {}
    given = crew.get("crew") if isinstance(crew, dict) else None
    found = given.get("ciOnlySkips") if isinstance(given, dict) else None
    return [r for r in found if isinstance(r, str) and r] if isinstance(found, list) else []


def compose_file(read: Callable[[str], str | None]) -> str | None:
    """The compose file a project's `devcontainer.json` names, from the repository's root."""
    found = next(((p, t) for p in devcontainer.PATHS if (t := read(p)) is not None), None)
    if found is None:
        return None
    try:
        raw = json.loads(devcontainer.strip_comments(found[1]))
    except ValueError:
        return None
    return _compose_path(raw, found[0]) if isinstance(raw, dict) else None


def _compose_path(raw: dict[str, object], path: str) -> str | None:
    """`dockerComposeFile`, resolved from the definition's own folder; None unless one file."""
    named = raw.get("dockerComposeFile")
    if isinstance(named, list) and len(named) == 1:
        named = named[0]
    if not isinstance(named, str):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(path), named))


def _composed(raw: dict[str, object], path: str, read: Callable[[str], str | None]) -> Declared:
    """The services of a definition that names a compose file."""
    if raw.get("dockerComposeFile") is None:
        return Declared(path=path)
    compose = _compose_path(raw, path)
    if compose is None:
        why = f"`{path}`'s `dockerComposeFile` names several files: the crew reads one"
        return Declared(problems={"compose": why}, path=path)
    found_compose = read(compose)
    if found_compose is None:
        why = f"`{path}` names `{compose}`, which isn't on the default branch"
        return Declared(problems={"compose": why}, path=path)
    try:
        spec = yaml.safe_load(found_compose)
    except yaml.YAMLError as exc:
        return Declared(problems={"compose": f"`{compose}` doesn't parse: {exc}"}, path=path)
    given = spec.get("services") if isinstance(spec, dict) else None
    if not isinstance(given, dict):
        return Declared(problems={"compose": f"`{compose}` has no `services`"}, path=path)
    workspace = raw.get("service")
    wanted = raw.get("runServices")
    started = wanted if isinstance(wanted, list) else None
    names = [n for n in given if n != workspace and (started is None or n in started)]
    result = Declared(path=path)
    for name in names:
        service, why = _service(str(name), given[name], compose, result.not_honoured)
        if service is not None:
            result.services.append(service)
        else:
            result.problems[str(name)] = why
    return result


def _service(
    name: str, raw: object, compose: str, not_honoured: dict[str, str]
) -> tuple[Service | None, str]:
    """One compose service as the crew starts it, or why it can't be."""
    where = f"`{compose}`'s `{name}`"
    if not NAME.fullmatch(name):
        return None, f"{where}: a service's name is letters, digits, `_`, `.` and `-`"
    if not isinstance(raw, dict):
        return None, f"{where} isn't an object"
    for key in raw:
        if key not in HONOURED:
            not_honoured[f"{name}.{key}"] = NOT_HONOURED
    image = raw.get("image")
    if not isinstance(image, str) or image.startswith("-") or any(c.isspace() for c in image):
        return None, f"{where} names no image: one is a name, never an option"
    if "@sha256:" not in image:
        # The image rule (§19): what the host pulls is pinned, never a tag that can move.
        return None, f"{where} names no image pinned by digest (name@sha256:...)"
    user = raw.get("user")
    if not isinstance(user, str | int) or _root(str(user)):
        return None, f"{where} names no `user` other than root: a service never runs as root"
    env = raw.get("environment") or {}
    if isinstance(env, list):
        env = dict(str(e).partition("=")[::2] for e in env)
    if not isinstance(env, dict):
        return None, f"{where}'s `environment` isn't a map or a list"
    for key, value in env.items():
        if not isinstance(value, str | int) or "${" in str(value):
            return None, f"{where}'s `environment.{key}` isn't a plain value"
    command = raw.get("command")
    if command is not None and not _words(command):
        return None, f"{where}'s `command` isn't an array of words: nothing runs in a shell"
    health = None
    test = (
        (raw.get("healthcheck") or {}).get("test")
        if isinstance(raw.get("healthcheck"), dict)
        else None
    )
    if test is not None:
        if not (_words(test) and test[0] == "CMD" and len(test) > 1):
            return (
                None,
                f"{where}'s `healthcheck.test` isn't [\"CMD\", ...]: nothing runs in a shell",
            )
        health = list(test[1:])
    tmpfs = raw.get("tmpfs") or []
    tmpfs = [tmpfs] if isinstance(tmpfs, str) else tmpfs
    if not _words(tmpfs) and tmpfs:
        return None, f"{where}'s `tmpfs` isn't a list of paths"
    return Service(
        name,
        image,
        str(user),
        {str(k): str(v) for k, v in env.items()},
        list(command) if command is not None else None,
        health,
        [str(p).partition(":")[0] for p in tmpfs],
    ), ""


def _root(user: str) -> bool:
    """A user or group that's root, by name or by number however written (`00`, `+0`),
    or one that names nothing or reads as an option."""
    if not user.strip() or user.startswith("-"):
        return True
    for part in user.split(":"):
        part = part.strip()
        if part in ROOT_NAMES:
            return True
        try:
            if int(part) == 0:
                return True
        except ValueError:
            continue
    return False


def _words(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(w, str) and w for w in value)
