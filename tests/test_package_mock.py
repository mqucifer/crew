"""delivery-history's mock: built like a real package, and kept current (crew#521).

crew-presentation builds against it before the first release, so it must match the
schema and show every shape the schema allows a consumer to meet.
"""

from __future__ import annotations

import json

import jsonschema

from crew_org.packages import contract, mock


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
    }
    assert {e["by"] for e in events if e["kind"] == "card_moved"} == {
        "crew",
        "person",
        "platform",
    }
    assert any(e["kind"] == "attempt" and e["reason"] for e in events)
    assert {s["pattern"] for s in analysis["stories"]} == {
        "first_try",
        "crew_fix",
        "escalated",
        "retried",
        "superseded",
        "blocked",
        "open",
    }
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
    assert json.loads(files["manifest.json"])["left_out"] == {}
