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

Work that leaves for a rewrite, blocked, stopped or ruled against, is kept first:
delivery pushes it to a branch of its own (`Leaving.keep`), and the note links it.
The note says what happened in plain words: why the session ended, its last run,
the gates' earlier findings, what it changed, its last words when it was stopped,
and a line for the rewrite when the evidence points at the story (crew#778).
"""

from __future__ import annotations

from collections import defaultdict
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
from crew_org.session import signals
from crew_org.session.conversation import BACKSTOP_S, QUIET_LIMIT, Ending
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
# How much of a gate's finding the note quotes: its first paragraph, cut to this (crew#778).
FINDING_CHARS = 400


@dataclass
class Kept:
    """Where a delivery's work was kept as it left for a rewrite, and what it holds."""

    branch: str
    link: str
    # Each file the work changes against its base, as "`path` (how)".
    files: list[str] = field(default_factory=list)
    # Each merged test it changes, `path::name` -> "changed" or "removed".
    tests: dict[str, str] = field(default_factory=dict)
    # Its last commit holds edits made after the last green run, which no run proved.
    unproven: bool = False


class NotKept(RuntimeError):
    """The push that keeps the work failed: handled as any failed push is."""

    def __init__(self, branch: str, said: str) -> None:
        super().__init__(f"could not push `{branch}` to keep its work: {said}")
        self.branch = branch


@dataclass
class Leaving:
    """What delivery adds to a note on work that leaves it for a rewrite (crew#778)."""

    # Pushes the work to a branch of its own: None when it's no different from main.
    keep: Callable[[], Kept | None]
    # The delivery's model time and sessions, so far.
    model_s: float = 0.0
    sessions: int = 1
    # The gates' findings at each earlier `done` that they sent back.
    findings: list[list[gates.Finding]] = field(default_factory=list)
    kept: Kept | None = None

    def take(self) -> Kept | None:
        """Keep the work, for the note's link and for delivery if the route fails after."""
        self.kept = self.keep()
        return self.kept


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
    leaving: Leaving | None = None,
) -> Route:
    """Where `ending` sends the story; a return or a block is made on the way.

    `leaving` keeps the work before a return or a block is written, and adds the
    delivery's side to the note.
    """
    if ending.how == "interrupted":
        raise ValueError(f"an interrupted session resumes (P3), it isn't routed: {ending.why}")
    if ending.how == "blocked":
        return _blocked(
            ending,
            card=card,
            repo=repo,
            story=story,
            leaving=leaving,
            board=board,
            issues=issues,
            sink=sink,
        )
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
            leaving=leaving,
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
    leaving: Leaving | None,
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
    kept = leaving.take() if leaving is not None else None
    comment = (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{number} went back to refinement: at `done`, the Business Analyst ruled it "
        "shouldn't change the merged tests its Developer changed.**\n\n"
        f"*{card.title}*'s Existing tests line says the merged tests stay unchanged. "
        "Its session changed these, and said why:\n\n"
        + "\n".join(f"- `{test}`: {why}" for test, why in sorted(verdict.disagree.items()))
        + f"\n\n**The Business Analyst:** {said}\n\n"
        + evidence(ending, leaving, kept, story)
        + "\n\nWhen this epic is split again, each story that touches this behaviour "
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
    story: str,
    leaving: Leaving | None,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
) -> Route:
    """A `blocked` session, or a stopped one, to the Business Analyst or the Sponsor."""
    kept = leaving.take() if leaving is not None else None
    kind = ending.said.get("kind") or ""
    if kind == "choice":
        comment = (
            "**Blocked: a choice for the Sponsor.** The Developer's session ended without "
            "the work, on a choice no one has made:\n\n"
            f"{_quoted(ending.said.get('missing') or '')}\n\n"
            f"{evidence(ending, leaving, kept, story)}"
        )
        return blocked_for_a_person(
            card,
            repo=repo,
            comment=comment,
            rule=Rule.SESSION_CHOICE,
            board=board,
            issues=issues,
            sink=sink,
        )
    return _return(
        card,
        repo=repo,
        comment=returned_note(ending, card=card, story=story, leaving=leaving, kept=kept),
        reason="session blocked",
        rule=Rule.RETURN_SESSION_BLOCKED,
        board=board,
        issues=issues,
        sink=sink,
        kind=kind,
        stop=ending.stop,
    )


