"""The Goal's look on GitHub: what it's shown, and its answers as the Goal's comment (crew#858).

`gather` collects what both roles are shown: the Goal, the Sponsor's decisions for it
(`decisions.py`), the project's decision log, and its epics. Those it reads for are the
approved ones not yet in the Goal's record; the ones the record already covers, and
the delivered ones, come with them as what's settled. A later look is shown the
Goal's record and target diagram as they are.

`render` writes both answers as one marked comment on the Goal, with the answers
again as data. It is the audit trail of the look, and the settle reads its answers
from it, so a settle that waits on the Sponsor's reply settles the same answers a
tick later, not a second look's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from crew_org.columns import INBOX
from crew_org.columns import NEEDS_REFINEMENT as REFINEMENT
from crew_org.crews.goal_look_crew import (
    APPROVED,
    LOOKED_AT,
    EpicText,
    GoalContext,
    LookResult,
    ReaderLook,
    SystemLook,
    run_look,
)
from crew_org.crews.settle_crew import GoalSettlement, look_items, settle_goal
from crew_org.events import CrewEvent, EventKind, EventSink, attributed, held
from crew_org.flows import artifacts
from crew_org.flows import record as record_flow
from crew_org.flows import settle as settle_flow
from crew_org.flows.decisions import collect_decisions
from crew_org.flows.decisions import render as render_decisions
from crew_org.flows.project_log import read_log
from crew_org.llm import reraise_if_down
from crew_org.session import steps
from crew_org.tools.github_issues import IssueClient, from_sponsor

if TYPE_CHECKING:
    from crew_org.tools.github_project import Card

LOOK_MARKER = "<!-- crew:goal-look -->"
_DATA = re.compile(r"<!-- crew:goal-look-data (.*?) -->", re.S)
DIAGRAM_MARKER = "<!-- crew:goal-diagram -->"
# The diagram's source again, so a later look and the readers take it as written.
_DIAGRAM = re.compile(r"```mermaid\n(.*?)\n```", re.S)

# The Product Owner's approval footer, which is the crew's, not the epic's.
_FOOTER = re.compile(r"\n---\n\s*\nProposed by the Product Owner")


def _text(issue: dict[str, Any]) -> str:
    body = record_flow.split(issue.get("body") or "")[0]
    body = _FOOTER.split(body.strip())[0].strip()
    return f"# {issue.get('title', '')}\n\n{body}"


def diagram_comment(issues: IssueClient, repo: str, goal: int) -> dict[str, Any] | None:
    """The Goal's current target diagram comment: the latest, or None."""
    found = [c for c in issues.comments(repo, goal) if DIAGRAM_MARKER in (c.get("body") or "")]
    return found[-1] if found else None


def diagram_of(issues: IssueClient, repo: str, goal: int) -> str:
    """The Goal's target diagram's Mermaid source, or "" before its look."""
    comment = diagram_comment(issues, repo, goal)
    found = _DIAGRAM.search((comment or {}).get("body") or "")
    return found.group(1).strip() if found else ""


def gather(
    issues: IssueClient,
    repo: str,
    goal: int,
    *,
    approved: Iterable[int],
    search: Iterable[str],
) -> GoalContext:
    """What the look is shown for `goal`. `approved` is the epics it reads for, of those
    the record doesn't cover yet; `search` is where the Sponsor's decisions are looked for."""
    parent = issues.get(repo, goal)
    ref = f"{issues.owner}/{repo}"
    held = record_flow.goal_record_comment(issues, repo, goal)
    record = record_flow.comment_text(held.get("body") or "") if held else ""
    covered = set(record_flow.parse(record).looked_at) if record else set()
    wanted = set(approved)
    epics: list[EpicText] = []
    for child in issues.sub_issues(repo, goal):
        epic = f"{ref}#{child['number']}"
        if child.get("state") == "open" and epic in covered:
            epics.append(EpicText(epic, LOOKED_AT, _text(child)))
        elif child.get("state") == "open" and child["number"] in wanted:
            epics.append(EpicText(epic, APPROVED, _text(child)))
        elif child.get("state") != "open" and child.get("state_reason") == "completed":
            epics.append(EpicText(epic, "delivered", _text(child)))
    collected = collect_decisions(issues, goal_repo=repo, goal=goal, repos=search)
    return GoalContext(
        goal_ref=f"{ref}#{goal}",
        goal=f"{parent.get('title', '')}\n\n{(parent.get('body') or '').strip()}".strip(),
        decisions=render_decisions(collected, goal=f"{ref}#{goal}"),
        epics=epics,
        project_log=read_log(issues, repo),
        record=record,
        diagram=diagram_of(issues, repo, goal),
    )


