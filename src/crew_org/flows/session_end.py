"""Where a Developer session's ending sends its story (crew#778, ADR 0032).

A session ends one of three ways (decision 3):

- **done:** the gates read the branch's diff (`gates.at_done`). It goes on to
  review, to QA when an empty diff's criteria are already proven (crew#221), back
  to a new session with every finding (P2), or to the Business Analyst's ruling
  when the story says merged tests stay unchanged and `done` changed some.
- **blocked:** a missing file or fact, or a stop (no progress, the backstop, a
  ceiling, replies that call no tool), goes back to the Business Analyst who wrote
  the story, with the session's evidence, by the path a story takes back to
  refinement. A choice no one has made goes to the Sponsor as one reaches a person
  today: the card is blocked, saying so (decision 9).
- **interrupted:** a failed call or a lost workspace, nothing about the story. It
  resumes from the last checkpoint (P3), and isn't routed here.

A return or a block is made here, through today's paths. Review, QA and a new
session aren't: delivery commits, pushes and opens the pull request first, or
starts the session, as it does today.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.columns import BLOCKED
from crew_org.events import CrewEvent, EventKind, EventSink, held
from crew_org.flows import artifacts, delivery, gates
from crew_org.flows.board_flow import STORY_PROBLEM_MARKER
from crew_org.flows.conclusion import story_rows
from crew_org.flows.moves import move_card
from crew_org.flows.story_problem import return_to_refinement
from crew_org.project import ProjectRecord
from crew_org.rulebook import says
from crew_org.rules import Rule
from crew_org.session.conversation import Ending
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.tools.workspace import CheckResult, check

REVIEW = "review"
QA = "qa"
SESSION = "session"
REFINEMENT = "refinement"
BLOCKED_FOR_A_PERSON = "blocked"

# What a session's `blocked` says is missing, as the return says it.
_MISSING = {
    "file": "a file it couldn't see",
    "fact": "a fact the story doesn't give",
}
_STOPS = {
    "no_progress": "no progress: the same first error after 3 runs, with edits between",
    "backstop": "the backstop: 2 hours of model time",
    "ceiling": "a ceiling",
    "no_tool_call": "3 replies in a row that called no tool",
}


@dataclass
class Route:
    """Where an ending sends the story, and what was written on the way."""

    to: str
    rule: str
    # On the epic for a return, on the story when it's blocked for a person.
    comment: str = ""
    # A new session's start, in place of the hints (P2).
    start: str = ""
    # For review: the pull request's sections of the merged tests the branch changes.
    sections: list[str] = field(default_factory=list)
    verdict: gates.Verdict | None = None


def route(
    ending: Ending,
    *,
    card: Card,
    repo: str,
    worktree: Path,
    story: str,
    record: ProjectRecord | None,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    run_checks: Callable[[Path], CheckResult] = check,
) -> Route:
    """Where `ending` sends the story; a return or a block is made on the way."""
    if ending.how == "interrupted":
        raise ValueError(f"an interrupted session resumes (P3), it isn't routed: {ending.why}")
    if ending.how == "blocked":
        return _blocked(ending, card=card, repo=repo, board=board, issues=issues, sink=sink)
    number = card.number or 0

    def note(text: str) -> None:
        sink.note(EventKind.NOTE, f"#{number} {text}"[:300], card=number)

    verdict = gates.at_done(
        worktree,
        ending.said,
        card=card,
        story=story,
        record=record,
        run_checks=run_checks,
        note=note,
    )
    # Which rules sent a story back, for the measures of decision 8: names only (ADR 0010).
    rules = [finding.rule for finding in verdict.findings]
    sink.emit(
        CrewEvent(
            kind=EventKind.GATE_CHECKED,
            role="Developer",
            card=number,
            summary=f"#{number} gates at done: " + (", ".join(rules) if rules else "passed"),
            detail={
                "repo": repo,
                "passed": verdict.passed,
                "rules": rules,
                "already_done": verdict.already_done,
            },
        )
    )
    if verdict.already_done:
        return Route(QA, "decision.delivery.outcome", verdict=verdict)
    if not verdict.passed:
        return Route(
            SESSION,
            verdict.findings[0].rule,
            start=rework_start(verdict.findings, ending.said),
            verdict=verdict,
        )
    if verdict.disagree:
        return _ruled(
            ending,
            verdict,
            card=card,
            repo=repo,
            story=story,
            board=board,
            issues=issues,
            sink=sink,
        )
    return _to_review(verdict)


def rework_start(findings: list[gates.Finding], said: dict[str, Any]) -> str:
    """What a new session starts with after the gates sent the branch back (P2)."""
    lines = [
        "## The gates at `done` sent your branch back",
        "",
        "The branch is as the last session left it. Answer every finding below, "
        "then call `done` again.",
        "",
    ]
    for i, finding in enumerate(findings, 1):
        lines += [f"### {i}.", "", finding.says.strip(), ""]
    return "\n".join(lines) + "\n" + last_done(said)


def last_done(said: dict[str, Any]) -> str:
    """`done`'s statement, as the next session or the pull request reads it."""
    lines = ["## The last `done`", "", (said.get("summary") or "").strip(), "", "**Criteria:**"]
    lines += [f"- {p.get('criterion')}: `{p.get('test')}`" for p in said.get("criteria") or []]
    changed = said.get("changed_tests") or []
    if changed:
        lines += ["", "**Merged tests changed:**"]
        lines += [f"- `{t.get('test')}`: {t.get('why')}" for t in changed]
    lines += ["", f"**Docs:** {said.get('docs') or ''}"]
    return "\n".join(lines) + "\n"


