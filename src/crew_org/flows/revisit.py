"""The Architect revisits a project's design when delivery shows it's needed (#192).

Decided 2026-09-25 by the Sponsor: "I want the crew to determine this... I
don't want to be asked if a refactor is ok or appropriate." The Sponsor's gate
is for what to build. How it's built is the Architect's, so this runs without
one:

1. Evidence. Enough of one sprint's stories rebuilt over the same file
   (`strain`) sends the Architect back to the design, with that evidence and
   the approved epics still to be split as its reason. It may change nothing.
2. Review. The Code Reviewer checks the revision against the crew-wide and
   project guidelines, as it checks every design (#144).
3. Merge. The crew opens the revision as a pull request, approves it with the
   reviewing identity, and merges it once CI passes. The record's intent
   section, the Sponsor's, is never part of it.
4. Work. Each declared change that needs work on the code becomes a technical
   epic, straight into Needs Refinement. This happens for any merged design
   pull request, a Sponsor's `crew design` as well (#154).

While a revision is open, or its technical epics are, the project's other
approved epics wait to be split, so their stories are written against the
structure the Architect chose rather than piling into the one it replaces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from crew_org.columns import NEEDS_REFINEMENT
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.board_flow import EPIC_TYPE, TECHNICAL, approved_epics
from crew_org.flows.design import (
    REVISIT_LABEL,
    REVISIT_MARKER,
    declared_changes,
    design,
    open_design_pr,
    workflows,
)
from crew_org.flows.merge import BEHIND
from crew_org.flows.moves import move_card
from crew_org.flows.strain import REVISIT_CONFLICTS, evidence, read_rebuilds, strained
from crew_org.llm import reraise_if_down
from crew_org.project import ProjectRecordError, read_record
from crew_org.tools.github_project import Card

BY = "Architect"
# Labels this creates, with what they mean, for a repository that lacks them.
LABELS = {
    REVISIT_LABEL: ("c5def5", "The Architect revisited this project's design (crew#192)"),
    TECHNICAL: ("5319e7", "Decided by the crew: how the project is built (crew#192)"),
}
# On a merged design pull request, once its changes are epics: filed once.
EPICS_MARKER = "<!-- crew:technical-epics -->"


@dataclass
class Revisits:
    """What the Architect decided this pass, and what is waiting on it."""

    # (repo, pull request URL): a revision proposed.
    proposed: list[tuple[str, str]] = field(default_factory=list)
    # (repo, issue): looked, and found nothing to change.
    unchanged: list[tuple[str, int]] = field(default_factory=list)
    # (repo, why): a revision the Code Reviewer refused twice.
    refused: list[tuple[str, str]] = field(default_factory=list)
    # (repo, pull request): a revision the crew merged.
    merged: list[tuple[str, int]] = field(default_factory=list)
    # (repo, epic): technical work filed from a merged design.
    epics: list[tuple[str, int]] = field(default_factory=list)
    # (repo, why): something that went wrong, retried next pass.
    failed: list[tuple[str, str]] = field(default_factory=list)
    # Projects whose other approved epics wait, and why.
    holds: dict[str, str] = field(default_factory=dict)

    @property
    def moved(self) -> bool:
        return bool(self.proposed or self.unchanged or self.refused or self.merged or self.epics)


def revisit_designs(
    issues: Any,
    reviewer: Any,
    board: Any,
    sink: EventSink,
    ws: Any,
    cards: list[Card],
    *,
    repos: set[str],
    events_dir: Path | None,
    propose_design: Callable[..., Any],
    review_design: Callable[..., Any],
    threshold: int = REVISIT_CONFLICTS,
) -> Revisits:
    result = Revisits()
    rebuilds = read_rebuilds(events_dir)
    for repo in sorted(repos):
        try:
            _revisit(
                issues,
                reviewer,
                board,
                sink,
                ws,
                cards,
                repo=repo,
                repos=repos,
                rebuilds=rebuilds,
                propose_design=propose_design,
                review_design=review_design,
                threshold=threshold,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            why = f"{type(exc).__name__}: {exc}"[:200]
            result.failed.append((repo, why))
            sink.note(EventKind.NOTE, f"{repo}: design revisit failed: {why}"[:120])
    return result


def _revisit(
    issues,
    reviewer,
    board,
    sink,
    ws,
    cards,
    *,
    repo,
    repos,
    rebuilds,
    propose_design,
    review_design,
    threshold,
    result,
) -> None:
    for pull in issues.closed_pulls(repo):
        if pull.get("merged_at"):
            file_technical_epics(issues, board, sink, repo=repo, pull=pull, result=result)

    open_revision = next(
        (p for p in issues.open_pulls(repo) if REVISIT_MARKER in (p.get("body") or "")), None
    )
    if open_revision is not None:
        if _merge(issues, reviewer, sink, repo=repo, pull=open_revision, result=result):
            file_technical_epics(
                issues,
                board,
                sink,
                repo=repo,
                pull=issues.pull(repo, open_revision["number"]),
                result=result,
            )
        else:
            result.holds[repo] = (
                f"the Architect's design revision, PR #{open_revision['number']}, lands first"
            )
            return

    # Filed this pass as well as on the board: `cards` was read before them,
    # and refinement runs next in this same pass.
    technical = sorted(
        {
            c.number or 0
            for c in cards
            if (c.repo or repo) == repo
            and c.work_type == EPIC_TYPE
            and c.state != "CLOSED"
            and TECHNICAL in c.labels
        }
        | {n for r, n in result.epics if r == repo}
    )
    if technical:
        result.holds[repo] = "the Architect's technical work lands first: " + ", ".join(
            f"#{n}" for n in technical
        )

    strains = strained(
        rebuilds, cards, repo, since=last_revisit(issues, repo), threshold=threshold, repos=repos
    )
    if not strains:
        return
    _revise(
        issues,
        sink,
        ws,
        cards,
        repo=repo,
        strains=strains,
        propose_design=propose_design,
        review_design=review_design,
        result=result,
    )


def last_revisit(issues: Any, repo: str) -> str:
    """When the Architect last looked at this project's design, or '' if never."""
    stamps = [i.get("created_at") or "" for i in issues.labelled(repo, REVISIT_LABEL)]
    stamps = [s for s in stamps if s]
    return max(stamps, key=_when) if stamps else ""


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _revise(
    issues, sink, ws, cards, *, repo, strains, propose_design, review_design, result
) -> None:
    from crew_org.flows.onboard import describe  # noqa: PLC0415

    clone = ws.for_repo(repo).current()
    try:
        record = read_record(clone)
    except ProjectRecordError:
        return  # not the Architect's to fix; the standup already says so (#132)
    if record is None:
        return
    waiting = [
        c for c in approved_epics(cards) if (c.repo or repo) == repo and TECHNICAL not in c.labels
    ]
    reason = (
        "Stories keep colliding in the same files, so work that should run side by "
        f"side is rebuilt instead:\n{evidence(strains)}"
    )
    if waiting:
        reason += "\n\nApproved epics waiting to be split here, which the design should serve:\n"
        reason += "\n".join(
            f"- #{c.number} {c.title}\n\n{_indent(issues.get(repo, c.number or 0).get('body'))}"
            for c in waiting
        )
    branch = issues.repository(repo)["default_branch"]
    for name, (color, description) in LABELS.items():
        issues.ensure_label(repo, name, color=color, description=description)
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_STARTED,
            role=BY,
            summary=f"{repo}: revisit the design",
            detail={"repo": repo, "evidence": evidence(strains)},
        )
    )
    designed = design(
        record,
        repository=describe(clone, branch=branch, protection=None),
        ci=workflows(clone),
        propose_design=propose_design,
        review_design=review_design,
        reason=reason,
    )

    if designed.record is None:
        why = "; ".join(designed.refused)
        _record_look(
            issues,
            sink,
            repo=repo,
            title="The Architect's design revision was refused",
            body=f"The Architect revisited the design because:\n\n{reason}\n\n"
            f"The Code Reviewer refused both proposals:\n\n"
            + "\n".join(f"- {r}" for r in designed.refused)
            + "\n\nNothing changed. New evidence starts another revisit.",
        )
        result.refused.append((repo, why))
        return

    if designed.record.design == record.design and not designed.proposal.changes:
        number = _record_look(
            issues,
            sink,
            repo=repo,
            title="The Architect revisited the design: no change",
            body=f"The Architect revisited the design because:\n\n{reason}\n\n"
            f"It found nothing to change. {designed.proposal.summary}",
        )
        result.unchanged.append((repo, number))
        return

    url = open_design_pr(ws, issues, repo, designed, base=branch, reason=reason, revisit=True)
    result.proposed.append((repo, url))
    result.holds[repo] = f"the Architect's design revision lands first: {url}"
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role=BY,
            summary=f"{repo}: design revision proposed",
            detail={"repo": repo, "url": url},
        )
    )


