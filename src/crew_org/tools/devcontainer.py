"""A project's workspace, from its `devcontainer.json`: the part the crew honours (crew#778).

The Development Container specification (containers.dev) is the standard file a
person's editor opens too, and it's the one source of a project's image and setup
(the Sponsor, 2026-10-18). The crew reads three things from it: the image, the one
setup command, and the environment. Whatever would loosen the container, build an
image or run on the host stays the crew's to set (ADR 0006). Those fields are named
as not honoured rather than skipped quietly: a file that asks for a feature and
doesn't get it misleads whoever reads it. The rest (`name`, `customizations`,
`forwardPorts` and the like) are for a person's editor, and the crew ignores them.

Without the file, the design's image and setup are used, as before.
"""

from __future__ import annotations

import json
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
    "dockerComposeFile": "the workspace is one container, from `image`",
    "initializeCommand": "it runs on the host",
    "postStartCommand": "the crew runs one setup command, once",
    "postAttachCommand": "the crew runs one setup command, once",
}


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


def find(root: Path) -> Path | None:
    """The project's `devcontainer.json`, or None."""
    return next((root / p for p in PATHS if (root / p).is_file()), None)


def read(root: Path) -> Definition | None:
    """The project's workspace definition, or None when it has no file."""
    found = find(root)
    if found is None:
        return None
    path = found.relative_to(root).as_posix()
    try:
        raw = json.loads(strip_comments(found.read_text(encoding="utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        return Definition(path, problems=[f"`{path}` doesn't parse: {exc}"])
    if not isinstance(raw, dict):
        return Definition(path, problems=[f"`{path}` isn't an object"])

    problems: list[str] = []
    not_honoured = {name: why for name, why in NOT_HONOURED.items() if raw.get(name) is not None}
    image = raw.get("image")
    if not isinstance(image, str) or not image.strip():
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
    return Definition(path, image, setup, env, problems, not_honoured)


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
