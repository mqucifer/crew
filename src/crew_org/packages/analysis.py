"""The crew's own analysis in delivery-history: written by code from the data, never a model.

ADR 0022 makes the analysis the crew's part, and ADR 0020 keeps a model's words
out of the package. So every sentence here is a fixed template with measures,
names and card numbers filled in: template-based data-to-text, after Reiter and
Dale's pipeline (choose the facts, order them, word them). A fact the data
doesn't hold is left unsaid, never guessed.

Three parts, as crew-presentation's design reference shows them (crew#521):
- **what changed:** each measure that moved since the sprint before.
  sprint-metrics' measures are read from its own answer, `prior` against now:
  it computes, the crew picks and words. The crew's own fixes merged are the
  crew's measure.
- **what needs attention,** as each sprint stood at its end: epics waiting at
  the Sponsor's gate, stories in Blocked, and a story refused `REPEATS` times or
  more with the same reason.
- **decided by the crew:** the Architect's design revisions the crew merged,
  and the technical epics it filed, from a design or a failing `main`, which
  skip the Sponsor's gate (crew#192, crew#335).
- **a story's pattern:** one per story or bug, the facts behind it, and what it
  cost.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from crew_org import columns
from crew_org.packages import delivery_history as dh
from crew_org.work_types import DELIVERED, EPIC

# Refused this many times with the same reason in a sprint, a story needs attention.
REPEATS = 3

# sprint-metrics' measures the crew words, in the order it says them: what it
# calls each, and whether a rise is for the better.
MEASURES: dict[dh.Measure, tuple[str, bool]] = {
    "first_attempt_rate_percent": ("the first-try rate", True),
    "throughput": ("stories delivered", True),
    "points_delivered": ("points delivered", True),
    "escalation_count": ("escalations", False),
    "blocked_aging_days": ("days spent blocked", False),
    "wip_violations": ("breaks of the work-in-progress limit", False),
}


def analyse(
    events: list[dh.Event],
    cards: list[dh.Card],
    sprints: list[dh.Sprint],
    *,
    answers: dict[str, dict[str, Any]],
    crew_repository: str,
    zone: ZoneInfo,
    day: date,
) -> dh.Analysis:
    """The analysis of everything up to `day`, from the package's own events and cards."""
    started = [s for s in sorted(sprints, key=lambda s: s.start) if s.start <= day]
    out = []
    for i, sprint in enumerate(started):
        before = started[i - 1] if i else None
        as_of = _end_of(min(sprint.end, day), zone)
        out.append(
            dh.SprintAnalysis(
                sprint=sprint.name,
                as_of=as_of,
                changed=_changed(sprint, before, answers, events, zone),
                attention=_attention(sprint, as_of, events, cards, zone),
                decided=_decided(sprint, events, cards, zone),
            )
        )
    return dh.Analysis(sprints=out, stories=_stories(events, cards))


# --- what changed --------------------------------------------------------------------


def _changed(
    sprint: dh.Sprint,
    before: dh.Sprint | None,
    answers: dict[str, dict[str, Any]],
    events: list[dh.Event],
    zone: ZoneInfo,
) -> list[dh.Change]:
    out: list[dh.Change] = []
    answer = answers.get(sprint.name) or {}
    prior = answer.get("prior") or {}
    for measure, (name, rise_is_better) in MEASURES.items():
        now, previous = _number(answer.get(measure)), _number(prior.get(measure))
        if now is None or previous is None or now == previous:
            continue
        unit = "%" if measure.endswith("_percent") else ""
        out.append(
            dh.Change(
                measure=measure,
                source="sprint-metrics",
                previous=previous,
                now=now,
                better=(now > previous) == rise_is_better,
                text=_moved(name, previous, now, unit),
            )
        )
    if before is not None:
        now_fixes = _fixes_in(sprint, events, zone)
        previous_fixes = _fixes_in(before, events, zone)
        if now_fixes != previous_fixes:
            out.append(
                dh.Change(
                    measure="crew_fixes_merged",
                    source="crew",
                    previous=previous_fixes,
                    now=now_fixes,
                    # More fixes to the crew is neither: it found more, or broke more.
                    better=None,
                    text=_moved("fixes to the crew merged", previous_fixes, now_fixes, ""),
                )
            )
    return out


def _moved(name: str, previous: float, now: float, unit: str) -> str:
    way = "rose" if now > previous else "fell"
    return f"{_capital(name)} {way} from {_figure(previous)}{unit} to {_figure(now)}{unit}."


def _fixes_in(sprint: dh.Sprint, events: list[dh.Event], zone: ZoneInfo) -> int:
    return sum(
        1
        for e in events
        if isinstance(e, dh.PullMerged) and e.crews_own and _within(e.at, sprint, zone)
    )


# --- what needs attention ----------------------------------------------------------------