def _indent(text: str | None) -> str:
    return "\n".join(f"  {line}" for line in (text or "").splitlines())


def _record_look(issues, sink, *, repo: str, title: str, body: str) -> int:
    """A revisit that opened no pull request, recorded so its evidence isn't weighed again."""
    issue = issues.create(repo, title, artifacts.signed(body, BY), labels=[REVISIT_LABEL])
    issues.close(repo, issue["number"], reason="completed")
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role=BY,
            summary=f"{repo}#{issue['number']}: {title}"[:120],
            detail={"repo": repo},
        )
    )
    return issue["number"]


def _merge(issues, reviewer, sink, *, repo: str, pull: dict, result: Revisits) -> bool:
    """Approve and merge the crew's own design revision. False while it can't yet."""
    number = pull["number"]
    if not any(r.get("state") == "APPROVED" for r in reviewer.pull_reviews(repo, number)):
        reviewer.create_review(
            repo,
            number,
            event="APPROVE",
            body=artifacts.signed(
                "Checked against the crew-wide guidelines and this project's own before it "
                "was proposed. The design is the Architect's; the crew merges it once CI "
                "passes (mqucifer/crew#192).",
                "Code Reviewer",
            ),
        )
    detail = issues.pull(repo, number)
    if detail.get("mergeable_state") == BEHIND:
        issues.update_branch(repo, number, head=(detail.get("head") or {}).get("sha"))
        return False
    try:
        issues.merge_pull(repo, number)
    except Exception as exc:  # noqa: BLE001
        # Checks still running, most often. Tried again next pass.
        sink.note(EventKind.NOTE, f"{repo} PR #{number} not merged yet: {exc}"[:120])
        return False
    result.merged.append((repo, number))
    sink.note(EventKind.NOTE, f"{repo}: merged the Architect's design revision, PR #{number}")
    return True


