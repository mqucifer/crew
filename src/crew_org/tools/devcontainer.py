"""A project's workspace, from its `devcontainer.json`: the part the crew honours (crew#778).

The Development Container specification (containers.dev) is the standard file a
person's editor opens too, and it's the one source of a project's image and setup
(the Sponsor, 2026-10-18). The crew reads three things from it: the image, the one
setup command, and the environment. A file that names a compose file gives the image
as its workspace service's, and the compose file's other services run beside the
workspace (`tools/services.py`, crew#814). Whatever would loosen the container, build an
image or run on the host stays the crew's to set (ADR 0006). Those fields are named
as not honoured rather than skipped quietly: a file that asks for a feature and
doesn't get it misleads whoever reads it. The rest (`name`, `customizations`,
`forwardPorts` and the like) are for a person's editor, and the crew ignores them. A
field that isn't the specification's at all is named as unknown, with the fields the
crew reads instead: on mqucifer/sprint-metrics#739 a setup named `setupCommands` never
ran, and nothing said so (crew#839).

Without the file, the design's image and setup are used, as before.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from crew_org.tools.sandbox import OWN_ENV

# Where the specification looks, in its order.
PATHS = (".devcontainer/devcontainer.json", ".devcontainer.json")

# The lifecycle commands that run once, when the container is created. The crew runs
# one of them as its setup, with the network, before the session's container starts.
SETUP_FIELDS = ("onCreateCommand", "updateContentCommand", "postCreateCommand")

NOT_HONOURED = {
    "runArgs": "the crew sets the container's flags",
    "mounts": "only the worktree and the download cache are mounted",
    "workspaceMount": "the worktree is mounted at /work",
    "privileged": "the container never runs privileged",
    "capAdd": "every capability is dropped",
    "securityOpt": "the crew sets the security options",
    "init": "the crew sets the init process",
    "appPort": "the container has no network after setup",
    "features": "nothing is installed into the image: name an image that has it",
    "overrideFeatureInstallOrder": "nothing is installed into the image",
    "build": "the image is pulled, never built: name it by digest",
    "dockerFile": "the image is pulled, never built: name it by digest",
    "initializeCommand": "it runs on the host",
    "postStartCommand": "the crew runs one setup command, once",
    "postAttachCommand": "the crew runs one setup command, once",
}

# Every field the specification defines, from its schema (devcontainers/spec,
# schemas/devContainer.base.schema.json at c95ffee, 2026-10-23): one the crew doesn't
# read is honoured, named as not honoured, or for a person's editor, never unknown.
SPEC_FIELDS = frozenset(
    {
        "$schema",
        "appPort",
        "build",
        "capAdd",
        "containerEnv",
        "containerUser",
        "context",
        "customizations",
        "dockerComposeFile",
        "dockerFile",
        "features",
        "forwardPorts",
        "hostRequirements",
        "image",
        "init",
        "initializeCommand",
        "mounts",
        "name",
        "onCreateCommand",
        "otherPortsAttributes",
        "overrideCommand",
        "overrideFeatureInstallOrder",
        "portsAttributes",
        "postAttachCommand",
        "postCreateCommand",
        "postStartCommand",
        "privileged",
        "remoteEnv",
        "remoteUser",
        "runArgs",
        "runServices",
        "secrets",
        "securityOpt",
        "service",
        "shutdownAction",
        "updateContentCommand",
        "updateRemoteUserUID",
        "userEnvProbe",
        "waitFor",
        "workspaceFolder",
        "workspaceMount",
    }
)
# The fields the crew reads, for an unknown field's reply.
READ = "`image` (or `dockerComposeFile` and `service`), one setup command and `containerEnv`"
SETUP_LIKE = re.compile(r"setup|command|install|script|bootstrap|create", re.IGNORECASE)


@dataclass(frozen=True)
class Definition:
    """What a project's `devcontainer.json` gives the crew."""

    path: str
    image: str | None = None
    setup: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Why it can't be used as written. Its commands aren't run.
    problems: list[str] = field(default_factory=list)
    # Each field the crew doesn't honour, with why. Ignored, so the workspace is still safe.
    not_honoured: dict[str, str] = field(default_factory=dict)
    # Each field the specification doesn't define, with what the crew reads instead.
    # Ignored too, so it's the gate's to refuse, never the start's (crew#839).
    unknown: dict[str, str] = field(default_factory=dict)


