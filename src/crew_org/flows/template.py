"""The files every crew project starts with, from `mqucifer/project-template` (ADR 0030).

The same in every project, and nothing in them depends on the language: CI that
runs what the design names, the board automation, and a `.gitignore`. A new
repository is created from the template. `crew onboard` proposes them to one that
lacks them, as crew-presentation did: GitHub copies a template only once.

The crew's own files (`project.CREW_FILES`) are proposed whenever they differ, so
a project catches up when the template changes. The rest, such as `.gitignore`,
only when missing: a project adds its own lines to them.

After onboarding, each tick takes the template's later changes to every project
as a pull request there, proved by that project's own checks (crew#829). What a
project last took is recorded in the project, in `TAKEN_PATH`, so a change is
compared three ways: the template then, the template now, and the project's file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from crew_org.events import EventKind, EventSink
from crew_org.flows.artifacts import for_the_sponsor, signed
from crew_org.project import CREW_FILES, TEMPLATE_TAKEN, is_protected

TEMPLATE = "project-template"
CHECKS_WORKFLOW = "checks.yml"

# The template's README describes the template, not the project.
NOT_COPIED = frozenset({"README.md"})

# The template's commit a project last took, kept in the project (crew#829).
TAKEN_PATH = TEMPLATE_TAKEN
# The template's CI, which a project with none recorded taken may lack by its own
# choice: sprint-metrics has its own CI, and a second `checks.yml` going red there
# teaches nothing (the operator, 2026-10-24). Onboarding still proposes it.
PROJECT_CI = (".github/workflows/checks.yml", ".github/crew/")
BY = "DevOps Engineer"
# Numbered for crew#829, which takes the template's changes: the branch has no issue
# of its own, and one per template commit keeps a closed pull request from reopening.
BRANCH = "chore/829-project-template-{sha}"


def files(issues: Any, ref: str | None = None) -> dict[str, str]:
    """The template's files, by path, at `ref`: its default branch unless named."""
    ref = ref or issues.repository(TEMPLATE).get("default_branch") or "main"
    found = {}
    for path in issues.tree(TEMPLATE, ref):
        if path in NOT_COPIED:
            continue
        text = issues.file_at(TEMPLATE, path, ref)
        if text is not None:
            found[path] = text
    return found


def wanted(template: dict[str, str], merged: Path) -> dict[str, str]:
    """What onboarding proposes from `template` to a project checked out at `merged`.

    Only what would change: the pull request names each file it proposes, and
    crew-presentation's named `board.yml`, which was already the template's.
    """
    return {
        path: text
        for path, text in template.items()
        if not (merged / path).exists()
        or (
            is_protected(path, list(CREW_FILES))
            and (merged / path).read_text(encoding="utf-8") != text
        )
    }


def taken_text(commit: str) -> str:
    """`TAKEN_PATH`'s text, naming the template's commit a project has taken."""
    return (
        "# The mqucifer/project-template commit this project's template files were last\n"
        "# taken from. Each tick proposes the template's later changes (crew#829).\n"
        f"commit: {commit}\n"
    )


def taken(text: str | None) -> str | None:
    """The template commit `TAKEN_PATH`'s text names, or None for a project with none."""
    if not text:
        return None
    loaded = yaml.safe_load(text)
    commit = loaded.get("commit") if isinstance(loaded, dict) else None
    return str(commit) if commit else None


@dataclass
class Take:
    """What a project takes from the template at `head`."""

    head: str
    # By path, the template's text now, or None where the template removed the file.
    files: dict[str, str | None] = field(default_factory=dict)
    # Files the project changed that the template changed too: named, never overwritten.
    conflicts: list[str] = field(default_factory=list)


def take(issues: Any, merged: Path, recorded: str | None, head: str) -> Take:
    """What a project checked out at `merged` takes from the template at `head`.

    With a commit recorded, each file the template changed since then: taken where
    the project's file is still the template's as it was, and a conflict where the
    project changed it too. With none, what onboarding would propose, less the
    template's CI the project doesn't have.
    """
    now = files(issues, head)
    if recorded is None:
        return Take(
            head,
            {
                path: text
                for path, text in wanted(now, merged).items()
                if (merged / path).exists() or not is_protected(path, list(PROJECT_CI))
            },
        )
    then = files(issues, recorded)
    found = Take(head)
    for path in sorted(set(then) | set(now)):
        was, will = then.get(path), now.get(path)
        has = _text(merged / path)
        if was == will or has == will:
            continue
        if has == was:
            found.files[path] = will
        else:
            found.conflicts.append(path)
    return found


@dataclass
class TemplatePulls:
    # (repo, pull request URL): a pull request taking the template's changes.
    opened: list[tuple[str, str]] = field(default_factory=list)
    # (repo, why): waiting on the Sponsor or on a permission.
    held: list[tuple[str, str]] = field(default_factory=list)
    # (repo, why): something that went wrong, tried again next pass.
    failed: list[tuple[str, str]] = field(default_factory=list)


def propose_changes(issues: Any, sink: EventSink, ws: Any, *, repos: set[str]) -> TemplatePulls:
    """Each project behind the template gets one pull request with its changes (crew#829).

    The Sponsor's merge is the one approval: a template file reaches a project only
    as that project's pull request (2026-10-16).
    """
    result = TemplatePulls()
    if not repos:
        return result
    try:
        head = issues.head_sha(TEMPLATE, issues.default_branch(TEMPLATE))
    except Exception as exc:  # noqa: BLE001
        why = f"template unread: {type(exc).__name__}: {exc}"[:200]
        result.failed += [(repo, why) for repo in sorted(repos)]
        return result
    for repo in sorted(repos):
        try:
            _propose(issues, sink, ws, repo=repo, head=head, result=result)
        except Exception as exc:  # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"[:200]
            result.failed.append((repo, why))
            sink.note(
                EventKind.NOTE, f"{repo}: template changes not proposed: {why}"[:120], repo=repo
            )
    return result