def _attention(
    sprint: dh.Sprint,
    as_of: datetime,
    events: list[dh.Event],
    cards: list[dh.Card],
    zone: ZoneInfo,
) -> list[dh.Attention]:
    moves = _moves_by_card(events)
    waiting: list[dh.Attention] = []
    blocked: list[dh.Attention] = []
    for card in cards:
        if card.created and card.created > as_of or card.closed and card.closed <= as_of:
            continue
        key = (card.repo, card.number)
        column, since = _column_at(card, moves.get(key, []), as_of)
        ref = dh.CardRef(repo=card.repo, number=card.number)
        for_how_long = f", for {_span(as_of - since)}" if since else ""
        if card.type == EPIC and column == columns.INBOX:
            text = f"{_name(ref)} is waiting for the Sponsor's approval{for_how_long}."
            waiting.append(_attend("waiting_on_sponsor", ref, since, text))
        elif card.type in DELIVERED and column == columns.BLOCKED:
            text = f"{_name(ref)} is blocked{for_how_long}."
            blocked.append(_attend("blocked", ref, since, text))
    return waiting + blocked + _repeated_refusals(sprint, events, zone)


def _attend(kind: Any, ref: dh.CardRef, since: datetime | None, text: str) -> dh.Attention:
    return dh.Attention(kind=kind, card=ref, since=since, count=None, reason=None, text=text)


def _column_at(
    card: dh.Card, moves: list[dh.CardMoved], at: datetime
) -> tuple[str | None, datetime | None]:
    """The card's column at `at`, and since when; (None, None) when the data doesn't say.

    The latest move by then, or the status the board last set by then. Failing
    both, the column the first move after it left from, with no "since".
    """
    facts = [(m.at, m.to_column) for m in moves]
    if card.status_set and card.status:
        facts.append((card.status_set, card.status))
    by_then = [f for f in facts if f[0] <= at]
    if by_then:
        since, column = max(by_then, key=lambda f: f[0])
        return column, since
    later = sorted((m for m in moves if m.at > at), key=lambda m: m.at)
    return (later[0].from_column, None) if later else (None, None)


def _repeated_refusals(
    sprint: dh.Sprint, events: list[dh.Event], zone: ZoneInfo
) -> list[dh.Attention]:
    """A story refused `REPEATS` times or more in the sprint, for the same written reason.

    Only a reason the crew's check wrote counts: the same failure class alone
    could be different failures.
    """
    seen: dict[tuple[str, int, str], list[datetime]] = {}
    for e in events:
        if isinstance(e, dh.Attempt) and e.reason and _within(e.at, sprint, zone):
            seen.setdefault((e.card.repo, e.card.number, e.reason), []).append(e.at)
    out = []
    for (repo, number, reason), times in sorted(seen.items()):
        if len(times) < REPEATS:
            continue
        ref = dh.CardRef(repo=repo, number=number)
        out.append(
            dh.Attention(
                kind="repeated_refusal",
                card=ref,
                since=min(times),
                count=len(times),
                reason=reason,
                text=f"{_name(ref)} was refused {len(times)} times with the same reason.",
            )
        )
    return out


# --- decided by the crew ----------------------------------------------------------------


def _decided(
    sprint: dh.Sprint, events: list[dh.Event], cards: list[dh.Card], zone: ZoneInfo
) -> list[dh.Decided]:
    out = [
        dh.Decided(
            kind="design_revision_merged",
            at=e.at,
            repo=e.repo,
            number=e.number,
            text=f"The crew merged {e.repo}#{e.number}, the Architect's revision of the "
            "project's design.",
        )
        for e in events
        if isinstance(e, dh.PullMerged) and e.design_revision and _within(e.at, sprint, zone)
    ]
    out += [
        dh.Decided(
            kind="technical_epic_filed",
            at=c.created,
            repo=c.repo,
            number=c.number,
            text=f"The crew filed {c.repo}#{c.number} as technical work, which skips the "
            "Sponsor's gate.",
        )
        for c in cards
        if c.type == EPIC and c.technical and c.created and _within(c.created, sprint, zone)
    ]
    return sorted(out, key=lambda d: (d.at, d.repo, d.number))


# --- a story's pattern ---------------------------------------------------------------------


def _stories(events: list[dh.Event], cards: list[dh.Card]) -> list[dh.StoryPattern]:
    by_card: dict[tuple[str, int], list[dh.Event]] = {}
    for e in events:
        ref = getattr(e, "card", None)
        if isinstance(ref, dh.CardRef):
            by_card.setdefault((ref.repo, ref.number), []).append(e)
    fixes = [e for e in events if isinstance(e, dh.PullMerged) and e.crews_own]
    out = []
    for card in cards:
        mine = by_card.get((card.repo, card.number))
        if card.type in DELIVERED and mine:
            out.append(_story(card, mine, fixes))
    return out


