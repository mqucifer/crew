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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from crew_org.crews.panel_crew import PanelContext, PanelResult
from crew_org.crews.settle_crew import (
    Conclusion,
    DesignSettlement,
    Elsewhere,
    Row,
    Settlement,
    numbered,
    settle,
    settle_design,
)
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import artifacts, proposed_goal
from crew_org.flows import record as record_flow
from crew_org.flows.panel import PANEL_MARKER
from crew_org.session import steps
from crew_org.tools.github_issues import IssueClient, from_sponsor

CONCLUSION_HEADER = record_flow.HEADER
SETTLE_QUESTION_MARKER = "<!-- crew:settle-question -->"
# On the comment saying the Architect couldn't settle a design question before the split.
DESIGN_QUESTION_MARKER = "<!-- crew:design-question -->"
NEEDS_HUMAN = "needs:human"
# Rows that decide something for every epic go into the project's decision log
# too (crew#468). The conclusion names them, and the label says the log hasn't
# caught up yet; the revisit phase, which has the clone and the approving
# identity, writes them and takes the label off once they've merged.
TO_LOG_LABEL = "decision:to-log"
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


# A Goal's reference as the panel's context gives it: owner/repo#number.
_GOAL_REF = re.compile(r"([\w.-]+)#(\d+)$")


def explicit(text: str, *, owner: str, repo: str) -> str:
    """A cell for the epic's record, from any writer: references explicit and linked."""
    return _cell(text, owner=owner, repo=repo, known={repo})


def _source(row: Row) -> str:
    """A call the Product Owner made itself says so, and the Goal's intent it serves."""
    if not row.own_call:
        return row.source
    return f'{record_flow.OWN_CALL}, serving the Goal: "{row.serves}". {row.source}'


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
    """(asked, the Sponsor's reply). Only the Sponsor's words answer (crew#399).

    A question counts only after the latest panel comment: it cites that panel's
    N-numbers. An epic re-panelled while it waited (a panel from before ADR 0035's
    forms reads as none) has new numbers, so its old question is asked again.
    """
    comments = issues.comments(repo, epic)
    panelled = max(
        (i for i, c in enumerate(comments) if PANEL_MARKER in (c.get("body") or "")), default=-1
    )
    asked = [
        i
        for i, c in enumerate(comments)
        if i > panelled and SETTLE_QUESTION_MARKER in (c.get("body") or "")
    ]
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
    reading: steps.Reading | None = None,
) -> Settlement:
    """The Product Owner's settlement, from one session over the code (crew#807).

    A conclusion its checks refuse is answered in the session, each refusal recorded
    on its call's event by its rule; a session that stops fails the settle.
    """
    try:
        settlement: Settlement = attributed(settle, card=card, repo=repo)(
            context, panel, reply=reply, reading=reading
        )
    except steps.StepStopped as exc:
        raise SettleFailed(f"the conclusion wasn't given: {exc}") from exc
    return settlement


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


def _examples(panel: PanelResult) -> tuple[record_flow.Example, ...]:
    """QA's examples as the record's E rows: written as QA gave them, never settled.

    They become the stories' criteria (ADR 0035). As numbered notes, every epic with
    an example paid a full settle, and "nothing raised" never held.
    """
    return tuple(
        record_flow.Example(f"E{n}", e.rule, e.example, e.edge, e.source)
        for n, e in enumerate(panel.examples(), 1)
    )