def _list(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items] or ["- (none)"]


def render(result: LookResult, context: GoalContext) -> str:
    """Both answers as the Goal's comment, with the answers as data."""
    lines = [
        LOOK_MARKER,
        "## The Goal's look",
        "",
        "The Architect and the UX Designer read "
        + ", ".join(context.looking_at)
        + " before any is split. Kept as the record of the look; the Goal's record is "
        "what the Product Owner settles from it.",
        "",
    ]
    if (system := result.system) is not None:
        lines += ["### Architect", "", "**Already supported:**", ""]
        lines += _list([f"{s.what} ({s.where})" for s in system.supported])
        lines += ["", "**Decided:**", ""]
        lines += _list(
            [
                f"**{d.context}:** {d.decision} Weighed: {d.weighed} Binds: {', '.join(d.epics)}."
                for d in system.decided
            ]
        )
        lines += ["", "**Risks and seams:**", ""]
        lines += _list([f"{c.risk} ({', '.join(c.epics)})" for c in system.callouts])
        if system.beyond_the_goal:
            lines += ["", "**Beyond the Goal, for the Sponsor:**", ""]
            lines += _list([f"{b.question} {b.why}" for b in system.beyond_the_goal])
        lines.append("")
    if (reader := result.reader) is not None:
        lines += ["### UX Designer", "", f"**The reader:** {reader.reader}", ""]
        for view in reader.epics:
            lines += [
                f"- **{view.epic}:** {view.meets} Missing: {view.missing}",
                *(f"  - {c}" for c in view.criteria),
            ]
        lines.append("")
    for role, why in result.failed.items():
        lines += [f"### {role}", "", f"Did not answer: {why}", ""]
    data = {
        "looked_at": context.looking_at,
        "system": result.system.model_dump() if result.system else None,
        "reader": result.reader.model_dump() if result.reader else None,
        "failed": result.failed,
    }
    # `--` can't appear inside an HTML comment; JSON reads `-` back as `-`.
    escaped = json.dumps(data).replace("--", "\\u002d\\u002d")
    lines += ["", f"<!-- crew:goal-look-data {escaped} -->"]
    return "\n".join(lines).rstrip() + "\n"


def read_look(comment: str) -> tuple[list[str], LookResult] | None:
    """The epics a look read and its answers, from its comment, or None."""
    found = _DATA.search(comment)
    if not found:
        return None
    data = json.loads(found.group(1))
    return list(data["looked_at"]), LookResult(
        SystemLook.model_validate(data["system"]) if data.get("system") else None,
        ReaderLook.model_validate(data["reader"]) if data.get("reader") else None,
        data.get("failed") or {},
    )


# --- the step: once a Goal's epics are approved, one look and one settle (crew#858) ------------

GOAL_QUESTION_MARKER = "<!-- crew:goal-question -->"
NEEDS_HUMAN = "needs:human"
TECHNICAL = "technical"


class LookFailed(RuntimeError):
    """A role of the look didn't answer, or the settle's session stopped."""


def _ref(issues: IssueClient, repo: str, number: int) -> str:
    return f"{issues.owner}/{repo}#{number}"


def _epics_of(cards: list[Card], repo: str, goal: int) -> list[Card]:
    return [
        c
        for c in cards
        if c.parent == goal
        and (c.repo or repo) == repo
        and c.work_type == "Epic"
        and c.state != "CLOSED"
        and c.number is not None
    ]


def _record_of(issues: IssueClient, repo: str, goal: int) -> record_flow.Record | None:
    held = record_flow.goal_record_comment(issues, repo, goal)
    return record_flow.parse(record_flow.comment_text(held.get("body") or "")) if held else None


def _unsettled(issues: IssueClient, repo: str, goal: int) -> int | None:
    """Where the Goal's latest look sits among its comments, while its settle hasn't
    written it into the record; None when there's no look or it's settled.

    A look settled is one whose epics the record covers: the settle writes them last.
    """
    comments = issues.comments(repo, goal)
    looks = [i for i, c in enumerate(comments) if LOOK_MARKER in (c.get("body") or "")]
    if not looks:
        return None
    found = read_look(comments[looks[-1]].get("body") or "")
    record = _record_of(issues, repo, goal)
    covered = set(record.looked_at) if record is not None else set()
    if found is not None and set(found[0]) <= covered:
        return None
    return looks[-1]


