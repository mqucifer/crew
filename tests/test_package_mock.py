"""delivery-history's mock: built like a real package, and kept current (crew#521).

crew-presentation builds against it before the first release, so it must match the
schema and show every shape the schema allows a consumer to meet.
"""

from __future__ import annotations

import json

import jsonschema

from crew_org.packages import contract, mock
from crew_org.packages.sprint_metrics_feed import feed


def test_the_committed_mock_is_what_the_model_builds_now():
    assert mock.read(contract.CONTRACTS) == mock.files(), (
        "the mock is stale: run `crew package schema`"
    )


def test_the_mock_matches_the_committed_schema():
    def schema(name):
        return json.loads((contract.CONTRACTS / name).read_text())

    files = mock.files()
    jsonschema.validate(json.loads(files["delivery-history.json"]), schema("schema.json"))
    jsonschema.validate(json.loads(files["manifest.json"]), schema("manifest.schema.json"))
    events = [json.loads(text) for path, text in files.items() if path.startswith("events/")]
    for period in events:
        jsonschema.validate(period, schema("events.schema.json"))


def test_the_mock_shows_every_kind_a_consumer_meets():
    files = mock.files()
    index = json.loads(files["delivery-history.json"])
    events = [
        e
        for path, text in files.items()
        if path.startswith("events/")
        for e in json.loads(text)["events"]
    ]
    analysis = index["analysis"]
    sprints = analysis["sprints"]
    assert {e["kind"] for e in events} == {
        "card_moved",
        "work_started",
        "work_finished",
        "attempt",
        "blocked",
        "escalated",
        "model_call",
        "pull_merged",
        "session_started",
        "session_turn",
        "tool_call",
        "session_compacted",
        "session_ended",
        "gate_checked",
        "returned",
        "sent_back",
    }
    assert {e["by"] for e in events if e["kind"] == "card_moved"} == {
        "crew",
        "person",
        "platform",
    }
    assert any(e["kind"] == "attempt" and e["reason"] for e in events)
    assert {a["kind"] for s in sprints for a in s["attention"]} == {
        "waiting_on_sponsor",
        "blocked",
        "repeated_refusal",
    }
    assert {d["kind"] for s in sprints for d in s["decided"]} == {
        "design_revision_merged",
        "technical_epic_filed",
    }
    assert {c["source"] for s in sprints for c in s["changed"]} == {"sprint-metrics", "crew"}
    assert {d["outcome"] for d in analysis["deliveries"]} == {"returned", "open"}
    assert any(e["kind"] == "work_finished" and e["verdict"] for e in events)
    assert json.loads(files["manifest.json"])["left_out"] == {}


def _built():
    history, periods, _ = mock.build_without_answers()
    return feed(history, periods.values()).events


def test_each_sprint_s_totals_in_the_mock_agree_with_its_own_events():
    """Hand-written, Sprint 6's said 6 delivered and 3 first attempts (crew#963)."""
    sent = _built()
    answers = mock.range_body()["sprints"]
    for sprint, answer in answers.items():
        finished = {e["card_id"] for e in sent if e["type"] == "finished" and e["sprint"] == sprint}
        missed = {e["card_id"] for e in sent if e["type"] == "attempt_failed"}
        assert answer["throughput"] == answer["first_attempt_denominator"] == len(finished)
        assert answer["first_attempt_numerator"] == len(finished - missed)
        assert answer["points_delivered"] == 3 * len(finished)
        returns = [e for e in sent if e["type"] == "attempt_failed" and e["card_id"] in finished]
        assert sum(c["count"] for c in answer["failure_breakdown"]) == len(returns)


def test_the_mock_s_answers_are_the_numbers_its_stories_add_up_to():
    five, six = (mock.range_body()["sprints"][s] for s in ("Sprint 5", "Sprint 6"))
    assert (five["throughput"], five["first_attempt_numerator"]) == (3, 2)
    assert five["first_attempt_rate_percent"] == 67
    assert five["failure_breakdown"] == [{"class": "VERIFY", "role": "Developer", "count": 1}]
    # #125 and #128 landed with no return; #126, #127 and #133 came back.
    assert (six["throughput"], six["first_attempt_numerator"]) == (5, 2)
    assert (six["first_attempt_rate_percent"], six["points_delivered"]) == (40, 15)
    assert six["failure_breakdown"] == [
        {"class": "VERIFY", "role": "Developer", "count": 3},
        {"class": "EDIT", "role": "Developer", "count": 3},
    ]
    # Ties alphabetical, as sprint-metrics words its top causes.
    assert six["top_failure_causes"] == [
        {"class": "EDIT", "role": "Developer", "count": 3},
        {"class": "VERIFY", "role": "Developer", "count": 3},
    ]
    assert (six["superseded_count"], six["superseded_points"]) == (1, 3)
    assert six["superseded_breakdown"] == {"sponsor": 0, "story_problem": 1}
    assert six["prior"]["throughput"] == 3 and six["delta"]["throughput"] == 2


def test_the_package_carries_no_crew_calculated_first_try():
    """The crew logs what happened; sprint-metrics decides what first try means (crew#963)."""
    index = contract.schemas()["schema.json"]
    assert "stories" not in index["$defs"]["Analysis"]["properties"]
    assert not {"StoryPattern", "Repeated", "StoryCost"} & set(index["$defs"])
    built = json.loads(mock.files()["delivery-history.json"])
    assert "stories" not in built["analysis"]
    # Sprint-metrics' own numbers keep their names; the crew's verdict's are gone.
    for name, schema in contract.schemas().items():
        text = json.dumps(schema)
        assert "first_try" not in text, name
        assert "first attempt" not in text, name


def test_each_story_keeps_its_events():
    files = mock.files()
    events = [
        e
        for path, text in files.items()
        if path.startswith("events/")
        for e in json.loads(text)["events"]
    ]
    of_127 = [e for e in events if (e.get("card") or {}).get("number") == 127]
    assert {"card_moved", "attempt", "blocked", "model_call"} <= {e["kind"] for e in of_127}
