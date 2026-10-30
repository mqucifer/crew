"""The crew's own analysis in delivery-history, written by code from the data (crew#521).

What changed, what needs attention and a story's pattern, as crew-presentation's
design reference shows them. Every sentence is a template with measures and names
filled in (ADR 0020, ADR 0022).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from crew_org.packages import analysis
from crew_org.packages import delivery_history as dh

ZONE = ZoneInfo("America/Chicago")
DAY = date(2026, 10, 11)
# 01:00 in Chicago on DAY.
T0 = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
SPRINT = dh.Sprint(name="Sprint 22", start=DAY, end=DAY)
BEFORE = dh.Sprint(name="Sprint 21", start=DAY - timedelta(days=1), end=DAY - timedelta(days=1))
REASON = "no definition named DEFAULT_THRESHOLDS"


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def card(
    number,
    *,
    type_="Story",
    status="Done",
    status_set=None,
    closed_as=None,
    closed=None,
    technical=False,
    created=None,
):
    return dh.Card(
        repo="sm",
        number=number,
        type=type_,
        title=f"card {number}",
        url=None,
        parent=None,
        points=3,
        sprint=SPRINT.name,
        status=status,
        status_set=status_set,
        technical=technical,
        created=created or T0 - timedelta(days=2),
        closed=closed,
        closed_as=closed_as,
    )


def ref(number: int) -> dh.CardRef:
    return dh.CardRef(repo="sm", number=number)


def moved(number, minutes, from_, to) -> dh.CardMoved:
    return dh.CardMoved(
        at=at(minutes), card=ref(number), from_column=from_, to_column=to, by="crew", role=None
    )


def attempt(number, minutes, n, failure="EDIT", reason=None) -> dh.Attempt:
    return dh.Attempt(
        at=at(minutes),
        card=ref(number),
        role="Developer",
        number=n,
        failure_class=failure,
        decision="retry_local",
        reason=reason,
    )


def call(number, minutes, *, prompt=1000, completion=200, reasoning=150, seconds=30.0):
    return dh.ModelCall(
        at=at(minutes),
        card=ref(number),
        role="Developer",
        step="deliver",
        model="crew-code-think",
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        seconds=seconds,
        failed=False,
    )


def crew_fix(number, minutes, about=(127,)) -> dh.PullMerged:
    return dh.PullMerged(
        at=at(minutes),
        repo="crew",
        number=number,
        title="fix",
        closes=[],
        crews_own=True,
        design_revision=False,
        about=[ref(n) for n in about],
    )


def analyse(events, cards, *, sprints=(SPRINT,), answers=None) -> dh.Analysis:
    return analysis.analyse(
        sorted(events, key=lambda e: e.at),
        cards,
        list(sprints),
        answers=answers or {},
        crew_repository="crew",
        zone=ZONE,
        day=DAY,
    )


def story(result: dh.Analysis, number: int) -> dh.StoryPattern:
    return next(s for s in result.stories if s.card.number == number)


# --- a story's pattern ----------------------------------------------------------------


def test_a_story_delivered_with_no_miss_landed_first_try():
    events = [
        dh.WorkStarted(at=at(0), card=ref(1), pull=None, role="Developer"),
        call(1, 5),
        moved(1, 20, "In Progress", "Reviewing"),
        moved(1, 40, "Merging", "Done"),
    ]
    found = story(analyse(events, [card(1, closed_as="completed")]), 1)
    assert found.pattern == "first_try"
    assert found.text == "Landed first try."
    assert found.delivered == at(40)
    assert found.wall_clock_seconds == 40 * 60


def test_a_crew_fix_merging_between_its_misses_and_delivery_is_its_pattern():
    """The story-journey board's pattern: refused, the crew fixed itself, then it landed."""
    events = [
        attempt(127, 0, 1, reason=REASON),
        attempt(127, 2, 2, reason=REASON),
        attempt(127, 4, 3, reason=REASON),
        moved(127, 6, "In Progress", "Blocked"),
        crew_fix(208, 15),
        crew_fix(209, 24),
        # Close in time, but its issue doesn't cite this story.
        crew_fix(211, 25, about=(128,)),
        moved(127, 30, "Merging", "Done"),
        # Merged after it landed: not between.
        crew_fix(210, 50),
    ]
    found = story(analyse(events, [card(127, closed_as="completed")]), 127)
    assert found.pattern == "crew_fix"
    assert [f.number for f in found.crew_fixes] == [208, 209]
    assert (found.misses, found.blocks) == (3, 1)
    assert found.repeated == dh.Repeated(failure_class="EDIT", reason=REASON, count=3)
    assert found.text == (
        "Missed 3 times and was blocked once, then landed after the crew's own fixes "
        "crew#208 and crew#209 merged. It was refused 3 times with the same reason."
    )