def _refining_now(issues: IssueClient, repo: str, goal: int) -> bool:
    """The Goal is mid-look: a look posted, its settle not written yet (waiting on the
    Sponsor, or failed and to be tried again). A Goal that refined before doesn't count."""
    return _unsettled(issues, repo, goal) is not None


def pending_replies(issues: IssueClient, repo: str, goal: int) -> set[str]:
    """The ids of the Sponsor's comments on the Goal since a look its settle hasn't
    written yet: the settle reads them as its answer, so they aren't rows of their own."""
    at = _unsettled(issues, repo, goal)
    if at is None:
        return set()
    return {
        str(c.get("id"))
        for c in issues.comments(repo, goal)[at + 1 :]
        if "<!-- crew:" not in (c.get("body") or "") and from_sponsor(issues, c)
    }


class Turns:
    """Which of a repository's Goals is refining: one at a time (the Sponsor, 2026-10-22).

    A Goal refines while it has an approved epic that isn't split. Among the Goals that
    do, one mid-look (its settle not written yet) keeps its turn, even when a
    higher-priority Goal is approved meanwhile, or a Goal that refined before gets a new
    epic; otherwise the first in admission's order (Priority, the Product Owner's Rank,
    number) takes it. A Goal's epics can be proposed and approved while another
    refines. A technical epic has no Goal, so nothing here holds it.

    `split` is the epics split so far this pass, which the board doesn't show yet.
    """

    def __init__(self, issues: IssueClient, cards: list[Card], split: list[int]) -> None:
        self.issues = issues
        self.cards = cards
        self.split = split
        self._marked: dict[tuple[str, int], bool] = {}

    def _unsplit(self, repo: str, card: Card) -> bool:
        from crew_org.flows.board_flow import STORY_SPLIT_MARKER  # noqa: PLC0415

        number = card.number or 0
        if number in self.split:
            return False
        key = (repo, number)
        if key not in self._marked:
            self._marked[key] = self.issues.has_comment_marked(repo, number, STORY_SPLIT_MARKER)
        return not self._marked[key]

    def refining(self, repo: str) -> int | None:
        """The Goal whose turn it is in `repo`, or None when no Goal waits to refine."""
        from crew_org.flows.sprint import priority_key  # noqa: PLC0415

        goals = {
            c.parent
            for c in self.cards
            if c.parent is not None
            and (c.repo or repo) == repo
            and c.work_type == "Epic"
            and c.state != "CLOSED"
            and c.status == REFINEMENT
            and TECHNICAL not in c.labels
            and self._unsplit(repo, c)
        }
        if not goals:
            return None
        by_number = {c.number: c for c in self.cards if (c.repo or repo) == repo}

        def order(goal: int) -> tuple[Any, ...]:
            card = by_number.get(goal)
            return priority_key(card) if card is not None else (99, float("inf"), goal)

        begun = [g for g in goals if _refining_now(self.issues, repo, g)]
        return min(begun or goals, key=order)


def _question_and_reply(
    issues: IssueClient, repo: str, goal: int
) -> tuple[bool, str, tuple[str, ...]]:
    """(asked since the latest look, the Sponsor's reply, the ids of the comments it is).
    Only the Sponsor's words answer."""
    comments = issues.comments(repo, goal)
    looks = [i for i, c in enumerate(comments) if LOOK_MARKER in (c.get("body") or "")]
    since = comments[looks[-1] + 1 :] if looks else comments
    asked = [i for i, c in enumerate(since) if GOAL_QUESTION_MARKER in (c.get("body") or "")]
    if not asked:
        return False, "", ()
    replies = [
        c
        for c in since[asked[-1] + 1 :]
        if "<!-- crew:" not in (c.get("body") or "") and from_sponsor(issues, c)
    ]
    text = "\n\n".join(r for c in replies if (r := (c.get("body") or "").strip()))
    return True, text, tuple(str(c.get("id")) for c in replies)


def _latest_look(issues: IssueClient, repo: str, goal: int) -> tuple[list[str], LookResult] | None:
    for comment in reversed(issues.comments(repo, goal)):
        body = comment.get("body") or ""
        if LOOK_MARKER in body and (found := read_look(body)):
            return found
    return None


