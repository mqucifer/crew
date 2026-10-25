"""`delivery-history`: the crew's record of how it delivers, as a data package (crew#521).

crew-presentation shows it; it depends on nothing else (ADR 0021). Each part's
scope is ADR 0022: sprint-metrics keeps and computes, the crew adds and
packages, crew-presentation unwraps and displays.

**The model is the allow-list (ADR 0020).** A field reaches the package only
if it's declared here, and every model forbids extra fields, so something
added to an event later stays local until someone decides it may leave. What
may leave: measures, names and states, titles already public on GitHub, and
refusal reasons the crew's own checks wrote with only names filled in
(`reasons.py`). Never a prompt, an answer, review text or reasoning, and never
an event's summary, which carries prompt snippets.

**The schema is generated from this model** into `contracts/delivery-history/`
by `crew package schema`, and its version lives here. A change to the model
without a new version fails the tests (ADR 0021).

This is the crew's part. sprint-metrics' answers join it, as a MINOR version,
once sprint-metrics publishes its schemas (mqucifer/sprint-metrics#461) and
takes the crew's sprints (mqucifer/sprint-metrics#462). Nothing is released
until then: the Sponsor, 2026-10-08.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, get_args
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from crew_org.packages.reasons import published_reason
from crew_org.sendbacks import Step

NAME: Literal["delivery-history"] = "delivery-history"
# MAJOR when a consumer could break on the change, MINOR when it only adds.
SCHEMA_VERSION = "1.11.0"

# How long before its card was filed an event may be and still be that card's.
# The board dates a card when the issue was created; the crew logs the card
# being created a moment after.
FILED_SLACK = timedelta(minutes=5)


class _Part(BaseModel):
    # Extra fields are refused, so nothing leaves that isn't declared. A field
    # with a default is always written, so the schema says it's always there.
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )


class CardRef(_Part):
    """A card, by repository and number: numbers repeat across repositories."""

    repo: str
    number: int


class Sprint(_Part):
    """A board iteration: its name and the days it runs, in the package's timezone."""

    name: str
    start: date
    end: date = Field(description="The sprint's last day, inclusive.")


class Card(_Part):
    """A card on the board. Its title is already public on GitHub."""

    repo: str
    number: int
    type: Literal["Goal", "Epic", "Story", "Bug"] | None
    title: str
    url: str | None
    parent: int | None = Field(description="The parent card's number, in the same repository.")
    points: float | None
    sprint: str | None
    status: str | None
    status_set: datetime | None = Field(description="When its status was last set, by anyone.")
    created: datetime | None
    closed: datetime | None
    technical: bool = Field(
        description="Technical work the crew found itself, from a design or a failing main. "
        "It skips the Sponsor's gate (crew#192, crew#335)."
    )
    closed_as: Literal["completed", "not_planned", "duplicate"] | None = Field(
        description="How the issue closed. A story closed as not planned was superseded: "
        "never built, though the board shows it Done."
    )
    superseded_because: Literal["sponsor", "story_problem"] | None = Field(
        default=None,
        description="Why a story closed as not planned was superseded: its epic was split "
        "again at the Sponsor's request, or after a story problem. None when no re-split "
        "recorded it.",
    )


Mover = Literal["crew", "person", "platform"]


class CardMoved(_Part):
    """A card moving between columns.

    `by` says who: the crew (and `role`, the role that moved it), a person, or
    the platform (the board's own workflow). The Sponsor's approvals are a
    person moving a Goal or an Epic.
    """

    kind: Literal["card_moved"] = "card_moved"
    at: datetime
    card: CardRef
    from_column: str | None
    to_column: str | None
    by: Mover
    role: str | None


class PullRef(_Part):
    repo: str
    number: int


class WorkStarted(_Part):
    """A role starting work on a card, or on no card (planning, the retro).

    A review is work on a pull request: `pull` names it, and `card` the card it
    closes, when it closes exactly one.
    """

    kind: Literal["work_started"] = "work_started"
    at: datetime
    card: CardRef | None
    pull: PullRef | None
    role: str


Verdict = Literal["approved", "changes_requested", "accepted", "returned"]


class WorkFinished(_Part):
    kind: Literal["work_finished"] = "work_finished"
    at: datetime
    card: CardRef | None
    pull: PullRef | None
    role: str
    verdict: Verdict | None = Field(
        default=None,
        description="A review's or QA's verdict: approved or changes requested, accepted "
        "or returned. None for other work.",
    )


Decision = Literal["retry_local", "return_to_refinement", "file_prompt_defect", "escalate", "block"]


class Attempt(_Part):
    """An attempt that didn't land, and what the crew decided to do about it.

    `reason` is present only when one of the crew's own checks wrote it with
    nothing but names filled in (ADR 0020); otherwise the failure class stands
    alone.

    Escalation's policy decides these, so they end with escalation (ADR 0032, Stage
    E): earlier sprints keep theirs, and later ones have none. A story delivered in
    Developer sessions misses on review's and QA's returns instead (`verdict` on
    `work_finished`), and its deliveries are in the analysis.
    """

    kind: Literal["attempt"] = "attempt"
    at: datetime
    card: CardRef
    role: str | None
    number: int | None = Field(description="Which local attempt this was, from 1.")
    failure_class: str
    decision: Decision | None
    reason: str | None


class Blocked(_Part):
    kind: Literal["blocked"] = "blocked"
    at: datetime
    card: CardRef
    role: str | None


class Escalated(_Part):
    """An attempt sent to the escalation model, which the sprint's budget pays for.

    Escalation retires with ADR 0032 (Stage E): earlier sprints keep these, and later
    ones have none. A stuck story goes back to the Business Analyst instead
    (`returned`).
    """

    kind: Literal["escalated"] = "escalated"
    at: datetime
    card: CardRef
    role: str | None


class ModelCall(_Part):
    """One call to a model: what it cost in tokens and time. No money is recorded."""

    kind: Literal["model_call"] = "model_call"
    at: datetime
    card: CardRef | None
    role: str | None
    step: str | None = Field(description="What the call was for, such as deliver or refine.")
    model: str | None = Field(description="The model alias the crew asked for.")
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cached_tokens: int | None = Field(
        default=None, description="Of its prompt tokens, those the server's prefix cache held."
    )
    seconds: float | None
    failed: bool