def _to_review(verdict: gates.Verdict) -> Route:
    return Route(
        REVIEW, "decision.delivery.outcome", sections=gates.pr_sections(verdict), verdict=verdict
    )


def _ruled(
    ending: Ending,
    verdict: gates.Verdict,
    *,
    card: Card,
    repo: str,
    story: str,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
) -> Route:
    """The Business Analyst rules on merged tests changed where the story says they stay.

    The ruling is asked what it's asked today on a merged test's failure (crew#584),
    with each test's reason from `done`: the Developer changed it, and said why.
    """
    number = card.number or 0
    changed = {
        test: f"changed by the Developer, who says: {why or 'no reason given'}"
        for test, why in verdict.disagree.items()
    }
    ruling = delivery.rule_on_contract(
        issues,
        sink,
        card=card,
        repo=repo,
        story=story,
        rows=story_rows(issues, card, repo),
        failures=changed,
    )
    if ruling is not None and ruling.decision == "amend":
        verdict.declared.update(verdict.disagree)
        verdict.disagree = {}
        return _to_review(verdict)
    if ruling is not None and ruling.decision == "repair":
        finding = gates.Finding(
            "check.gates.the_business_analyst_ruled_that",
            says(
                "check.gates.the_business_analyst_ruled_that",
                tests=", ".join(f"`{t}`" for t in sorted(changed)),
                why=ruling.why,
            ),
        )
        verdict.findings.append(finding)
        return Route(
            SESSION, finding.rule, start=rework_start([finding], ending.said), verdict=verdict
        )
    said = ruling.why if ruling is not None else "no ruling could be had."
    comment = (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{number} went back to refinement: at `done`, the Business Analyst ruled it "
        "shouldn't change the merged tests its Developer changed.**\n\n"
        f"*{card.title}*'s Existing tests line says the merged tests stay unchanged. "
        "Its session changed these, and said why:\n\n"
        + "\n".join(f"- `{test}`: {why}" for test, why in sorted(verdict.disagree.items()))
        + f"\n\n**The Business Analyst:** {said}"
        "\n\nWhen this epic is split again, each story that touches this behaviour "
        "must say whether the change is opt-in or an expected contract change that "
        "updates those tests."
    )
    return _return(
        card,
        repo=repo,
        comment=comment,
        reason="contract ruling",
        rule=Rule.RETURN_CONTRACT_RULING,
        board=board,
        issues=issues,
        sink=sink,
        verdict=verdict,
    )


def _blocked(
    ending: Ending,
    *,
    card: Card,
    repo: str,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
) -> Route:
    """A `blocked` session, or a stopped one, to the Business Analyst or the Sponsor."""
    kind = ending.said.get("kind") or ""
    if kind == "choice":
        comment = (
            "**Blocked: a choice for the Sponsor.** The Developer's session ended without "
            "the work, on a choice no one has made:\n\n"
            f"{_quoted(ending.said.get('missing') or '')}\n\n{evidence(ending)}"
        )
        return _blocked_for_a_person(
            card,
            repo=repo,
            comment=comment,
            rule=Rule.SESSION_CHOICE,
            board=board,
            issues=issues,
            sink=sink,
        )
    number = card.number or 0
    ended = _STOPS.get(ending.stop, ending.stop) if kind == "stop" else _MISSING.get(kind, kind)
    comment = (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{number} went back to refinement: its Developer session ended blocked, on "
        f"{ended}.**\n\n"
        f"*{card.title}*. What's missing, in the session's words:\n\n"
        f"{_quoted(ending.said.get('missing') or '')}\n\n{evidence(ending)}\n\n"
        "When this epic is split again, the story has to give what's missing, or say "
        "why it can't be done."
    )
    return _return(
        card,
        repo=repo,
        comment=comment,
        reason="session blocked",
        rule=Rule.RETURN_SESSION_BLOCKED,
        board=board,
        issues=issues,
        sink=sink,
        kind=kind,
        stop=ending.stop,
    )


