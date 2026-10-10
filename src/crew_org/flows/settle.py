"""The settle step: the panel's notes become the epic's conclusion, in its body (crew#440).

The conclusion goes under its own header at the end of the epic's body, below
the text the Sponsor approved, which is never touched. Nobody has to read ten
comments to learn what was decided, and the split, the criteria check and
every later step read this table instead of the discussion.

When the Product Owner can't answer a note from what the project has written
down, it asks the Sponsor one question, as `product_step` does for a story
problem, and the epic waits. The Sponsor's reply comment is what the next pass
reads; the conclusion is written then.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import ValidationError

from crew_org.crews.panel_crew import PanelContext, PanelResult
from crew_org.crews.settle_crew import (
    Conclusion,
    Row,
    Settlement,
    Unsettled,
    check_covers,
    check_grounded,
    check_not_cut,
    check_product_calls,
    numbered,
    settle,
)
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import artifacts
from crew_org.flows import record as record_flow
from crew_org.llm import reraise_if_down
from crew_org.tools.github_issues import IssueClient, from_sponsor

CONCLUSION_HEADER = record_flow.HEADER
SETTLE_QUESTION_MARKER = "<!-- crew:settle-question -->"
NEEDS_HUMAN = "needs:human"
# Rows that decide something for every epic go into the project's decision log
# too (crew#468). The conclusion names them, and the label says the log hasn't
# caught up yet; the revisit phase, which has the clone and the approving
# identity, writes them and takes the label off once they've merged.
TO_LOG_LABEL = "decision:to-log"
# A conclusion the schema or the coverage check refuses is asked for again, told why.
ATTEMPTS = 3

_BARE = re.compile(r"(?<![\w/#])#(\d+)\b")


class Outcome(Enum):
    WRITTEN = "written"
    WAITING = "waiting for the Sponsor"
    ALREADY = "already settled"


class SettleFailed(RuntimeError):
    """The Product Owner's conclusion was refused every time, or the body changed under it."""


@dataclass(frozen=True)
class Settled:
    outcome: Outcome
    text: str = ""


def has_conclusion(body: str) -> bool:
    return record_flow.has_record(body)


def _cell(text: str, *, owner: str, repo: str, known: set[str]) -> str:
    """One table cell: references explicit and linked, and nothing that breaks the table.

    A bare number means this epic's repository: the members copy the epics'
    own bare numbers, and a conclusion is written into that repository
    (crew#456).
    """
    text = " ".join(text.split()).replace("|", "\\|")
    text = _BARE.sub(lambda m: f"{owner}/{repo}#{m.group(1)}", text)
    # `repo#n` for a repository the crew knows, qualified with the owner.
    word = "|".join(sorted((re.escape(k) for k in known), key=len, reverse=True))
    if word:
        text = re.sub(
            rf"(?<![\w/.\[-])({word})#(\d+)\b", lambda m: f"{owner}/{m.group(1)}#{m.group(2)}", text
        )
    return re.sub(
        r"(?<![\w/.\[-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)#(\d+)\b",
        lambda m: (
            f"[{m.group(0)}](https://github.com/{m.group(1)}/{m.group(2)}/issues/{m.group(3)})"
        ),
        text,
    )


def _source(row: Row) -> str:
    """A call the Product Owner made itself says so, and the Goal's words it stays within."""
    if not row.own_call:
        return row.source
    return f'{record_flow.OWN_CALL}, within the Goal: "{row.goal_wording}". {row.source}'


def from_conclusion(
    conclusion: Conclusion,
    *,
    owner: str,
    repo: str,
    today: str,
    known: set[str] | None = None,
) -> record_flow.Record:
    """The epic's first record, from the settle. IDs, status and authorship are the code's."""

    def c(text: str) -> str:
        return _cell(text, owner=owner, repo=repo, known={repo, *(known or ())})

    decisions = tuple(
        record_flow.Decision(
            id=f"R{i}",
            context=c(r.context),
            decision=c(r.decision),
            consequences=c(r.consequences),
            source=c(_source(r)),
            set_by=record_flow.PRODUCT_OWNER,
            date=today,
        )
        for i, r in enumerate(conclusion.rows, 1)
    )
    return record_flow.Record(
        decisions=decisions,
        open=tuple(
            record_flow.Question(
                f"Q{i}",
                c(q.question),
                c(q.impact),
                record_flow.IMPLEMENTER if q.settled_by == "implementer" else record_flow.ARCHITECT,
            )
            for i, q in enumerate(conclusion.open, 1)
        ),
        for_infra=tuple(
            record_flow.Item(f"I{i}", c(f.item)) for i, f in enumerate(conclusion.for_infra, 1)
        ),
        dismissed=tuple(record_flow.Item(f"N{d.note}", c(d.why)) for d in conclusion.dismissed),
        to_log=tuple(f"R{i}" for i, r in enumerate(conclusion.rows, 1) if r.project_wide),
    )


def render(
    conclusion: Conclusion,
    *,
    owner: str,
    repo: str,
    known: set[str] | None = None,
    today: str | None = None,
) -> str:
    """The conclusion as the epic's body carries it: the epic's first record."""
    day = today or datetime.now(UTC).date().isoformat()
    return record_flow.render(
        from_conclusion(conclusion, owner=owner, repo=repo, today=day, known=known)
    )