class PullMerged(_Part):
    """A pull request merging. In the crew's own repository, that's a fix to the crew."""

    kind: Literal["pull_merged"] = "pull_merged"
    at: datetime
    repo: str
    number: int
    title: str
    closes: list[CardRef]
    crews_own: bool = Field(description="Merged into the crew's own repository.")
    design_revision: bool = Field(
        description="The Architect's revision of a project's design, which the crew merges "
        "itself (crew#192)."
    )
    about: list[CardRef] = Field(
        description="The cards the crew issues it closes cite, as the crew recorded them "
        "(crew#449). A fix to the crew names the stories whose trouble led to it."
    )


# --- the Developer's session (crew#778, ADR 0032): what it did, by names and counts ------

# The session's tools (decision 2), and the caller's step after a run.
Tool = Literal[
    "view",
    "search",
    "find",
    "tests_for",
    "replace_text",
    "replace_definition",
    "write_file",
    "delete_file",
    "run",
    "git",
    "done",
    "blocked",
    "after_run",
]
# How a session or a delivery was stopped. `gate_repeat` stops a delivery between its
# sessions, as `backstop` can: the gates' same findings 3 rounds in a row (crew#790).
# `context_full`: a compaction that left the prompt at or over its limit (crew#857).
Stop = Literal["no_progress", "backstop", "ceiling", "no_tool_call", "gate_repeat", "context_full"]
ToolResult = Literal["ok", "refused", "failed", "passed", "error", "end"]
How = Literal["done", "blocked", "interrupted"]
BlockedOn = Literal["file", "fact", "choice", "stop"]


class SessionStarted(_Part):
    """A Developer session starting on a story: the size of each part of its start."""

    kind: Literal["session_started"] = "session_started"
    at: datetime
    card: CardRef
    start_chars: dict[str, int] = Field(
        description="Each part's size in characters, and `total`: story, declared_tests, "
        "project, map, hints or instead_of_hints, rules."
    )
    rework: bool = Field(
        description="It started from the branch's history, such as the gates' or review's "
        "findings or a checkpoint, in place of the hints: a rework round or a resume."
    )


class SessionTurn(_Part):
    """The signals after one model call of a session, recorded on every call."""

    kind: Literal["session_turn"] = "session_turn"
    at: datetime
    card: CardRef
    turn: int = Field(description="The model call's number in its session, from 1.")
    tool_calls: int | None = Field(description="Each count is None if it wasn't recorded.")
    since_action: int | None = Field(
        description="Model calls in a row with no edit applied and no run."
    )
    most_views: int | None = Field(description="The most views of one file since its last edit.")
    edits_since_run: int | None
    empty_lookups: int | None = Field(description="This call's lookups that found nothing.")
    reasoning_tokens: int | None
    model_seconds: float | None = Field(description="The session's model time so far.")


class ToolCall(_Part):
    """One tool call of a session: which tool, how it went, and its sizes. Never its
    arguments or its reply, a path, a pattern or a command."""

    kind: Literal["tool_call"] = "tool_call"
    at: datetime
    card: CardRef
    turn: int
    tool: Tool | None = Field(description="None for a tool there isn't, which it named.")
    result: ToolResult
    rule: str | None = Field(description="The declared rule a refusal was by (ADR 0031).")
    found_nothing: bool = Field(description="A lookup that found no file, definition or hit.")
    argument_chars: int | None
    reply_chars: int | None
    seconds: float | None
    run: Literal["tests", "suite", "lint"] | None = Field(description="What a run ran.")
    exit_codes: list[int] | None = Field(description="A run's commands' exit codes, in order.")


class SessionCompacted(_Part):
    """A session's history compacted before a prompt would pass its limit: one of these
    each time, since a long session compacts more than once."""

    kind: Literal["session_compacted"] = "session_compacted"
    at: datetime
    card: CardRef
    turn: int = Field(description="The call it was compacted before.")
    estimated_tokens: int | None
    turns_masked: int | None = Field(description="Tool replies shrunk to their one line.")


class SessionEnded(_Part):
    """How a session ended, what it cost, and its signals at the end."""

    kind: Literal["session_ended"] = "session_ended"
    at: datetime
    card: CardRef
    how: How
    blocked_on: BlockedOn | None = Field(
        description="What a blocked session lacked: a file, a fact, a choice, or `stop`."
    )
    stop: Stop | None = Field(description="What stopped it, when it didn't end itself.")
    ceiling: Literal["reading", "re_viewing"] | None = Field(
        description="The ceiling a `ceiling` stop reached."
    )
    why: Literal["call_failed", "workspace_gone"] | None = Field(description="What interrupted it.")
    model_calls: int | None = Field(description="Each count is None if it wasn't recorded.")
    model_seconds: float | None
    prompt_tokens: int | None
    cached_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    edits: int | None
    runs: int | None
    criteria: int | None = Field(description="At `done`, the criteria it named a test for.")
    changed_tests: int | None = Field(description="At `done`, the merged tests it changed.")
    since_action: int | None = Field(description="Its signals at the end; None if unrecorded.")
    most_views: int | None
    edits_since_run: int | None
    empty_lookups: int | None = Field(description="The session's lookups that found nothing.")


class GateChecked(_Part):
    """The crew's gate on a session's branch at `done`, before review (ADR 0032).

    A gate's return is the Developer still working, not an attempt (the Sponsor,
    2026-10-18, crew#778): its findings are named by their rules, never their text.
    """

    kind: Literal["gate_checked"] = "gate_checked"
    at: datetime
    card: CardRef
    passed: bool
    rules: list[str] = Field(description="The declared rules its findings were by (ADR 0031).")
    already_done: bool = Field(
        description="The branch had nothing to change: the story goes to QA with an empty diff."
    )


ReturnReason = Literal[
    "gate round trips",
    "pinned tests",
    "review conflicts with a criterion",
    "session blocked",
    "contract ruling",
]