def evidence(ending: Ending) -> str:
    """The session's evidence, for the Business Analyst or the Sponsor (decision 9)."""
    found = ending.evidence or {}
    lines: list[str] = []
    last = found.get("last_failure")
    if last:
        failing = ", ".join(f"`{t}`" for t in last.get("failing") or [])
        lines.append(
            f"**The last failure:** `run {last.get('what')}`: {last.get('first_error') or 'failed'}"
            + (f" (failing: {failing})" if failing else "")
        )
    else:
        lines.append("**The last failure:** none; its last run passed, or it never ran.")
    tried = found.get("tried") or {}
    if tried:
        read = ", ".join(f"`{p}`" for p in tried.get("files_read") or []) or "nothing"
        edited = ", ".join(f"`{p}`" for p in tried.get("files_edited") or []) or "nothing"
        used = ", ".join(f"{t} {n}" for t, n in sorted((tried.get("tools_used") or {}).items()))
        lines.append(
            f"**What it tried:** {tried.get('runs', 0)} runs. Read {read}. Edited {edited}."
            + (f" Tools: {used}." if used else "")
        )
    signals = found.get("signals") or {}
    if signals:
        lines.append(
            "**Its signals at the end:** "
            f"{signals.get('since_action', 0)} calls since an edit or run; "
            f"{signals.get('most_views', 0)} views of `{signals.get('most_viewed') or '-'}` "
            "with no edit between; "
            f"{signals.get('reasoning_tokens', 0)} reasoning tokens in its last call; "
            f"{signals.get('edits_since_run', 0)} edits since its last run; "
            f"{signals.get('empty_lookups_total', 0)} lookups that found nothing."
        )
    stop = found.get("stop")
    if stop:
        replies = stop.get("replies") if isinstance(stop, dict) else None
        if replies:
            lines.append("**Its replies:**\n\n" + "\n\n".join(_quoted(r) for r in replies))
        else:
            lines.append(f"**What stopped it:** `{json.dumps(stop, sort_keys=True)}`")
    return "\n\n".join(lines)


def _return(
    card: Card,
    *,
    repo: str,
    comment: str,
    reason: str,
    rule: str,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    verdict: gates.Verdict | None = None,
    **detail: Any,
) -> Route:
    """Back to the Business Analyst by the path a story takes back to refinement (#189).

    A story with no epic has nothing to be split again: it's blocked for a person,
    with the same evidence, as today.
    """
    if card.parent is not None and return_to_refinement(
        board,
        issues,
        sink,
        card,
        repo=repo,
        cards=board.cards(),
        comment=comment,
        reason=reason,
        **detail,
    ):
        held(rule, repo=card.repo or repo, card=card.number)
        return Route(REFINEMENT, rule, comment=comment, verdict=verdict)
    return _blocked_for_a_person(
        card,
        repo=repo,
        comment=comment.replace(STORY_PROBLEM_MARKER + "\n", ""),
        rule=Rule.SESSION_NO_EPIC,
        board=board,
        issues=issues,
        sink=sink,
    )


def _blocked_for_a_person(
    card: Card,
    *,
    repo: str,
    comment: str,
    rule: str,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
) -> Route:
    """Blocked, labelled and said why: how a decision for a person reaches them today."""
    number = card.number or 0
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=BLOCKED,
        by="Developer",
        card=number,
        frm=card.status,
        summary=comment.splitlines()[0][:80],
        kind=EventKind.CARD_BLOCKED,
        rule=rule,
    )
    artifacts.label(issues, sink, repo=repo, number=number, by="Developer", add=["blocked"])
    artifacts.comment(issues, sink, repo=repo, number=number, body=comment, by="Developer")
    return Route(BLOCKED_FOR_A_PERSON, rule, comment=comment)


def _quoted(text: str) -> str:
    return "\n".join(f"> {line}" for line in (text.strip() or "(nothing said)").splitlines())
