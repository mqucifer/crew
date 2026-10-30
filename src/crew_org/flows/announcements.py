"""A producer's announced new major opens an item in each project that uses it (crew#960).

A producer announces a breaking change and ships it a week later (ADR 0008 in
mqucifer/project-print). The crew's first, `delivery-history` 2.0.0 (crew#959), reached
crew-presentation only as text in its sessions' context, through the README section its
record `uses`: nothing turned it into work.

**Read from what the producer publishes,** never a list in the crew: `announced.json`
beside the contract files a project's record names, on the producer's default branch.
Each version in it says the major it replaces, the date, where its contract is and
which section of which file is its changelog. A producer that publishes none files
nothing, and the pass says so.

**One Task per project, producer and version,** the Architect's (the role that rules
whether a project can still read a change, ADR 0021), in Needs Refinement for the
Business Analyst's triage, as a finding is. It's never filed again, even once closed.
Only a project that pins the major being replaced gets one: what it pins is read from
the producer's `versions.json` at the pinned release, leaving out what was only
announced there.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from crew_org.columns import NEEDS_REFINEMENT
from crew_org.events import EventKind, EventSink
from crew_org.flows import role_tasks as rt
from crew_org.llm import reraise_if_down
from crew_org.project import RECORD_PATH, ProjectRecordError, Use, parse
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

ANNOUNCED_FILE = "announced.json"
VERSIONS_FILE = "versions.json"
RULE = "decision.announcements.announced_tasks"

# A producer's file at a ref: its text, or None when there's no such file.
Read = Callable[[str, str, str], "str | None"]


def producer_file(repo: str, path: str, ref: str = "HEAD") -> str | None:
    """Read as the usage notes are, without a token: every producer the crew uses is public."""
    from crew_org.usage import _file  # noqa: PLC0415

    try:
        return _file(repo, path, ref)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


class Unreadable(Exception):
    """What an announcement needs that couldn't be read."""


@dataclass(frozen=True)
class Item:
    """What a pass files in a project for one announced version."""

    repo: str
    key: str
    title: str
    body: str
    version: str
    # The Task already filed for it, open or closed, or None.
    filed: int | None = None


@dataclass
class Plan:
    repo: str
    items: list[Item] = field(default_factory=list)
    # Producers the project uses that publish no announcements.
    unannounced: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass
class Announced:
    opened: list[tuple[str, int]] = field(default_factory=list)
    unannounced: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


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


def pinned_version(use: Use, tag: str, read: Read) -> str:
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


def _body(
    use: Use,
    *,
    key: str,
    package: str,
    version: str,
    replaces: str,
    on: str,
    contract: str,
    log: dict[str, str],
    changelog: str,
    tag: str,
    pinned: str,
) -> str:
    where = f"https://github.com/{use.repo}/tree/HEAD/{contract}"
    return rt.render(
        rt.ARCHITECT,
        kind=rt.ANNOUNCED,
        key=key,
        why=f"{use.repo} announced `{package}` {version}: it replaces {replaces} on "
        f"**{on}**, whether this project has moved or not (ADR 0008 in "
        f"mqucifer/project-print). This project pins `{tag}` in `{use.pin}`, which "
        f"follows {pinned}.\n\n"
        f"**The new contract**, its schemas and mock: {where}\n\n"
        f"**{use.repo}'s changelog for {version}**, from its `{log['file']}`, "
        f'"{log["section"]}":\n\n{changelog}',
        work=f"Rule whether this project can still read `{package}` {version} (ADR 0021): "
        "what it reads that leaves or changes shape, against the new contract's schemas "
        f"and mock. Say what it must change to adopt {version}, or that it keeps {pinned} "
        "and loses what changed.",
        done_when=["Triaged: the Business Analyst writes what done means."],
    )


def _items(repo: str, use: Use, pin: str | None, read: Read, ref: str) -> list[Item] | None:
    """What the project files for `use`'s announcements; None when it publishes none."""
    from crew_org.usage import section  # noqa: PLC0415

    folder = contract_dir(use)
    announced = _json(read, use.repo, _at(folder, ANNOUNCED_FILE), ref)
    if announced is None:
        return None
    if not isinstance(announced, dict):
        raise Unreadable(f"{use.repo}'s `{_at(folder, ANNOUNCED_FILE)}` isn't a map of versions")
    if not pin:
        raise Unreadable(f"`{use.pin}` names no release of {use.repo}")
    pinned = pinned_version(use, pin, read)
    package = posixpath.basename(folder) or use.repo.rsplit("/", 1)[-1]
    at = _major(pinned)
    items = []
    for version, said in sorted(announced.items(), key=lambda kv: _version(kv[0])):
        try:
            replaces, on = str(said["replaces"]), str(said["on"])
            contract, log = str(said["contract"]), said["changelog"]
            replaced = _major(replaces.removesuffix(".x"))
            file, heading = str(log["file"]), str(log["section"])
        except (KeyError, TypeError, ValueError):
            raise Unreadable(f"{use.repo}'s announcement of {version} is incomplete") from None
        # Only the readers of the major it replaces have anything to move from.
        if replaced != at or _major(version) <= at:
            continue
        text = read(use.repo, file, ref)
        changelog = section(text, heading) if text is not None else None
        if changelog is None:
            raise Unreadable(f'{use.repo}\'s `{file}` has no section "{heading}" for {version}')
        key = f"{use.repo}:{package}@{version}"
        items.append(
            Item(
                repo=repo,
                key=key,
                title=f"{package} {version} replaces {replaces} on {on}",
                body=_body(
                    use,
                    key=key,
                    package=package,
                    version=version,
                    replaces=replaces,
                    on=on,
                    contract=contract,
                    log=log,
                    changelog=changelog,
                    tag=pin,
                    pinned=pinned,
                ),
                version=version,
            )
        )
    return items


