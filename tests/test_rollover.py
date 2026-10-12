"""A sprint closes itself once its time is up, once, at a tick's start or end (ADR 0028)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from crew_org.flows import rollover
from crew_org.flows.retro import marker

SPRINTS = [
    {"title": "Sprint 22", "startDate": "2026-10-11", "duration": 1},
    {"title": "Sprint 23", "startDate": "2026-10-12", "duration": 1},
]


class Board:
    """`sprints()` is every sprint the board has, finished ones included."""

    def __init__(self, cards):
        self._cards = cards

    def sprints(self):
        return SPRINTS

    def cards(self):
        return self._cards


class Issues:
    def __init__(self, retros=()):
        self.retros = [{"number": 9, "body": marker(s)} for s in retros]

    def labelled(self, repo, label):
        return self.retros


def card(sprint):
    return SimpleNamespace(sprint=sprint, work_type="Story")


def due(board, issues, sprint, today):
    return rollover.due(board, issues, sprint=sprint, today=today, crew_repo="crew")


def test_a_sprint_past_its_last_day_with_work_and_no_retro_is_due():
    assert due(Board([card("Sprint 22")]), Issues(), "Sprint 22", date(2026, 10, 12))


def test_a_sprint_on_its_last_day_is_not_due():
    assert not due(Board([card("Sprint 22")]), Issues(), "Sprint 22", date(2026, 10, 11))


def test_a_sprint_already_closed_is_not_closed_again():
    """Whatever closes it first records the retro: a tick, or `crew sprint close`."""
    closed = Issues(retros=["Sprint 22"])
    assert not due(Board([card("Sprint 22")]), closed, "Sprint 22", date(2026, 10, 12))


def test_a_sprint_with_no_cards_has_nothing_to_close():
    assert not due(Board([card("Sprint 23")]), Issues(), "Sprint 22", date(2026, 10, 12))


def test_no_sprint_is_never_due():
    assert not due(Board([]), Issues(), None, date(2026, 10, 12))


def test_the_last_ended_sprint_is_the_one_whose_last_day_is_past():
    assert rollover.last_ended(SPRINTS, date(2026, 10, 11)) is None, "Sprint 22's day"
    assert rollover.last_ended(SPRINTS, date(2026, 10, 12)) == "Sprint 22"
    assert rollover.last_ended(SPRINTS, date(2026, 10, 30)) == "Sprint 23"
