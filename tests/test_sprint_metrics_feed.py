"""The crew's history as sprint-metrics' events (crew#280)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import get_args

import pytest

from crew_org import sendbacks
from crew_org.packages import delivery_history as dh
from crew_org.packages import sprint_metrics_feed as smf

DAY = date(2026, 10, 11)
T0 = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)


def _card(
    number: int,
    *,
    type_: str = "Story",
    closed_as: str | None = "completed",
    because: str | None = None,
) -> dh.Card:
    return dh.Card(
        repo="sm",
        number=number,
        type=type_,  # type: ignore[arg-type]
        title=f"card {number}",
        url=None,
        parent=None,
        points=3,
        sprint="Sprint 22",
        status="Done",
        status_set=None,
        technical=False,
        created=T0 - timedelta(hours=5),
        closed=None,
        closed_as=closed_as,  # type: ignore[arg-type]
        superseded_because=because,  # type: ignore[arg-type]
    )


def _ref(number: int) -> dh.CardRef:
    return dh.CardRef(repo="sm", number=number)


def _moved(number: int, minutes: int, from_: str | None, to: str) -> dh.CardMoved:
    return dh.CardMoved(
        at=T0 + timedelta(minutes=minutes),
        card=_ref(number),
        from_column=from_,
        to_column=to,
        by="crew",
        role="Developer",
    )


def _attempt(number: int, minutes: int, n: int, failure: str) -> dh.Attempt:
    return dh.Attempt(
        at=T0 + timedelta(minutes=minutes),
        card=_ref(number),
        role="Developer",
        number=n,
        failure_class=failure,
        decision="retry_local",
        reason=None,
    )


def _history(cards: list[dh.Card]) -> dh.DeliveryHistory:
    return dh.DeliveryHistory(
        schema_version=dh.SCHEMA_VERSION,
        date=DAY,
        timezone="America/Chicago",
        crew_repository="crew",
        repositories=["sm"],
        sprints=[dh.Sprint(name="Sprint 22", start=DAY, end=DAY)],
        cards=cards,
        periods=[],
    )


def _period(events: list, sprint: str | None = "Sprint 22") -> dh.PeriodEvents:
    return dh.PeriodEvents(
        schema_version=dh.SCHEMA_VERSION, sprint=sprint, start=DAY, end=DAY, events=events
    )


def test_a_story_becomes_started_attempts_and_finished_with_its_first_miss():
    found = smf.feed(
        _history([_card(1)]),
        [
            _period(
                [
                    _moved(1, 0, "Sprint Backlog", "In Progress"),
                    _attempt(1, 10, 1, "VERIFY"),
                    _attempt(1, 20, 2, "SCHEMA"),
                    _moved(1, 30, "Merging", "Done"),
                ]
            )
        ],
    )
    assert found.sprints == [
        {
            "name": "Sprint 22",
            "start_date": "2026-10-11",
            "end_date": "2026-10-11",
            "timezone": "America/Chicago",
        }
    ]
    assert [e["type"] for e in found.events] == [
        "started",
        "attempt_failed",
        "attempt_failed",
        "finished",
    ]
    first, finished = found.events[1], found.events[-1]
    assert first["card_id"] == "sm#1" and first["sprint"] == "Sprint 22"
    assert (first["attempt_number"], first["failure_class"], first["failure_role"]) == (
        1,
        "VERIFY",
        "Developer",
    )
    assert finished["card"] == {
        "created": "2026-10-11",
        "created_at": "2026-10-11T01:00:00+00:00",
        "points": 3,
        "failure_class": "VERIFY",
        "failure_role": "Developer",
    }
    assert finished["api_version"] == "1"


def test_finished_carries_no_count_of_attempts_sprint_metrics_counts_them():
    """The crew logs what happened; sprint-metrics decides what first try means (crew#963)."""
    found = smf.feed(_history([_card(1)]), [_period([_moved(1, 30, "Merging", "Done")])])
    assert found.events[0]["card"] == {
        "created": "2026-10-11",
        "created_at": "2026-10-11T01:00:00+00:00",
        "points": 3,
    }


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _session(number: int, minutes: float) -> dh.SessionStarted:
    return dh.SessionStarted(
        at=_at(minutes), card=_ref(number), start_chars={"total": 1}, rework=False
    )


def _sent_back(number: int, minutes: float, back: sendbacks.SendBack) -> dh.SentBack:
    return dh.SentBack(
        at=_at(minutes),
        repo="sm",
        card=_ref(number),
        sent_from=back.sent_from,
        sent_to=back.sent_to,
        rule=str(back.rule),
        round=1,
        counts={},
    )


def _returns(found: smf.Feed) -> list[tuple]:
    return [
        (e["failure_class"], e.get("failure_role"), "attempt_number" in e)
        for e in found.events
        if e["type"] == "attempt_failed"
    ]


def test_every_return_of_a_sessions_story_goes_as_a_miss_by_its_step():
    """CI failing and a conflict rebuild are returns too: sprint-metrics can't count what
    it isn't sent (crew#963)."""
    events = [
        _session(1, 0),
        _moved(1, 1, "In Progress", "Reviewing"),
        _sent_back(1, 2, sendbacks.CI_BEFORE_REVIEW),
        _moved(1, 3, "In Progress", "Reviewing"),
        _sent_back(1, 4, sendbacks.MERGE_CONFLICT),
        _sent_back(1, 5, sendbacks.MERGE_CI_FAILED),
        _sent_back(1, 6, sendbacks.MERGE_CHANGES_REQUESTED),
        _sent_back(1, 7, sendbacks.DEPLOY_REVIEW_CHANGES),
        _sent_back(1, 8, sendbacks.QA_UNPROVEN),
        _moved(1, 9, "Merging", "Done"),
        _sent_back(1, 10, sendbacks.REVERTED),
    ]
    found = smf.feed(_history([_card(1)]), [_period(events)])
    assert _returns(found) == [
        ("CI", None, False),
        ("CONFLICT", None, False),
        ("CI", None, False),
        ("REVIEW", None, False),
        ("REVIEW", "DevOps Engineer", False),
        ("QA", "QA Engineer", False),
        ("REVERT", None, False),
    ]
    (finished,) = [e for e in found.events if e["type"] == "finished"]
    assert "attempts" not in finished["card"]
    assert finished["card"]["failure_class"] == "CI"


def test_the_gates_and_the_developers_own_send_backs_arent_returns():
    """The gate's before review (the Sponsor, 2026-10-18), and the Developer's own stop,
    which the crew counts for its blocks."""
    events = [
        _session(1, 0),
        _sent_back(1, 1, sendbacks.GATE_FINDINGS),
        _sent_back(1, 2, sendbacks.CONTRACT_REPAIR),
        _sent_back(1, 3, sendbacks.SESSION_BLOCKED),
        _sent_back(1, 4, sendbacks.ROUND_TRIPS),
        _moved(1, 5, "Merging", "Done"),
    ]
    assert _returns(smf.feed(_history([_card(1)]), [_period(events)])) == []


def test_a_reviews_verdict_and_its_send_back_are_one_return():
    """Each send-back writes its event beside the verdict (crew#862): one return, sent once."""
    verdict = dh.WorkFinished(
        at=_at(2), card=_ref(1), pull=None, role="Code Reviewer", verdict="changes_requested"
    )
    events = [
        _session(1, 0),
        _moved(1, 1, "In Progress", "Reviewing"),
        verdict,
        _sent_back(1, 2.5, sendbacks.REVIEW_CHANGES),
        _moved(1, 3, "Reviewing", "In Progress"),
        # Asked again an hour later: a second return.
        _sent_back(1, 60, sendbacks.REVIEW_CHANGES),
        _moved(1, 90, "Merging", "Done"),
    ]
    found = smf.feed(_history([_card(1)]), [_period(events)])
    assert _returns(found) == [
        ("REVIEW", "Code Reviewer", False),
        ("REVIEW", "Code Reviewer", False),
    ]


def test_a_block_and_the_move_to_blocked_are_one_block_and_leaving_it_unblocks():
    blocked = dh.Blocked(at=T0 + timedelta(minutes=5), card=_ref(1), role="Developer")
    found = smf.feed(
        _history([_card(1)]),
        [
            _period(
                [
                    blocked,
                    _moved(1, 6, "In Progress", "Blocked"),
                    _moved(1, 60, "Blocked", "Sprint Backlog"),
                ]
            )
        ],
    )
    assert [e["type"] for e in found.events] == ["blocked", "unblocked"]


def test_the_moves_of_goals_and_epics_send_nothing():
    """`_card`'s date is the evening before Sprint 22, so these send no `filed` either."""
    found = smf.feed(
        _history([_card(1, type_="Epic"), _card(2, type_="Goal")]),
        [_period([_moved(1, 0, "Ready", "Done"), _moved(2, 0, "Inbox (Goals)", "Done")])],
    )
    assert found.events == []
    assert found.left_out == {"card filed outside every sprint": 2}


def test_what_cant_be_placed_is_left_out_and_counted_never_guessed():
    found = smf.feed(
        _history([_card(1, closed_as="not_planned")]),
        [
            _period([_moved(1, 0, "Ready", "Done"), _moved(9, 0, "Ready", "In Progress")]),
            _period([_moved(1, 0, "Sprint Backlog", "In Progress")], sprint=None),
        ],
    )
    assert found.events == []
    assert found.left_out == {
        "superseded with no reason recorded": 1,
        "card not in the history": 1,
        "between sprints": 1,
        "card filed outside every sprint": 1,
    }


def test_a_superseded_story_is_sent_as_superseded_with_why():
    """sprint-metrics 1.3.0's event (crew#680): not finished, and no replaced_by."""
    found = smf.feed(
        _history([_card(1, closed_as="not_planned", because="story_problem")]),
        [_period([_moved(1, 0, "Ready", "Done")])],
    )
    assert found.events == [
        {
            "api_version": "1",
            "card_id": "sm#1",
            "type": "superseded",
            "timestamp": (T0).isoformat(),
            "sprint": "Sprint 22",
            "card": {
                "created": "2026-10-11",
                "created_at": "2026-10-11T01:00:00+00:00",
                "points": 3,
            },
            "because": "story_problem",
        }
    ]
    assert found.left_out == {"card filed outside every sprint": 1}


def test_push_sends_sprints_first_and_keeps_going_past_a_refusal():
    found = smf.feed(
        _history([_card(1)]),
        [
            _period(
                [_moved(1, 0, "Sprint Backlog", "In Progress"), _moved(1, 30, "Merging", "Done")]
            )
        ],
    )
    sent: list[str] = []

    def send(path: str, body: dict) -> tuple[int, str]:
        sent.append(body.get("type", path))
        return (400, '{"error": "nope"}') if body.get("type") == "started" else (200, "{}")

    pushed = smf.push(found, send)
    assert sent == ["/sprints", "started", "finished"]
    assert pushed.sprints == {200: 1} and pushed.events == {400: 1, 200: 1}
    assert pushed.reasons == {"started 400": 'sm#1: {"error": "nope"}'}


# --- how each card was filed (crew#985) -------------------------------------------------


FILED_AT = T0 + timedelta(hours=1)


def _filed_card(
    number: int = 1,
    *,
    repo: str = "sm",
    type_: str | None = "Story",
    created: datetime | None = FILED_AT,
    sprint: str | None = "Sprint 22",
    **filed,
) -> dh.Card:
    return _card(number).model_copy(
        update={
            "repo": repo,
            "type": type_,
            "created": created,
            "sprint": sprint,
            "filed": dh.Filed(**filed),
        }
    )


def _filed_events(found: smf.Feed) -> list[dict]:
    return [e for e in found.events if e["type"] == "filed"]


def test_a_card_sends_one_filed_event_of_facts_at_the_time_it_was_filed():
    card = _filed_card(by="crew", activity="task", kind="story-test", for_project="sprint-metrics")
    found = smf.feed(_history([card]), [])
    assert _filed_events(found) == [
        {
            "api_version": "1",
            "card_id": "sm#1",
            "type": "filed",
            "timestamp": FILED_AT.isoformat(),
            "sprint": "Sprint 22",
            "card": {"created": "2026-10-11", "created_at": FILED_AT.isoformat()},
            "filer": "crew",
            "opener": "process",
            "project": "sprint-metrics",
        }
    ]


@pytest.mark.parametrize(
    ("activity", "opener"),
    [
        ("finding", "found"),
        ("split", "split"),
        ("task", "process"),
        ("technical_epic", "process"),
        ("proposed_goal", "process"),
        ("skeleton_goal", "process"),
        ("record_change", "process"),
        ("retro", "process"),
        ("standup", "process"),
    ],
)
def test_what_opened_a_card_is_found_split_or_the_crews_own_process(activity, opener):
    found = smf.feed(_history([_filed_card(by="crew", activity=activity)]), [])
    assert _filed_events(found)[0]["opener"] == opener


def test_every_activity_the_package_names_has_an_opener():
    assert set(smf._OPENER) == set(get_args(dh.FiledActivity))
    assert set(smf._OPENER.values()) == {"found", "process", "split"}


@pytest.mark.parametrize("by", ["crew", "sponsor", "operator", "other"])
def test_who_filed_a_card_is_sent_as_the_package_says(by):
    found = smf.feed(_history([_filed_card(by=by)]), [])
    assert _filed_events(found)[0]["filer"] == by


def test_what_isnt_known_is_left_out_of_the_event_never_guessed():
    event = _filed_events(smf.feed(_history([_filed_card(by="unknown")]), []))[0]
    assert not {"filer", "opener", "project"} & set(event)


def test_a_crew_card_with_nothing_written_says_who_filed_it_and_no_opener():
    event = _filed_events(smf.feed(_history([_filed_card(by="crew")]), []))[0]
    assert event["filer"] == "crew"
    assert "opener" not in event


def test_a_filed_event_carries_no_points():
    """Points aren't a filing fact, and the service reads a superseded card's from its first row."""
    event = _filed_events(smf.feed(_history([_filed_card(by="crew")]), []))[0]
    assert set(event["card"]) == {"created", "created_at"}


@pytest.mark.parametrize("type_", ["Goal", "Epic", "Story", "Bug", None])
def test_every_card_is_filed_whatever_its_type(type_):
    """A QA Task has no type in the package and a Goal is not delivered, but both were filed."""
    found = smf.feed(_history([_filed_card(type_=type_, by="crew", activity="task")]), [])
    assert [e["card_id"] for e in _filed_events(found)] == ["sm#1"]


def test_a_card_counts_in_the_sprint_it_was_filed_in_not_the_one_it_was_planned_for():
    history = _history([_filed_card(by="crew", sprint="Sprint 23")]).model_copy(
        update={
            "sprints": [
                dh.Sprint(name="Sprint 22", start=DAY, end=DAY),
                dh.Sprint(
                    name="Sprint 23", start=DAY + timedelta(days=1), end=DAY + timedelta(days=1)
                ),
            ]
        }
    )
    assert _filed_events(smf.feed(history, []))[0]["sprint"] == "Sprint 22"


def test_the_day_a_card_was_filed_is_the_packages_timezones():
    """03:00 UTC on the 12th is still the 11th in Chicago, so still Sprint 22."""
    late = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
    found = smf.feed(_history([_filed_card(created=late, by="crew")]), [])
    assert _filed_events(found)[0]["sprint"] == "Sprint 22"


def test_a_card_filed_outside_every_sprint_is_left_out_and_counted():
    between = DAY + timedelta(days=9)
    created = datetime(between.year, between.month, between.day, 12, tzinfo=UTC)
    found = smf.feed(_history([_filed_card(created=created, by="crew")]), [])
    assert _filed_events(found) == []
    assert found.left_out == {"card filed outside every sprint": 1}


def test_a_card_with_no_filed_date_is_left_out_and_counted():
    found = smf.feed(_history([_filed_card(created=None, by="crew")]), [])
    assert _filed_events(found) == []
    assert found.left_out == {"card with no created date": 1}


def test_the_same_number_in_two_repositories_is_two_cards():
    cards = [_filed_card(7, repo="qa-suite", by="crew"), _filed_card(7, repo="sm", by="crew")]
    found = smf.feed(_history(cards), [])
    assert [e["card_id"] for e in _filed_events(found)] == ["qa-suite#7", "sm#7"]


def test_a_filed_event_goes_among_the_others_oldest_first():
    card = _filed_card(by="crew")
    found = smf.feed(
        _history([card]),
        [_period([_moved(1, 30, "Ready", "In Progress"), _moved(1, 120, "In Progress", "Done")])],
    )
    assert [e["type"] for e in found.events] == ["started", "filed", "finished"]


def test_filed_events_add_nothing_to_a_story_s_other_events():
    period = _period([_moved(1, 30, "Ready", "In Progress"), _moved(1, 120, "In Progress", "Done")])
    before = smf.feed(_history([_card(1)]), [period])
    after = smf.feed(_history([_filed_card(by="crew")]), [period])
    others = [e for e in after.events if e["type"] != "filed"]
    assert [(e["type"], e["timestamp"]) for e in others] == [
        (e["type"], e["timestamp"]) for e in before.events
    ]


def test_a_second_push_of_a_filed_event_is_the_same_event():
    """The service stores an event once by card, type and time, so the time can't move."""
    card = _filed_card(by="crew", activity="finding")
    stored: dict[tuple, dict] = {}

    def send(path, body):
        if path == "/events":
            stored.setdefault((body["card_id"], body["type"], body["timestamp"]), body)
        return 200, ""

    smf.push(smf.feed(_history([card]), []), send)
    smf.push(smf.feed(_history([card]), []), send)
    assert len(stored) == 1


def test_a_cut_to_some_sprints_keeps_only_their_filed_events():
    history = _history([_filed_card(by="crew")])
    found = smf.feed(history, [])
    assert _filed_events(smf.only(found, ["Sprint 22"])) == _filed_events(found)
    assert _filed_events(smf.only(found, ["Sprint 23"])) == []
