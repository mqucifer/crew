"""A sprint's close releases the delivery-history package (crew#724), up to that sprint (crew#738).

Nothing released one: `delivery-history-2026-10-13` stayed the only package
through two sprint closes, so crew-presentation's site showed nothing newer.
"""

from __future__ import annotations

from datetime import date

import pytest

from crew_org import cli
from crew_org.flows.rollover import last_end
from crew_org.packages import release
from crew_org.packages.delivery_history import Sprint
from crew_org.packages.gather import covered

SPRINTS = [{"title": "Sprint 25", "startDate": "2026-10-14", "duration": 1}]
ENDED = date(2026, 10, 14)


def test_a_package_is_due_until_one_is_released_after_the_sprint_ended():
    ended = date(2026, 10, 14)
    assert release.due([], ended)
    assert release.due(["delivery-history-2026-10-13", "v1.3.1"], ended)
    assert release.due(["delivery-history-2026-10-14"], ended), "released on its last day"
    assert not release.due(["delivery-history-2026-10-15-2"], ended)


class Issues:
    def __init__(self, tags, retro=12):
        self.tags = tags
        self.retro = retro
        self.comments: list[tuple[str, int, str]] = []

    def releases(self, repo):
        return [{"tag_name": t} for t in self.tags]

    def comment(self, repo, number, body):
        self.comments.append((repo, number, body))


class Board:
    def sprints(self):
        return SPRINTS


@pytest.fixture
def rolled(monkeypatch):
    """`_roll_over` with the close and the release stood in, recording what ran."""
    ran: list[str] = []
    state = {"due": True, "closed": False}

    def close(*a, **k):
        ran.append("close")
        state["closed"] = True

    monkeypatch.setattr("crew_org.flows.rollover.due", lambda *a, **k: state["due"])
    monkeypatch.setattr(cli, "_close_and_report", close)

    def release_after(*a, through, **k):
        ran.append("release")
        state["through"] = through

    monkeypatch.setattr(cli, "_release_after_close", release_after)
    monkeypatch.setattr(cli, "sprint_today", lambda org: date(2026, 10, 15))
    monkeypatch.setattr(
        "crew_org.flows.retro.existing_retro",
        lambda issues, repo, sprint: 12 if state["closed"] else None,
    )

    def roll(issues):
        cli._roll_over(Board(), issues, org={}, sprint="Sprint 25", owner="o", env={})
        return ran

    return roll, state


def test_a_close_at_a_ticks_start_releases_the_package(rolled):
    roll, _ = rolled
    assert roll(Issues(["delivery-history-2026-10-13"])) == ["close", "release"]


def test_a_close_releases_the_package_through_the_closed_sprint(rolled):
    roll, state = rolled
    roll(Issues([]))
    assert state["through"] == ENDED


def test_the_release_passes_the_closed_sprint_on(monkeypatch):
    seen = {}

    def package(dry_run, through):
        seen["through"] = through
        return "t", None, "u"

    monkeypatch.setattr(cli, "_release_package", package)
    monkeypatch.setattr("crew_org.flows.retro.existing_retro", lambda *a: 12)
    cli._release_after_close(Issues([]), sprint="Sprint 25", crew_repo="crew", through=ENDED)
    assert seen["through"] == ENDED


def test_a_package_released_at_a_close_leaves_out_the_next_sprint():
    sprint = [
        Sprint(name="Sprint 26", start=date(2026, 10, 15), end=date(2026, 10, 15)),
        Sprint(name="Sprint 27", start=date(2026, 10, 16), end=date(2026, 10, 16)),
        Sprint(name="Sprint 28", start=date(2026, 10, 17), end=date(2026, 10, 17)),
    ]
    today = date(2026, 10, 16)
    names = [s.name for s in covered(sprint, today=today, through=date(2026, 10, 15))]
    assert names == ["Sprint 26"]
    pushed = [s.name for s in covered(sprint, today=today)]
    assert pushed == ["Sprint 26", "Sprint 27"], "uncut, as the push to sprint-metrics uses it"


def test_a_release_by_hand_stops_at_the_last_sprint_that_ended():
    sprints = [
        {"title": "Sprint 26", "startDate": "2026-10-15", "duration": 1},
        {"title": "Sprint 27", "startDate": "2026-10-16", "duration": 1},
    ]
    assert last_end(sprints, date(2026, 10, 16)) == date(2026, 10, 15)
    assert last_end(sprints, date(2026, 10, 15)) is None, "none has ended yet"


def test_a_sprint_closed_without_a_package_gets_one_at_the_next_tick(rolled):
    roll, state = rolled
    state["due"], state["closed"] = False, True
    assert roll(Issues(["delivery-history-2026-10-13"])) == ["release"]


def test_once_a_package_is_out_after_the_sprint_nothing_more_is_released(rolled):
    roll, state = rolled
    state["due"], state["closed"] = False, True
    assert roll(Issues(["delivery-history-2026-10-15"])) == []


def test_a_sprint_not_closed_yet_gets_no_package(rolled, monkeypatch):
    roll, state = rolled
    state["due"] = False
    assert roll(Issues([])) == []


def test_a_close_that_fails_releases_nothing(rolled, monkeypatch):
    roll, _ = rolled

    def broken(*a, **k):
        raise RuntimeError("GitHub is down")

    monkeypatch.setattr(cli, "_close_and_report", broken)
    assert roll(Issues([])) == []


def test_the_retro_says_what_was_released(monkeypatch):
    issues = Issues([])
    monkeypatch.setattr(
        cli, "_release_package", lambda dry_run, through: ("delivery-history-x", None, "u")
    )
    monkeypatch.setattr("crew_org.flows.retro.existing_retro", lambda *a: 12)
    cli._release_after_close(issues, sprint="Sprint 25", crew_repo="crew", through=ENDED)
    assert issues.comments and "`delivery-history-x` is released" in issues.comments[0][2]


def test_the_retro_says_why_nothing_was_released(monkeypatch):
    issues = Issues([])
    refused = "sprint-metrics hasn't answered for Sprint 25"
    monkeypatch.setattr(cli, "_release_package", lambda dry_run, through: ("t", refused, None))
    monkeypatch.setattr("crew_org.flows.retro.existing_retro", lambda *a: 12)
    cli._release_after_close(issues, sprint="Sprint 25", crew_repo="crew", through=ENDED)
    assert refused in issues.comments[0][2] and "next tick tries again" in issues.comments[0][2]


def test_a_release_that_errors_never_fails_the_close(monkeypatch):
    issues = Issues([])

    def boom(dry_run, through):
        raise ConnectionError("no network")

    monkeypatch.setattr(cli, "_release_package", boom)
    monkeypatch.setattr("crew_org.flows.retro.existing_retro", lambda *a: 12)
    cli._release_after_close(issues, sprint="Sprint 25", crew_repo="crew", through=ENDED)
    assert "ConnectionError" in issues.comments[0][2]
