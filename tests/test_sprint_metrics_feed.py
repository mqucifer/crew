"""The crew's history as sprint-metrics' events (crew#280)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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
        analysis=dh.Analysis(sprints=[], stories=[]),
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


def test_goals_and_epics_send_nothing():
    found = smf.feed(
        _history([_card(1, type_="Epic"), _card(2, type_="Goal")]),
        [_period([_moved(1, 0, "Ready", "Done"), _moved(2, 0, "Inbox (Goals)", "Done")])],
    )
    assert found.events == [] and not found.left_out


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
    assert not found.left_out


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