def instead(name: str) -> str:
    """What an unknown field's reply says the crew reads for that purpose."""
    if SETUP_LIKE.search(name):
        setup = ", ".join(f"`{f}`" for f in SETUP_FIELDS)
        return f"isn't a devcontainer field: the crew's setup is one of {setup}, as words"
    return f"isn't a devcontainer field: the crew reads {READ}"


class Outside(ValueError):
    """A path the branch names that leads out of the repository, or through a link."""


def inside(root: Path, path: str) -> Path:
    """`path` in the repository at `root`, or `Outside` (crew#839).

    The branch names these paths (`dockerComposeFile`, a file it adds), so an absolute
    path, one that climbs out, or a symbolic link anywhere on the way would have the
    crew read a host file, such as a settings file holding a token, and quote it back
    in a parse error. None of those is followed; the refusal names the path, never what
    is there.
    """
    import posixpath  # noqa: PLC0415

    named = posixpath.normpath(path)
    if posixpath.isabs(named) or named.startswith("~") or named.split("/")[0] == "..":
        raise Outside(f"`{path}` is outside the repository: not read")
    base = root.resolve()
    target = base
    for part in named.split("/"):
        if part in ("", "."):
            continue
        target = target / part
        if target.is_symlink():
            raise Outside(f"`{path}` goes through a symbolic link: not read")
    if not target.resolve().is_relative_to(base):
        raise Outside(f"`{path}` is outside the repository: not read")
    return target


def read_inside(root: Path, path: str) -> str | None:
    """The text of `path` in the repository at `root`, None when it isn't a text file
    there, or `Outside` when it leads out of the repository (`inside`)."""
    target = inside(root, path)
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def find(root: Path) -> Path | None:
    """The project's `devcontainer.json`, or None. One that's a link is found, so `read`
    can refuse it, never followed."""
    return next((root / p for p in PATHS if (root / p).is_symlink() or (root / p).is_file()), None)


def read(root: Path) -> Definition | None:
    """The project's workspace definition, or None when it has no file."""
    found = find(root)
    if found is None:
        return None
    path = found.relative_to(root).as_posix()
    try:
        text = read_inside(root, path)
    except Outside as exc:
        return Definition(path, problems=[str(exc)])
    try:
        raw = json.loads(strip_comments(text or ""))
    except (ValueError, UnicodeDecodeError) as exc:
        return Definition(path, problems=[f"`{path}` doesn't parse: {exc}"])
    if not isinstance(raw, dict):
        return Definition(path, problems=[f"`{path}` isn't an object"])

    problems: list[str] = []
    not_honoured = {name: why for name, why in NOT_HONOURED.items() if raw.get(name) is not None}
    unknown = {str(name): instead(str(name)) for name in raw if name not in SPEC_FIELDS}
    image = raw.get("image")
    if raw.get("dockerComposeFile") is not None and image is None:
        image, why = _compose_image(root, path, raw, not_honoured, unknown)
        if why:
            problems.append(why)
    elif not isinstance(image, str) or not image.strip():
        problems.append(f"`{path}` names no `image`")
        image = None

    named = [f for f in SETUP_FIELDS if raw.get(f) is not None]
    setup = None
    if len(named) > 1:
        problems.append(
            f"`{path}` names {', '.join(f'`{f}`' for f in named)}: the crew runs one setup "
            "command, a script if it needs several steps"
        )
    elif named:
        setup, why = _command(raw[named[0]])
        if why:
            problems.append(f"`{path}`'s `{named[0]}` {why}")

    env: dict[str, str] = {}
    given = raw.get("containerEnv") or {}
    if not isinstance(given, dict):
        problems.append(f"`{path}`'s `containerEnv` isn't an object")
        given = {}
    for name, value in given.items():
        if not isinstance(value, str):
            problems.append(f"`{path}`'s `containerEnv.{name}` isn't a string")
        elif "${" in value:
            # `${localEnv:...}` would read the host's environment: nothing is substituted.
            problems.append(f"`{path}`'s `containerEnv.{name}` uses a variable, never filled in")
        elif name in OWN_ENV:
            not_honoured[f"containerEnv.{name}"] = "the crew sets it"
        else:
            env[name] = value
    return Definition(path, image, setup, env, problems, not_honoured, unknown)


