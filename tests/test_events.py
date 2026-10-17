from __future__ import annotations

from crew_org.events import CrewEvent, EventKind, EventSink


def test_emitted_events_reach_subscribers(tmp_path):
    sink = EventSink(tmp_path / "events.jsonl")
    seen: list[CrewEvent] = []
    sink.subscribe(seen.append)
    sink.note(EventKind.TICK_STARTED, "tick 1")
    assert [e.kind for e in seen] == [EventKind.TICK_STARTED]


def test_events_persist_and_replay_in_order(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = EventSink(path)
    sink.note(EventKind.TICK_STARTED, "tick 1")
    sink.note(EventKind.CARD_CLAIMED, "card 7", card=7)
    sink.note(EventKind.TICK_FINISHED, "done")

    replayed = EventSink(path).replay()
    assert [e.kind for e in replayed] == [
        EventKind.TICK_STARTED,
        EventKind.CARD_CLAIMED,
        EventKind.TICK_FINISHED,
    ]
    assert replayed[1].detail["card"] == 7


def test_a_broken_subscriber_cannot_kill_the_run(tmp_path):
    sink = EventSink(tmp_path / "events.jsonl")
    survived: list[CrewEvent] = []

    def explodes(_e: CrewEvent) -> None:
        raise RuntimeError("the live view crashed")

    sink.subscribe(explodes)
    sink.subscribe(survived.append)
    sink.note(EventKind.NOTE, "still fine")  # must not raise
    assert len(survived) == 1


def test_sink_without_a_path_keeps_no_record(tmp_path):
    sink = EventSink(None)
    sink.note(EventKind.NOTE, "ephemeral")
    assert sink.replay() == []


def test_a_notes_card_is_the_events_card():
    """crew#756: a hold note kept its card only in its detail, so a count by card missed it."""
    from crew_org.events import EventKind, EventSink

    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    sink.note(EventKind.NOTE, "#7 waits for #6 in the same epic", card=7, repo="sprint-metrics")
    sink.note(EventKind.NOTE, "no card here")
    assert (seen[0].card, seen[0].detail["card"]) == (7, 7)
    assert seen[1].card is None


def test_a_tick_names_the_holds_no_event_names_once_each():
    """crew#756: a hold or skip that writes no event is listed on the tick's end."""
    from crew_org.events import held, held_so_far, holding

    held("hold.sibling", repo="sm", card=7)  # outside a tick: nowhere to go, and no error
    token = holding()
    held("hold.sibling", repo="sm", card=7)
    held("hold.sibling", repo="sm", card=7)
    held("decision.merge.merge_approved", repo="sm", card=9)
    assert held_so_far(token) == [
        {"rule": "hold.sibling", "repo": "sm", "card": 7},
        {"rule": "decision.merge.merge_approved", "repo": "sm", "card": 9},
    ]


def test_a_hold_is_noted_once_per_card_per_sprint(tmp_path):
    """crew#756: hold.sibling was noted on every pass, 333 notes on 55 cards in a month."""
    from crew_org.events import EventSink, first_hold

    sink = EventSink(tmp_path / "tick.jsonl")
    once = dict(rule="hold.sibling", repo="sm", card=7)
    assert first_hold(sink, sprint="Sprint 30", **once)
    assert not first_hold(sink, sprint="Sprint 30", **once)
    assert first_hold(sink, sprint="Sprint 30", **{**once, "card": 8})
    assert first_hold(sink, sprint="Sprint 31", **once)
    assert first_hold(EventSink(None), sprint="Sprint 30", **once)
    assert first_hold(EventSink(None), sprint="Sprint 30", **once)