def test_an_escalated_story_and_a_retried_one_say_so():
    events = [
        attempt(1, 0, 1, failure="TEST"),
        dh.Escalated(at=at(1), card=ref(1), role="Developer"),
        moved(1, 10, "Merging", "Done"),
        attempt(2, 0, 1, failure="TEST"),
        attempt(2, 2, 2, failure="LINT"),
        moved(2, 10, "Merging", "Done"),
    ]
    result = analyse(events, [card(1), card(2)])
    assert story(result, 1).pattern == "escalated"
    assert story(result, 1).text == "Missed once, then landed after it was escalated."
    assert story(result, 2).pattern == "retried"
    assert story(result, 2).text == "Missed twice, then landed."
    # A failure class alone isn't the same failure: no reason, no repeat.
    assert story(result, 2).repeated is None


def test_a_superseded_story_was_never_built_though_it_moved_to_done():
    events = [attempt(5, 0, 1), moved(5, 10, "Ready", "Done")]
    found = story(analyse(events, [card(5, closed_as="not_planned")]), 5)
    assert (found.pattern, found.delivered) == ("superseded", None)
    assert found.text == "Superseded: closed as not planned, never built."


def test_an_open_story_is_blocked_or_not_delivered_yet():
    events = [attempt(6, 0, 1), moved(6, 2, "In Progress", "Blocked"), call(7, 0)]
    result = analyse(events, [card(6, status="Blocked"), card(7, status="In Progress")])
    assert story(result, 6).text == "Blocked now; it missed once and was blocked once first."
    assert story(result, 7).pattern == "open"
    assert story(result, 7).text == "Not delivered yet."
    assert story(result, 7).wall_clock_seconds is None
    # No call placed to it: its cost is unknown, not nothing.
    assert story(result, 6).cost is None


def test_a_storys_cost_is_its_calls_tokens_and_time():
    events = [call(1, 0, seconds=12.25), call(1, 5, prompt=500, reasoning=None, seconds=7.5)]
    cost = story(analyse(events, [card(1, status="In Progress")]), 1).cost
    assert cost == dh.StoryCost(
        model_calls=2,
        prompt_tokens=1500,
        completion_tokens=400,
        reasoning_tokens=150,
        model_seconds=19.8,
    )


def test_goals_epics_and_cards_with_no_events_have_no_pattern():
    events = [moved(10, 0, "Inbox (Goals)", "Needs Refinement")]
    result = analyse(events, [card(10, type_="Epic"), card(11)])
    assert result.stories == []


# --- what needs attention ------------------------------------------------------------------


def test_an_epic_at_the_gate_and_a_blocked_story_at_the_sprints_end_need_attention():
    cards = [
        card(62, type_="Epic", status="Inbox (Goals)", status_set=at(-60 * 24)),
        card(127, status="Blocked"),
        # Not known to be anywhere at the sprint's end: said nothing about.
        card(130, status="Blocked", status_set=None),
    ]
    events = [moved(127, 10, "In Progress", "Blocked")]
    [sprint] = analyse(events, cards).sprints
    assert sprint.as_of == datetime(2026, 10, 11, 23, 59, 59, 999999, tzinfo=ZONE)
    assert [(a.kind, a.card.number) for a in sprint.attention] == [
        ("waiting_on_sponsor", 62),
        ("blocked", 127),
    ]
    waiting, blocked = sprint.attention
    assert waiting.text == "sm#62 is waiting for the Sponsor's approval, for 1 day."
    assert blocked.since == at(10)
    assert blocked.text == "sm#127 is blocked, for 22 hours."


def test_a_card_that_left_the_column_or_closed_by_the_sprints_end_doesnt():
    cards = [
        card(127, status="Done", status_set=at(30)),
        card(62, type_="Epic", status="Inbox (Goals)", closed=at(5), closed_as="completed"),
    ]
    events = [moved(127, 10, "In Progress", "Blocked"), moved(127, 20, "Blocked", "In Progress")]
    [sprint] = analyse(events, cards).sprints
    assert sprint.attention == []


def test_a_column_before_any_record_is_where_the_first_move_left_from():
    cards = [card(62, type_="Epic", status="Needs Refinement", status_set=None)]
    later = SPRINT.end + timedelta(days=1)
    events = [moved(62, 60 * 24, "Inbox (Goals)", "Needs Refinement")]
    result = analysis.analyse(
        events,
        cards,
        [SPRINT, dh.Sprint(name="Sprint 23", start=later, end=later)],
        answers={},
        crew_repository="crew",
        zone=ZONE,
        day=later,
    )
    first = result.sprints[0]
    [waiting] = first.attention
    assert (waiting.kind, waiting.since) == ("waiting_on_sponsor", None)
    assert waiting.text == "sm#62 is waiting for the Sponsor's approval."