def file_technical_epics(
    issues: Any, board: Any, sink: EventSink, *, repo: str, pull: dict, result: Revisits
) -> list[int]:
    """Turn a merged design's changes that need work into technical epics, once."""
    changes = [c for c in declared_changes(pull.get("body") or "") if c.get("needs_work")]
    if not changes or issues.has_comment_marked(repo, pull["number"], EPICS_MARKER):
        return []
    color, description = LABELS[TECHNICAL]
    issues.ensure_label(repo, TECHNICAL, color=color, description=description)
    filed: list[int] = []
    for change in changes:
        issue = issues.create(
            repo,
            str(change["what"])[:200],
            artifacts.signed(
                f"**What changes:** {change['what']}\n\n"
                f"**What the project does now:** {change.get('was') or 'nothing'}\n\n"
                f"**Why:** {change.get('why') or ''}\n\n"
                f"From the design merged in #{pull['number']}. Technical work: the crew "
                "decided it, so it goes straight to refinement rather than to the Sponsor's "
                "gate (mqucifer/crew#192).",
                BY,
            ),
            labels=[TECHNICAL],
        )
        item = board.add_issue(issue["node_id"])
        move_card(
            board,
            sink,
            item_id=item,
            to=NEEDS_REFINEMENT,
            by=BY,
            card=issue["number"],
            summary=f"technical epic — {str(change['what'])[:50]}",
        )
        board.set_select(item, "Work Type", EPIC_TYPE)
        filed.append(issue["number"])
        result.epics.append((repo, issue["number"]))
    issues.comment(
        repo,
        pull["number"],
        artifacts.signed(
            f"{EPICS_MARKER}\nTechnical epics filed from this design: "
            + ", ".join(f"#{n}" for n in filed),
            BY,
        ),
    )
    return filed