class Returned(_Part):
    """A story sent back to refinement, to the Business Analyst who wrote it (ADR 0032)."""

    kind: Literal["returned"] = "returned"
    at: datetime
    card: CardRef
    reason: ReturnReason
    blocked_on: BlockedOn | None = Field(description="For a blocked session, what it lacked.")
    stop: Stop | None = Field(description="For a stopped session or delivery, what stopped it.")


class SentBack(_Part):
    """Finished work sent back to its own step or an earlier one, to be done again (crew#862).

    Rework, counted per step: which step sent it, which it went back to, and the rule
    that decided. Every send-back writes one, beside the events that record their own
    parts (`returned`, `gate_checked`, a move's rule). A refusal inside a session, a
    hold, a block for a person and a finding filed as new work aren't send-backs.
    """

    kind: Literal["sent_back"] = "sent_back"
    at: datetime
    repo: str
    card: CardRef | None = Field(
        description="The card sent back; None for a project's design, which no card holds."
    )
    sent_from: Step = Field(
        description="The step that sent it back. A later MINOR may add steps: accept one "
        "you don't know."
    )
    sent_to: Step = Field(
        description="The step it went back to. A later MINOR may add steps: accept one "
        "you don't know."
    )
    rule: str = Field(description="The declared rule that decided it (ADR 0031).")
    round: int | None = Field(
        description="Its round at that step, counted from 1; None where the step keeps none."
    )
    counts: dict[str, int] = Field(
        description="What it carried back, by name: findings, conflicts, stories, unproven."
    )


Event = Annotated[
    CardMoved
    | WorkStarted
    | WorkFinished
    | Attempt
    | Blocked
    | Escalated
    | ModelCall
    | PullMerged
    | SessionStarted
    | SessionTurn
    | ToolCall
    | SessionCompacted
    | SessionEnded
    | GateChecked
    | Returned
    | SentBack,
    Field(discriminator="kind"),
]


class Period(_Part):
    """Days whose events share a file: a sprint, or a run of days between sprints."""

    sprint: str | None = Field(description="The sprint's name; none for days between sprints.")
    start: date
    end: date = Field(description="The period's last day, inclusive.")
    file: str = Field(description="Its events file, relative to the package's root.")
    events: int


# --- the crew's analysis (ADR 0022): written by `analysis.py` from the data, never a model


Measure = Literal[
    "first_attempt_rate_percent",
    "throughput",
    "points_delivered",
    "escalation_count",
    "blocked_aging_days",
    "wip_violations",
    "cycle_time_minutes",
    "lead_time_minutes",
    "superseded_count",
    "crew_fixes_merged",
]


class Change(_Part):
    """A measure that moved since the sprint before.

    sprint-metrics' measures are its own numbers, as it answered them: the crew
    picks which moves to say and words them, and never recomputes one.
    """

    measure: Measure
    source: Literal["sprint-metrics", "crew"]
    previous: float
    now: float
    better: bool | None = Field(description="Whether it moved for the better; none if neither.")
    text: str = Field(description="The sentence the crew's code wrote from these values.")


class Attention(_Part):
    """Something that needed a person, or kept going wrong, as the sprint stood at its end."""

    kind: Literal["waiting_on_sponsor", "blocked", "repeated_refusal"]
    card: CardRef
    since: datetime | None = Field(description="When it started waiting, or was blocked.")
    count: int | None = Field(description="For a repeated refusal, how many times it came.")
    reason: str | None = Field(description="For a repeated refusal, the reason that repeated.")
    text: str = Field(description="The sentence the crew's code wrote from these values.")


class Decided(_Part):
    """Something the crew decided on its own in the sprint, without asking anyone."""

    kind: Literal["design_revision_merged", "technical_epic_filed"]
    at: datetime
    repo: str
    number: int = Field(description="The pull request merged, or the epic filed.")
    text: str = Field(description="The sentence the crew's code wrote from these values.")


class SprintAnalysis(_Part):
    sprint: str
    as_of: datetime = Field(
        description="The moment it describes: the sprint's end, or the "
        "package's date for a sprint still running."
    )
    changed: list[Change]
    attention: list[Attention]
    decided: list[Decided]


Pattern = Literal["first_try", "crew_fix", "escalated", "retried", "superseded", "blocked", "open"]


class Repeated(_Part):
    """The refusal a story got most often, when the crew's check wrote its reason."""

    failure_class: str
    reason: str
    count: int


class StoryCost(_Part):
    """What a story's model calls cost in tokens and time. No money is recorded."""

    model_calls: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int = Field(
        default=0, description="Of its prompt tokens, those the server's prefix cache held."
    )
    model_seconds: float = Field(description="Time spent in its model calls.")


class StoryPattern(_Part):
    """A story's path, as one pattern, the facts behind it, and a sentence.

    The patterns, first that fits: `superseded` (closed as not planned),
    `first_try` (delivered with no miss and no block), `crew_fix` (delivered,
    and a fix to the crew merged between its first miss and its delivery, from
    a crew issue that cites it), `escalated`, `retried` (delivered after
    misses), `blocked` (in Blocked now), `open`. A fix whose issue doesn't cite
    the story isn't counted, however close in time: nothing is guessed.
    """

    card: CardRef
    pattern: Pattern
    misses: int = Field(
        description="Attempts that didn't land. For a story delivered in sessions, its "
        "returns by review or QA, or to the Business Analyst; never the gate's before review."
    )
    blocks: int = Field(description="Moves into Blocked.")
    escalations: int = Field(
        description="Times it was escalated: none in sprints after escalation retired (ADR 0032)."
    )
    crew_fixes: list[PullRef] = Field(
        description="Fixes to the crew, from issues that cite it, that merged between its "
        "first miss and its delivery."
    )
    repeated: Repeated | None
    delivered: datetime | None
    wall_clock_seconds: float | None = Field(
        description="From the first work on it to its delivery; none until it's delivered."
    )
    cost: StoryCost | None = Field(
        description="None when none of its model calls could be placed: older calls don't "
        "name their repository, and are left out rather than guessed."
    )
    text: str = Field(description="The sentence the crew's code wrote from these values.")


