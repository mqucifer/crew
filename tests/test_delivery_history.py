"""`delivery-history`: what the crew publishes about itself, and its contract (crew#521).

ADR 0020 sets what may leave: measures, names and states, public titles, and
refusal reasons the crew's own checks wrote with only names filled in. ADR 0021
sets the contract: generated from the model, its version in code, and a real
package proven against it. The events here are shaped as the event log records
them, including the fields that must never leave.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta

import jsonschema
import pytest

from crew_org.packages import contract
from crew_org.packages import delivery_history as dh
from crew_org.packages.reasons import published_reason
from crew_org.tools.github_project import Card

T = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
PROMPT = "## The repository as it stands\n\nWhat this project is for: its onboarding record"


def at(minutes: float) -> str:
    return (T + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def board_card(repo, number, *, created=None, work_type="Story", title=None) -> Card:
    return Card(
        item_id=f"{repo}-{number}",
        number=number,
        repo=repo,
        title=title or f"{repo} card {number}",
        work_type=work_type,
        status="Done",
        created=created or T - timedelta(days=1),
    )


def inputs(events, *, cards=None, pulls=None, day=date(2026, 10, 7)) -> dh.Inputs:
    return dh.Inputs(
        events=events,
        cards=cards if cards is not None else [board_card("sprint-metrics", 455)],
        sprints=dh.sprints_from([{"title": "Sprint 18", "startDate": "2026-10-07", "duration": 1}]),
        pulls=pulls or {},
        crew_repository="crew",
        repositories=["sprint-metrics"],
        timezone="America/Chicago",
        date=day,
        generated=T,
    )


def built(events, **kwargs) -> tuple[dict, dh.Manifest]:
    """The index, with every period's events gathered back into one list, oldest first."""
    history, files, manifest = dh.build(inputs(events, **kwargs))
    data = json.loads(history.model_dump_json())
    data["events"] = [
        e
        for period in data["periods"]
        for e in json.loads(files[period["file"]].model_dump_json())["events"]
    ]
    return data, manifest


# The real shapes, with the fields that must stay local.
MOVE = (
    "tick",
    {
        "at": at(0),
        "kind": "card.moved",
        "role": "Code Reviewer",
        "card": 455,
        "summary": "diff approved",
        "detail": {"from": "Reviewing", "to": "QAing", "repo": "sprint-metrics"},
    },
)
CALL = (
    "tick",
    {
        "at": at(1),
        "kind": "llm.finished",
        "role": "Developer",
        "card": 455,
        "summary": PROMPT,
        "detail": {
            "model": "crew-code-think",
            "for": "deliver",
            "prompt_tokens": 47864,
            "completion_tokens": 10184,
            "reasoning_tokens": 8412,
            "duration_s": 335.8,
            "call_id": "c1",
            "response_id": "r1",
            "repo": "sprint-metrics",
        },
    },
)
SCHEMA_REFUSAL = (
    "tick",
    {
        "at": at(2),
        "kind": "escalation.decided",
        "role": "Developer",
        "card": 455,
        "summary": "SCHEMA — retry_local",
        "detail": {
            "failure_class": "SCHEMA",
            "attempt": 2,
            "error": "OpenAI API call failed: 1 validation error for Implementation\n"
            "  Value error, "
            "an implementation must create a file or edit one [type=value_error, "
            "input_value={'summary': 'The model wrote this'}, input_type=dict]",
        },
    },
)
VERIFY_FAILURE = (
    "tick",
    {
        "at": at(3),
        "kind": "escalation.decided",
        "role": "Developer",
        "card": 455,
        "summary": "VERIFY — escalate",
        "detail": {
            "failure_class": "VERIFY",
            "attempt": 3,
            "output": "E   ImportError: cannot import name 'InMemorySpanExporter'",
            "failing_commands": ["uv run pytest -q"],
        },
    },
)


# --- what leaves ------------------------------------------------------------------


def test_a_move_says_who_moved_the_card_and_between_which_columns():
    data, _ = built([MOVE])
    [move] = data["events"]

    assert move == {
        "kind": "card_moved",
        "at": "2026-10-07T20:00:00Z",
        "card": {"repo": "sprint-metrics", "number": 455},
        "from_column": "Reviewing",
        "to_column": "QAing",
        "by": "crew",
        "role": "Code Reviewer",
    }


