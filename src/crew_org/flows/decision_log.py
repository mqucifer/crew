"""The Product Owner's project-wide decisions reach the project's decision log (crew#468).

When the Product Owner settles an epic, a row that decides something for every epic
in the project (how a sprint counts a story, a standard, a versioning rule) is marked
in the conclusion and the epic is labelled. This writes those rows into the project's
`docs/decisions/` as entries, in the form the project's log already uses, so the
panel and the split of every later epic are shown them.

It's an ordinary pull request from a clone, as a design revision is (crew#192), and
the crew merges it itself: the decision is already made and recorded on the epic, and
there's nothing to build or test. One log pull request per project at a time, so two
can't number their entries alike. Safe to run every pass: an entry already in the log
names its epic and row, and isn't written twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from crew_org.events import EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows import record as record_flow
from crew_org.flows.merge import Landing, land
from crew_org.flows.project_log import DECISIONS_DIR
from crew_org.flows.settle import TO_LOG_LABEL

BY = "Product Owner"
# On the crew's log pull request, so the next pass finds it and merges it.
LOG_PR = "<!-- crew:decision-log -->"
# In each entry: the epic and row it came from, so it is never written twice.
FROM = "<!-- crew:from epic {epic} {row} -->"
_FROM = re.compile(r"<!-- crew:from epic (\d+) (R\d+) -->")
_ENTRY = re.compile(r"^(\d{4})-.+\.md$")
OWN_CALL = record_flow.OWN_CALL


@dataclass
class Row:
    epic: int
    title: str
    id: str
    context: str
    decision: str
    consequences: str
    source: str


@dataclass
class DecisionLogs:
    # (repo, pull request URL): a log pull request opened.
    opened: list[tuple[str, str]] = field(default_factory=list)
    # (repo, pull request): a log pull request the crew merged.
    merged: list[tuple[str, int]] = field(default_factory=list)
    # (repo, why): something that went wrong, tried again next pass.
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        return bool(self.opened or self.merged)


def write_logs(
    issues: Any,
    reviewer: Any,
    sink: EventSink,
    ws: Any,
    *,
    repos: set[str],
    today: date,
) -> DecisionLogs:
    result = DecisionLogs()
    for repo in sorted(repos):
        try:
            _write_log(issues, reviewer, sink, ws, repo=repo, today=today, result=result)
        except Exception as exc:  # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"[:200]
            result.failed.append((repo, why))
            sink.note(EventKind.NOTE, f"{repo}: decision log not written: {why}"[:120])
    return result


def _write_log(
    issues: Any,
    reviewer: Any,
    sink: EventSink,
    ws: Any,
    *,
    repo: str,
    today: date,
    result: DecisionLogs,
) -> None:
    waiting = next((p for p in issues.open_pulls(repo) if LOG_PR in (p.get("body") or "")), None)
    if waiting is not None and not _merge(
        issues, reviewer, sink, repo=repo, pull=waiting, result=result
    ):
        return
    labelled = [i for i in issues.labelled(repo, TO_LOG_LABEL) if "pull_request" not in i]
    if not labelled:
        return
    base = issues.repository(repo).get("default_branch") or "main"
    names = sorted(n for n in issues.list_dir(repo, DECISIONS_DIR, base) if _ENTRY.match(n))
    logged = {
        (int(epic), row)
        for name in names
        for epic, row in _FROM.findall(issues.file_at(repo, f"{DECISIONS_DIR}/{name}", base) or "")
    }
    rows: list[Row] = []
    for issue in labelled:
        epic = int(issue["number"])
        mine = to_log(epic, issue.get("title") or "", issue.get("body") or "")
        new = [r for r in mine if (epic, r.id) not in logged]
        if not new:
            # All in the log already: the label's work is done.
            artifacts.label(issues, sink, repo=repo, number=epic, by=BY, remove=[TO_LOG_LABEL])
        rows += new
    if not rows:
        return
    after = max((int(_ENTRY.match(n).group(1)) for n in names), default=0)  # type: ignore[union-attr]
    entries = [entry(after + i, row, today) for i, row in enumerate(rows, 1)]
    url = _open_pr(ws, issues, repo=repo, base=base, entries=entries, rows=rows, today=today)
    result.opened.append((repo, url))
    sink.note(EventKind.NOTE, f"{repo}: {len(rows)} decisions proposed for the project's log")


def to_log(epic: int, title: str, body: str) -> list[Row]:
    """The binding rows the epic's record marks as deciding something for every epic."""
    found = record_flow.parse(record_flow.split(body)[1])
    return [
        Row(epic, title, d.id, d.context, d.decision, d.consequences, d.source)
        for d in found.binding()
        if d.id in found.to_log
    ]