def returned_note(
    ending: Ending,
    *,
    card: Card,
    story: str,
    leaving: Leaving | None = None,
    kept: Kept | None = None,
) -> str:
    """What the epic is told when a `blocked` or stopped session sends its story back.

    A stop says why it stopped, with its number, and the line for the rewrite only
    when the evidence points at the story. The session's own `blocked` keeps its words.
    """
    number = card.number or 0
    found = evidence(ending, leaving, kept, story)
    kind = ending.said.get("kind") or ""
    if kind != "stop":
        return (
            f"{STORY_PROBLEM_MARKER}\n"
            f"**#{number} went back to refinement: its Developer session ended blocked, on "
            f"{_MISSING.get(kind, kind)}.**\n\n"
            f"*{card.title}*. What's missing, in the session's words:\n\n"
            f"{_quoted(ending.said.get('missing') or '')}\n\n{found}\n\n"
            "When this epic is split again, the story has to give what's missing, or say "
            "why it can't be done."
        )
    rewrite = for_the_rewrite(ending, kept, story)
    return (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{number} went back to refinement: its Developer session was stopped by "
        f"{stopped_by(ending, leaving)}.**\n\n"
        f"*{card.title}*.\n\n{found}" + (f"\n\n{rewrite}" if rewrite else "")
    )


def stopped_by(ending: Ending, leaving: Leaving | None = None) -> str:
    """What stopped a session, in words, with its number."""
    seen = (ending.evidence or {}).get("stop") or {}
    if ending.stop == "backstop":
        minutes = round(leaving.model_s / 60) if leaving else seen.get("model_minutes")
        sessions = leaving.sessions if leaving else 1
        return (
            f"the backstop: {minutes} minutes of model time over "
            f"{_count(sessions, 'session')}, the delivery's {BACKSTOP_S // 3600} hours"
        )
    if ending.stop == "ceiling" and seen.get("ceiling") == "re_viewing":
        return (
            f"a ceiling: {seen.get('views')} views of `{seen.get('file')}` with no edit to it "
            f"between, at a limit of {signals.VIEWS_OF_ONE_FILE}"
        )
    if ending.stop == "ceiling":
        return (
            f"a ceiling: {seen.get('calls')} model calls in a row with no edit and no run, "
            f"at a limit of {signals.READING_CALLS}"
        )
    if ending.stop == "no_progress":
        return (
            f"no progress: the same first error in `{seen.get('test') or 'its run'}` after "
            "3 runs, with edits between"
        )
    if ending.stop == "no_tool_call":
        return f"{QUIET_LIMIT} replies in a row that called no tool"
    return ending.stop or "a stop"


def for_the_rewrite(ending: Ending, kept: Kept | None, story: str) -> str:
    """One line for the rewrite, when the evidence points at the story; else nothing."""
    said: list[str] = []
    if ending.stop == "no_progress":
        seen = (ending.evidence or {}).get("stop") or {}
        said.append(
            f"`{seen.get('test') or 'Its test'}` failed the same way through 3 runs with edits "
            "between: check its criterion can be met as written."
        )
    if ending.stop == "ceiling":
        said.append(
            "It read without acting until a ceiling stopped it: check the story says where "
            "the change goes and which test proves it."
        )
    unnamed = gates.unnamed_by_story(sorted(kept.tests), story) if kept else []
    if unnamed:
        said.append(
            f"It changed {_count(len(unnamed), 'merged test')} the Existing tests line doesn't "
            "name: say whether this behaviour is opt-in, or a contract change that names them."
        )
    return f"**For the rewrite:** {' '.join(said)}" if said else ""


