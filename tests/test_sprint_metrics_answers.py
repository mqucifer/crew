"""sprint-metrics' answers in delivery-history (crew#521, criterion 2).

The package carries sprint-metrics' answer for every sprint it covers, checked
against sprint-metrics' published schema at the release the crew runs, and its
schema embeds that one (ADR 0021, ADR 0022).
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import jsonschema
import pytest

from crew_org.packages import contract, mock, release
from crew_org.packages import delivery_history as dh
from crew_org.packages import sprint_metrics_answers as sma
from crew_org.packages.gather import history_now

ROOT = Path(__file__).resolve().parents[1]
FIVE, SIX = "Sprint 5", "Sprint 6"


def body() -> dict:
    """A /range answer for Sprints 5 and 6 in the pinned release's shape: the mock's."""
    return copy.deepcopy(mock.range_body())


# --- the release the crew runs ------------------------------------------------------------


def test_the_release_s_published_schema_is_here():
    schema = sma.range_schema()
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert "cycle_time_minutes" in sma.entry_schema()["required"]


def test_the_crew_reads_4_2_1_whose_answers_have_no_escalation_measures():
    """sprint-metrics 4.0.0 took the escalation measures out of /range (crew#905)."""
    assert sma.RELEASE == "4.2.1"
    named = sma.entry_schema()["properties"]
    assert "escalation_count" not in named and "escalation_rate_percent" not in named
    assert "window" in sma.range_schema()["required"]


def test_the_mock_s_failure_causes_are_4_x_s_list_of_class_role_and_count():
    """1.x answered `{"VERIFY": 2}`; 4.x answers a list, as its failure breakdown is."""
    six = mock.range_body()["sprints"][SIX]
    assert six["top_failure_causes"] == [
        {"class": "EDIT", "role": "Developer", "count": 3},
        {"class": "VERIFY", "role": "Developer", "count": 3},
    ]
    assert (
        sma.checked(mock.range_body(), [FIVE, SIX])[SIX]["top_failure_causes"]
        == (six["top_failure_causes"])
    )


def test_the_package_schema_embeds_sprint_metrics_own():
    answers = contract.schemas()["schema.json"]["$defs"]["SprintMetrics"]["properties"]["answers"]
    embedded = dict(answers["additionalProperties"])
    assert embedded.pop("$comment") == sma.SCHEMA_URL.format(release=sma.RELEASE)
    assert embedded == sma.entry_schema()


# --- checking an answer -------------------------------------------------------------------


def test_an_answer_is_carried_with_only_what_the_schema_names():
    found = body()
    found["sprints"][SIX]["new_measure"] = 4
    found["sprints"][SIX]["failure_breakdown"][0]["why"] = "the model wrote a sentence here"
    found["sprints"][SIX]["flags"]["a sentence as a key"] = True
    answers = sma.checked(found, [FIVE, SIX])
    six = answers[SIX]
    assert "new_measure" not in six
    assert six["failure_breakdown"][0] == {"class": "VERIFY", "role": "Developer", "count": 3}
    assert "a sentence as a key" not in six["flags"]
    assert six["prior"]["throughput"] == 3 and answers[FIVE]["prior"] is None


def test_cards_named_in_an_answer_are_carried_whole():
    """4.x names stories, epics and Goals by card: one word with a `#`, never a sentence."""
    found = body()
    six = found["sprints"][SIX]
    six["roster"] = {
        "completed_stories": ["sprint-metrics#133", "mqucifer/crew#905"],
        "released_epics": None,
        "achieved_goals": None,
    }
    six["rework"] = {
        "started": 6,
        "sent_backs": [
            {
                "card_id": "sprint-metrics#133",
                "send_backs": [{"step": "Developer", "reason": "the model wrote a sentence"}],
            }
        ],
    }
    answers = sma.checked(found, [FIVE, SIX])
    roster = answers[SIX]["roster"]
    assert roster["completed_stories"] == ["sprint-metrics#133", "mqucifer/crew#905"]
    [sent] = answers[SIX]["rework"]["sent_backs"]
    assert sent["card_id"] == "sprint-metrics#133"
    assert sent["send_backs"] == [{"step": "Developer", "reason": None}]
    jsonschema.validate(answers[SIX], sma.entry_schema())


def test_an_answer_from_an_older_release_is_refused_and_says_which_it_needs():
    found = body()
    del found["window"]  # 1.3.x doesn't answer it
    with pytest.raises(sma.Unanswered, match=rf"Is the service running {re.escape(sma.RELEASE)}\?"):
        sma.checked(found, [FIVE, SIX])


def test_a_sprint_with_no_answer_is_refused():
    found = body()
    del found["sprints"][FIVE]
    with pytest.raises(sma.Unanswered, match="no answer for Sprint 5"):
        sma.checked(found, [FIVE, SIX])


# --- asking the service -------------------------------------------------------------------


def test_the_service_is_asked_once_for_the_range(monkeypatch):
    asked = []

    def get(url, *, params, timeout):
        asked.append((url, params))
        return httpx.Response(200, json=body())

    monkeypatch.setattr(httpx, "get", get)
    answers = sma.ask("http://127.0.0.1:8080/", [FIVE, SIX])
    assert asked == [("http://127.0.0.1:8080/range", {"start": FIVE, "end": SIX})]
    assert list(answers) == [FIVE, SIX]


@pytest.mark.parametrize(
    "answer, said",
    [
        (httpx.ConnectError("refused"), "couldn't be reached"),
        (httpx.Response(503, json={"retryable": True}), "answered 503"),
    ],
)
def test_a_service_that_doesn_t_answer_says_why(monkeypatch, answer, said):
    def get(url, *, params, timeout):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(sma.Unanswered, match=said):
        sma.ask("http://127.0.0.1:8080", [FIVE, SIX])


# --- in the package -----------------------------------------------------------------------


def test_the_mock_carries_every_sprint_s_answer_and_follows_the_schema():
    history, _, _ = dh.build(mock.inputs())
    assert history.sprint_metrics is not None
    assert set(history.sprint_metrics.answers) == {FIVE, SIX}
    schema = json.loads((contract.CONTRACTS / "schema.json").read_text())
    jsonschema.validate(json.loads(history.model_dump_json()), schema)
    broken = json.loads(history.model_dump_json())
    del broken["sprint_metrics"]["answers"][SIX]["throughput"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, schema)


def test_a_package_goes_only_with_an_answer_for_every_sprint_it_covers():
    history, _, _ = dh.build(mock.inputs())
    assert release.refusal(history) is None
    part = history.sprint_metrics
    assert part is not None
    short = history.model_copy(
        update={"sprint_metrics": part.model_copy(update={"answers": {SIX: part.answers[SIX]}})}
    )
    assert release.refusal(short) == "sprint-metrics has no answer for Sprint 5"
    none = history.model_copy(update={"sprint_metrics": None})
    assert "sprint-metrics' answers aren't in the package" in (release.refusal(none) or "")


# --- gathering ----------------------------------------------------------------------------


class Board:
    def cards(self):
        return mock.inputs().cards

    def sprints(self):
        return mock.SPRINTS


class Issues:
    def all_pulls(self, repo):
        return mock.inputs().pulls.get(repo, [])


ORG = {"sprint": {"timezone": "America/Chicago"}}


def gathered(monkeypatch, tmp_path, **kw):
    import crew_org.packages.gather as gather

    monkeypatch.setattr(gather, "sprint_today", lambda org: date(2026, 9, 25))
    monkeypatch.setattr(gather, "datetime", _Frozen)
    return history_now(Board(), Issues(), org=ORG, crew_repo="crew", events_dir=tmp_path, **kw)


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 26, tzinfo=UTC)


