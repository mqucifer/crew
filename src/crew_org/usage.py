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

A major the producer has announced to replace the pinned one is shown beside it
(crew#969): what differs from the pinned fields, in the same text, and the announced
schemas and mock as files in the session's checkout, since a session has no network.
On mqucifer/crew-presentation#211 the Business Analyst searched the 1.x package for
`top_failure_causes`, because the 2.0.0 shape was nowhere it could read, and only the
mock shows it: the schema types it as array, object or null. The mock is 84 KB and few
sessions need it, so it's never in the text.

Read without a token: every producer the crew uses is public. A private one
would need the crew's token here.
"""

from __future__ import annotations

import json
import posixpath
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx

from crew_org.git_ops import exclude_folder

if TYPE_CHECKING:
    from crew_org.project import Use

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{file}"
TIMEOUT = 15

ANNOUNCED_FILE = "announced.json"
VERSIONS_FILE = "versions.json"
# Where a session's checkout holds an announced major's files: never committed, since
# the clone's `info/exclude` names it, so a test that stays carries its own fixture.
ANNOUNCED_DIR = ".announced"

# A producer's file at a ref: its text, or None when there's no such file.
Read = Callable[[str, str, str], "str | None"]

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


class Unreadable(Exception):
    """What an announcement needs that couldn't be read."""


def producer_file(repo: str, path: str, ref: str = "HEAD") -> str | None:
    """A producer's file, or None when it has no such file."""
    try:
        return _file(repo, path, ref)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


def _major(version: str) -> int:
    return int(version.split(".", 1)[0])


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _json(read: Read, repo: str, path: str, ref: str) -> Any:
    text = read(repo, path, ref)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise Unreadable(f"{repo}'s `{path}` at `{ref}` isn't JSON") from None


def contract_dir(use: Use) -> str:
    """Where the producer keeps the contract the record names: its files' shared folder."""
    folders = [posixpath.dirname(p) for p in use.contract]
    return posixpath.commonpath(folders) if folders else ""


def _at(folder: str, name: str) -> str:
    return posixpath.join(folder, name) if folder else name


def pinned_version(use: Use, tag: str, read: Read = producer_file) -> str:
    """The version the project's pinned release follows: the latest its `versions.json`
    records that wasn't only announced there."""
    folder = contract_dir(use)
    versions = _json(read, use.repo, _at(folder, VERSIONS_FILE), tag)
    if not isinstance(versions, dict) or not versions:
        raise Unreadable(f"{use.repo} records no versions at `{tag}`")
    announced = _json(read, use.repo, _at(folder, ANNOUNCED_FILE), tag) or {}
    following = [v for v in versions if v not in announced]
    if not following:
        raise Unreadable(f"{use.repo} records only announced versions at `{tag}`")
    return str(max(following, key=_version))


@dataclass(frozen=True)
class Announcement:
    """One announced version that replaces the major a project pins."""

    package: str
    version: str
    replaces: str
    on: str
    # Its folder at the producer, and its files there beside the record's contract.
    contract: str
    changelog_file: str
    changelog_section: str
    mock: tuple[str, ...]
    # The version the project's pinned release follows.
    pinned: str


def _inside(path: str) -> str:
    """A path a producer names under a folder, refused if it could lead out of it: it's
    written on the host (`place_announced`)."""
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(path)
    return path


def announcing(
    use: Use, tag: str | None, read: Read = producer_file, ref: str = "HEAD"
) -> list[Announcement] | None:
    """What the producer announces to replace the major at `tag`; None when it publishes
    no announcements. `ref` is the producer's ref to read them at."""
    folder = contract_dir(use)
    announced = _json(read, use.repo, _at(folder, ANNOUNCED_FILE), ref)
    if announced is None:
        return None
    if not isinstance(announced, dict):
        raise Unreadable(f"{use.repo}'s `{_at(folder, ANNOUNCED_FILE)}` isn't a map of versions")
    if not tag:
        raise Unreadable(f"`{use.pin}` names no release of {use.repo}")
    pinned = pinned_version(use, tag, read)
    package = posixpath.basename(folder) or use.repo.rsplit("/", 1)[-1]
    at = _major(pinned)
    found = []
    for version, said in announced.items():
        try:
            replaced = _major(str(said["replaces"]).removesuffix(".x"))
            _version(version)
            log = said["changelog"]
            announcement = Announcement(
                package=package,
                version=version,
                replaces=str(said["replaces"]),
                on=str(said["on"]),
                contract=str(said["contract"]),
                changelog_file=str(log["file"]),
                changelog_section=str(log["section"]),
                # A producer that names no mock still has its fields shown.
                mock=tuple(_inside(str(path)) for path in said.get("mock") or ()),
                pinned=pinned,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Unreadable(f"{use.repo}'s announcement of {version} is incomplete") from None
        # Only the readers of the major it replaces have anything to move from.
        if replaced == at and _major(version) > at:
            found.append(announcement)
    return sorted(found, key=lambda a: _version(a.version))


def announced_folder(use: Use, found: Announcement) -> str:
    """Where a session's checkout holds `found`'s files."""
    name = f"{found.package}-{found.version}"
    return posixpath.join(ANNOUNCED_DIR, use.repo.replace("/", "-"), name)


def contract_files(use: Use) -> list[str]:
    """The record's contract files, as named under their shared folder."""
    folder = contract_dir(use)
    return [posixpath.relpath(path, folder) if folder else path for path in use.contract]


def _announced_at(use: Use, found: Announcement, name: str) -> str | None:
    return producer_file(use.repo, _at(found.contract, name), "HEAD")


def _differs(use: Use, found: Announcement, name: str, release: str) -> str:
    """One contract file at the announced major, beside what the pinned release provides:
    only what leaves, comes or changes what its schema says. The rest is listed above."""
    text = _announced_at(use, found, name)
    if text is None:
        return f"`{name}`: {use.repo} announces none."
    before = producer_file(use.repo, _at(contract_dir(use), name), release)
    if before == text:
        return f"`{name}`: as above."
    if not name.endswith(".json") or before is None:
        return f"`{name}`:\n\n{_provided(name, text)}"
    try:
        old, new = described_fields(json.loads(before)), described_fields(json.loads(text))
    except (ValueError, AttributeError):
        return f"`{name}`:\n\n{_provided(name, text)}"
    left = [f for f in old if f not in new]
    came = {f: said for f, said in new.items() if f not in old or old[f] != said}
    if not left and not came:
        return f"`{name}`: as above."
    lines = [f"`{name}`:"]
    if left:
        lines += ["", "Leaves: " + ", ".join(f"`{f}`" for f in left)]
    if came:
        lines += ["", "New, or said differently:"]
        lines += [f"- `{f}`: {said}" if said else f"- `{f}`" for f, said in came.items()]
    return "\n".join(lines)


def _where(use: Use, found: Announcement, root: Path) -> str:
    """How a session reaches the announced files, the mock among them."""
    folder = announced_folder(use, found)
    if not (root / folder).is_dir():
        return (
            f"Its schemas and mock are at https://github.com/{use.repo}/tree/HEAD/"
            f"{found.contract}, which a session has no network to read."
        )
    mock = "".join(f", `{path}`" for path in found.mock)
    return (
        f"Its schemas{' and mock' if found.mock else ''} are files at `{folder}/` in this "
        f"checkout, to view and search, and for a test to read with no network: "
        f"{', '.join(f'`{n}`' for n in contract_files(use))}{mock}. A field's shape can change "
        "with its name the same, and only the mock shows that. The folder is never committed "
        "and isn't in CI, so a test that stays carries what it uses as its own fixture."
    )


def _headline(found: Announcement) -> str:
    return f"`{found.package}` {found.version} replaces {found.replaces} on {found.on}"


def announced(use: Use, root: Path | None) -> str:
    """What's announced to replace the major this project pins, or "" if nothing is."""
    release = pinned(use, root)
    if root is None or release is None or not use.contract:
        return ""
    try:
        return "\n\n".join(
            "\n\n".join(
                [
                    f"**What {use.repo} has announced: {_headline(found)}**, whether this "
                    "project has moved or not. The list above stays what it pins until then; "
                    "this is what changes.",
                    *(_differs(use, found, n, release) for n in contract_files(use)),
                    _where(use, found, root),
                ]
            )
            for found in announcing(use, release) or []
        )
    except (Unreadable, httpx.HTTPError) as exc:
        return f"(What {use.repo} has announced couldn't be read: {exc}.)"


def announced_summary(use: Use, root: Path | None) -> list[str]:
    """One line per announced major, for a start that keeps the fields in a note."""
    release = pinned(use, root)
    if root is None or release is None or not use.contract:
        return []
    try:
        found = announcing(use, release) or []
    except (Unreadable, httpx.HTTPError):
        return []
    return [
        f"{_headline(a)}: its schemas and mock are at `{announced_folder(use, a)}/`, never "
        "committed; a test that stays carries what it uses as its own fixture."
        if (root / announced_folder(use, a)).is_dir()
        else f"{_headline(a)}."
        for a in found
    ]


def place_announced(uses: list[Use], root: Path, ref: str = "HEAD") -> list[str]:
    """Put each announced major's schemas and mock in a checkout, for a session with no
    network to read and its tests to build against. Why any couldn't be, one line each:
    the session goes on, told where they are instead."""
    gaps = []
    for use in uses:
        release = pinned(use, root)
        if release is None or not use.contract:
            continue
        try:
            for found in announcing(use, release, ref=ref) or []:
                _place(use, found, root, ref)
        except (Unreadable, httpx.HTTPError, OSError, subprocess.CalledProcessError) as exc:
            gaps.append(f"{use.repo}'s announced files weren't placed: {exc}"[:200])
    return gaps


def _place(use: Use, found: Announcement, root: Path, ref: str) -> None:
    """All of its files or none: read first, so a mock half there can't mislead a test."""
    files = {}
    for name in [*contract_files(use), *found.mock]:
        text = producer_file(use.repo, _at(found.contract, name), ref)
        if text is None:
            raise Unreadable(f"`{_at(found.contract, name)}` isn't at `{ref}`")
        files[name] = text
    target = root / announced_folder(use, found)
    exclude_folder(root, ANNOUNCED_DIR)
    if target.exists():
        shutil.rmtree(target)
    for name, text in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


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
    """Every field the pinned releases' JSON schemas define, by path and by name, and the
    majors announced to replace them: an adoption epic's stories name those (crew#969)."""
    names: set[str] = set()

    def add(schema: Any) -> None:
        for field in schema_fields(schema) if isinstance(schema, dict) else []:
            names.add(field)
            names.add(field.rsplit(".", 1)[-1].removesuffix("[]"))

    for use in uses:
        release = pinned(use, root)
        if release is None:
            continue
        for path in use.contract:
            if not path.endswith(".json"):
                continue
            try:
                add(json.loads(_file(use.repo, path, release)))
            except (httpx.HTTPError, ValueError):
                continue
        try:
            for found in announcing(use, release) or []:
                for name in contract_files(use):
                    text = _announced_at(use, found, name) if name.endswith(".json") else None
                    add(json.loads(text) if text else None)
        except (Unreadable, httpx.HTTPError, ValueError):
            continue
    return frozenset(names)