def look_step(
    board: Any,
    issues: IssueClient,
    sink: EventSink,
    result: Any,
    epic: Card,
    repo: str,
    *,
    cards: list[Card],
    turns: Turns,
    search: list[str],
    reading: Callable[[], Any] = lambda: None,
) -> bool:
    """True when the Goal's record covers `epic` and it may be split now.

    Safe to run every pass: a Goal with a record covering the epic isn't looked at
    again, one waiting on the Sponsor stays waiting, and an epic with a record of its
    own (split before the Goal had one) goes on from it. `reading` gives the checkout
    both roles and the settle read, made only when one runs.
    """
    number, goal = epic.number or 0, epic.parent
    if goal is None or record_flow.split(issues.get(repo, number).get("body") or "")[1]:
        return True
    ref = _ref(issues, repo, number)
    held_record = record_flow.goal_record_comment(issues, repo, goal)
    record = record_flow.comment_text(held_record.get("body") or "") if held_record else ""
    covered = set(record_flow.parse(record).looked_at) if record else set()
    # Covered first: its Goal has refined it, so a re-split waits for no other Goal.
    if ref in covered:
        return True
    siblings = _epics_of(cards, repo, goal)
    waiting = [c for c in siblings if c.status == INBOX]
    if waiting:
        named = ", ".join(_ref(issues, repo, c.number or 0) for c in waiting)
        held("skip.goal_look.waits_for_the_goals_look_still", repo=repo, card=number)
        result.skipped.append(
            (number, f"waits for the Goal's look: {named} still waits for the Sponsor")
        )
        return False
    turn = turns.refining(repo)
    if turn is not None and turn != goal:
        held("skip.goal_look.waits_for_one_goal_refines_at", repo=repo, card=number)
        result.skipped.append(
            (number, f"waits for {_ref(issues, repo, turn)}: one Goal refines at a time")
        )
        return False
    to_read = sorted(
        c.number or 0 for c in siblings if _ref(issues, repo, c.number or 0) not in covered
    )
    wanted = [_ref(issues, repo, n) for n in to_read]
    try:
        context = gather(issues, repo, goal, approved=to_read, search=search)
        # Seen before it crowds the context (crew#468).
        sink.note(
            EventKind.NOTE,
            f"#{goal} look context: {len(context.decisions):,} chars of Goal decisions, "
            f"{len(context.project_log):,} of the project's log, {len(context.epics)} epics",
            card=goal,
            decisions_chars=len(context.decisions),
            project_log_chars=len(context.project_log),
            epics=len(context.epics),
        )
        found = _latest_look(issues, repo, goal)
        if found is not None and sorted(found[0]) == sorted(wanted):
            look = found[1]
        else:
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_STARTED,
                    role="Goal look",
                    card=goal,
                    summary=f"the Goal's look on {len(wanted)} epics",
                )
            )
            look = attributed(run_look, card=goal, repo=repo)(context, reading=reading())
            if look.system is None or look.reader is None:
                raise LookFailed("; ".join(f"{r}: {why}" for r, why in look.failed.items()))
            artifacts.comment(
                issues, sink, repo=repo, number=goal, body=render(look, context), by=None
            )
        asked, reply, answered = _question_and_reply(issues, repo, goal)
        if asked and not reply:
            held("skip.goal_look.waits_for_the_sponsors_answer_on", repo=repo, card=number)
            result.skipped.append((number, "waits for the Sponsor's answer on the Goal"))
            return False
        settlement = None
        if look_items(look, context.looking_at):
            try:
                settlement = attributed(settle_goal, card=goal, repo=repo)(
                    context, look, reply=reply, reading=reading()
                )
            except steps.StepStopped as exc:
                raise LookFailed(f"the Goal's record wasn't settled: {exc}") from exc
            if settlement.conclusion is None:
                _ask(issues, sink, repo, goal, settlement.sponsor_question)
                held("skip.goal_look.waits_for_the_sponsors_answer_on", repo=repo, card=number)
                result.skipped.append((number, "waits for the Sponsor's answer on the Goal"))
                return False
        _write(
            board,
            issues,
            sink,
            repo=repo,
            goal=goal,
            context=context,
            look=look,
            settlement=settlement,
            first=not record,
            answered=answered,
        )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        result.failed.append(
            (number, f"the Goal's look couldn't settle it: {type(exc).__name__}: {exc}")
        )
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FAILED, role="Product Owner", card=goal, summary=str(exc)[:100]
            )
        )
        return False
    return True