@dataclass
class Planned:
    """What one project would be proposed, before anything is written."""

    base: str
    branch: str
    recorded: str | None
    # None when nothing is proposed: up to date, held, or nothing it has changed.
    found: Take | None = None
    held: str = ""


def plan(issues: Any, ws: Any, *, repo: str, head: str) -> Planned:
    """What `repo` would be proposed from the template at `head`. Reads only."""
    from crew_org.flows.delivery import workflows_not_permitted  # noqa: PLC0415

    base = issues.default_branch(repo)
    recorded = taken(issues.file_at(repo, TAKEN_PATH, base))
    planned = Planned(base, BRANCH.format(sha=head[:7]), recorded)
    if recorded == head:
        return planned
    earlier = issues.pulls_for_branch(repo, planned.branch)
    if earlier:
        pull = earlier[0]
        if pull.get("state") == "open":
            planned.held = f"template pull request open: {pull['html_url']}"
        elif not pull.get("merged_at"):
            planned.held = (
                f"template pull request closed unmerged ({pull['html_url']}): "
                "waits for the template's next change"
            )
        return planned
    found = take(issues, ws.for_repo(repo).current(), recorded, head)
    if recorded is not None and not found.files and not found.conflicts:
        # Nothing the project has changed: the next change still compares from
        # the commit it took, so moving the record alone is a pull request for nothing.
        return planned
    if why := workflows_not_permitted(sorted(found.files)):
        planned.held = f"template changes not proposed: {why}"
        return planned
    planned.found = found
    return planned


def preview(issues: Any, ws: Any, repo: str) -> str:
    """What the tick would propose to `repo` now, as text, without writing anything."""
    head = issues.head_sha(TEMPLATE, issues.default_branch(TEMPLATE))
    planned = plan(issues, ws, repo=repo, head=head)
    if planned.held:
        return f"{repo}: held: {planned.held}"
    if planned.found is None:
        return f"{repo}: nothing to propose (template at {head[:7]})"
    return f"{repo}: would open {planned.branch} against {planned.base}\n\n" + pull_body(
        planned.found, planned.recorded
    )


def _propose(
    issues: Any, sink: EventSink, ws: Any, *, repo: str, head: str, result: TemplatePulls
) -> None:
    planned = plan(issues, ws, repo=repo, head=head)
    if planned.held:
        result.held.append((repo, planned.held))
    found = planned.found
    if found is None:
        return
    branch, base, recorded = planned.branch, planned.base, planned.recorded

    ws = ws.for_repo(repo)
    with ws:
        root = ws.open(branch)
        for path, text in found.files.items():
            target = root / path
            if text is None:
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        recorded_file = root / TAKEN_PATH
        recorded_file.parent.mkdir(parents=True, exist_ok=True)
        recorded_file.write_text(taken_text(head), encoding="utf-8")
        ws.commit(
            f"chore: this project takes mqucifer/project-template at {head[:7]}\n\n"
            "Proposed by the crew's tick (crew#829)."
        )
        ws.push(force=True)
    pull = issues.create_pull(
        repo,
        title=f"chore: take mqucifer/project-template's changes up to {head[:7]}",
        head=branch,
        base=base,
        body=signed(pull_body(found, recorded), BY),
    )
    for_the_sponsor(issues, repo, pull["number"])
    result.opened.append((repo, pull["html_url"]))
    sink.note(
        EventKind.NOTE, f"{repo}: template changes proposed: {pull['html_url']}"[:120], repo=repo
    )


def pull_body(found: Take, recorded: str | None) -> str:
    """The pull request's description: what it takes, and what it leaves for a person."""
    since = (
        f"since `{recorded[:7]}`, the commit this project last took"
        if recorded
        else "that this project lacks or that differ, as onboarding would propose them, "
        "less the template's CI this project doesn't have"
    )
    compare = (
        f"https://github.com/mqucifer/{TEMPLATE}/compare/{recorded}...{found.head}"
        if recorded
        else f"https://github.com/mqucifer/{TEMPLATE}/tree/{found.head}"
    )
    lines = [
        f"Takes mqucifer/{TEMPLATE}'s files at `{found.head[:7]}` ({compare}), {since} "
        "(ADR 0030, crew#829). Your merge is the only step: this project's own checks "
        "run on it.",
        "",
    ]
    if found.files:
        lines += ["## Taken", ""]
        lines += [
            f"- `{path}`" + (" (removed from the template)" if text is None else "")
            for path, text in sorted(found.files.items())
        ]
        lines.append("")
    if found.conflicts:
        lines += [
            "## Not taken: changed in this project too",
            "",
            "Each of these files differs from the template's, as this project last took it, "
            "and the template changed it since. Nothing here overwrites it: compare it with "
            "the template's and take what applies by hand.",
            "",
        ]
        lines += [f"- `{path}`" for path in found.conflicts]
        lines.append("")
    lines.append(
        f"`{TAKEN_PATH}` records `{found.head[:7]}` as taken, so the next change to the "
        "template is compared from here."
    )
    return "\n".join(lines)


def _text(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None