Gap = Literal["session_not_ended", "session_not_started"]


class Delivery(_Part):
    """One delivery of a story in Developer sessions, from its first session to its outcome.

    Derived from the package's own events (crew#778, decision 8). An attempt is the
    branch handed to review and QA (the Sponsor, 2026-10-18): `submissions` counts
    them, and the gate's returns before review are `gate_rounds`, which don't spoil a
    first attempt. Outcomes: `done` (moved to Done), `returned` (to the Business
    Analyst), `blocked` (left for a person: a choice, or nowhere to return it),
    `interrupted` (its last session was, and none followed), `open`.
    """

    card: CardRef
    number: int = Field(description="Which delivery of the story this is, from 1.")
    started: datetime
    ended: datetime | None
    outcome: Literal["done", "returned", "blocked", "interrupted", "open"]
    sessions: int
    submissions: int = Field(
        description="Times its branch was handed to review and QA: a move into Reviewing, "
        "or into QAing from anywhere else, as a story already done is."
    )
    gate_rounds: int = Field(description="Times the gate at `done` returned it before review.")
    gate_rules: list[str] = Field(description="The rules the gate's returns were by.")
    review_returns: int = Field(description="Returns by review or QA.")
    rework_rounds: int = Field(
        description="Sessions started after a return by the gate, review or QA (P2)."
    )
    resumes: int = Field(description="Sessions started after an interrupted one (P3).")
    model_calls: int
    model_seconds: float
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    runs: int
    first_green_run: int | None = Field(
        description="Of its runs, from 1, the first whole suite that passed."
    )
    stop: Stop | None = Field(
        description="What stopped its last session, or the delivery between sessions "
        "(the backstop, or the gates' same findings again), if anything did."
    )
    blocked_on: BlockedOn | None
    ceiling: Literal["reading", "re_viewing"] | None
    since_action: int | None = Field(description="Its last session's signals at the end.")
    most_views: int | None
    edits_since_run: int | None
    empty_lookups: int | None
    returned_because: ReturnReason | None
    gaps: list[Gap] = Field(
        description="What its records lacked, built around: a session with no end (a "
        "crash, or still running), or an end with no start. Nothing is guessed."
    )


class Analysis(_Part):
    sprints: list[SprintAnalysis] = Field(description="Every sprint started by the package's date.")
    stories: list[StoryPattern] = Field(description="Every story and bug with events.")
    deliveries: list[Delivery] = Field(
        default_factory=list,
        description="Every delivery in Developer sessions, by story, oldest first.",
    )


class SprintMetrics(_Part):
    """sprint-metrics' answers for every sprint the package covers, as it gave them (ADR 0022).

    sprint-metrics keeps and computes; the crew carries its answers unchanged,
    cut to what its published schema names. Each answer follows that schema at
    `release`, which this one embeds.
    """

    release: str = Field(description="The sprint-metrics release that answered, e.g. 1.3.0.")
    answers: dict[str, dict[str, Any]] = Field(
        description="Each sprint's answer by its name: an entry of sprint-metrics' GET /range, "
        "with the sprint before it as `prior`."
    )


class DeliveryHistory(_Part):
    """The package's index: everything up to its date, so a consumer needs only the latest.

    The events are in one file per period, so a page loads only the sprint it
    shows: the history grows by a sprint's worth each sprint, and a year of it
    in one file would be tens of megabytes (the Sponsor, 2026-10-08).
    """

    package: Literal["delivery-history"] = NAME
    schema_version: str
    date: date
    timezone: str = Field(description="The timezone sprint days are counted in.")
    crew_repository: str
    repositories: list[str] = Field(description="The delivery repositories it covers.")
    sprints: list[Sprint]
    cards: list[Card]
    periods: list[Period] = Field(description="Every period with events, oldest first.")
    analysis: Analysis
    sprint_metrics: SprintMetrics | None = Field(
        default=None,
        description="sprint-metrics' part. None only in a build without the service: "
        "a release always carries it.",
    )


class PeriodEvents(_Part):
    """One period's events, oldest first."""

    package: Literal["delivery-history"] = NAME
    schema_version: str
    sprint: str | None
    start: date
    end: date
    events: list[Event]


class Manifest(_Part):
    """What a release holds, and the schema version it follows."""

    package: Literal["delivery-history"] = NAME
    date: date
    schema_version: str
    generated: datetime
    events: int
    left_out: dict[str, int] = Field(
        description="Events the crew recorded but couldn't place, by why. None are guessed."
    )
    gaps: dict[str, int] = Field(
        default_factory=dict,
        description="What the crew's records lacked and the build went around, by what: "
        "a session with no end, or an end with no start (`Delivery.gaps`).",
    )


# --- building it ---------------------------------------------------------------


@dataclass
class Inputs:
    """What the package is built from. Raw: nothing here leaves as it is."""

    # (the log it came from, the event as recorded), e.g. ("tick", {...}).
    events: list[tuple[str, dict[str, Any]]]
    cards: list[Any]  # tools.github_project.Card
    sprints: list[Sprint]
    # Per repository, every pull request (IssueClient.all_pulls).
    pulls: dict[str, list[dict[str, Any]]]
    crew_repository: str
    repositories: list[str]
    timezone: str
    date: date
    generated: datetime
    # sprint-metrics' answers for the sprints it covers, or None without the service.
    sprint_metrics: SprintMetrics | None = None


# The labels that mark the crew's own design decisions (crew#192): on a technical
# epic, and on the issue a design revision's pull request closes. The flows'
# own constants, restated so the package doesn't import the tick's modules.
TECHNICAL = "technical"
REVISIT_LABEL = "design:revisit"

# Roles whose work events name the pull request they review, not a card.
REVIEWERS = frozenset({"Code Reviewer", "DevOps Engineer"})

# Logs a person wrote by hand: a move recorded there was a person's.
PEOPLES_LOGS = frozenset({"operator", "sponsor"})

_VALUE_ERROR = re.compile(r"Value error, (.+?) \[type=value_error", re.DOTALL)


