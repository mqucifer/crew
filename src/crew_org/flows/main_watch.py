"""A workflow failing on a default branch becomes a technical epic (crew#335, phase 1).

The crew watched its pull requests and the merge queue (#326), and nothing
after that. A workflow that failed on `main` once merged, such as the release
workflow cutting 1.0.0, was seen only if a person happened to look. The
Sponsor or Claude checked by hand.

The route is the one the Architect's design changes already use: a technical
epic skips the Sponsor's gate, is split and delivered like any other, and
holds the project's product epics until it lands (#192), which is what a red
`main` should do. One per failing workflow while it's open, with the failing
step's log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crew_org.columns import NEEDS_REFINEMENT
from crew_org.events import EventKind, EventSink
from crew_org.flows.ci import report
from crew_org.flows.moves import move_card
from crew_org.flows.revisit import LABELS, TECHNICAL
from crew_org.tools.github_issues import FAILED_CONCLUSIONS

# On the epic: which workflow it's about, so it's filed once while open.
RED_MARKER = "<!-- crew:main-red workflow={workflow} -->"
EPIC_TYPE = "Epic"


@dataclass
class MainWatch:
    filed: list[tuple[str, int]] = field(default_factory=list)
    closed: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _open_red(issues: Any, repo: str) -> dict[str, dict]:
    """Open epics this watcher filed, by the workflow they're about."""
    found: dict[str, dict] = {}
    for issue in issues.labelled(repo, TECHNICAL):
        if issue.get("state") != "open":
            continue
        body = issue.get("body") or ""
        start = body.find("<!-- crew:main-red workflow=")
        if start == -1:
            continue
        workflow = body[start + len("<!-- crew:main-red workflow=") :].split(" -->", 1)[0]
        found[workflow] = issue
    return found


def watch_default_branches(
    issues: Any, board: Any, sink: EventSink, *, repos: set[str] | list[str]
) -> MainWatch:
    """File a technical epic for each workflow whose latest run on the default branch failed."""
    result = MainWatch()
    for repo in sorted(repos):
        try:
            branch = issues.repository(repo)["default_branch"]
            latest = issues.latest_runs(repo, branch)
            open_red = _open_red(issues, repo)
        except Exception as exc:  # noqa: BLE001
            result.failed.append((repo, str(exc)[:120]))
            continue
        for workflow, run in sorted(latest.items()):
            red = run.get("conclusion") in FAILED_CONCLUSIONS
            epic = open_red.get(workflow)
            if red and epic is None:
                _file(issues, board, sink, repo=repo, branch=branch, run=run, result=result)
            elif not red and epic is not None:
                _close_if_untouched(issues, sink, repo=repo, epic=epic, run=run, result=result)
    return result


def _file(issues, board, sink, *, repo, branch, run, result) -> None:
    workflow = run.get("path") or run.get("name") or "a workflow"
    name = run.get("name") or workflow
    sha = (run.get("head_sha") or "")[:7]
    try:
        failed = issues.failed_jobs(repo, run["id"])
    except Exception:  # noqa: BLE001
        failed = []
    log = report(issues, repo, failed) if failed else "_No failed job could be read._"
    number = file_technical_epic(
        issues,
        board,
        sink,
        repo=repo,
        title=f"Fix: `{name}` fails on {branch}",
        body=(
            f"{RED_MARKER.format(workflow=workflow)}\n"
            f"**The work:** make `{workflow}` pass on `{branch}` again. Its latest run on "
            f"`{branch}` ([run]({run.get('html_url', '')}), commit `{sha}`) failed after "
            "merge, where no pull request or merge-queue check saw it.\n\n"
            f"{log}\n\n"
            "Technical work: found by the crew, so it goes straight to refinement rather "
            "than to the Sponsor's gate, and holds this project's product epics until it "
            "lands (mqucifer/crew#192, #335). Closed when its stories are done, or by the "
            "watcher if the workflow passes again before any work starts."
        ),
    )
    result.filed.append((repo, number))


def file_technical_epic(issues, board, sink, *, repo: str, title: str, body: str) -> int:
    """A technical epic in Needs Refinement: the crew found the work, so no Sponsor gate (#192)."""
    color, description = LABELS[TECHNICAL]
    issues.ensure_label(repo, TECHNICAL, color=color, description=description)
    issue = issues.create(repo, title, body, labels=[TECHNICAL])
    item = board.add_issue(issue["node_id"])
    move_card(
        board,
        sink,
        item_id=item,
        to=NEEDS_REFINEMENT,
        # Code, not a model: the DevOps Engineer's duty, attributed to no role,
        # as the merge queue's moves are (constitution §20).
        by=None,
        card=issue["number"],
        summary=f"technical epic — {title}"[:100],
    )
    board.set_select(item, "Work Type", EPIC_TYPE)
    return issue["number"]


def _close_if_untouched(issues, sink, *, repo, epic, run, result) -> None:
    """Green again before any work began (a flaky run, or fixed by other work): close it.

    An epic already split is left to finish through its stories.
    """
    number = epic["number"]
    try:
        if issues.sub_issues(repo, number):
            return
    except Exception:  # noqa: BLE001
        return
    sha = (run.get("head_sha") or "")[:7]
    issues.comment(
        repo,
        number,
        f"Passes again on `{sha}` ([run]({run.get('html_url', '')})) before any work "
        "began here. Closed.",
    )
    issues.close(repo, number, reason="completed")
    sink.note(EventKind.NOTE, f"#{number} closed: `{run.get('name')}` passes again", card=number)
    result.closed.append((repo, number))