def test_a_move_someone_else_made_is_theirs():
    seen = (
        "tick",
        {
            "at": at(5),
            "kind": "card.seen_moved",
            "card": 455,
            "summary": "moved to Needs Refinement by a person",
            "detail": {
                "from": "Inbox (Goals)",
                "to": "Needs Refinement",
                "repo": "sprint-metrics",
                "by": "person",
            },
        },
    )
    hand = ("operator", {**MOVE[1], "at": at(6)})

    data, _ = built([seen, hand])

    assert [(e["by"], e["role"]) for e in data["events"]] == [("person", None), ("person", None)]


def test_a_model_call_carries_tokens_and_time_and_never_its_prompt():
    data, _ = built([CALL])
    [call] = data["events"]

    assert call["prompt_tokens"] == 47864
    assert call["reasoning_tokens"] == 8412
    assert call["seconds"] == 335.8
    assert call["model"] == "crew-code-think"
    assert PROMPT not in json.dumps(data)
    assert "summary" not in json.dumps(data)


def test_a_failed_call_logged_twice_is_one_call():
    failed = (
        "tick",
        {**CALL[1], "kind": "llm.failed", "detail": {**CALL[1]["detail"], "error": "boom"}},
    )

    data, _ = built([failed, failed])

    assert [e["failed"] for e in data["events"]] == [True]
    assert "boom" not in json.dumps(data)


def test_a_refusal_the_crews_check_wrote_carries_its_reason_and_never_the_answer():
    data, _ = built([SCHEMA_REFUSAL])
    [attempt] = data["events"]

    assert attempt["reason"] == "an implementation must create a file or edit one"
    assert attempt["failure_class"] == "SCHEMA"
    assert attempt["decision"] == "retry_local"
    assert attempt["number"] == 2
    assert "The model wrote this" not in json.dumps(data)


def test_a_failure_the_crew_didnt_word_shows_its_class_alone():
    data, _ = built([VERIFY_FAILURE])
    [attempt] = data["events"]

    assert attempt["reason"] is None
    assert attempt["failure_class"] == "VERIFY"
    assert attempt["decision"] == "escalate"
    assert "ImportError" not in json.dumps(data)


def test_a_review_is_work_on_its_pull_request_and_the_card_it_closes():
    review = (
        "tick",
        {
            "at": at(0),
            "kind": "agent.started",
            "role": "Code Reviewer",
            "card": 463,
            "summary": "PR #463 by the crew",
            "detail": {},
        },
    )
    pulls = {
        "sprint-metrics": [
            {
                "number": 463,
                "title": "Document the /trend endpoint",
                "created_at": at(-30),
                "merged_at": at(10),
                "closed_at": at(10),
                "closes": [("sprint-metrics", 455)],
            }
        ]
    }

    data, _ = built([review], pulls=pulls)
    work = [e for e in data["events"] if e["kind"] == "work_started"]

    assert work == [
        {
            "kind": "work_started",
            "at": "2026-10-07T20:00:00Z",
            "card": {"repo": "sprint-metrics", "number": 455},
            "pull": {"repo": "sprint-metrics", "number": 463},
            "role": "Code Reviewer",
        }
    ]
    [merged] = [e for e in data["events"] if e["kind"] == "pull_merged"]
    assert merged["closes"] == [{"repo": "sprint-metrics", "number": 455}]
    assert merged["crews_own"] is False


def test_a_reviewers_number_no_pull_request_fits_is_left_out_not_taken_for_a_card():
    """crew-presentation#15 and an unlisted sprint-metrics pull request 15 share a number."""
    review = (
        "tick",
        {"at": at(0), "kind": "agent.started", "role": "Code Reviewer", "card": 15, "detail": {}},
    )

    data, manifest = built([review], cards=[board_card("crew-presentation", 15)])

    assert data["events"] == []
    assert manifest.left_out == {"work: pull request unknown": 1}


# --- placing a card ---------------------------------------------------------------


