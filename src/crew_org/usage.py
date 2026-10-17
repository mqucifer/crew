"""A producer's usage notes, followed from a record's `uses` (ADR 0021; crew#690).

Records point to the source's notes and never copy a shape. crew-presentation's
record said "used as the crew's README says", and nothing followed it: the
Architect guessed the package's layout, and a story's gate would have refused
every real package. Each section is read from the producer's default branch, once
per run, so the roles see what it says today.

A `uses` entry that names its `pin` and `contract` also shows what the producer
provides at the release this project pins: a JSON schema's fields, a
stylesheet's tokens and classes, a document as written. Read at the pinned tag,
not the producer's latest, since a producer's schema moves on between releases.
crew-presentation's roles saw only the notes, and an epic promised two numbers
its package doesn't carry.

Read without a token: every producer the crew uses is public. A private one
would need the crew's token here.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from crew_org.project import Use

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{file}"
TIMEOUT = 15

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@lru_cache(maxsize=32)
def _file(repo: str, file: str, ref: str = "HEAD") -> str:
    response = httpx.get(
        RAW.format(repo=repo, ref=ref, file=file), timeout=TIMEOUT, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def section(markdown: str, heading: str) -> str | None:
    """The text under `heading`, to the next heading at its level or above."""
    wanted = heading.strip().casefold()
    lines = markdown.splitlines()
    level, start, fenced = 0, None, False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else _HEADING.match(line)
        if not found:
            continue
        depth = len(found.group(1))
        if start is None:
            if found.group(2).strip().casefold() == wanted:
                level, start = depth, i + 1
        elif depth <= level:
            return "\n".join(lines[start:i]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else None


def notes(use: Use) -> str:
    """What `use` points to, or why it couldn't be read."""
    try:
        text = _file(use.repo, use.file)
    except httpx.HTTPError as exc:
        return f"(Couldn't read `{use.file}` from {use.repo}: {exc}.)"
    found = section(text, use.section)
    if found is None:
        return f'({use.repo}\'s `{use.file}` has no section "{use.section}".)'
    return found


# A schema reaches deep: sprint-metrics' answers sit inside the package's index.
DEPTH = 8
_TOKEN = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
_CLASS = re.compile(r"(?<![\w-])\.([A-Za-z_][A-Za-z0-9_-]*)")


def pinned(use: Use, root: Path | None) -> str | None:
    """The release this project names in `use.pin`, or None."""
    if root is None or not use.pin:
        return None
    path = root / use.pin
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.strip()
    return None


def schema_fields(schema: dict[str, Any]) -> list[str]:
    """Every field a JSON schema defines, as a path: `sprints[].name`."""
    return list(described_fields(schema))


def described_fields(schema: dict[str, Any]) -> dict[str, str]:
    """Every field a JSON schema defines, by path, with what the schema says of it.

    Its description, and that it's deprecated when it is: the facts that say which
    of two fields to use. Shown only the names, the UX Designer was offered
    `cycle_time_days` beside `cycle_time_minutes` and picked days, and the site
    showed "0 days" for one-day sprints (crew#761). Empty for a field the schema
    says nothing about.
    """
    defs = schema.get("$defs") or schema.get("definitions") or {}
    found: dict[str, str] = {}

    def walk(node: Any, path: str, depth: int, seen: frozenset[str]) -> None:
        if not isinstance(node, dict) or depth > DEPTH:
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            name = ref.rsplit("/", 1)[-1]
            if name in seen:
                return
            walk(defs.get(name), path, depth, seen | {name})
            return
        for key in ("anyOf", "oneOf", "allOf"):
            for option in node.get(key) or []:
                walk(option, path, depth, seen)
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]", depth, seen)
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            walk(extra, f"{path}.<key>" if path else "<key>", depth, seen)
        for name, sub in (node.get("properties") or {}).items():
            child = f"{path}.{name}" if path else name
            if child not in found:
                found[child] = _said(sub)
            walk(sub, child, depth + 1, seen)

    walk(schema, "", 0, frozenset())
    return found