def _ask(issues: IssueClient, sink: EventSink, repo: str, goal: int, question: str) -> None:
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=goal,
        body=f"{GOAL_QUESTION_MARKER}\n**A question for the Sponsor.** {question}\n\n"
        "Nothing the project has written down answers it. Reply here; the Goal's look is "
        "settled into its record with your answer.",
        by="Product Owner",
    )
    artifacts.label(
        issues,
        sink,
        repo=repo,
        number=goal,
        by="Product Owner",
        add=[NEEDS_HUMAN],
        rule="decision.goal_look.look_step",
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.PRODUCT_ASKED,
            role="Product Owner",
            card=goal,
            summary=f"Goal #{goal}: asks the Sponsor — {question}"[:120],
            detail={
                "repo": repo,
                "question": question,
                "step": "goal settle",
                "rule": "decision.goal_look.look_step",
            },
        )
    )


def rows_from(
    look: LookResult,
    settlement: GoalSettlement | None,
    *,
    owner: str,
    repo: str,
    today: str,
    source: str,
    looked_at: Sequence[str] = (),
) -> tuple[
    list[record_flow.Decision],
    list[record_flow.Question],
    list[record_flow.Item],
    list[record_flow.Item],
    list[bool],
]:
    """What the look and its settle add to the Goal's record, in order, with no IDs yet.

    The Architect's decisions and the reader's criteria are theirs, as they set them;
    the Product Owner's rows are its settle's. The last list says which rows are
    project-wide, for the decision log.
    """

    def c(text: str) -> str:
        return settle_flow.explicit(text, owner=owner, repo=repo)

    def binds(epics: list[str]) -> tuple[str, ...]:
        return (record_flow.ALL,) if record_flow.ALL in epics else tuple(epics)

    decisions: list[record_flow.Decision] = []
    wide: list[bool] = []
    assert look.system is not None and look.reader is not None
    for d in look.system.decided:
        decisions.append(
            record_flow.Decision(
                id="",
                context=c(d.context),
                decision=c(d.decision),
                consequences=c(d.consequences),
                source=c(f"The Architect's call in the Goal's look, {source}: weighed {d.weighed}"),
                set_by=record_flow.ARCHITECT,
                date=today,
                binds=binds(d.epics),
            )
        )
        wide.append(False)
    for view in look.reader.epics:
        if looked_at and view.epic not in looked_at:
            continue  # a delivered epic is shown as background; nothing is split from it
        for criterion in view.criteria:
            decisions.append(
                record_flow.Decision(
                    id="",
                    context="What the reader sees",
                    decision=c(criterion),
                    consequences="A story's criteria carry it",
                    source=c(f"The UX Designer, in the Goal's look, {source}"),
                    set_by=record_flow.UX_DESIGNER,
                    date=today,
                    binds=(view.epic,),
                )
            )
            wide.append(False)
    questions: list[record_flow.Question] = []
    infra: list[record_flow.Item] = []
    dismissed: list[record_flow.Item] = []
    if settlement is not None and settlement.conclusion is not None:
        conclusion = settlement.conclusion
        for r in conclusion.rows:
            decisions.append(
                record_flow.Decision(
                    id="",
                    context=c(r.context),
                    decision=c(r.decision),
                    consequences=c(r.consequences),
                    source=c(settle_flow._source(r)),
                    set_by=record_flow.PRODUCT_OWNER,
                    date=today,
                    binds=binds(r.epics),
                )
            )
            wide.append(r.project_wide)
        questions = [
            record_flow.Question(
                "", c(q.question), c(q.impact), record_flow.IMPLEMENTER, binds(q.epics)
            )
            for q in conclusion.open
        ]
        infra = [record_flow.Item("", c(f.item)) for f in conclusion.for_infra]
        dismissed = [record_flow.Item(f"N{d.note}", c(d.why)) for d in conclusion.dismissed]
    return decisions, questions, infra, dismissed, wide