@dataclass
class _Placer:
    """Which repository an event's card number belongs to, or None. Never a guess.

    Moves name their repository since crew#521, and model calls since #179;
    older events give only a number, which repeats across repositories. So: the
    repository the event names; else the only repository with that number filed
    by then; else the one this number was last seen in. Anything else is left
    out and counted.
    """

    cards: list[Any]
    # Per repository, its pull requests (IssueClient.all_pulls).
    pulls: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    last: dict[int, str] = field(default_factory=dict)

    def pull(
        self, number: int, named: str | None, at: datetime
    ) -> tuple[PullRef, CardRef | None] | None:
        """The pull request a reviewer's number means, and the card it closes.

        Issues and pull requests share one sequence in a repository, so a
        number is one or the other there. Across repositories it's placed only
        when exactly one thing fits: a pull request open at that moment, or a
        card filed by then. Otherwise it's left out.
        """
        fits: list[tuple[str, dict[str, Any] | None]] = []
        for repo, pulls in self.pulls.items():
            if named and repo != named:
                continue
            for p in pulls:
                created = _when(p.get("created_at"))
                if p["number"] != number or created is None:
                    continue
                ended = _when(p.get("merged_at")) or _when(p.get("closed_at"))
                if created - FILED_SLACK <= at and (ended is None or at <= ended + FILED_SLACK):
                    fits.append((repo, p))
        for c in self.cards:
            filed = c.created is None or c.created <= at + FILED_SLACK
            if c.number == number and (not named or c.repo == named) and filed:
                fits.append((c.repo, None))
        if len(fits) != 1:
            return None
        repo, pull = fits[0]
        if pull is None:
            return None
        closes = [CardRef(repo=r, number=n) for r, n in pull.get("closes") or [] if r]
        return PullRef(repo=repo, number=number), (closes[0] if len(closes) == 1 else None)

    def place(self, number: int, named: str | None, at: datetime) -> str | None:
        if named:
            self.last[number] = named
            return named
        filed = {
            c.repo
            for c in self.cards
            if c.number == number
            and c.repo
            and (c.created is None or c.created <= at + FILED_SLACK)
        }
        if len(filed) == 1:
            return str(next(iter(filed)))
        last = self.last.get(number)
        if last in filed:
            return last
        return None


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reason(failure_class: str, detail: dict[str, Any]) -> str | None:
    """The refusal's reason, if the crew's own check wrote it (ADR 0020)."""
    if failure_class == "SCHEMA":
        found = _VALUE_ERROR.search(str(detail.get("error") or ""))
        return published_reason(found.group(1)) if found else None
    if failure_class == "EDIT":
        return published_reason(str(detail.get("error") or ""))
    if failure_class == "BOUNDS":
        reasons = detail.get("reasons") or []
        return published_reason(str(reasons[0])) if reasons else None
    return None


def _sent_back(
    at: datetime,
    where: Any,
    detail: dict[str, Any],
    card: Callable[[], CardRef | None] | None,
    left_out: dict[str, int],
) -> SentBack | None:
    """A send-back, or None, counted in `left_out`, when its card or names can't be read."""
    ref = card() if card is not None else None
    steps = {s.value for s in Step}
    rule = detail.get("rule")
    if (
        (card is not None and ref is None)
        or not (ref is not None or isinstance(where, str))
        or detail.get("sent_from") not in steps
        or detail.get("sent_to") not in steps
        or not (isinstance(rule, str) and _RULE_ID.fullmatch(rule))
    ):
        left_out["sent back: unreadable"] = left_out.get("sent back: unreadable", 0) + 1
        return None
    round_ = detail.get("round")
    given = detail.get("counts")
    counts = given if isinstance(given, dict) else {}
    return SentBack(
        at=at,
        repo=ref.repo if ref is not None else str(where),
        card=ref,
        sent_from=Step(detail["sent_from"]),
        sent_to=Step(detail["sent_to"]),
        rule=rule,
        round=round_ if isinstance(round_, int) and not isinstance(round_, bool) else None,
        counts={
            k: v
            for k, v in counts.items()
            if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        },
    )


def _decision(summary: str) -> Decision | None:
    """The escalation policy's decision, from the summary its code writes: "CLASS — decision"."""
    _, _, tail = summary.partition(" — ")
    value = tail.strip()
    allowed = ("retry_local", "return_to_refinement", "file_prompt_defect", "escalate", "block")
    return value if value in allowed else None  # type: ignore[return-value]