def _compose_image(
    root: Path,
    path: str,
    raw: dict[str, object],
    not_honoured: dict[str, str],
    unknown: dict[str, str],
) -> tuple[str | None, str | None]:
    """The workspace's image from its service in the compose file, or why there's none.

    Only the image is read of the workspace's service: each other field is named, as not
    honoured or unknown, since on mqucifer/sprint-metrics#739 the `HOME` and `PATH` its
    `environment` set did nothing, and nothing said so (the operator, crew#839).
    """
    import posixpath  # noqa: PLC0415

    import yaml  # type: ignore[import-untyped]  # noqa: PLC0415

    from crew_org.tools import services  # noqa: PLC0415

    named, service = raw.get("dockerComposeFile"), raw.get("service")
    named = named[0] if isinstance(named, list) and len(named) == 1 else named
    if not isinstance(named, str) or not isinstance(service, str):
        return None, f"`{path}` names one compose file and its workspace `service` to use them"
    compose = posixpath.normpath(posixpath.join(posixpath.dirname(path), named))
    try:
        text = read_inside(root, compose)
    except Outside as exc:
        return None, f"`{path}`'s compose file: {exc}"
    if text is None:
        return None, f"`{path}`'s compose file `{compose}` isn't there"
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"`{path}`'s compose file `{compose}` can't be read: {exc}"
    found = ((spec or {}).get("services") or {}).get(service) if isinstance(spec, dict) else None
    image = found.get("image") if isinstance(found, dict) else None
    for key in found if isinstance(found, dict) else ():
        if key == "image" or str(key).startswith("x-"):
            continue
        if services.known(str(key)):
            not_honoured[f"{service}.{key}"] = WORKSPACE_SERVICE.get(
                str(key), "only the workspace service's `image` is read"
            )
        else:
            unknown[f"{service}.{key}"] = f"in `{compose}` {services.UNKNOWN}"
    if not isinstance(image, str) or not image.strip():
        return None, f"`{compose}`'s `{service}` names no `image`"
    return image, None


# Why the workspace service's own fields aren't read, where the specification has a
# field for it in `devcontainer.json`.
WORKSPACE_SERVICE = {
    "environment": "the workspace's environment is `devcontainer.json`'s `containerEnv`",
    "user": "the crew sets the workspace's user",
    "command": "the crew starts the workspace itself",
    "healthcheck": "the crew starts the workspace itself",
}


def _command(value: object) -> tuple[list[str] | None, str | None]:
    """A lifecycle command as the words the crew runs, or why it can't be."""
    if isinstance(value, str):
        # The specification runs a string in a shell, which the crew never does (§19.4).
        return None, "is a string, which runs in a shell: give it as an array of words"
    if isinstance(value, list) and value and all(isinstance(w, str) and w for w in value):
        return list(value), None
    if isinstance(value, dict):
        return None, "names several commands: the crew runs one, a script if it needs several"
    return None, "isn't an array of words"


def strip_comments(text: str) -> str:
    """JSON with comments and trailing commas (JSONC, which the specification allows) as JSON."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c in "}]":
            # A trailing comma: the last thing before this bracket, past whitespace.
            at = len(out) - 1
            while at >= 0 and out[at].isspace():
                at -= 1
            if at >= 0 and out[at] == ",":
                del out[at]
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)