def evidence(
    ending: Ending,
    leaving: Leaving | None = None,
    kept: Kept | None = None,
    story: str = "",
) -> str:
    """The session's evidence, for the Business Analyst or the Sponsor (decision 9).

    Its last run, the gates' earlier findings, what it changed, its last words when
    it was stopped, and where its work is kept: each in words, never as data.
    """
    found = ending.evidence or {}
    lines = [_last_run(found.get("last_run"))]
    rounds = leaving.findings if leaving is not None else []
    if any(rounds):
        listed = [
            f"- At `done` {i}: {_first_paragraph(finding.says)}"
            for i, findings in enumerate(rounds, 1)
            for finding in findings
        ]
        lines.append("**The gates' findings at its earlier `done`s:**\n\n" + "\n".join(listed))
    if kept is not None:
        lines.append(
            f"**What it changed:** {_count(len(kept.files), 'file')}"
            + (": " + ", ".join(kept.files) if kept.files else "")
            + "."
        )
        lines.append(_merged_tests(kept.tests, story))
    words = found.get("last_words") or ""
    if ending.said.get("kind") == "stop" and words:
        lines.append(f"**Its last words:**\n\n{_quoted(words)}")
    if kept is not None:
        lines.append(
            f"**Its work is kept** on [`{kept.branch}`]({kept.link}), with no pull request."
            + (
                " Its last commit holds the edits made after its last green run, which no "
                "run proved."
                if kept.unproven
                else ""
            )
        )
    elif leaving is not None:
        lines.append("**Nothing was kept:** its branch was no different from main.")
    return "\n\n".join(lines)


def _last_run(run: dict[str, Any] | None) -> str:
    if not run:
        return "**Its last run:** none; it never ran its checks."
    if run.get("ok"):
        return f"**Its last run:** `run {run.get('what')}`, green."
    failing = ", ".join(f"`{t}`" for t in run.get("failing") or [])
    return (
        f"**Its last run:** `run {run.get('what')}` failed: "
        f"{run.get('first_error') or 'no first error was read'}"
        + (f" (failing: {failing})" if failing else "")
        + "."
    )


def _merged_tests(tests: dict[str, str], story: str) -> str:
    """The merged tests the work changes, counted and named by file, and those the story
    doesn't name called out. Never a `path::name` line, so the epic's record doesn't pin
    them as broken (`story_problem.record_pinned`)."""
    if not tests:
        return "**Merged tests it changed:** none."
    unnamed = set(gates.unnamed_by_story(sorted(tests), story))
    named = sorted(set(tests) - unnamed)
    said = f"**Merged tests it changed:** {len(tests)}."
    if named:
        said += f" The story's Existing tests line names {len(named)}: " + _by_file(
            {t: tests[t] for t in named}
        )
    if unnamed:
        said += (
            f"\n\n**{_count(len(unnamed), 'merged test')} the story doesn't name**, beyond its "
            "scope:\n\n" + _by_file({t: tests[t] for t in unnamed}, listed=True)
        )
    return said


def _by_file(tests: dict[str, str], *, listed: bool = False) -> str:
    """Tests grouped by file: `path`: `name`, `name` (removed)."""
    files: dict[str, list[str]] = defaultdict(list)
    for key, how in sorted(tests.items()):
        path, _, name = key.partition("::")
        files[path].append(
            f"`{name}`" + (" (removed)" if how == "removed" else "") if name else "the file"
        )
    groups = [f"`{path}`: {', '.join(names)}" for path, names in files.items()]
    return "\n".join(f"- {g}" for g in groups) if listed else "; ".join(groups) + "."


def _first_paragraph(text: str) -> str:
    first = text.strip().split("\n\n", 1)[0].replace("\n", " ")
    return first if len(first) <= FINDING_CHARS else first[: FINDING_CHARS - 1].rstrip() + "…"


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}" + ("" if n == 1 else "s")


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
    return blocked_for_a_person(
        card,
        repo=repo,
        comment=comment.replace(STORY_PROBLEM_MARKER + "\n", ""),
        rule=Rule.SESSION_NO_EPIC,
        board=board,
        issues=issues,
        sink=sink,
    )


def blocked_for_a_person(
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
