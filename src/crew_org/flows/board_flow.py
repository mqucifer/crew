"""The reconciliation pass — one tick of the board.

A tick reads the board, decides what is actionable, and acts. It runs to
quiescence rather than one transition at a time: the Sponsor controls when the
process runs, not the hops within it.

Phase 1 is deliberately read-only. The crew proposes epics as issue comments
and moves nothing, so its decomposition can be judged before it is trusted with
the board.

One exception, and it is not a judgement the crew is making: a card carrying
the `goal` label with no Work Type is typed `Goal` before selection. Work Type
is a project field that nothing filing an issue can set, so this records what a
person already said in the only place they could say it. It changes no column
and decides nothing.

This is plain reconciliation code, not a CrewAI Flow. A Flow earns its place
when routing genuinely branches by card state; for a single linear pass it
would add ceremony that obscures what is happening. It arrives with Phase 2.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass, field

from crew_org.columns import BLOCKED, INBOX, READY
from crew_org.columns import NEEDS_REFINEMENT as REFINEMENT
from crew_org.config import load_org
from crew_org.crews.refinement_crew import (
    Epic,
    EpicProposal,
    Story,
    StoryProposal,
    propose_epics,
    split_epic,
)
from crew_org.design import DesignPolicy, EpicShape
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.moves import move_card
from crew_org.git_ops import Workspace
from crew_org.llm import reraise_if_down
from crew_org.process import ProcessRules
from crew_org.project import ProjectRecordError, brief, read_record
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient, within
from crew_org.tools.repo_context import repository_context

# Marks a comment as the crew's, so a repeated tick recognises its own work.
# Ticks are reconciliation passes and run repeatedly; without this a goal would
# accrue one identical proposal per tick.
EPIC_PROPOSAL_MARKER = "<!-- crew:epic-proposal -->"

STORY_SPLIT_MARKER = "<!-- crew:story-split -->"

# A split that failed, and a split the crew has stopped attempting. Both are
# comments because the comment history *is* the record (§10) — and a failed
# split used to write nothing at all on the card, so there was nothing for a
# later pass to read and nothing for a person to find.
SPLIT_FAILURE_MARKER = "<!-- crew:split-failed"
SPLIT_PARKED_MARKER = "<!-- crew:split-parked -->"

# How many times the same failure is tolerated before the epic is parked. A
# generation that fails identically will not succeed on the next identical
# attempt: epic sprint-metrics#49 failed the same way six times in one tick —
# reasoning_tokens 16,386, text_tokens 0, finish=length — and was retried each
# pass because nothing recorded that it had already failed. Distinct failures
# still retry; it is the identical repeat that is pointless.
IDENTICAL_FAILURES_BEFORE_PARKING = 2

GOAL_TYPE = "Goal"
EPIC_TYPE = "Epic"
STORY_TYPE = "Story"
# The label a person can actually apply when filing a Goal. Work Type is a
# project field, and nothing that creates an issue can set one — not the issue
# template's front matter, not `gh issue create`, not the new-issue form. A
# label is the only signal available at the moment a Goal is written, so the
# board reconciles the field from it rather than asking the Sponsor to
# remember a second step in a second place.
GOAL_LABEL = "goal"
NEEDS_HUMAN = "needs:human"
# The Sponsor's only verb beyond approve and reject. Without it a rejection
# teaches the crew nothing: the same goal decomposed again produces the same
# epics, because nothing about the rejection is an input to anything.
NEEDS_REWORK = "needs:rework"
NEEDS_DESIGN = "needs:design"
# A story held in refinement only because Ready was full. The label is what
# tells it apart from a story that genuinely needs refining — one filed by
# hand, or returned by escalation — so the pass that lets it in when Ready
# drains touches nothing else.
HELD_FOR_ROOM = "held:wip"


@dataclass
class TickResult:
    """What a tick actually did. The standup is written from this."""

    considered: int = 0
    proposed: list[int] = field(default_factory=list)
    epics_created: list[int] = field(default_factory=list)
    epics_refined: list[int] = field(default_factory=list)
    stories_created: list[int] = field(default_factory=list)
    design_required: list[int] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    # Epics the crew has stopped trying to split. Its own list because a
    # failure that will be retried and one that will not are different news:
    # the first is noise in a run, the second is work for a person.
    parked: list[int] = field(default_factory=list)
    # Stories held for room: let into Ready this pass, and still waiting.
    admitted: list[int] = field(default_factory=list)
    waiting: list[tuple[int, str]] = field(default_factory=list)

    @property
    def quiescent(self) -> bool:
        return not self.proposed


def render_epic_body(epic: Epic, goal_number: int, goal_title: str) -> str:
    """An epic issue, written for the Sponsor deciding whether to approve it."""
    return "\n".join(
        [
            f"**Outcome** — {epic.outcome}",
            "",
            f"**Why** — {epic.rationale}",
            "",
            f"**Stands alone because** — {epic.separately_deliverable}",
            "",
            "---",
            "",
            f"Proposed by the Product Owner from #{goal_number} *({goal_title})*.",
            "",
            "**Awaiting Sponsor approval.** Approve by moving this card out of "
            "`Inbox (Goals)` into `Needs Refinement`. Close it to reject.",
        ]
    )


def render_proposal(
    goal_title: str, proposal: EpicProposal, epic_numbers: dict[str, int] | None = None
) -> str:
    """Format a proposal for a human reader who was not present.

    The Sponsor reads this on the card and either approves the epics or does
    not, so it states conclusions and the reasoning behind the ordering — it
    does not narrate the crew's deliberation.
    """
    lines = [
        EPIC_PROPOSAL_MARKER,
        "## Proposed epics",
        "",
        f"**Product Owner** decomposed *{goal_title}* into "
        f"{len(proposal.epics)} epic{'s' if len(proposal.epics) != 1 else ''}, "
        "ordered so the most valuable is deliverable first.",
        "",
    ]
    for i, epic in enumerate(proposal.epics, 1):
        created = (epic_numbers or {}).get(epic.title)
        heading = f"### {i}. {epic.title}" + (f" — #{created}" if created else "")
        lines += [
            heading,
            "",
            f"**Outcome** — {epic.outcome}",
            "",
            f"**Why** — {epic.rationale}",
            "",
        ]
    lines += [
        "### Ordering",
        "",
        proposal.ordering_rationale,
        "",
        "---",
        "",
        "Each epic is now a card in `Inbox (Goals)` labelled `needs:human`, nested "
        "under this goal.",
        "",
        "**Approve or reject each epic individually** — move its card to "
        "`Needs Refinement` to approve, or close it to reject. They are separate "
        "decisions; you can take some and not others.",
        "",
        "This goal card stays where it is. It is the parent tracker, not a card to "
        "move, and its `needs:human` label has been cleared now that the decision "
        "sits with the epics.",
    ]
    return "\n".join(lines)


def sponsor_notes(issues: IssueClient, repo: str, number: int) -> str:
    """What a person has said on this card since the crew last answered it.

    Only comments after the crew's own last marked comment, so a rework reads
    the objection to the proposal it is replacing rather than the whole history
    of the card. Comments carrying a crew marker are the crew's own and are
    skipped: a role reading its own last output back as instruction is noise.
    """
    try:
        comments = issues.comments(repo, number)
    except Exception:  # noqa: BLE001
        return ""

    markers = (EPIC_PROPOSAL_MARKER, STORY_SPLIT_MARKER, "<!-- crew:")
    last_crew = -1
    for i, c in enumerate(comments):
        if any(m in (c.get("body") or "") for m in markers):
            last_crew = i
    since = comments[last_crew + 1 :]
    return "\n\n".join(
        (c.get("body") or "").strip()
        for c in since
        if not any(m in (c.get("body") or "") for m in markers)
    ).strip()


def unstarted_children(issues: IssueClient, cards: list[Card], repo: str, number: int):
    """(closable, started) — the cards a rework would supersede, and those it
    must not.

    Replacing a decomposition while its stories are being built would throw away
    work in flight. A rework that would do that is refused instead, naming the
    cards, because that is a decision for the person asking.
    """
    # Keyed by repository as well as number: a sub-issue lives in its parent's
    # repository, and a bare number would match a card of the same number in
    # another one — closing or refusing to close the wrong card.
    by_key = {c.key: c for c in cards}
    try:
        children = [child["number"] for child in issues.sub_issues(repo, number)]
    except Exception:  # noqa: BLE001
        return [], []

    closable, started = [], []
    for child in children:
        card = by_key.get((repo, child))
        if card is None or card.state == "CLOSED":
            continue
        (started if card.status not in (INBOX, REFINEMENT, READY) else closable).append(child)
    return closable, started


def rework_gate(
    issues: IssueClient,
    sink: EventSink,
    result: TickResult,
    cards: list[Card],
    card: Card,
    repo: str,
    marker: str,
) -> tuple[bool, str]:
    """(proceed, what the Sponsor said).

    Three things stopped a decomposition being redone, and only the first is
    the obvious one: the marker made the step one-shot, nothing read comments as
    input, and a re-run added cards beside the old ones rather than replacing
    them.
    """
    number = card.number or 0
    answered = issues.has_comment_marked(repo, number, marker)
    wants_rework = NEEDS_REWORK in card.labels

    if answered and not wants_rework:
        # Recorded, not announced. A reconciliation pass finds most of the board
        # already answered every time it runs, and emitting one note per card
        # filled the log with eight lines a pass saying nothing happened. The
        # count goes in the tick's summary instead.
        result.skipped.append((number, "already answered"))
        return False, ""
    if not wants_rework:
        return True, ""

    closable, started = unstarted_children(issues, cards, repo, number)
    if started:
        why = "work already started on " + ", ".join(f"#{n}" for n in started)
        result.skipped.append((number, f"rework refused — {why}"))
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                card=number,
                summary=f"rework refused — {why}"[:100],
            )
        )
        return False, ""

    for child in closable:
        with contextlib.suppress(Exception):
            issues.close(repo, child, reason="not_planned")
    if closable:
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                card=number,
                summary=f"superseded {', '.join(f'#{n}' for n in closable)}"[:100],
            )
        )
    with contextlib.suppress(Exception):
        # The Sponsor's label, spent. Nobody's role to claim.
        artifacts.label(issues, sink, repo=repo, number=number, by=None, remove=[NEEDS_REWORK])
    return True, sponsor_notes(issues, repo, number)


def goals_missing_work_type(cards: list[Card]) -> list[Card]:
    """Cards filed as a Goal that the board has not typed yet.

    A person writing a Goal applies the `goal` label, because that is the only
    thing they can set while filing the issue. `goal_cards` filters on Work
    Type, which nothing at creation time can populate — so a correctly filed
    Goal lands on the board invisible to the crew, and the tick used to do no
    more than name it in a note nobody was reading.
    """
    return [
        c
        for c in cards
        if c.status == INBOX and c.state != "CLOSED" and not c.work_type and GOAL_LABEL in c.labels
    ]


def stamp_goal_work_type(board: ProjectClient, sink: EventSink, cards: list[Card]) -> list[Card]:
    """Set Work Type from the `goal` label, and return the board as it now is.

    The updated cards are returned rather than re-read so that the Goal is
    decomposed on the tick that typed it. Re-reading would cost a round trip to
    say the same thing, and deferring to the next tick would make a Goal wait
    for a pass that does nothing but look at it.

    Authorship is deliberately not checked here. Typing the card is what makes
    it a Goal at all, and an unauthored one then shows up in
    `unauthored_goals`, reported as a Goal the Sponsor did not write — which is
    the intended outcome. Skipping it here would instead leave it untyped and
    silent, which is the failure this function exists to end.
    """
    pending = goals_missing_work_type(cards)
    if not pending:
        return cards

    stamped: set[str] = set()
    for c in pending:
        try:
            board.set_select(c.item_id, "Work Type", GOAL_TYPE)
        except Exception as exc:  # noqa: BLE001
            sink.emit(
                CrewEvent(
                    kind=EventKind.NOTE,
                    card=c.number or 0,
                    summary=f"could not set Work Type: {type(exc).__name__}: {exc}"[:100],
                )
            )
            continue
        stamped.add(c.item_id)
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                card=c.number or 0,
                summary=f"typed {GOAL_TYPE} from the `{GOAL_LABEL}` label",
            )
        )

    return [
        c.model_copy(update={"work_type": GOAL_TYPE}) if c.item_id in stamped else c for c in cards
    ]


def goal_cards(cards: list[Card], *, sponsor: str | None = None) -> list[Card]:
    """Cards awaiting an epic proposal.

    Filtered by Work Type, not just by column: the epics the crew creates land
    in the same column awaiting approval, and must never be mistaken for goals
    and decomposed again.

    And filtered by author. A Goal is the only artifact the Sponsor writes, and
    two of the three on this board were written by the crew — one of which
    duplicated a crew capability as product work in the pilot and redirected a
    day of delivery onto the wrong ladder. `sponsor` unset keeps the old
    behaviour, so a board with no Sponsor configured still works.
    """
    return [
        c
        for c in cards
        if c.status == INBOX
        and c.state != "CLOSED"
        and c.work_type == GOAL_TYPE
        and (sponsor is None or c.author == sponsor)
    ]


def unauthored_goals(cards: list[Card], *, sponsor: str | None = None) -> list[Card]:
    """Goals nobody with the authority to set one wrote.

    Reported rather than silently skipped: a card that could be acted on and is
    not needs a reason, or it sits on the board forever for causes nobody can
    see.
    """
    if sponsor is None:
        return []
    return [
        c
        for c in cards
        if c.status == INBOX
        and c.state != "CLOSED"
        and c.work_type == GOAL_TYPE
        and c.author != sponsor
    ]


def create_epic_cards(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    goal: Card,
    proposal: EpicProposal,
) -> dict[str, int]:
    """Turn proposed epics into cards awaiting the Sponsor.

    They land in Inbox (Goals) with needs:human, nested under the goal. That is
    the human gate the constitution describes: the Sponsor approves an epic by
    moving its card, which is the only way work leaves that column.
    """
    created: dict[str, int] = {}
    for epic in proposal.epics:
        issue = issues.create(
            repo,
            epic.title,
            render_epic_body(epic, goal.number or 0, goal.title),
            labels=[NEEDS_HUMAN],
        )
        number = issue["number"]
        created[epic.title] = number

        item = board.add_issue(issue["node_id"])
        move_card(
            board,
            sink,
            item_id=item,
            to=INBOX,
            by="Product Owner",
            card=number,
            summary=f"epic card created — {epic.title[:50]}",
        )
        board.set_select(item, "Work Type", EPIC_TYPE)
        if goal.priority:
            board.set_select(item, "Priority", goal.priority)

        # Nesting is best effort: a board that shows the hierarchy is better,
        # but a missing link must not cost us the epic.
        try:
            issues.add_sub_issue(repo, goal.number or 0, issue["id"])
        except Exception as exc:  # noqa: BLE001
            sink.emit(
                CrewEvent(
                    kind=EventKind.NOTE,
                    card=number,
                    summary=f"could not nest under #{goal.number}: {exc}"[:100],
                )
            )

    return created


def approved_epics(cards: list[Card]) -> list[Card]:
    """Epics the Sponsor has released from the gate, awaiting story splitting."""
    return [
        c
        for c in cards
        if c.status == REFINEMENT and c.state != "CLOSED" and c.work_type == EPIC_TYPE
    ]


def render_story_body(story: Story, epic_number: int, epic_title: str) -> str:
    """A story issue, written so a test can be derived from it directly."""
    lines = [
        f"As a **{story.as_a}**, I want **{story.i_want}**, so that **{story.so_that}**.",
        "",
        "## Acceptance criteria",
        "",
    ]
    for i, ac in enumerate(story.acceptance_criteria, 1):
        lines += [
            f"{i}. **Given** {ac.given}",
            f"   **When** {ac.when}",
            f"   **Then** {ac.then}",
            "",
        ]
    lines += [
        f"**Estimate** — {story.points} points",
        "",
        "---",
        "",
        f"Split from #{epic_number} *({epic_title})* by the Business Analyst.",
    ]
    return "\n".join(lines)


def render_split(
    epic_title: str, proposal: StoryProposal, decision, numbers: dict[str, int]
) -> str:
    """The record of a split, for whoever reads the epic later."""
    total = sum(s.points for s in proposal.stories)
    lines = [
        STORY_SPLIT_MARKER,
        "## Stories",
        "",
        f"**Business Analyst** split *{epic_title}* into {len(proposal.stories)} stories "
        f"totalling {total} points.",
        "",
    ]
    for story in proposal.stories:
        number = numbers.get(story.title)
        ref = f" — #{number}" if number else ""
        lines.append(f"- **[{story.points}]** {story.title}{ref}")
    lines += [
        "",
        "### Design",
        "",
        ("**Required.** " if decision.required else "**Not required.** ") + decision.reason,
        "",
    ]
    if decision.required:
        lines.append(
            "Labelled `needs:design`. The Architect writes a design note before "
            "implementation begins."
        )
    return "\n".join(lines)


class RepoContext:
    """The repository each card is about, read once per tick and reused.

    Refinement touches several cards across one or two repositories in a pass,
    and the context is identical for all of them — so reading it once is both
    cheaper and what keeps the cached prefix intact between calls.

    A repository that cannot be read is not a reason to stop refining. The
    agent is then working the way it always has, which is worse but not broken,
    and the miss is reported rather than hidden.
    """

    def __init__(self, ws: Workspace | None, sink: EventSink) -> None:
        self._ws = ws
        self._sink = sink
        self._cache: dict[str, str] = {}

    def for_repo(self, repo: str) -> str:
        if self._ws is None:
            return ""
        if repo not in self._cache:
            try:
                clone = self._ws.for_repo(repo).current()
                self._cache[repo] = self._record(repo, clone) + repository_context(
                    clone, editing=False
                )
            except Exception as exc:  # noqa: BLE001
                self._sink.note(
                    EventKind.NOTE,
                    f"refining {repo} without its code: {exc}"[:120],
                )
                self._cache[repo] = ""
        return self._cache[repo]

    def _record(self, repo: str, clone) -> str:
        """The project's record, first (#131): what the work is for, before the code.

        Refinement splits epics against the project's purpose and scope. A record
        that can't be read is reported and refining goes on without it, as it
        does without code it can't read.
        """
        try:
            record = read_record(clone)
        except ProjectRecordError as exc:
            self._sink.note(EventKind.NOTE, f"refining {repo} without its record: {exc}"[:120])
            return ""
        return f"{brief(record)}\n\n" if record else ""


def failure_fingerprint(exc: Exception) -> str:
    """A short, stable name for *how* something failed.

    Digits are stripped before hashing: a token count, a duration and a
    line number differ between two runs of the same dead end, and treating
    those as distinct failures is what let one epic be retried six times.

    Short enough to sit in a comment marker, long enough not to collide
    across the handful of ways a split can fail.
    """
    text = f"{type(exc).__name__}:{re.sub(r'[0-9]+', '', str(exc))}".strip()[:400]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def failures_since_parking(issues: IssueClient, repo: str, number: int) -> list[str]:
    """Fingerprints of splits that have failed since this epic was last parked.

    Scoped to the last parking on purpose. Parking is how a person is asked to
    look at the epic, and moving it back is how they say they have — so the
    count restarts and the epic gets its attempts again, rather than being
    parked on the first failure for ever after.
    """
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, number)]
    except Exception:  # noqa: BLE001
        return []
    last_park = max(
        (i for i, body in enumerate(bodies) if SPLIT_PARKED_MARKER in body),
        default=-1,
    )
    return [
        body.split(SPLIT_FAILURE_MARKER, 1)[1].split("-->", 1)[0].strip()
        for body in bodies[last_park + 1 :]
        if SPLIT_FAILURE_MARKER in body
    ]


def park_epic(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    number: int,
    detail: str,
) -> None:
    """Stop attempting this split, and say so where a person will find it."""
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=BLOCKED,
        by=None,
        card=number,
        frm=card.status,
        summary="split failed identically — not retried",
        kind=EventKind.CARD_BLOCKED,
    )
    artifacts.label(issues, sink, repo=repo, number=number, by=None, add=["blocked", "needs:human"])
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=number,
        body=f"{SPLIT_PARKED_MARKER}\n**Blocked — this epic will not be split again.** "
        f"The Business Analyst failed the same way "
        f"{IDENTICAL_FAILURES_BEFORE_PARKING} times:\n\n```\n{detail[:600]}\n```\n\n"
        "An identical repeat is not going to converge, so retrying it every pass only "
        "spends wall-clock and GPU. The usual cause is an epic large enough that the "
        "split exhausts its token budget before any answer begins.\n\n"
        "Split this epic into smaller ones, or narrow it, and move it back to "
        "`Needs Refinement` — the count restarts from here.",
        by=None,
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.CARD_BLOCKED,
            role="Business Analyst",
            card=number,
            summary=f"split failed identically {IDENTICAL_FAILURES_BEFORE_PARKING} times",
        )
    )


def admit_held_stories(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    rules: ProcessRules,
    result: TickResult,
    *,
    cards: list[Card],
    default_repo: str,
) -> list[Card]:
    """Let stories held for room into Ready, now that it may have some.

    The hold is decided once, when the story is created. Without this nothing
    looked again: sprint-metrics #33 and #34 were held while Ready was 10 of 10
    and still sat in refinement with Ready at 3, until they were moved by hand.

    Returns the cards with the admitted stories' status updated, so the passes
    after this one count Ready as it now is.
    """
    counts = board.counts(cards)
    admitted: dict[str, Card] = {}

    for card in cards:
        if (
            card.status != REFINEMENT
            or card.state == "CLOSED"
            or card.work_type != STORY_TYPE
            or HELD_FOR_ROOM not in card.labels
        ):
            continue
        number = card.number or 0
        verdict = rules.may_move(frm=REFINEMENT, to=READY, counts=counts)
        if not verdict.allowed:
            result.waiting.append((number, verdict.reason))
            continue

        # Bookkeeping, not judgement: the Business Analyst already decided this
        # story belongs in Ready, and only the limit said not yet.
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=READY,
            by=None,
            card=number,
            summary=f"{READY} has room — letting in a story held for it",
        )
        artifacts.label(
            issues,
            sink,
            repo=card.repo or default_repo,
            number=number,
            by=None,
            remove=[HELD_FOR_ROOM],
        )
        counts[READY] = counts.get(READY, 0) + 1
        admitted[card.item_id] = card.model_copy(
            update={"status": READY, "labels": card.labels - {HELD_FOR_ROOM}}
        )
        result.admitted.append(number)

    if result.waiting:
        sink.note(
            EventKind.NOTE,
            f"waiting for room in {READY}: " + ", ".join(f"#{n}" for n, _ in result.waiting),
        )
    return [admitted.get(c.item_id, c) for c in cards]


def refine_epics(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    rules: ProcessRules,
    design: DesignPolicy,
    result: TickResult,
    *,
    cards: list[Card],
    default_repo: str,
    context: RepoContext,
) -> None:
    """Split approved epics into stories, and decide whether design is warranted."""
    counts = board.counts(cards)

    for epic_card in approved_epics(cards):
        repo = epic_card.repo or default_repo
        number = epic_card.number
        assert number is not None

        proceed, notes = rework_gate(
            issues, sink, result, cards, epic_card, repo, STORY_SPLIT_MARKER
        )
        if not proceed:
            continue

        # What this epic has already failed at. Read before the attempt, so a
        # dead end is recognised rather than walked into again.
        seen_failures = failures_since_parking(issues, repo, number)

        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_STARTED,
                role="Business Analyst",
                card=number,
                summary=f"split {epic_card.title[:50]}",
            )
        )
        try:
            # The whole epic body. It was cut at 800 characters while the Product
            # Owner one step earlier was given its goal whole — and the Business
            # Analyst is the role that writes the acceptance criteria, so what it
            # cannot see becomes a criterion nobody can satisfy.
            proposal = split_epic(
                epic_card.title,
                _goal_body(issues, repo, number),
                repository=context.for_repo(repo),
                feedback=notes,
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((number, f"{type(exc).__name__}: {exc}"))
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FAILED,
                    role="Business Analyst",
                    card=number,
                    summary=str(exc)[:100],
                )
            )
            fingerprint = failure_fingerprint(exc)
            if seen_failures.count(fingerprint) + 1 >= IDENTICAL_FAILURES_BEFORE_PARKING:
                park_epic(
                    board,
                    issues,
                    sink,
                    epic_card,
                    repo=repo,
                    number=number,
                    detail=f"{type(exc).__name__}: {exc}",
                )
                result.parked.append(number)
            else:
                # Recorded so the next pass can tell a repeat from a new
                # failure. Nothing was written here at all, which is why every
                # pass saw an epic that had simply never been split.
                artifacts.comment(
                    issues,
                    sink,
                    repo=repo,
                    number=number,
                    body=f"{SPLIT_FAILURE_MARKER} {fingerprint} -->\n"
                    f"**The split failed.** `{type(exc).__name__}: {str(exc)[:300]}`\n\n"
                    "It will be attempted once more. An identical failure after that "
                    "parks the epic rather than retrying it every pass.",
                    by=None,
                )
            continue

        numbers: dict[str, int] = {}
        for story in proposal.stories:
            issue = issues.create(
                repo, story.title, render_story_body(story, number, epic_card.title)
            )
            numbers[story.title] = issue["number"]

            item = board.add_issue(issue["node_id"])
            board.set_select(item, "Work Type", STORY_TYPE)
            board.set_number(item, "Points", story.points)
            if epic_card.priority:
                board.set_select(item, "Priority", epic_card.priority)

            # Ready is a queue, but it still has a limit. A story that cannot
            # enter waits in refinement rather than being dropped.
            verdict = rules.may_move(frm=REFINEMENT, to=READY, counts=counts)
            column = READY if verdict.allowed else REFINEMENT
            move_card(
                board,
                sink,
                item_id=item,
                to=column,
                by="Business Analyst",
                card=issue["number"],
                summary=f"story card created — {story.title[:50]}",
            )
            counts[column] = counts.get(column, 0) + 1
            if not verdict.allowed:
                sink.emit(
                    CrewEvent(
                        kind=EventKind.NOTE,
                        card=issue["number"],
                        summary=f"held in {REFINEMENT}: {verdict.reason}"[:100],
                    )
                )
                artifacts.label(
                    issues, sink, repo=repo, number=issue["number"], by=None, add=[HELD_FOR_ROOM]
                )

            # Nesting is best effort; a missing link is not worth losing the story.
            with contextlib.suppress(Exception):
                issues.add_sub_issue(repo, number, issue["id"])
            result.stories_created.append(issue["number"])

        decision = design.decide(
            EpicShape(
                points_total=sum(s.points for s in proposal.stories),
                story_count=len(proposal.stories),
                labels=frozenset(epic_card.labels),
            )
        )
        if decision.required:
            artifacts.label(
                issues, sink, repo=repo, number=number, by="Architect", add=[NEEDS_DESIGN]
            )
            result.design_required.append(number)

        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=number,
            body=render_split(epic_card.title, proposal, decision, numbers),
            by="Business Analyst",
        )

        # Moving the card out of the gate *was* the approval. Leaving the label
        # on means the board keeps asking for a decision already made — the same
        # staleness the goal card had.
        artifacts.label(issues, sink, repo=repo, number=number, by=None, remove=[NEEDS_HUMAN])

        result.epics_refined.append(number)
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FINISHED,
                role="Business Analyst",
                card=number,
                summary=f"{len(numbers)} stories"
                + (" — design required" if decision.required else ""),
            )
        )


def tick(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    *,
    default_repo: str,
    org: dict | None = None,
    ws: Workspace | None = None,
    sponsor: str | None = None,
    repos: set[str] | None = None,
) -> TickResult:
    """The refinement phase: goals become epics, approved epics become stories.

    Two passes, in dependency order. It stops at Ready because that is where
    refinement ends, not because a Sponsor is waiting: section 1 of the
    constitution names exactly two human gates — epics in `Inbox (Goals)` and
    the sprint review — and admission is neither. `crew_org.flows.loop` runs the
    phase that follows.

    This used to claim sprint admission was "still the Sponsor's decision",
    which invented a third gate the constitution does not have and `crew sprint
    start` explicitly denies: "approving an epic was the scope decision, so
    this is mechanical". Work sat in Ready looking like it needed a human.
    """
    org = org or load_org()
    rules = ProcessRules.from_config(org)
    design = DesignPolicy.from_config(org)

    result = TickResult()
    context = RepoContext(ws, sink)
    sink.note(EventKind.TICK_STARTED, "reading board", tick=1)

    # Only the repositories the crew works in. A Goal anywhere else is not the
    # crew's to decompose, however it is typed.
    cards = within(board.cards(), repos)
    sink.note(EventKind.NOTE, f"{len(cards)} cards on the board", counts=board.counts(cards))

    cards = stamp_goal_work_type(board, sink, cards)

    untyped = [c.number for c in cards if c.status == INBOX and not c.work_type]
    if untyped:
        sink.note(
            EventKind.NOTE,
            f"ignoring untyped cards in {INBOX}: {untyped} — set Work Type",
        )

    cards = admit_held_stories(
        board, issues, sink, rules, result, cards=cards, default_repo=default_repo
    )

    for card in unauthored_goals(cards, sponsor=sponsor):
        number = card.number or 0
        result.skipped.append((number, f"a Goal written by {card.author}, not the Sponsor"))
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                card=number,
                summary=f"not decomposed — a Goal written by {card.author}"[:100],
            )
        )

    for card in goal_cards(cards, sponsor=sponsor):
        result.considered += 1
        repo = card.repo or default_repo
        number = card.number
        assert number is not None

        proceed, notes = rework_gate(issues, sink, result, cards, card, repo, EPIC_PROPOSAL_MARKER)
        if not proceed:
            continue

        sink.emit(
            CrewEvent(
                kind=EventKind.CARD_CLAIMED,
                role="Product Owner",
                card=number,
                summary=card.title[:80],
            )
        )
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_STARTED,
                role="Product Owner",
                card=number,
                summary="decompose goal into epics",
            )
        )
        try:
            proposal = propose_epics(
                f"{card.title}\n\n{_goal_body(issues, repo, number)}",
                repository=context.for_repo(repo),
                feedback=notes,
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((number, f"{type(exc).__name__}: {exc}"))
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FAILED,
                    role="Product Owner",
                    card=number,
                    summary=str(exc)[:100],
                )
            )
            continue

        epic_numbers = create_epic_cards(
            board, issues, sink, repo=repo, goal=card, proposal=proposal
        )
        result.epics_created.extend(epic_numbers.values())

        # The comment is written last, because it is also the idempotency
        # marker: if card creation fails halfway, the next tick retries rather
        # than recording work that did not happen.
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=number,
            body=render_proposal(card.title, proposal, epic_numbers),
            by="Product Owner",
        )

        # The decision has moved to the epics. Leaving needs:human on the goal
        # would show the Sponsor four things demanding attention when only
        # three do, and make the goal look like the card to move.
        artifacts.label(issues, sink, repo=repo, number=number, by=None, remove=[NEEDS_HUMAN])

        result.proposed.append(number)
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FINISHED,
                role="Product Owner",
                card=number,
                summary=f"{len(epic_numbers)} epic cards awaiting approval",
            )
        )

    # Epics approved in an earlier tick are refined now. Epics created moments
    # ago are not: they are sitting at the Sponsor's gate, unapproved.
    refine_epics(
        board,
        issues,
        sink,
        rules,
        design,
        result,
        cards=cards,
        default_repo=default_repo,
        context=context,
    )

    sink.note(
        EventKind.TICK_FINISHED,
        f"{len(result.proposed)} goals decomposed, {len(result.epics_created)} epics created, "
        f"{len(result.epics_refined)} epics refined, {len(result.stories_created)} stories, "
        f"{len(result.failed)} failed",
    )
    return result


def _goal_body(issues: IssueClient, repo: str, number: int) -> str:
    """The goal's own text. The title alone is rarely the whole ask."""
    try:
        return (issues.get(repo, number).get("body") or "").strip()
    except Exception:  # noqa: BLE001
        return ""