def test_the_same_written_reason_three_times_in_a_sprint_needs_attention():
    events = [
        attempt(127, 0, 1, reason=REASON),
        attempt(127, 2, 2, reason=REASON),
        attempt(127, 4, 3, reason=REASON),
        attempt(128, 0, 1, failure="TEST"),
        attempt(128, 2, 2, failure="TEST"),
        attempt(128, 4, 3, failure="TEST"),
    ]
    [sprint] = analyse(events, [card(127), card(128)]).sprints
    [repeated] = sprint.attention
    assert (repeated.kind, repeated.card.number, repeated.count) == ("repeated_refusal", 127, 3)
    assert repeated.reason == REASON
    assert repeated.text == "sm#127 was refused 3 times with the same reason."


# --- what changed --------------------------------------------------------------------------


def test_what_changed_is_sprint_metrics_numbers_worded_by_the_crew():
    answers = {
        SPRINT.name: {
            "first_attempt_rate_percent": 55,
            "throughput": 15,
            "escalation_count": 1,
            "points_delivered": 40,
            "wip_violations": None,
            "prior": {
                "first_attempt_rate_percent": 40,
                "throughput": 22,
                "escalation_count": 2,
                "points_delivered": 40,
                "wip_violations": 3,
            },
        }
    }
    [sprint] = analyse([], [], answers=answers).sprints
    # sprint-metrics stopped answering escalations in 4.0.0: one still sent isn't worded.
    assert [(c.measure, c.previous, c.now, c.better) for c in sprint.changed] == [
        ("first_attempt_rate_percent", 40, 55, True),
        ("throughput", 22, 15, False),
    ]
    assert [c.text for c in sprint.changed] == [
        "The first-try rate rose from 40% to 55%.",
        "Stories delivered fell from 22 to 15.",
    ]
    assert {c.source for c in sprint.changed} == {"sprint-metrics"}


def test_times_are_worded_in_days_hours_and_minutes_and_superseding_is_neither():
    answers = {
        SPRINT.name: {
            "cycle_time_minutes": 655,
            "lead_time_minutes": 2939,
            "superseded_count": 2,
            "prior": {"cycle_time_minutes": 410, "lead_time_minutes": 2880, "superseded_count": 0},
        }
    }
    [sprint] = analyse([], [], answers=answers).sprints
    # 2 days and 59 minutes against 2 days: too small a move to say, so unsaid.
    assert [(c.measure, c.better, c.text) for c in sprint.changed] == [
        (
            "cycle_time_minutes",
            False,
            "The time from starting a story to delivering it rose from 6 hours 50 minutes "
            "to 10 hours 55 minutes.",
        ),
        ("superseded_count", None, "Stories superseded rose from 0 to 2."),
    ]


def test_fixes_to_the_crew_are_the_crews_own_measure_against_the_sprint_before():
    events = [crew_fix(1, -60 * 24), crew_fix(2, 0), crew_fix(3, 5), crew_fix(4, 10)]
    first, second = analyse(events, [], sprints=(BEFORE, SPRINT)).sprints
    assert first.changed == []
    [change] = second.changed
    assert (change.source, change.previous, change.now, change.better) == ("crew", 1, 3, None)
    assert change.text == "Fixes to the crew merged rose from 1 to 3."


def test_a_sprint_not_started_by_the_packages_date_isnt_analysed():
    later = dh.Sprint(name="Sprint 23", start=DAY + timedelta(days=1), end=DAY + timedelta(days=1))
    result = analyse([], [], sprints=(SPRINT, later))
    assert [s.sprint for s in result.sprints] == [SPRINT.name]


# --- decided by the crew -------------------------------------------------------------------


def test_a_design_revision_merged_and_a_technical_epic_filed_are_the_crews_decisions():
    revision = dh.PullMerged(
        at=at(5),
        repo="sm",
        number=122,
        title="chore: revisit this project's design",
        closes=[ref(121)],
        crews_own=False,
        design_revision=True,
        about=[],
    )
    cards = [
        card(123, type_="Epic", technical=True, created=at(10)),
        # Approved by the Sponsor, not the crew's call; and a technical story is its epic's.
        card(59, type_="Epic", created=at(1)),
        card(124, technical=True, created=at(11)),
        # Filed the sprint before.
        card(100, type_="Epic", technical=True, created=at(-60 * 24)),
    ]
    [sprint] = analyse([revision], cards).sprints
    assert [(d.kind, d.number) for d in sprint.decided] == [
        ("design_revision_merged", 122),
        ("technical_epic_filed", 123),
    ]
    assert [d.text for d in sprint.decided] == [
        "The crew merged sm#122, the Architect's revision of the project's design.",
        "The crew filed sm#123 as technical work, which skips the Sponsor's gate.",
    ]