def settle_epic(
    board: Any,
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    epic: int,
    context: PanelContext,
    panel: PanelResult,
    known: set[str] | None = None,
    reading: steps.Reading | None = None,
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
            record=record_flow.Record(nothing_raised=True, examples=_examples(panel)),
            read=issue.get("body") or "",
        )
        sink.note(
            EventKind.NOTE,
            f"epic #{epic}: the panel raised nothing",
            card=epic,
            rule="decision.settle.settle_epic",
        )
        return Settled(Outcome.WRITTEN, record_flow.render(written))

    asked, reply = _reply(issues, repo, epic)
    if asked and not reply:
        return Settled(Outcome.WAITING)

    settlement = propose(context, panel, reply, card=epic, repo=repo, reading=reading)
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
        artifacts.label(
            issues,
            sink,
            repo=repo,
            number=epic,
            by="Product Owner",
            add=[NEEDS_HUMAN],
            rule="decision.settle.settle_epic",
        )
        sink.emit(
            CrewEvent(
                kind=EventKind.PRODUCT_ASKED,
                role="Product Owner",
                card=epic,
                summary=f"epic #{epic}: asks the Sponsor — {question}"[:120],
                detail={
                    "repo": repo,
                    "question": question,
                    "step": "settle",
                    "rule": "decision.settle.settle_epic",
                },
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
    record = replace(record, examples=_examples(panel))
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
            issues,
            sink,
            repo=repo,
            number=epic,
            by="Product Owner",
            add=[TO_LOG_LABEL],
            rule="decision.settle.settle_epic",
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
    if elsewhere := settlement.conclusion.elsewhere:
        _points_elsewhere(
            board,
            issues,
            sink,
            context=context,
            epic=f"{issues.owner}/{repo}#{epic}",
            elsewhere=elsewhere,
        )
    return Settled(Outcome.WRITTEN, text)


def _points_elsewhere(
    board: Any,
    issues: IssueClient,
    sink: EventSink,
    *,
    context: PanelContext,
    epic: str,
    elsewhere: Elsewhere,
) -> None:
    """ "The Goal points elsewhere", on the epic's Goal, where its direction is the
    Sponsor's (ADR 0018). The epic's record is written and goes on regardless."""
    # Every epic the panel reads has its Goal (refine_panel's first check).
    found = _GOAL_REF.search(context.goal_ref)
    assert found is not None, context.goal_ref
    repo, goal = found.group(1), int(found.group(2))
    proposed = proposed_goal.bring(
        issues,
        board,
        sink,
        repo=repo,
        goal=goal,
        elsewhere=elsewhere,
        step="settle",
        rule="decision.settle.settle_epic",
    )
    asked = (
        f"**A question for the Sponsor about this Goal.** {elsewhere.question}"
        if elsewhere.question
        else f"**A Goal I propose:** {proposed}, a draft in `Inbox (Goals)`. Add the `goal` "
        "label to approve it, edit it first, or close it."
    )
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=goal,
        body=f"### The Goal points elsewhere\n\nFrom settling {epic}: {elsewhere.why}\n\n{asked}",
        by="Product Owner",
    )
    artifacts.label(
        issues,
        sink,
        repo=repo,
        number=goal,
        by="Product Owner",
        add=[NEEDS_HUMAN],
        rule="decision.settle.settle_epic",
    )


def propose_design(
    context: PanelContext,
    *,
    record: str,
    questions: str,
    asked: list[str],
    card: int,
    repo: str,
    reading: steps.Reading | None = None,
) -> DesignSettlement:
    """The Architect's answers, from one session over the code (crew#807)."""
    try:
        answer: DesignSettlement = attributed(settle_design, card=card, repo=repo)(
            context, record=record, questions=questions, asked=asked, reading=reading
        )
    except steps.StepStopped as exc:
        raise SettleFailed(f"the Architect's answers weren't given: {exc}") from exc
    return answer


def settle_design_questions(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    epic: int,
    context: PanelContext,
    reading: steps.Reading | None = None,
) -> Settled:
    """The Architect settles the record's design questions before the split (ADR 0018).

    Each answer becomes a binding row, set by the Architect, that replaces its
    question, so the split writes criteria against settled contracts and no
    question is open for the Architect at split time (the Definition of Ready).
    Answers still refused after the retries are for a person: the epic waits,
    labelled, until one settles them in the record or takes the label off.
    """
    issue: dict[str, Any] = issues.get(repo, epic)
    body = issue.get("body") or ""
    _, text = record_flow.split(body)
    asked = record_flow.parse(text).for_architect()
    if not asked:
        return Settled(Outcome.ALREADY)
    labels = {
        (label.get("name") if isinstance(label, dict) else str(label))
        for label in issue.get("labels") or []
    }
    # Only its own wait holds it. The label is on every epic from its proposal
    # (the approval gate) and after a settle question, until the split takes it
    # off; read alone, it kept every approved epic from reaching the split (crew#608).
    if NEEDS_HUMAN in labels and any(
        DESIGN_QUESTION_MARKER in (c.get("body") or "") for c in issues.comments(repo, epic)
    ):
        return Settled(Outcome.WAITING)
    ids, table = record_flow.architect_questions(text)
    try:
        answer = propose_design(
            context,
            record=text,
            questions=table,
            asked=ids,
            card=epic,
            repo=repo,
            reading=reading,
        )
    except SettleFailed as exc:
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=epic,
            body=f"{DESIGN_QUESTION_MARKER}\n**{', '.join(ids)} can't be settled before the "
            f"split.** {exc}\n\nSettle them in the record, as rows by the Architect or the "
            "Sponsor that replace each question, or take `needs:human` off to have the "
            "Architect try again. The epic isn't split until then.",
            by=record_flow.ARCHITECT,
        )
        artifacts.label(
            issues, sink, repo=repo, number=epic, by=record_flow.ARCHITECT, add=[NEEDS_HUMAN]
        )
        return Settled(Outcome.WAITING, str(exc))
    today = datetime.now(UTC).date().isoformat()
    questions = {q.id: q for q in asked}

    def c(text: str) -> str:
        return _cell(text, owner=issues.owner, repo=repo, known={repo})

    def change(current: record_flow.Record) -> record_flow.Record:
        for a in answer.settled:
            current = current.add(
                record_flow.Decision(
                    id="",
                    context=c(questions[a.question].question),
                    decision=c(a.decision),
                    consequences=c(a.consequences),
                    source=c(a.source),
                    set_by=record_flow.ARCHITECT,
                    date=today,
                    replaces=a.question,
                )
            )
        return current

    try:
        written = record_flow.edit(
            issues,
            sink,
            repo=repo,
            epic=epic,
            by=record_flow.ARCHITECT,
            change=change,
            expected=body,
            why="The design questions, settled before the split (ADR 0018).",
        )
    except record_flow.RecordChanged as exc:
        raise SettleFailed(str(exc)) from exc
    sink.note(
        EventKind.NOTE,
        f"epic #{epic}: the Architect settled {', '.join(ids)} before the split",
        card=epic,
        settled=len(ids),
    )
    return Settled(Outcome.WRITTEN, record_flow.render(written))