def _event(
    log: str, raw: dict[str, Any], placer: _Placer, left_out: dict[str, int], seen: set[str]
) -> Event | None:
    kind = raw.get("kind")
    at = _when(raw.get("at"))
    if at is None:
        return None
    detail = raw.get("detail") or {}
    role = raw.get("role") or None
    number = raw.get("card")
    # Where it was written, from its context since crew#449; its detail before that.
    where = (raw.get("ctx") or {}).get("repo") or detail.get("repo")

    def card() -> CardRef | None:
        if not isinstance(number, int):
            return None
        repo = placer.place(number, where, at)
        return CardRef(repo=repo, number=number) if repo else None

    def needs_card(what: str) -> CardRef | None:
        ref = card()
        if ref is None:
            left_out[f"{what}: card's repository unknown"] = (
                left_out.get(f"{what}: card's repository unknown", 0) + 1
            )
        return ref

    if kind == "card.moved":
        ref = needs_card("card_moved")
        if ref is None:
            return None
        person = log in PEOPLES_LOGS
        return CardMoved(
            at=at,
            card=ref,
            from_column=detail.get("from"),
            to_column=detail.get("to"),
            by="person" if person else "crew",
            role=None if person else role,
        )
    if kind == "card.seen_moved":
        ref = needs_card("card_moved")
        by = detail.get("by")
        if ref is None or by not in ("person", "platform"):
            return None
        return CardMoved(
            at=at,
            card=ref,
            from_column=detail.get("from"),
            to_column=detail.get("to"),
            by=by,
            role=None,
        )
    if kind in ("agent.started", "agent.finished"):
        if not role:
            return None
        cls = WorkStarted if kind == "agent.started" else WorkFinished
        # A review names its pull request in `pr` and its card as the card (crew#449).
        # Before that, a reviewer's number was the pull request's.
        pr = detail.get("pr") if "pr" in detail else number
        # A role's Task is its card's work, even a reviewer's (crew#839).
        if role in REVIEWERS and isinstance(pr, int) and not detail.get("task_role"):
            # Placed by its pull request, never as a card: a pull request it can't
            # place could share its number with another repository's card.
            reviewed = placer.pull(pr, where, at)
            if reviewed is None:
                left_out["work: pull request unknown"] = (
                    left_out.get("work: pull request unknown", 0) + 1
                )
                return None
            if cls is WorkFinished:
                return WorkFinished(
                    at=at, card=reviewed[1], pull=reviewed[0], role=role, verdict=_verdict(detail)
                )
            return cls(at=at, card=reviewed[1], pull=reviewed[0], role=role)
        ref = card() if isinstance(number, int) else None
        if isinstance(number, int) and ref is None:
            needs_card("work")
            return None
        if cls is WorkFinished:
            return WorkFinished(at=at, card=ref, pull=None, role=role, verdict=_verdict(detail))
        return cls(at=at, card=ref, pull=None, role=role)
    if kind == "escalation.decided":
        ref = needs_card("attempt")
        if ref is None:
            return None
        failure_class = str(detail.get("failure_class") or "")
        if not re.fullmatch(r"[A-Z]+", failure_class):
            return None
        return Attempt(
            at=at,
            card=ref,
            role=role,
            number=_int(detail.get("attempt")),
            failure_class=failure_class,
            decision=_decision(str(raw.get("summary") or "")),
            reason=_reason(failure_class, detail),
        )
    if kind == "card.blocked":
        ref = needs_card("blocked")
        return Blocked(at=at, card=ref, role=role) if ref else None
    if kind == "escalation.sent":
        ref = needs_card("escalated")
        return Escalated(at=at, card=ref, role=role) if ref else None
    if kind in ("llm.finished", "llm.failed"):
        # A failed call was logged twice (Sprint 17's retro); a call is one call.
        call = detail.get("call_id")
        if call:
            key = f"{kind}:{call}"
            if key in seen:
                return None
            seen.add(key)
        ref = card() if isinstance(number, int) else None
        if isinstance(number, int) and ref is None:
            needs_card("model_call")
            return None
        duration = detail.get("duration_s")
        return ModelCall(
            at=at,
            card=ref,
            role=role,
            step=detail.get("for"),
            model=detail.get("model"),
            prompt_tokens=_int(detail.get("prompt_tokens")),
            completion_tokens=_int(detail.get("completion_tokens")),
            reasoning_tokens=_int(detail.get("reasoning_tokens")),
            cached_tokens=_int(detail.get("cached_tokens")),
            seconds=float(duration) if isinstance(duration, (int, float)) else None,
            failed=kind == "llm.failed",
        )
    if kind in _SESSION_KINDS or (kind == "tool.finished" and "result" in detail):
        # A refinement step's session names its step (crew#807). The package carries
        # the Developer's sessions, a story's delivery; a step's isn't one, and is
        # counted as left out until the package has a place for it.
        if detail.get("step"):
            left_out["refinement session"] = left_out.get("refinement session", 0) + 1
            return None
        ref = needs_card("session")
        if ref is None:
            return None
        # A session can crash mid-write, or an older one lack a field: such an event is
        # left out and counted, and the rest is built (the Sponsor, 2026-10-18).
        try:
            return _session_event(kind, at, ref, detail)
        except (ValueError, TypeError):
            left_out[f"{kind}: malformed"] = left_out.get(f"{kind}: malformed", 0) + 1
            return None
    if kind == "gate.checked":
        ref = needs_card("gate")
        if ref is None:
            return None
        if not isinstance(detail.get("passed"), bool):
            left_out["gate.checked: malformed"] = left_out.get("gate.checked: malformed", 0) + 1
            return None
        rules = [
            r for r in _list(detail.get("rules")) if isinstance(r, str) and _RULE_ID.fullmatch(r)
        ]
        return GateChecked(
            at=at,
            card=ref,
            passed=detail["passed"],
            rules=sorted(set(rules)),
            already_done=detail.get("already_done") is True,
        )
    if kind == "work.sent_back":
        return _sent_back(at, where, detail, card if isinstance(number, int) else None, left_out)
    if kind == "story.returned":
        ref = needs_card("returned")
        reason = _one_of(detail.get("reason"), ReturnReason)
        if ref is None or reason is None:
            if ref is not None:
                left_out["returned: reason unknown"] = (
                    left_out.get("returned: reason unknown", 0) + 1
                )
            return None
        return Returned(
            at=at,
            card=ref,
            reason=reason,
            blocked_on=_one_of(detail.get("kind"), BlockedOn),
            stop=_one_of(detail.get("stop"), Stop),
        )
    return None


# The session's own kinds; its tool calls are `tool.finished` with a `result`, which a
# step's read rounds don't write (ADR 0025), and its caller's failures `tool.failed`.
_SESSION_KINDS = frozenset(
    {"session.started", "session.turn", "session.compacted", "session.ended", "tool.failed"}
)
# A rule's id, as the catalog names it (ADR 0031): names and dots, nothing said.
_RULE_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+")


def _one_of(value: Any, allowed: Any) -> Any:
    """`value` if it's one of a Literal's values, else None: a name, never free text."""
    return value if isinstance(value, str) and value in get_args(allowed) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _needed(value: Any) -> int:
    """A count an event can't do without, such as its call's number."""
    found = _int(value)
    if found is None:
        raise ValueError("a required count is missing")
    return found


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _verdict(detail: dict[str, Any]) -> Verdict | None:
    """A review's or QA's verdict, from the flag its event carries."""
    if isinstance(detail.get("approved"), bool):
        return "approved" if detail["approved"] else "changes_requested"
    if isinstance(detail.get("accepted"), bool):
        return "accepted" if detail["accepted"] else "returned"
    return None


