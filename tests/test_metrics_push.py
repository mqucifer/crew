"""Each pass pushes to sprint-metrics, and a push that fails goes again (crew#280)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from crew_org.events import EventSink
from crew_org.flows import metrics_push as mp
from crew_org.packages import sprint_metrics_feed as smf

TODAY = date(2026, 10, 12)
SPRINTS = [
    SimpleNamespace(name="Sprint 21", start=date(2026, 10, 10), end=date(2026, 10, 10)),
    SimpleNamespace(name="Sprint 22", start=date(2026, 10, 11), end=date(2026, 10, 11)),
    SimpleNamespace(name="Sprint 23", start=date(2026, 10, 12), end=date(2026, 10, 12)),
    SimpleNamespace(name="Sprint 24", start=date(2026, 10, 13), end=date(2026, 10, 13)),
]


def event(sprint, n):
    return {"card_id": f"sm#{n}", "type": "started", "sprint": sprint}


FEED = smf.Feed(
    sprints=[{"name": s.name} for s in SPRINTS],
    events=[event("Sprint 21", 1), event("Sprint 22", 2), event("Sprint 23", 3)],
)


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """Each push's feed, in order; the service refuses while `refuse` holds a reason."""
    pushes: list[smf.Feed] = []
    refuse: dict[str, str] = {}

    def push_to(url, found):
        pushes.append(found)
        return smf.Pushed(reasons=dict(refuse))

    history = SimpleNamespace(sprints=SPRINTS)
    monkeypatch.setattr(mp, "history_now", lambda *a, **k: (history, {}, None))
    monkeypatch.setattr(mp.smf, "feed", lambda h, p: FEED)
    monkeypatch.setattr(mp.smf, "push_to", push_to)
    monkeypatch.setattr(mp, "sprint_today", lambda org: TODAY)
    return pushes, refuse


def crew(url="http://127.0.0.1:8080"):
    org = {"sprint_metrics": {"url": url}} if url else {}
    return SimpleNamespace(org=org, board=None, issues=None, crew_repo="crew", sink=EventSink(None))


def names(found):
    return [s["name"] for s in found.sprints], [e["card_id"] for e in found.events]


def test_without_an_address_nothing_is_pushed(sent, tmp_path):
    pushes, _ = sent
    assert mp.push_after_pass(crew(url=None), var=tmp_path) is None
    assert pushes == []


def test_the_first_push_sends_every_sprint_begun_and_marks_the_ended_ones(sent, tmp_path):
    pushes, _ = sent
    mp.push_after_pass(crew(), var=tmp_path)
    assert names(pushes[0]) == (["Sprint 21", "Sprint 22", "Sprint 23"], ["sm#1", "sm#2", "sm#3"])
    state = json.loads((tmp_path / "sprint-metrics" / mp.STATE).read_text())
    assert state["sprints"] == ["Sprint 21", "Sprint 22"], "the sprint under way isn't done"


def test_later_passes_send_only_the_sprint_under_way(sent, tmp_path):
    pushes, _ = sent
    mp.push_after_pass(crew(), var=tmp_path)
    mp.push_after_pass(crew(), var=tmp_path)
    assert names(pushes[1]) == (["Sprint 23"], ["sm#3"])


def test_a_refused_push_is_sent_again_by_the_next_pass(sent, tmp_path):
    pushes, refuse = sent
    refuse["started 500"] = "sm#2: the database is down"
    mp.push_after_pass(crew(), var=tmp_path)
    assert not (tmp_path / "sprint-metrics" / mp.STATE).exists()
    refuse.clear()
    mp.push_after_pass(crew(), var=tmp_path)
    assert names(pushes[1])[0] == ["Sprint 21", "Sprint 22", "Sprint 23"]


def test_a_service_that_cant_be_reached_raises_for_the_loop_to_note(sent, monkeypatch, tmp_path):
    def down(url, found):
        raise ConnectionError("refused")

    monkeypatch.setattr(mp.smf, "push_to", down)
    with pytest.raises(ConnectionError):
        mp.push_after_pass(crew(), var=tmp_path)
    assert not (tmp_path / "sprint-metrics" / mp.STATE).exists()


def test_a_cut_feed_keeps_only_the_sprints_named():
    cut = smf.only(FEED, ["Sprint 22"])
    assert names(cut) == (["Sprint 22"], ["sm#2"])