def test_an_old_move_with_no_repository_is_placed_by_the_cards_filed_then():
    """crew-presentation#455 doesn't exist yet when sprint-metrics#455 moves."""
    old = ("tick", {**MOVE[1], "detail": {"from": "Reviewing", "to": "QAing"}})
    cards = [
        board_card("sprint-metrics", 455),
        board_card("crew-presentation", 455, created=T + timedelta(days=1)),
    ]

    data, _ = built([old], cards=cards)

    assert data["events"][0]["card"] == {"repo": "sprint-metrics", "number": 455}


def test_a_number_two_repositories_had_then_is_left_out_and_counted():
    old = ("tick", {**MOVE[1], "detail": {"from": "Reviewing", "to": "QAing"}})
    cards = [board_card("sprint-metrics", 455), board_card("crew-presentation", 455)]

    data, manifest = built([old], cards=cards)

    assert data["events"] == []
    assert manifest.left_out == {"card_moved: card's repository unknown": 1}


def test_an_event_after_the_packages_date_is_left_for_the_next():
    """Chicago's 2026-10-07 ends at 05:00 UTC on the 8th."""
    late = ("tick", {**MOVE[1], "at": "2026-10-08T05:30:00Z"})
    evening = ("tick", {**MOVE[1], "at": "2026-10-08T04:30:00Z"})

    data, _ = built([late, evening])

    assert [e["at"] for e in data["events"]] == ["2026-10-08T04:30:00Z"]


def test_events_are_filed_by_the_sprint_their_day_falls_in_or_the_days_between():
    """No Sprint 15 or 16: 10-04 and 10-05 share one file between Sprint 14 and Sprint 17."""
    sprints = dh.sprints_from(
        [
            {"title": "Sprint 14", "startDate": "2026-10-03", "duration": 1},
            {"title": "Sprint 17", "startDate": "2026-10-06", "duration": 1},
        ]
    )
    days = [
        "2026-10-03T18:00:00Z",
        "2026-10-04T18:00:00Z",
        "2026-10-05T18:00:00Z",
        "2026-10-06T18:00:00Z",
    ]
    events = [("tick", {**MOVE[1], "at": when}) for when in days]
    history, files, _ = dh.build(dataclasses.replace(inputs(events), sprints=sprints))

    assert [(p.sprint, str(p.start), str(p.end), p.file, p.events) for p in history.periods] == [
        ("Sprint 14", "2026-10-03", "2026-10-03", "events/2026-10-03.json", 1),
        (None, "2026-10-04", "2026-10-05", "events/2026-10-04.json", 2),
        ("Sprint 17", "2026-10-06", "2026-10-06", "events/2026-10-06.json", 1),
    ]
    assert sorted(files) == [p.file for p in history.periods]
    assert files["events/2026-10-04.json"].sprint is None


def test_a_superseded_story_says_so_though_the_board_shows_it_done():
    """sprint-metrics#475 was closed as not planned and moved to Done all the same (crew#558)."""
    superseded = board_card("sprint-metrics", 475).model_copy(
        update={"state": "CLOSED", "state_reason": "NOT_PLANNED"}
    )
    landed = board_card("sprint-metrics", 496).model_copy(
        update={"state": "CLOSED", "state_reason": "COMPLETED"}
    )
    reopened = board_card("sprint-metrics", 501).model_copy(
        update={"state": "OPEN", "state_reason": "REOPENED"}
    )
    data, _ = built([], cards=[superseded, landed, reopened])
    by_number = {c["number"]: c for c in data["cards"]}
    assert by_number[475]["status"] == "Done"
    assert by_number[475]["closed_as"] == "not_planned"
    assert by_number[496]["closed_as"] == "completed"
    assert by_number[501]["closed_as"] is None


def test_a_sprint_runs_its_duration_from_its_start():
    [sprint] = dh.sprints_from([{"title": "Sprint 19", "startDate": "2026-10-08", "duration": 1}])

    assert (sprint.start, sprint.end) == (date(2026, 10, 8), date(2026, 10, 8))


# --- the allow-list -----------------------------------------------------------------


def test_the_model_refuses_a_field_it_doesnt_declare():
    with pytest.raises(ValueError):
        dh.CardRef(repo="sprint-metrics", number=1, summary=PROMPT)  # type: ignore[call-arg]


