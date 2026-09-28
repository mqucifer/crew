"""A red CI check reaches the Developer with its log (#325).

The crew's own checks run in a sandbox with no network: lint and tests. What
only CI can run, such as an image build or a release workflow, was proven
nowhere the crew could see. The Code Reviewer was shown a check's name and
its outcome; the merge queue removing a pull request for a failed check was
handled like a conflict and rebuilt from `main`, with no word on what failed.
A Docker story would retry blind until it blocked.

So a failed check returns the card the way a review's requested changes do:
the failing job's log tail on the pull request, marked with the head it
judged, which delivery reads as a verdict and as the sign a rework is due.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.columns import IN_PROGRESS, MERGING
from crew_org.events import EventSink
from crew_org.flows.moves import move_card
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

# On the pull request, naming the head that failed: delivery re-works a card
# whose current head carries it, and a new commit clears it.
CI_MARKER = "<!-- crew:ci head={head} -->"
CI_MARKER_PREFIX = "<!-- crew:ci head="
# Generous, not a budget (§16): the end of a log is where the failure is.
LOG_TAIL_CHARS = 6_000
MAX_JOBS = 3

_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?", re.MULTILINE)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_JOB = re.compile(r"/job/(\d+)")


def log_tail(text: str) -> str:
    """The failing step of a job log: from its header to the error, cleaned.

    A job log ends with post-job cleanup, so its last lines are rarely the
    failure. GitHub marks the failure `##[error]`, and the step that ran it
    starts at the `##[group]` before. Without a marker, the log's end.
    """
    lines = _ANSI.sub("", _STAMP.sub("", text)).strip().splitlines()
    error = next((i for i, line in enumerate(lines) if line.startswith("##[error]")), None)
    if error is not None:
        start = max(
            (i for i, line in enumerate(lines[:error]) if line.startswith("##[group]")),
            default=0,
        )
        errors = [line for line in lines[error:] if line.startswith("##[error]")]
        lines = lines[start:error] + errors
    clean = "\n".join(lines).strip()
    if len(clean) <= LOG_TAIL_CHARS:
        return clean
    cut = clean[-LOG_TAIL_CHARS:]
    return "…\n" + cut.split("\n", 1)[-1]


def job_id(run: dict[str, Any]) -> int | None:
    """The Actions job behind a check run or a job record."""
    found = _JOB.search(run.get("details_url") or run.get("html_url") or "")
    if found:
        return int(found.group(1))
    return run.get("id")


def report(issues: IssueClient, repo: str, failed: list[dict[str, Any]]) -> str:
    """Each failed job by name, with the tail of its log."""
    parts: list[str] = []
    for run in failed[:MAX_JOBS]:
        name = run.get("name") or "a check"
        outcome = run.get("conclusion") or "failed"
        ident = job_id(run)
        try:
            log = issues.job_log(repo, ident) if ident is not None else ""
        except Exception:  # noqa: BLE001
            log = ""
        parts.append(f"### `{name}`: {outcome}")
        if log:
            parts += ["", "```", log_tail(log), "```"]
        else:
            parts += [
                "",
                "_Its log couldn't be read: it has expired, or the crew's GitHub App "
                "lacks Actions read access._",
            ]
        parts.append("")
    if len(failed) > MAX_JOBS:
        rest = ", ".join(f"`{r.get('name')}`" for r in failed[MAX_JOBS:])
        parts.append(f"Also failed, not shown: {rest}.")
    return "\n".join(parts).strip()


def return_for_ci(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    pull: dict,
    failed: list[dict[str, Any]],
    where: str,
) -> None:
    """Send an approved card back to the Developer with what CI said."""
    number = card.number or 0
    head = (pull.get("head") or {}).get("sha", "")
    marker = CI_MARKER.format(head=head)
    names = ", ".join(f"`{r.get('name')}`" for r in failed[:MAX_JOBS])
    try:
        already = any(
            marker in (c.get("body") or "") for c in issues.comments(repo, pull["number"])
        )
    except Exception:  # noqa: BLE001
        already = False
    if not already:
        issues.comment(
            repo,
            pull["number"],
            f"{marker}\n**CI failed {where}: returned to the Developer.** {names} did not pass. "
            "The crew's own checks run lint and tests in a sandbox; this is what only CI "
            f"runs, and its log is below.\n\n{report(issues, repo, failed)}",
        )
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=IN_PROGRESS,
        by=None,
        card=number,
        frm=MERGING,
        summary=f"PR #{pull['number']}: {names} failed {where}, returned with the log"[:120],
    )


def latest_ci_verdict(issues: IssueClient, repo: str, pull: int) -> str:
    """The last CI failure reported on a pull request, for the next delivery."""
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, pull)]
    except Exception:  # noqa: BLE001
        return ""
    found = [b for b in bodies if CI_MARKER_PREFIX in b]
    return found[-1] if found else ""