def test_without_answers_history_now_builds_as_before(monkeypatch, tmp_path):
    """The tick's push passes no `answers`: its build is the one it always was."""
    history, files, manifest = gathered(monkeypatch, tmp_path)
    inputs = mock.inputs()
    expected = dh.build(
        dh.Inputs(
            events=[],
            cards=inputs.cards,
            sprints=[s for s in dh.sprints_from(mock.SPRINTS) if s.start <= date(2026, 9, 25)],
            pulls={"crew": inputs.pulls.get("crew", [])}
            | {r: inputs.pulls.get(r, []) for r in history.repositories},
            crew_repository="crew",
            repositories=history.repositories,
            timezone=history.timezone,
            date=date(2026, 9, 25),
            generated=datetime(2026, 9, 26, tzinfo=UTC),
        )
    )
    assert history.sprint_metrics is None
    assert (history, files, manifest) == expected


def test_answers_are_asked_for_the_sprints_started_by_today(monkeypatch, tmp_path):
    asked = []
    part = mock.inputs().sprint_metrics

    def answers(sprints):
        asked.append([s.name for s in sprints])
        return part

    history, _, _ = gathered(monkeypatch, tmp_path, answers=answers)
    assert asked == [[FIVE, SIX]]
    assert history.sprint_metrics == part


class PlannedAhead(Board):
    def sprints(self):
        return [*mock.SPRINTS, {"title": "Sprint 7", "startDate": "2026-09-26", "duration": 1}]


def test_a_sprint_the_board_plans_ahead_is_left_out(monkeypatch, tmp_path):
    """delivery-history-2026-10-13 carried Sprints 25 to 28 on 10-13; crew-presentation
    built pages and links for them, and its front page redirected to Sprint 28's."""
    import crew_org.packages.gather as gather

    monkeypatch.setattr(gather, "sprint_today", lambda org: date(2026, 9, 25))
    monkeypatch.setattr(gather, "datetime", _Frozen)
    history, _, _ = history_now(
        PlannedAhead(), Issues(), org=ORG, crew_repo="crew", events_dir=tmp_path
    )
    assert [s.name for s in history.sprints] == [FIVE, SIX]
    assert all(s.start <= date(2026, 9, 25) for s in history.sprints)