def _story(card: dh.Card, events: list[dh.Event], fixes: list[dh.PullMerged]) -> dh.StoryPattern:
    moves = [e for e in events if isinstance(e, dh.CardMoved)]
    attempts = [e for e in events if isinstance(e, dh.Attempt)]
    calls = [e for e in events if isinstance(e, dh.ModelCall)]
    blocks = [m for m in moves if m.to_column == columns.BLOCKED]
    escalations = sum(1 for e in events if isinstance(e, dh.Escalated))
    superseded = card.closed_as == "not_planned"
    delivered = None
    if not superseded:
        done = [m.at for m in moves if m.to_column == columns.DONE]
        delivered = min(done) if done else card.closed if card.closed_as == "completed" else None
    trouble = [a.at for a in attempts] + [b.at for b in blocks]
    me = dh.CardRef(repo=card.repo, number=card.number)
    crew_fixes = [
        dh.PullRef(repo=f.repo, number=f.number)
        for f in fixes
        if me in f.about and trouble and delivered and min(trouble) < f.at <= delivered
    ]
    if superseded:
        pattern: dh.Pattern = "superseded"
    elif delivered and not trouble:
        pattern = "first_try"
    elif delivered and crew_fixes and not escalations:
        pattern = "crew_fix"
    elif delivered and escalations:
        pattern = "escalated"
    elif delivered:
        pattern = "retried"
    elif card.status == columns.BLOCKED:
        pattern = "blocked"
    else:
        pattern = "open"
    reasons = Counter((a.failure_class, a.reason) for a in attempts if a.reason)
    repeated = None
    if reasons:
        (failure_class, reason), count = reasons.most_common(1)[0]
        if count > 1:
            repeated = dh.Repeated(failure_class=failure_class, reason=str(reason), count=count)
    worked = [e.at for e in events if isinstance(e, dh.WorkStarted | dh.ModelCall)]
    cost = (
        None
        if not calls
        else dh.StoryCost(
            model_calls=len(calls),
            prompt_tokens=sum(c.prompt_tokens or 0 for c in calls),
            completion_tokens=sum(c.completion_tokens or 0 for c in calls),
            reasoning_tokens=sum(c.reasoning_tokens or 0 for c in calls),
            model_seconds=round(sum(c.seconds or 0.0 for c in calls), 1),
        )
    )
    return dh.StoryPattern(
        card=dh.CardRef(repo=card.repo, number=card.number),
        pattern=pattern,
        misses=len(attempts),
        blocks=len(blocks),
        escalations=escalations,
        crew_fixes=crew_fixes,
        repeated=repeated,
        delivered=delivered,
        wall_clock_seconds=(
            (delivered - min(worked)).total_seconds()
            if delivered and worked and min(worked) <= delivered
            else None
        ),
        cost=cost,
        text=_story_text(pattern, len(attempts), len(blocks), crew_fixes, repeated),
    )


def _story_text(
    pattern: dh.Pattern,
    misses: int,
    blocks: int,
    crew_fixes: list[dh.PullRef],
    repeated: dh.Repeated | None,
) -> str:
    parts = []
    if misses:
        parts.append(f"missed {_times(misses)}")
    if blocks:
        parts.append(f"was blocked {_times(blocks)}")
    trouble = " and ".join(parts)
    if pattern == "superseded":
        text = "Superseded: closed as not planned, never built."
    elif pattern == "first_try":
        text = "Landed first try."
    elif pattern == "crew_fix":
        fixes = "fix" if len(crew_fixes) == 1 else "fixes"
        names = _join([_name(f) for f in crew_fixes])
        text = f"{_capital(trouble)}, then landed after the crew's own {fixes} {names} merged."
    elif pattern == "escalated":
        text = f"{_capital(trouble)}, then landed after it was escalated."
    elif pattern == "retried":
        text = f"{_capital(trouble)}, then landed."
    elif pattern == "blocked":
        text = f"Blocked now; it {trouble} first." if trouble else "Blocked now."
    else:
        text = f"Not delivered yet; it {trouble} so far." if trouble else "Not delivered yet."
    if repeated and repeated.count >= REPEATS:
        text += f" It was refused {repeated.count} times with the same reason."
    return text


# --- wording and time ----------------------------------------------------------------------


def _moves_by_card(events: Iterable[dh.Event]) -> dict[tuple[str, int], list[dh.CardMoved]]:
    out: dict[tuple[str, int], list[dh.CardMoved]] = {}
    for e in events:
        if isinstance(e, dh.CardMoved):
            out.setdefault((e.card.repo, e.card.number), []).append(e)
    return out


def _end_of(day: date, zone: ZoneInfo) -> datetime:
    """The last moment of `day` in `zone`."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone) - timedelta(
        microseconds=1
    )


def _within(at: datetime, sprint: dh.Sprint, zone: ZoneInfo) -> bool:
    return sprint.start <= at.astimezone(zone).date() <= sprint.end


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _figure(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _name(ref: dh.CardRef | dh.PullRef) -> str:
    return f"{ref.repo}#{ref.number}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _span(gap: timedelta) -> str:
    """A wait in the largest whole unit: days, then hours, then minutes."""
    seconds = max(int(gap.total_seconds()), 0)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return "under a minute"