def _session_event(kind: str, at: datetime, ref: CardRef, detail: dict[str, Any]) -> Event:
    """A session's event, by its names and counts only (crew#778, P5)."""
    if kind == "session.started":
        sizes = {k: v for k, v in _dict(detail.get("start_chars")).items() if _int(v) is not None}
        return SessionStarted(
            at=at, card=ref, start_chars=sizes, rework="instead_of_hints" in sizes
        )
    if kind == "session.turn":
        return SessionTurn(
            at=at,
            card=ref,
            turn=_needed(detail.get("turn")),
            tool_calls=_int(detail.get("tool_calls")),
            since_action=_int(detail.get("since_action")),
            most_views=_int(detail.get("most_views")),
            edits_since_run=_int(detail.get("edits_since_run")),
            empty_lookups=_int(detail.get("empty_lookups")),
            reasoning_tokens=_int(detail.get("reasoning_tokens")),
            model_seconds=_seconds(detail.get("model_s")),
        )
    if kind == "session.compacted":
        return SessionCompacted(
            at=at,
            card=ref,
            turn=_needed(detail.get("turn")),
            estimated_tokens=_int(detail.get("estimated_tokens")),
            turns_masked=_int(detail.get("turns_masked")),
        )
    if kind == "session.ended":
        said = _dict(detail.get("said"))
        evidence = _dict(detail.get("evidence"))
        signals = _dict(evidence.get("signals"))
        stopped = _dict(evidence.get("stop"))
        how = _one_of(detail.get("how"), How)
        if how is None:
            raise ValueError("a session's end says how it ended")
        done = how == "done"
        return SessionEnded(
            at=at,
            card=ref,
            how=how,
            blocked_on=_one_of(said.get("kind"), BlockedOn) if how == "blocked" else None,
            stop=_one_of(detail.get("stop"), Stop),
            ceiling=_one_of(stopped.get("ceiling"), Literal["reading", "re_viewing"]),
            why=_one_of(detail.get("why"), Literal["call_failed", "workspace_gone"]),
            model_calls=_int(detail.get("model_calls")),
            model_seconds=_seconds(detail.get("model_s")),
            prompt_tokens=_int(detail.get("prompt_tokens")),
            cached_tokens=_int(detail.get("cached_tokens")),
            completion_tokens=_int(detail.get("completion_tokens")),
            reasoning_tokens=_int(detail.get("reasoning_tokens")),
            edits=_int(detail.get("edits_applied")),
            runs=_int(detail.get("test_runs")),
            criteria=len(_list(said.get("criteria"))) if done else None,
            changed_tests=len(_list(said.get("changed_tests"))) if done else None,
            since_action=_int(signals.get("since_action")),
            most_views=_int(signals.get("most_views")),
            edits_since_run=_int(signals.get("edits_since_run")),
            empty_lookups=_int(signals.get("empty_lookups_total")),
        )
    rule = detail.get("rule")
    codes = [
        code
        for c in _list(detail.get("commands"))
        if isinstance(c, dict) and (code := _int(c.get("code"))) is not None
    ]
    result: ToolResult | None = (
        "error" if kind == "tool.failed" else _one_of(detail.get("result"), ToolResult)
    )
    if result is None:
        raise ValueError("a tool call says how it went")
    return ToolCall(
        at=at,
        card=ref,
        turn=_needed(detail.get("turn")),
        tool=_one_of(detail.get("tool"), Tool),
        result=result,
        rule=rule if isinstance(rule, str) and _RULE_ID.fullmatch(rule) else None,
        found_nothing=detail.get("found_nothing") is True,
        argument_chars=_int(detail.get("args_chars")),
        reply_chars=_int(detail.get("reply_chars")),
        seconds=_seconds(detail.get("duration_s")),
        run=_one_of(detail.get("run"), Literal["tests", "suite", "lint"]),
        exit_codes=codes if "commands" in detail else None,
    )


def _by(at: datetime, inputs: Inputs) -> bool:
    """Whether `at` falls on or before the package's date, counted in its timezone."""
    return at.astimezone(ZoneInfo(inputs.timezone)).date() <= inputs.date


def _citations(inputs: Inputs) -> dict[int, list[CardRef]]:
    """Each crew issue's cited cards, the latest recorded (crew#449). Only qualified names."""
    out: dict[int, list[CardRef]] = {}
    for _, raw in sorted(inputs.events, key=lambda pair: str(pair[1].get("at") or "")):
        detail = raw.get("detail") or {}
        if raw.get("kind") != "issue.cites" or detail.get("repo") != inputs.crew_repository:
            continue
        if not isinstance(raw.get("card"), int):
            continue
        refs = []
        for name in detail.get("cites") or []:
            repo, _, number = str(name).partition("#")
            if repo and number.isdigit():
                refs.append(CardRef(repo=repo, number=int(number)))
        out[raw["card"]] = refs
    return out


def _pull_events(inputs: Inputs) -> list[PullMerged]:
    out: list[PullMerged] = []
    cites = _citations(inputs)
    for repo, pulls in inputs.pulls.items():
        for pull in pulls:
            # Every pull request is listed; only a merge is an event here.
            at = _when(pull.get("merged_at"))
            if at is None or not _by(at, inputs):
                continue
            out.append(
                PullMerged(
                    at=at,
                    repo=repo,
                    number=pull["number"],
                    title=pull.get("title") or "",
                    closes=[
                        CardRef(repo=r, number=n)
                        for r, n in pull.get("closes") or []
                        if r and isinstance(n, int)
                    ],
                    crews_own=repo == inputs.crew_repository,
                    design_revision=REVISIT_LABEL in (pull.get("closes_labels") or []),
                    about=list(
                        dict.fromkeys(
                            ref
                            for r, n in pull.get("closes") or []
                            if r == inputs.crew_repository
                            for ref in cites.get(n, [])
                        )
                    ),
                )
            )
    return out


def _closed_as(card: Any) -> Literal["completed", "not_planned", "duplicate"] | None:
    """GitHub's reason for a closed issue. An open one, or one reopened, has none."""
    reason = (card.state_reason or "").lower()
    if card.state != "CLOSED" or reason not in ("completed", "not_planned", "duplicate"):
        return None
    return reason  # type: ignore[return-value]