def _reply(issues: IssueClient, repo: str, epic: int) -> tuple[bool, str]:
    """(asked, the Sponsor's reply). Only the Sponsor's words answer (crew#399)."""
    comments = issues.comments(repo, epic)
    asked = [i for i, c in enumerate(comments) if SETTLE_QUESTION_MARKER in (c.get("body") or "")]
    if not asked:
        return False, ""
    replies = [
        (c.get("body") or "").strip()
        for c in comments[asked[-1] + 1 :]
        if "<!-- crew:" not in (c.get("body") or "") and from_sponsor(issues, c)
    ]
    return True, "\n\n".join(r for r in replies if r)


def propose(
    context: PanelContext,
    panel: PanelResult,
    reply: str,
    *,
    card: int,
    repo: str,
    sink: EventSink | None = None,
) -> Settlement:
    """The Product Owner's settlement, asked for again if it is refused.

    Each refusal is written to the event log with its reason: a retry costs minutes,
    and without the reason nobody can tell what the model keeps getting wrong.
    """
    count = len(numbered(panel))
    feedback = ""
    for attempt in range(ATTEMPTS):
        try:
            settlement: Settlement = attributed(settle, card=card, repo=repo, attempt=attempt + 1)(
                context, panel, reply=reply, feedback=feedback
            )
            check_not_cut(settlement)
            check_grounded(settlement, context.goal)
            check_product_calls(settlement, panel)
            check_covers(settlement, count)
            return settlement
        except (ValidationError, Unsettled, ValueError) as exc:
            reraise_if_down(exc)
            feedback = str(exc)[:600]
            if sink is not None:
                sink.note(
                    EventKind.NOTE,
                    f"#{card} conclusion refused, attempt {attempt + 1} of {ATTEMPTS}: "
                    f"{feedback[:160]}",
                    card=card,
                    attempt=attempt + 1,
                    reason=feedback,
                )
    raise SettleFailed(f"the conclusion was refused {ATTEMPTS} times: {feedback}")


def _write(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    epic: int,
    record: record_flow.Record,
    read: str,
) -> record_flow.Record:
    """The epic's first record, written as any edit of it is (ADR 0023).

    `read` is the body before the model was asked: the approved text is the
    Sponsor's, and an edit made while the model was thinking is not overwritten.
    """
    try:
        return record_flow.edit(
            issues,
            sink,
            repo=repo,
            epic=epic,
            by=record_flow.PRODUCT_OWNER,
            change=lambda _: record,
            expected=read,
            why="Settled from the panel's notes.",
        )
    except record_flow.RecordChanged as exc:
        raise SettleFailed(str(exc)) from exc


def settle_epic(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    epic: int,
    context: PanelContext,
    panel: PanelResult,
    known: set[str] | None = None,
) -> Settled:
    """Settle an epic's panel notes into its body, or ask the Sponsor one question.

    Safe to run every tick: an epic with a conclusion is left alone, and one
    waiting on a reply stays waiting until the Sponsor has written it.
    """
    issue: dict[str, Any] = issues.get(repo, epic)
    if has_conclusion(issue.get("body") or ""):
        return Settled(Outcome.ALREADY)

    if not numbered(panel):
        # Nothing was raised, so there is nothing to settle and no model call. The
        # conclusion still goes in: it is what marks the epic as settled.
        written = _write(
            issues,
            sink,
            repo=repo,
            epic=epic,
            record=record_flow.Record(nothing_raised=True),
            read=issue.get("body") or "",
        )
        sink.note(EventKind.NOTE, f"epic #{epic}: the panel raised nothing", card=epic)
        return Settled(Outcome.WRITTEN, record_flow.render(written))

    asked, reply = _reply(issues, repo, epic)
    if asked and not reply:
        return Settled(Outcome.WAITING)

    settlement = propose(context, panel, reply, card=epic, repo=repo, sink=sink)
    if settlement.conclusion is None:
        question = settlement.sponsor_question
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=epic,
            body=f"{SETTLE_QUESTION_MARKER}\n**A question for the Sponsor.** {question}\n\n"
            "Nothing the project has written down answers it. Reply here; the panel's notes "
            "are settled with your answer.",
            by="Product Owner",
        )
        artifacts.label(issues, sink, repo=repo, number=epic, by="Product Owner", add=[NEEDS_HUMAN])
        sink.emit(
            CrewEvent(
                kind=EventKind.PRODUCT_ASKED,
                role="Product Owner",
                card=epic,
                summary=f"epic #{epic}: asks the Sponsor — {question}"[:120],
                detail={"repo": repo, "question": question, "step": "settle"},
            )
        )
        return Settled(Outcome.WAITING, question)

    record = from_conclusion(
        settlement.conclusion,
        owner=issues.owner,
        repo=repo,
        today=datetime.now(UTC).date().isoformat(),
        known=known,
    )
    text = record_flow.render(
        _write(issues, sink, repo=repo, epic=epic, record=record, read=issue.get("body") or "")
    )
    if any(r.project_wide for r in settlement.conclusion.rows):
        issues.ensure_label(
            repo,
            TO_LOG_LABEL,
            color="fbca04",
            description="Its conclusion decides something the project's log doesn't hold yet",
        )
        artifacts.label(
            issues, sink, repo=repo, number=epic, by="Product Owner", add=[TO_LOG_LABEL]
        )
    sink.emit(
        CrewEvent(
            kind=EventKind.NOTE,
            role="Product Owner",
            card=epic,
            summary=f"epic #{epic}: panel settled into its conclusion",
            detail={
                "repo": repo,
                "rows": len(settlement.conclusion.rows),
                "open": len(settlement.conclusion.open),
                "dismissed": len(settlement.conclusion.dismissed),
                "after_reply": bool(reply),
            },
        )
    )
    return Settled(Outcome.WRITTEN, text)
