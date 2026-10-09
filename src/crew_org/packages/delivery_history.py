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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from crew_org.packages.reasons import published_reason

NAME: Literal["delivery-history"] = "delivery-history"
# MAJOR when a consumer could break on the change, MINOR when it only adds.
SCHEMA_VERSION = "1.1.0"

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
    type: Literal["Goal", "Epic", "Story"] | None
    title: str
    url: str | None
    parent: int | None = Field(description="The parent card's number, in the same repository.")
    points: float | None
    sprint: str | None
    status: str | None
    created: datetime | None
    closed: datetime | None
    closed_as: Literal["completed", "not_planned", "duplicate"] | None = Field(
        description="How the issue closed. A story closed as not planned was superseded: "
        "never built, though the board shows it Done."
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


class WorkFinished(_Part):
    kind: Literal["work_finished"] = "work_finished"
    at: datetime
    card: CardRef | None
    pull: PullRef | None
    role: str


Decision = Literal["retry_local", "return_to_refinement", "file_prompt_defect", "escalate", "block"]


class Attempt(_Part):
    """An attempt that didn't land, and what the crew decided to do about it.

    `reason` is present only when one of the crew's own checks wrote it with
    nothing but names filled in (ADR 0020); otherwise the failure class stands
    alone.
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
    """An attempt sent to the escalation model, which the sprint's budget pays for."""

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


Event = Annotated[
    CardMoved | WorkStarted | WorkFinished | Attempt | Blocked | Escalated | ModelCall | PullMerged,
    Field(discriminator="kind"),
]


class Period(_Part):
    """Days whose events share a file: a sprint, or a run of days between sprints."""

    sprint: str | None = Field(description="The sprint's name; none for days between sprints.")
    start: date
    end: date = Field(description="The period's last day, inclusive.")
    file: str = Field(description="Its events file, relative to the package's root.")
    events: int


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

    def card() -> CardRef | None:
        if not isinstance(number, int):
            return None
        repo = placer.place(number, detail.get("repo"), at)
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
        if role in REVIEWERS and isinstance(number, int):
            # A reviewer's number is a pull request's. Never placed as a card: a
            # pull request it can't place could share its number with another
            # repository's card.
            reviewed = placer.pull(number, detail.get("repo"), at)
            if reviewed is None:
                left_out["work: pull request unknown"] = (
                    left_out.get("work: pull request unknown", 0) + 1
                )
                return None
            return cls(at=at, card=reviewed[1], pull=reviewed[0], role=role)
        ref = card() if isinstance(number, int) else None
        if isinstance(number, int) and ref is None:
            needs_card("work")
            return None
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
            seconds=float(duration) if isinstance(duration, (int, float)) else None,
            failed=kind == "llm.failed",
        )
    return None


def _by(at: datetime, inputs: Inputs) -> bool:
    """Whether `at` falls on or before the package's date, counted in its timezone."""
    return at.astimezone(ZoneInfo(inputs.timezone)).date() <= inputs.date


def _pull_events(inputs: Inputs) -> list[PullMerged]:
    out: list[PullMerged] = []
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
                )
            )
    return out


def _closed_as(card: Any) -> Literal["completed", "not_planned", "duplicate"] | None:
    """GitHub's reason for a closed issue. An open one, or one reopened, has none."""
    reason = (card.state_reason or "").lower()
    if card.state != "CLOSED" or reason not in ("completed", "not_planned", "duplicate"):
        return None
    return reason  # type: ignore[return-value]


def _cards(inputs: Inputs) -> list[Card]:
    out = []
    for c in inputs.cards:
        if not (c.repo and c.number):
            continue
        out.append(
            Card(
                repo=c.repo,
                number=c.number,
                type=c.work_type if c.work_type in ("Goal", "Epic", "Story") else None,
                title=c.title,
                url=c.url,
                parent=c.parent,
                points=c.points,
                sprint=c.sprint,
                status=c.status,
                created=c.created,
                closed=c.closed,
                closed_as=_closed_as(c),
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
    history = DeliveryHistory(
        schema_version=SCHEMA_VERSION,
        date=inputs.date,
        timezone=inputs.timezone,
        crew_repository=inputs.crew_repository,
        repositories=sorted(inputs.repositories),
        sprints=sprints,
        cards=_cards(inputs),
        periods=periods,
    )
    manifest = Manifest(
        date=inputs.date,
        schema_version=SCHEMA_VERSION,
        generated=inputs.generated,
        events=len(events),
        left_out=dict(sorted(left_out.items())),
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