def entry(number: int, row: Row, today: date) -> tuple[str, str]:
    """An entry in the project's log: its file name and text, in the log's own form."""
    slug = "-".join(re.findall(r"[a-z0-9]+", row.context.lower())[:6]) or "decision"
    call = (
        " It's the Product Owner's own call, within the Goal (crew ADR 0018)."
        if row.source.startswith(OWN_CALL)
        else ""
    )
    text = (
        f"# {number}. {row.context}\n\n"
        f"- **Date:** {today.isoformat()}\n"
        "- **Status:** Accepted\n\n"
        "## Context\n\n"
        f"The Product Owner settled this for epic #{row.epic} ({row.title}), row {row.id}, "
        f"and it applies to every epic in the project.{call}\n\n"
        f"## Decision\n\n{row.decision}\n\n"
        f"## Consequences\n\n{row.consequences}\n\n"
        f"## Source\n\n{row.source}; epic #{row.epic}'s refinement conclusion, {row.id}.\n\n"
        f"{FROM.format(epic=row.epic, row=row.id)}\n"
    )
    return f"{number:04d}-{slug}.md", text


def _open_pr(
    ws: Any,
    issues: Any,
    *,
    repo: str,
    base: str,
    entries: list[tuple[str, str]],
    rows: list[Row],
    today: date,
) -> str:
    from crew_org.git_ops import branch_name  # noqa: PLC0415

    first = rows[0].epic
    branch = branch_name(first, f"decision log {today.isoformat()}", kind="docs")
    clone = ws.for_repo(repo)
    with clone:
        path = clone.open(branch)
        (path / DECISIONS_DIR).mkdir(parents=True, exist_ok=True)
        for name, text in entries:
            (path / DECISIONS_DIR / name).write_text(text)
        epics = sorted({r.epic for r in rows})
        clone.commit(
            "docs: the Product Owner's decisions for every epic\n\n"
            + "".join(f"Refs #{n}\n" for n in epics)
        )
        clone.push(force=True)
    listed = "\n".join(
        f"- `{name}`: {r.context} (epic #{r.epic}, {r.id})"
        for (name, _), r in zip(entries, rows, strict=True)
    )
    body = (
        f"{LOG_PR}\nDecisions the Product Owner settled that apply to every epic in this "
        "project, added to its decision log so later epics are shown them "
        f"(mqucifer/crew#468).\n\n{listed}\n\n"
        "Each is already recorded in its epic's refinement conclusion. The crew merges this "
        "itself once CI passes: there's nothing to build or test."
    )
    pull = issues.create_pull(
        repo,
        title="docs: the Product Owner's decisions for every epic",
        head=branch,
        base=base,
        body=artifacts.signed(body, BY),
    )
    return str(pull["html_url"])


def _merge(
    issues: Any,
    reviewer: Any,
    sink: EventSink,
    *,
    repo: str,
    pull: dict[str, Any],
    result: DecisionLogs,
) -> bool:
    """Approve and merge the crew's own log pull request. False while it can't yet."""
    number = pull["number"]
    if not any(r.get("state") == "APPROVED" for r in reviewer.pull_reviews(repo, number)):
        reviewer.create_review(
            repo,
            number,
            event="APPROVE",
            body=artifacts.signed(
                "Each entry is a decision already recorded in its epic's refinement "
                "conclusion; the crew merges the log once CI passes (mqucifer/crew#468).",
                "Code Reviewer",
            ),
        )
    try:
        landed = land(issues, repo, number)
    except Exception as exc:  # noqa: BLE001
        sink.note(EventKind.NOTE, f"{repo} PR #{number} not merged yet: {exc}"[:120])
        return False
    if landed.how != Landing.MERGED:
        if landed.reason:
            sink.note(EventKind.NOTE, f"{repo} PR #{number} not merged yet: {landed.reason}"[:120])
        return False
    result.merged.append((repo, number))
    sink.note(EventKind.NOTE, f"{repo}: merged the decision log, PR #{number}")
    return True