# A re-split's reason as the tick records it, and as the package names it.
_RESPLIT_BECAUSE: dict[str, Literal["sponsor", "story_problem"]] = {
    "sponsor": "sponsor",
    "story problem": "story_problem",
}


def _superseded_because(
    inputs: Inputs,
) -> dict[tuple[str, int], Literal["sponsor", "story_problem"]]:
    """Why each superseded story was, by (repo, number): the last re-split that closed it."""
    out: dict[tuple[str, int], Literal["sponsor", "story_problem"]] = {}
    # A Goal's epics superseded by its rework, as an epic's stories by its re-split.
    resplits = (e for _, e in inputs.events if e.get("kind") in ("epic.resplit", "goal.reworked"))
    for event in sorted(resplits, key=lambda e: str(e.get("at") or "")):
        detail = event.get("detail") or {}
        because = _RESPLIT_BECAUSE.get(str(detail.get("because") or ""))
        repo = detail.get("repo")
        if because is None or not repo:
            continue
        for number in detail.get("superseded") or []:
            if isinstance(number, int):
                out[(repo, number)] = because
    return out


def _cards(inputs: Inputs) -> list[Card]:
    because = _superseded_because(inputs)
    out = []
    for c in inputs.cards:
        if not (c.repo and c.number):
            continue
        out.append(
            Card(
                repo=c.repo,
                number=c.number,
                type=c.work_type if c.work_type in ("Goal", "Epic", "Story", "Bug") else None,
                title=c.title,
                url=c.url,
                parent=c.parent,
                points=c.points,
                sprint=c.sprint,
                status=c.status,
                status_set=c.status_set,
                technical=TECHNICAL in (getattr(c, "labels", None) or ()),
                created=c.created,
                closed=c.closed,
                closed_as=_closed_as(c),
                superseded_because=(
                    because.get((c.repo, c.number)) if _closed_as(c) == "not_planned" else None
                ),
            )
        )
    return sorted(out, key=lambda c: (c.repo, c.number))


def _periods(
    events: list[Event], sprints: list[Sprint], zone: ZoneInfo
) -> list[tuple[str | None, date, date, list[Event]]]:
    """Events grouped by the sprint their day falls in, or by a run of days between sprints."""
    by_day: dict[date, list[Event]] = {}
    for event in events:
        by_day.setdefault(event.at.astimezone(zone).date(), []).append(event)

    def sprint_on(day: date) -> Sprint | None:
        return next((s for s in sprints if s.start <= day <= s.end), None)

    out: list[tuple[str | None, date, date, list[Event]]] = []
    for day in sorted(by_day):
        sprint = sprint_on(day)
        if sprint is not None:
            if out and out[-1][0] == sprint.name:
                out[-1][3].extend(by_day[day])
            else:
                out.append((sprint.name, sprint.start, sprint.end, list(by_day[day])))
            continue
        last = out[-1] if out else None
        if last is not None and last[0] is None and last[2] == day - timedelta(days=1):
            out[-1] = (None, last[1], day, last[3] + by_day[day])
        else:
            out.append((None, day, day, list(by_day[day])))
    return out


def build(inputs: Inputs) -> tuple[DeliveryHistory, dict[str, PeriodEvents], Manifest]:
    """The package's index, its events files by path, and its manifest.

    From the crew's events, the board and GitHub.
    """
    placer = _Placer(cards=list(inputs.cards), pulls=inputs.pulls)
    left_out: dict[str, int] = {}
    seen: set[str] = set()
    raw = sorted(inputs.events, key=lambda pair: str(pair[1].get("at") or ""))
    events: list[Event] = []
    for log, event in raw:
        at = _when(event.get("at"))
        if at is None or not _by(at, inputs):
            continue
        built = _event(log, event, placer, left_out, seen)
        if built is not None:
            events.append(built)
    events += _pull_events(inputs)
    events.sort(key=lambda e: e.at)
    sprints = sorted(inputs.sprints, key=lambda s: s.start)
    files: dict[str, PeriodEvents] = {}
    periods: list[Period] = []
    for sprint, start, end, grouped in _periods(events, sprints, ZoneInfo(inputs.timezone)):
        path = f"events/{start.isoformat()}.json"
        files[path] = PeriodEvents(
            schema_version=SCHEMA_VERSION, sprint=sprint, start=start, end=end, events=grouped
        )
        periods.append(Period(sprint=sprint, start=start, end=end, file=path, events=len(grouped)))
    # Imported here: the analysis reads this module's models.
    from crew_org.packages.analysis import analyse  # noqa: PLC0415

    cards = _cards(inputs)
    history = DeliveryHistory(
        schema_version=SCHEMA_VERSION,
        date=inputs.date,
        timezone=inputs.timezone,
        crew_repository=inputs.crew_repository,
        repositories=sorted(inputs.repositories),
        sprints=sprints,
        cards=cards,
        periods=periods,
        analysis=analyse(
            events,
            cards,
            sprints,
            answers=inputs.sprint_metrics.answers if inputs.sprint_metrics else {},
            crew_repository=inputs.crew_repository,
            zone=ZoneInfo(inputs.timezone),
            day=inputs.date,
        ),
        sprint_metrics=inputs.sprint_metrics,
    )
    manifest = Manifest(
        date=inputs.date,
        schema_version=SCHEMA_VERSION,
        generated=inputs.generated,
        events=len(events),
        left_out=dict(sorted(left_out.items())),
        gaps=dict(sorted(Counter(g for d in history.analysis.deliveries for g in d.gaps).items())),
    )
    return history, files, manifest


def sprints_from(iterations: Iterable[dict[str, Any]]) -> list[Sprint]:
    """The board's iterations as sprints: each runs `duration` days from its start."""
    out = []
    for it in iterations:
        start = date.fromisoformat(it["startDate"])
        out.append(
            Sprint(name=it["title"], start=start, end=start + timedelta(days=it["duration"] - 1))
        )
    return out