def _pin(issues: Any, repo: str, use: Use, branch: str) -> str | None:
    text = issues.file_at(repo, use.pin, branch) if use.pin else None
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), None)


def plan(issues: Any, repo: str, *, read: Read = producer_file, ref: str = "HEAD") -> Plan:
    """What a pass would file in `repo` for its producers' announcements, opening nothing.

    `ref` is the producers' ref to read the announcements at: a branch shows what an
    announcement not yet merged would file.
    """
    result = Plan(repo)
    branch = issues.default_branch(repo)
    text = issues.file_at(repo, RECORD_PATH, branch)
    if not text:
        return result
    try:
        uses = parse(text).intent.scope.uses
    except (ProjectRecordError, ValueError) as exc:
        result.failed.append(f"its record can't be read: {exc}"[:200])
        return result
    filed = None
    for use in uses:
        # A producer's own announcement is its own work, not a reader's.
        if use.repo == f"{issues.owner}/{repo}":
            continue
        try:
            found = _items(repo, use, _pin(issues, repo, use, branch), read, ref)
        except (Unreadable, httpx.HTTPError) as exc:
            result.failed.append(f"{use.repo}'s announcements unread: {exc}"[:200])
            continue
        if found is None:
            result.unannounced.append(use.repo)
            continue
        if found and filed is None:
            filed = {
                m.key: int(i["number"])
                for i in issues.labelled(repo, rt.ARCHITECT.label)
                if (m := rt.marked(i.get("body"))) is not None and m.kind == rt.ANNOUNCED
            }
        result.items += [replace(item, filed=(filed or {}).get(item.key)) for item in found]
    return result


def announced_tasks(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
    read: Read = producer_file,
) -> Announced:
    """Each project gets the Architect's Task for each announced major that replaces the
    one it pins, once. A producer that publishes none is noted; one that can't be read,
    or a Task GitHub refused, is held and tried again next pass."""
    result = Announced()
    for repo in sorted(repos):
        try:
            planned = plan(issues, repo, read=read)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((repo, f"announcements unread: {exc}"[:200]))
            continue
        result.failed += [(repo, why) for why in planned.failed]
        for producer in planned.unannounced:
            result.unannounced.append((repo, producer))
            sink.note(
                EventKind.NOTE,
                f"{repo}: {producer} publishes no announcements",
                repo=repo,
                producer=producer,
                rule="decision.announcements.none_published",
            )
        for item in planned.items:
            if item.filed is not None:
                continue
            try:
                opened = rt.open_task(
                    issues,
                    board,
                    sink,
                    repo=repo,
                    role=rt.ARCHITECT,
                    kind=rt.ANNOUNCED,
                    key=item.key,
                    title=item.title,
                    body=item.body,
                    points=None,
                    cards=cards,
                    column=NEEDS_REFINEMENT,
                    by=rt.ARCHITECT.name,
                    event="producer.announced",
                    rule=RULE,
                    detail={"key": item.key, "version": item.version},
                )
            except Exception as exc:  # noqa: BLE001
                reraise_if_down(exc)
                result.failed.append(
                    (
                        repo,
                        rt.not_opened(
                            sink,
                            repo=repo,
                            role=rt.ARCHITECT,
                            kind=rt.ANNOUNCED,
                            card=None,
                            why=exc,
                        ),
                    )
                )
                continue
            if opened.new:
                result.opened.append((repo, opened.number))
    return result


def render_plan(found: Plan) -> str:
    """The dry run, as `crew announced` prints it."""
    lines: list[str] = []
    for item in found.items:
        lines += [
            f"Already filed as {found.repo}#{item.filed}: {item.title}"
            if item.filed is not None
            else f"Would file in {found.repo}: {item.title}",
            "",
            item.body,
            "",
        ]
    if not found.items:
        lines.append(f"Nothing to file in {found.repo}.")
    lines += [f"{producer} publishes no announcements." for producer in found.unannounced]
    lines += [f"Held: {why}" for why in found.failed]
    return "\n".join(lines).rstrip()
