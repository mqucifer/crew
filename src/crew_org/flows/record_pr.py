"""A change to a project's record, proposed to the project as a pull request.

Every section of `.crew/project.yaml` changes this way, whoever owns it: the
Sponsor's intent from `crew onboard` (#130), the Architect's design from
`crew design` (#144), and later the crew's learned facts (#112). The project
reviews its own record, and nothing writes to its default branch.

Run again, the same change updates the same branch and pull request rather
than opening a second: the issue it closes is found by title, and the branch
is named for that issue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from crew_org.flows.artifacts import signed
from crew_org.project import RECORD_PATH, ProjectRecord, render


@dataclass(frozen=True)
class RecordChange:
    """What one kind of record change says about itself."""

    issue_title: str
    issue_body: str
    branch_summary: str
    commit_message: str  # the issue reference is added
    pr_title: str
    # Who is updating it, for the comment on a pull request that already exists.
    updated_by: str
    # The role that wrote the change, signed on the pull request and its comments.
    by: str
    # Labels on the issue the pull request closes.
    labels: tuple[str, ...] = ()


def propose(
    ws: Any,
    issues: Any,
    repo: str,
    record: ProjectRecord,
    change: RecordChange,
    *,
    base: str,
    body: Callable[[int], str],
    update_note: str = "",
) -> str:
    """Propose `record` as the project's record. Returns the pull request's URL.

    `body` writes the pull request's description, given the issue it closes.
    `update_note` is what's new, commented on a pull request that already exists.
    """
    from crew_org.git_ops import branch_name  # noqa: PLC0415

    found = next((i for i in issues.open_issues(repo) if i["title"] == change.issue_title), None)
    number = (
        found
        or issues.create(repo, change.issue_title, change.issue_body, labels=list(change.labels))
    )["number"]
    branch = branch_name(number, change.branch_summary, kind="chore")

    ws = ws.for_repo(repo)
    with ws:
        path = ws.open(branch)
        target = path / RECORD_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(record))
        ws.commit(f"{change.commit_message}\n\nRefs #{number}")
        ws.push(force=True)

    existing = next((p for p in issues.open_pulls(repo) if p["head"]["ref"] == branch), None)
    if existing:
        if update_note:
            issues.comment(
                repo,
                existing["number"],
                signed(f"Updated by {change.updated_by}.{update_note}", change.by),
            )
        return existing["html_url"]
    pull = issues.create_pull(
        repo, title=change.pr_title, head=branch, base=base, body=signed(body(number), change.by)
    )
    return pull["html_url"]
