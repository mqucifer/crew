"""Filed bugs, triaged into the flow (crew#407, ADR 0027).

A bug a collaborator files lands in Needs Refinement with no work type, and
nothing read it. Each refinement pass, the Business Analyst triages it
(`crews.bug_triage_crew`) and the outcome is carried out here:
- accepted: its criteria and estimate are written under the report, and the card
  becomes a `Bug` in Ready, admitted and built like a story with no epic; or a `Story`,
  when the report says the defect is in the project's tests (`in_its_tests`);
- not a bug, or a duplicate: commented with why, and closed;
- more information: the question is asked, and the card waits on `needs:human`.

A bug from anyone who isn't a collaborator is left for a person: ADR 0027 says
who files one. The crew's own App is no collaborator, and files a bug where a
decision says so (crew#839), so its bugs are triaged too.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.columns import NEEDS_REFINEMENT, READY
from crew_org.crews.bug_triage_crew import Triage, triage_bug
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.moves import move_card
from crew_org.llm import reraise_if_down
from crew_org.profiles import profile_for
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.work_types import BUG, STORY

BUG_LABEL = "bug"
NEEDS_HUMAN = "needs:human"
ROLE = "Business Analyst"
# Who may file one: anyone given access to the repository. Read isn't access given:
# a public repository grants it to everyone. GitHub's `author_association` was
# tried first and is wrong for this: the crew's App can't see private organization
# membership, and read Claude's account as NONE (2026-10-12).
COLLABORATORS = frozenset({"admin", "maintain", "write", "triage"})
NOT_A_COLLABORATOR = (
    "This was filed by someone who isn't a collaborator on the project, and only a "
    "collaborator files a bug (ADR 0027). Left for a person to decide."
)


def bugs_to_triage(cards: list[Card]) -> list[Card]:
    """Open bugs no one has typed yet, not waiting on a person."""
    return sorted(
        (
            c
            for c in cards
            if c.status == NEEDS_REFINEMENT
            and c.state != "CLOSED"
            and not c.work_type
            and BUG_LABEL in c.labels
            and NEEDS_HUMAN not in c.labels
        ),
        key=lambda c: (c.repo or "", c.number or 0),
    )


# Where a report says the defect is, as the crew files a bug (`role_tasks.file_findings`)
# and its operator does: a line `**Where:** ...` naming files.
_WHERE = re.compile(r"^\*\*Where:\*\*(.*)$", re.M)
_PATH = re.compile(r"`([\w./-]+\.\w+)(?:::[^`]*)?`")


def in_its_tests(report: str) -> list[str]:
    """The files a report's `**Where:**` names, when every one is a test; else none.

    A bug is fixed with a test that fails against the code as merged
    (`guard.reproduce.check`). When the defect is a test, its fix is in that test, so
    nothing can fail on main: mqucifer/sprint-metrics#868, sent back 4 times (crew#949).
    Such a report is a story's work. Only where the report says the defect is counts: a
    product bug's report names its failing test too.
    """
    named = [p for line in _WHERE.findall(report) for p in _PATH.findall(line)]
    return named if named and all(profile_for(p).is_test_path(p) for p in named) else []


def accepted_body(report: str, triage: Triage) -> str:
    """The report as filed, then its criteria and estimate in the story format (§3)."""
    lines = [report.rstrip(), "", "## Acceptance criteria", ""]
    for i, ac in enumerate(triage.acceptance_criteria, 1):
        lines += [
            f"{i}. **Given** {ac.given}",
            f"   **When** {ac.when}",
            f"   **Then** {ac.then}",
            "",
        ]
    lines += [
        f"**Estimate** — {triage.points} points",
        "",
        "---",
        "",
    ]
    tests = in_its_tests(report)
    if tests:
        lines.append(
            f"Triaged as a story by the {ROLE}: the defect is in the project's tests ("
            + ", ".join(f"`{t}`" for t in tests)
            + "), so its fix is too, and no test of it can fail against the code as merged, "
            f"as a bug's has to. {triage.reason}"
        )
    else:
        lines.append(f"Triaged as a bug by the {ROLE}: {triage.reason}")
    return "\n".join(lines)


def triage_bugs(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    cards: list[Card],
    *,
    default_repo: str,
    context: Any,
    failed: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Triage each bug waiting, and carry out its outcome. Returns (number, outcome)."""
    done: list[tuple[int, str]] = []
    for card in bugs_to_triage(cards):
        repo = card.repo or default_repo
        number = card.number or 0
        issue = issues.get(repo, number)
        author = (issue.get("user") or {}).get("login") or ""
        # The crew's own App has no permission on the repository: only org.yaml's
        # `trust.crew` names it, and none without it (crew#839).
        crew = getattr(getattr(issues, "trust", None), "crew", None) or frozenset()
        if author not in crew and issues.permission(repo, author) not in COLLABORATORS:
            artifacts.comment(
                issues, sink, repo=repo, number=number, body=NOT_A_COLLABORATOR, by=ROLE
            )
            artifacts.label(
                issues,
                sink,
                repo=repo,
                number=number,
                by=ROLE,
                add=[NEEDS_HUMAN],
                rule="decision.bugs.collaborators_only",
            )
            done.append((number, "not a collaborator"))
            continue
        report = issue.get("body") or ""
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_STARTED,
                role=ROLE,
                card=number,
                summary="triage a bug",
                # A card number alone names no repository (crew#752).
                detail={"repo": repo},
            )
        )
        try:
            shown, focus = context.focused_for(repo, f"{card.title}\n\n{report}")
            triage = triage_bug(
                card.title,
                report,
                number=number,
                repository=shown,
                reader=context.reader_for(repo, focus),
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            failed.append((number, f"{type(exc).__name__}: {exc}"))
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FAILED,
                    role=ROLE,
                    card=number,
                    summary=str(exc)[:100],
                    detail={"repo": repo},
                )
            )
            continue
        carry_out(board, issues, sink, card, repo=repo, report=report, triage=triage)
        done.append((number, triage.outcome))
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FINISHED,
                role=ROLE,
                card=number,
                summary=f"bug triaged: {triage.outcome}",
                detail={"repo": repo},
            )
        )
    return done


def carry_out(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    report: str,
    triage: Triage,
) -> None:
    """Do what the triage decided, on the issue and the board."""
    number = card.number or 0
    if triage.outcome == "accepted":
        issues.edit_issue(repo, number, body=accepted_body(report, triage))
        board.set_select(card.item_id, "Work Type", STORY if in_its_tests(report) else BUG)
        board.set_number(card.item_id, "Points", float(triage.points or 0))
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=READY,
            by=ROLE,
            card=number,
            frm=card.status,
            summary=f"bug accepted{' as a story' if in_its_tests(report) else ''}, "
            f"{triage.points} points",
            rule="decision.bugs.carry_out",
        )
    elif triage.outcome == "more_information":
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=number,
            body=f"Before this can be triaged: {triage.question}\n\n{triage.reason}",
            by=ROLE,
        )
        artifacts.label(
            issues,
            sink,
            repo=repo,
            number=number,
            by=ROLE,
            add=[NEEDS_HUMAN],
            rule="decision.bugs.carry_out",
        )
    else:
        said = (
            f"A duplicate of #{triage.duplicate_of}: {triage.reason}"
            if triage.outcome == "duplicate"
            else f"Not a bug: {triage.reason}"
        )
        artifacts.comment(issues, sink, repo=repo, number=number, body=said, by=ROLE)
        issues.close(
            repo, number, reason="duplicate" if triage.outcome == "duplicate" else "not_planned"
        )