# --- reasons -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # A crew check that filled in what the model sent.
        "these named tests don't exist in the repository: CI workflow — judged by review per §7 "
        "(crew#333); no automated test applies",
        # A line of the model's code as the name.
        "no definition named 'from sprint_metrics.scrape import run_scrape_server'. "
        "This file defines: Card",
        # Python's own message.
        "the file does not parse: unterminated string literal "
        "(detected at line 193) (<unknown>, line 193)",
        # A library's message, not the crew's.
        "Invalid response from LLM call - None or empty.",
    ],
)
def test_a_reason_with_anything_but_names_filled_in_isnt_published(message):
    assert published_reason(message) is None


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (
            "'test_trend_cycle_time_across_sprints' has no source. This answer changes the branch",
            "'test_trend_cycle_time_across_sprints' has no source",
        ),
        (
            "the edit to '.github/workflows/ci.yml' changes nothing",
            "the edit to '.github/workflows/ci.yml' changes nothing",
        ),
        (
            "no definition named '__init__'. This file defines: __all__",
            "no definition named '__init__'",
        ),
    ],
)
def test_a_crew_message_with_names_filled_in_is_published_as_its_first_clause(message, reason):
    assert published_reason(message) == reason


# --- the contract ------------------------------------------------------------------


def test_the_committed_schema_is_the_models():
    """Run `crew package schema` after changing the model."""
    for name, content in contract.schemas().items():
        committed = json.loads((contract.CONTRACTS / name).read_text())
        assert committed == content, f"{name} is stale: run `crew package schema`"


def test_the_schema_version_names_exactly_this_schema():
    """A changed schema needs a new SCHEMA_VERSION.

    MAJOR if a consumer could break on the change, MINOR if it only adds.
    """
    contract.check_version(
        contract.read_versions(), dh.SCHEMA_VERSION, contract.fingerprint(contract.schemas())
    )


def test_a_changed_schema_under_the_same_version_is_refused():
    with pytest.raises(contract.StaleVersion, match="raise it"):
        contract.check_version({"1.0.0": "old"}, "1.0.0", "new")


def test_a_new_version_must_be_later_than_the_last():
    with pytest.raises(contract.StaleVersion, match="isn't later"):
        contract.check_version({"1.1.0": "a"}, "1.0.1", "b")
    contract.check_version({"1.1.0": "a"}, "1.2.0", "b")


def test_a_package_built_from_every_kind_of_event_matches_the_committed_schema():
    events = [
        MOVE,
        CALL,
        SCHEMA_REFUSAL,
        VERIFY_FAILURE,
        (
            "tick",
            {"at": at(4), "kind": "card.blocked", "role": "Developer", "card": 455, "detail": {}},
        ),
        (
            "tick",
            {
                "at": at(4),
                "kind": "escalation.sent",
                "role": "Developer",
                "card": 455,
                "detail": {},
            },
        ),
        (
            "tick",
            {"at": at(4), "kind": "agent.finished", "role": "Developer", "card": 455, "detail": {}},
        ),
        (
            "tick",
            {
                "at": at(4),
                "kind": "agent.started",
                "role": "Scrum Master",
                "card": None,
                "detail": {},
            },
        ),
    ]
    pulls = {
        "crew": [
            {
                "number": 512,
                "title": "fix: a repair's tests named without code are dropped",
                "created_at": at(-60),
                "merged_at": at(5),
                "closed_at": at(5),
                "closes": [],
            }
        ]
    }
    history, files, manifest = dh.build(inputs(events, pulls=pulls))

    def schema(name):
        return json.loads((contract.CONTRACTS / name).read_text())

    jsonschema.validate(json.loads(history.model_dump_json()), schema("schema.json"))
    for period in files.values():
        jsonschema.validate(json.loads(period.model_dump_json()), schema("events.schema.json"))
    jsonschema.validate(json.loads(manifest.model_dump_json()), schema("manifest.schema.json"))
    assert {e.kind for period in files.values() for e in period.events} == {
        "card_moved",
        "model_call",
        "attempt",
        "blocked",
        "escalated",
        "work_started",
        "work_finished",
        "pull_merged",
    }
