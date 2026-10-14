"""Releasing a data package: its dated tag, its archive, and its consumers' pull requests.

A package (crew#521) is dated information, not a software version (ADR 0021). A release is
the tag `delivery-history-<date>` on the crew's repository (`-2` for a second on
one day), with the package's archive as its asset. It carries everything up to
its date, so a consumer needs only the latest. It reaches each consumer as a
pull request that changes the one line naming the tag, `delivery-history.tag` at
the consumer's root, and nothing else. The consumer's CI fetches and validates
that release, and merging deploys.

**Nothing is released until the schema is complete, sprint-metrics' part
included** (the Sponsor, 2026-10-08). `refusal()` says why a package can't go.

**Old releases keep their tags, and only their archives are pruned,**
grandfather-father-son: the latest `KEEP_DAILY` releases and the first release
of each month keep their archives (ADR 0021). A consumer reads only the latest.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from crew_org.flows.artifacts import for_the_sponsor
from crew_org.packages import contract
from crew_org.packages import delivery_history as dh

# The one line in a consumer's repository that names the release it shows.
TAG_FILE = "delivery-history.tag"
# Who builds on the package, and the Goal there it serves: each gets a pull request
# per release (ADR 0021), on a branch naming that Goal, since a branch names its issue.
CONSUMERS = {"crew-presentation": 10}
# Releases whose archives are kept, newest first, beside the first of each month.
KEEP_DAILY = 7
# A consumer pull request's branch: one per release, so a later one supersedes it.
_BRANCH = re.compile(rf"^chore/\d+-{dh.NAME}-")

_TAG = re.compile(rf"^{dh.NAME}-(\d{{4}}-\d{{2}}-\d{{2}})(?:-(\d+))?$")


def write(
    history: dh.DeliveryHistory,
    files: dict[str, dh.PeriodEvents],
    manifest: dh.Manifest,
    directory: Path,
) -> Path:
    """Write the package into `directory`, with its schemas, and archive it beside it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")
    (directory / f"{dh.NAME}.json").write_text(history.model_dump_json(indent=1) + "\n")
    for name, period in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(period.model_dump_json(indent=1) + "\n")
    for schema, content in contract.schemas().items():
        (directory / schema).write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
    return Path(shutil.make_archive(str(directory), "gztar", directory.parent, directory.name))


def refusal(history: dh.DeliveryHistory) -> str | None:
    """Why this package can't be released, or None.

    It goes only with sprint-metrics' answer for every sprint it covers (crew#521).
    """
    part = history.sprint_metrics
    if part is None:
        return (
            "sprint-metrics' answers aren't in the package, and nothing is released "
            "without them (the Sponsor, 2026-10-08; crew#521's second criterion)"
        )
    missing = [s.name for s in history.sprints if s.start <= history.date]
    missing = [name for name in missing if name not in part.answers]
    if missing:
        return f"sprint-metrics has no answer for {', '.join(missing)}"
    return None


def tag_day(tag: str) -> tuple[date, int] | None:
    """A release tag's date and its number that day (1 for the first), or None."""
    found = _TAG.match(tag)
    if not found:
        return None
    return date.fromisoformat(found.group(1)), int(found.group(2) or 1)


def next_tag(day: date, existing: Iterable[str]) -> str:
    """The tag for a release on `day`: `-2` and on for a second on one day."""
    taken = set(existing)
    base = f"{dh.NAME}-{day.isoformat()}"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def to_prune(releases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The package's releases whose archives go: not the latest `KEEP_DAILY`, nor a
    month's first. Other releases on the repository are never touched."""
    dated = sorted(
        ((when, r) for r in releases if (when := tag_day(str(r.get("tag_name")))) is not None),
        key=lambda pair: pair[0],
    )
    keep = {id(r) for _, r in dated[-KEEP_DAILY:]}
    months: set[tuple[int, int]] = set()
    for (day, _), release in dated:
        if (day.year, day.month) not in months:
            months.add((day.year, day.month))
            keep.add(id(release))
    return [r for _, r in dated if id(r) not in keep and r.get("assets")]


def notes(manifest: dh.Manifest) -> str:
    """The release's description: what it holds, from its manifest."""
    lines = [
        f"`{dh.NAME}` up to {manifest.date.isoformat()}: {manifest.events} events, "
        f"schema {manifest.schema_version}.",
        "",
        "The archive holds the package, its manifest and the schemas it follows. How to "
        'use it: the README\'s "Data packages: how projects use them" (ADR 0021, crew#521).',
    ]
    if manifest.left_out:
        lines += ["", "Left out, never guessed:"]
        lines += [f"- {count} × {why}" for why, count in manifest.left_out.items()]
    return "\n".join(lines)


@dataclass
class Released:
    """What a release did, or would do on a dry run."""

    tag: str
    url: str | None = None
    # (consumer, pull request URL or what stopped it)
    proposed: list[tuple[str, str]] = field(default_factory=list)
    # Release tags whose archives were pruned.
    pruned: list[str] = field(default_factory=list)


def publish(
    issues: Any,
    *,
    repo: str,
    tag: str,
    archive: Path,
    manifest: dh.Manifest,
) -> dict[str, Any]:
    """Create the release on the crew's default branch, with the archive as its asset."""
    release: dict[str, Any] = issues.create_release(
        repo, tag=tag, target="main", name=tag, body=notes(manifest)
    )
    issues.upload_release_asset(release, archive)
    return release


def prune(issues: Any, repo: str) -> list[str]:
    """Delete the archives the retention doesn't keep. The releases and tags stay."""
    pruned = []
    for release in to_prune(issues.releases(repo)):
        for asset in release.get("assets") or []:
            issues.delete_release_asset(repo, asset["id"])
        pruned.append(str(release["tag_name"]))
    return pruned


def propose(ws: Any, issues: Any, *, consumer: str, tag: str, url: str) -> str:
    """Open the pull request on `consumer` that names `tag`, and close the ones it supersedes.

    It changes `TAG_FILE` and nothing else. Returns its URL, or what stopped it.
    """
    branch = f"chore/{CONSUMERS[consumer]}-{tag}"
    open_pulls = issues.open_pulls(consumer)
    if any(p["head"]["ref"] == branch for p in open_pulls):
        return f"already proposed on {branch}"
    ws = ws.for_repo(consumer)
    with ws:
        path = ws.open(branch)
        (path / TAG_FILE).write_text(tag + "\n")
        if not ws.commit(f"chore: delivery-history is {tag}"):
            return f"already names {tag}"
        ws.push(force=True)
    body = (
        f"The crew released [`{tag}`]({url}). This changes the release the site names, "
        f"and nothing else: CI fetches it and validates it against the schema it carries, "
        "and merging deploys (mqucifer/crew ADR 0021)."
    )
    base = issues.repository(consumer).get("default_branch") or "main"
    pull = issues.create_pull(
        consumer, title=f"chore: delivery-history is {tag}", head=branch, base=base, body=body
    )
    for_the_sponsor(issues, consumer, pull["number"])
    for older in open_pulls:
        if _BRANCH.match(older["head"]["ref"]):
            issues.comment(
                consumer, older["number"], f"Superseded by {pull['html_url']}: {tag} is later."
            )
            issues.close_pull(consumer, older["number"])
    return str(pull["html_url"])