def _said(field: Any) -> str:
    if not isinstance(field, dict):
        return ""
    said = " ".join(str(field.get("description") or "").split())
    if field.get("deprecated") is True:
        said = f"deprecated. {said}".strip()
    return said


def css_names(text: str) -> tuple[list[str], list[str]]:
    """A stylesheet's custom properties (tokens) and class names."""
    without_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    tokens = sorted(set(_TOKEN.findall(without_comments)))
    selectors = re.sub(r"\{[^{}]*\}", "{}", without_comments)
    classes = sorted(set(_CLASS.findall(selectors)))
    return tokens, classes


def _provided(path: str, text: str) -> str:
    """What one contract file says the producer provides, briefly."""
    if path.endswith(".json"):
        try:
            schema = json.loads(text)
        except ValueError:
            return text
        fields = described_fields(schema) if isinstance(schema, dict) else {}
        if not fields:
            return text
        return "Fields:\n" + "\n".join(
            f"- `{f}`: {said}" if said else f"- `{f}`" for f, said in fields.items()
        )
    if path.endswith(".css"):
        tokens, classes = css_names(text)
        return (
            "Tokens: "
            + ", ".join(f"`{t}`" for t in tokens)
            + "\n\nClasses: "
            + ", ".join(f"`.{c}`" for c in classes)
        )
    return text


def contract(use: Use, root: Path | None) -> str:
    """What the producer provides at the release this project pins, or "" if none."""
    if not use.contract:
        return ""
    release = pinned(use, root)
    if release is None:
        return (
            f"(What {use.repo} provides couldn't be shown: this project names no release "
            f"in `{use.pin}`.)"
        )
    lines = [
        f"**What {use.repo} provides at `{release}`**, the release this project pins in "
        f"`{use.pin}`. Use only what is listed. Something it doesn't provide isn't "
        "there to use: name it as missing rather than guess or compute it."
    ]
    for path in use.contract:
        try:
            text = _file(use.repo, path, release)
        except httpx.HTTPError as exc:
            lines += ["", f"`{path}`: couldn't be read at `{release}`: {exc}."]
            continue
        lines += ["", f"`{path}`:", "", _provided(path, text)]
    return "\n".join(lines)


def unreadable(use: Use, root: Path | None) -> list[str]:
    """Why the roles couldn't be shown what `use` points to, one line per gap.

    `notes` and `contract` say so inside the prompt, where only a role reads it.
    The handover check names the same gaps before the crew takes a project over
    (crew#757).
    """
    gaps = []
    try:
        found = section(_file(use.repo, use.file), use.section)
    except httpx.HTTPError as exc:
        gaps.append(f"`{use.file}` can't be read from {use.repo}: {exc}")
    else:
        if found is None:
            gaps.append(f'{use.repo}\'s `{use.file}` has no section "{use.section}"')
    if not use.pin or not use.contract:
        gaps.append(
            f"it names no {'`pin`' if not use.pin else '`contract`'}, so the roles aren't "
            f"shown what {use.repo} provides at the release this project uses"
        )
        return gaps
    release = pinned(use, root)
    if release is None:
        return [*gaps, f"`{use.pin}` names no release"]
    for path in use.contract:
        try:
            _file(use.repo, path, release)
        except httpx.HTTPError as exc:
            gaps.append(f"`{path}` can't be read at `{release}`: {exc}")
    return gaps


def provided_fields(uses: list[Use], root: Path | None) -> frozenset[str]:
    """Every field the pinned releases' JSON schemas define, by path and by name."""
    names: set[str] = set()
    for use in uses:
        release = pinned(use, root)
        if release is None:
            continue
        for path in use.contract:
            if not path.endswith(".json"):
                continue
            try:
                schema = json.loads(_file(use.repo, path, release))
            except (httpx.HTTPError, ValueError):
                continue
            for field in schema_fields(schema) if isinstance(schema, dict) else []:
                names.add(field)
                names.add(field.rsplit(".", 1)[-1].removesuffix("[]"))
    return frozenset(names)