def _write(
    board: Any,
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    goal: int,
    context: GoalContext,
    look: LookResult,
    settlement: GoalSettlement | None,
    first: bool,
    answered: Sequence[str] = (),
) -> None:
    """The Goal's record and its target diagram, from the look and its settle.

    `answered` is the Sponsor's comments the settle read as its answer: the record
    keeps their ids, so they're never entered again as rows of their own.
    """
    today = datetime.now(UTC).date().isoformat()
    found = _latest_look_comment(issues, repo, goal)
    source = str(found.get("html_url") or "its comment") if found else "its comment"
    decisions, questions, infra, dismissed, wide = rows_from(
        look,
        settlement,
        owner=issues.owner,
        repo=repo,
        today=today,
        source=source,
        looked_at=context.looking_at,
    )

    def change(current: record_flow.Record) -> record_flow.Record:
        current = replace(current, on_goal=True)
        logged = list(current.to_log)
        for row, project_wide in zip(decisions, wide, strict=True):
            current = current.add(row)
            if project_wide:
                logged.append(current.decisions[-1].id)
        for q in questions:
            current = replace(current, open=(*current.open, replace(q, id=current.next_id("Q"))))
        start = len(current.for_infra)
        current = replace(
            current,
            for_infra=(
                *current.for_infra,
                *(replace(i, id=f"I{start + n}") for n, i in enumerate(infra, 1)),
            ),
            dismissed=(*current.dismissed, *dismissed),
            looked_at=tuple(dict.fromkeys([*current.looked_at, *context.looking_at])),
            replies=tuple(dict.fromkeys([*current.replies, *answered])),
            to_log=tuple(logged),
        )
        return current

    why = "Settled from the Goal's look."
    if first:
        written = record_flow.start_goal_record(
            issues,
            sink,
            repo=repo,
            goal=goal,
            by=record_flow.PRODUCT_OWNER,
            record=change(record_flow.Record(on_goal=True)),
            why=why,
        )
    else:
        written = record_flow.edit(
            issues, sink, repo=repo, epic=goal, by=record_flow.PRODUCT_OWNER, change=change, why=why
        )
    assert look.system is not None
    post_diagram(issues, sink, repo=repo, goal=goal, system=look.system)
    if written.to_log:
        issues.ensure_label(
            repo,
            settle_flow.TO_LOG_LABEL,
            color="fbca04",
            description="Its record decides something the project's log doesn't hold yet",
        )
        artifacts.label(
            issues,
            sink,
            repo=repo,
            number=goal,
            by="Product Owner",
            add=[settle_flow.TO_LOG_LABEL],
            rule="decision.goal_look.look_step",
        )
    sink.emit(
        CrewEvent(
            kind=EventKind.NOTE,
            role="Product Owner",
            card=goal,
            summary=f"Goal #{goal}: the look settled into its record",
            detail={
                "repo": repo,
                "epics": context.looking_at,
                "rows": len(decisions),
                "open": len(questions),
                "later": not first,
                "rule": "decision.goal_look.look_step",
            },
        )
    )
    if (
        settlement is not None
        and settlement.conclusion is not None
        and settlement.conclusion.elsewhere
    ):
        settle_flow._points_elsewhere(
            board,
            issues,
            sink,
            context=context,
            epic=context.goal_ref,
            elsewhere=settlement.conclusion.elsewhere,
        )


def _latest_look_comment(issues: IssueClient, repo: str, goal: int) -> dict[str, Any] | None:
    found = [c for c in issues.comments(repo, goal) if LOOK_MARKER in (c.get("body") or "")]
    return found[-1] if found else None


def post_diagram(
    issues: IssueClient, sink: EventSink, *, repo: str, goal: int, system: SystemLook
) -> None:
    """The Goal's target diagram as its own comment; a change is a new version of it.

    The one before stays, marked as replaced, so what each look drew can be read back.
    """
    new = ", ".join(f"`{a}`" for a in system.new) or "none"
    styles = "\n".join(f'UpdateElementStyle({a}, $borderColor="green")' for a in system.new)
    body = "\n".join(
        [
            DIAGRAM_MARKER,
            "## The Goal's target diagram",
            "",
            "What the repository's system will be once this Goal is delivered: C4, drawn by "
            f"the Architect in the Goal's look. New in this Goal, outlined green: {new}. "
            "The repository's own diagram changes only when the work is done.",
            "",
            "```mermaid",
            system.diagram.strip() + (f"\n{styles}" if styles else ""),
            "```",
            *(
                [
                    "",
                    "When the Goal's parts talk:",
                    "",
                    "```mermaid",
                    system.sequence.strip(),
                    "```",
                ]
                if system.sequence.strip()
                else []
            ),
        ]
    )
    before = diagram_comment(issues, repo, goal)
    artifacts.comment(issues, sink, repo=repo, number=goal, body=body, by="Architect")
    if before is not None:
        posted = diagram_comment(issues, repo, goal) or {}
        url = posted.get("html_url") or "a later comment"
        issues.edit_comment(
            repo,
            int(before["id"]),
            (before.get("body") or "").replace(
                DIAGRAM_MARKER, f"<!-- crew:goal-diagram-replaced -->\n_Replaced by {url}._", 1
            ),
        )
