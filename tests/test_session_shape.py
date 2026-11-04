"""The shape of a session, published for sprint-metrics before it's sent (crew#1000).

mqucifer/sprint-metrics#935 builds against the schema and the examples, and
mqucifer/crew#1001 sends what they show, so they're held to each other and to the
record's own fields.
"""

from __future__ import annotations

import json
from typing import Any, get_args

import jsonschema
import pytest

from crew_org.packages import contract, session_shape
from crew_org.packages.session_shape import Role, SessionEvent


def _committed(name: str) -> dict[str, Any]:
    return json.loads((session_shape.CONTRACT / name).read_text(encoding="utf-8"))


def _examples() -> dict[str, SessionEvent]:
    return session_shape.examples()


def _committed_examples() -> dict[str, str]:
    target = session_shape.CONTRACT / session_shape.EXAMPLES
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(target.glob("*.json"))}


def test_the_committed_schema_is_what_the_model_generates():
    assert _committed(session_shape.SCHEMA) == session_shape.schema(), (
        "the shape is stale: run `crew package schema`"
    )


def test_the_shape_s_version_names_exactly_this_schema():
    contract.check_version(
        contract.read_versions(session_shape.CONTRACT),
        session_shape.VERSION,
        contract.fingerprint({session_shape.SCHEMA: session_shape.schema()}),
    )


def test_the_committed_examples_are_what_the_model_builds():
    assert _committed_examples() == session_shape.example_files(), (
        "the examples are stale: run `crew package schema`"
    )


def test_each_committed_example_holds_to_the_committed_schema():
    schema = _committed(session_shape.SCHEMA)
    found = _committed_examples()
    assert found
    for name, text in found.items():
        jsonschema.validate(json.loads(text), schema)
        assert SessionEvent.model_validate_json(text), name


def test_the_schema_refuses_what_the_shape_leaves_out():
    schema = _committed(session_shape.SCHEMA)
    good = json.loads(session_shape.example_files()["developer-on-a-story.json"])
    for bad in (
        {**good, "summary": "a sentence"},
        {**good, "role": "Refinement panel"},
        {**good, "model_seconds": -1},
        {**good, "model_seconds": 12.5},
        {**good, "card_id": "127"},
        {**good, "type": "started"},
        {**good, "tokens": 100},
        {k: v for k, v in good.items() if k != "end"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


def test_it_is_sprint_metrics_4_5_0_s_session_event():
    """mqucifer/sprint-metrics' docs/service.md at v4.5.0 names these fields (crew#1001)."""
    assert set(SessionEvent.model_fields) == {
        "api_version",
        "type",
        "card_id",
        "timestamp",
        "sprint",
        "card",
        "role",
        "start",
        "end",
        "model_seconds",
        "input_tokens",
        "output_tokens",
    }


def test_there_are_sessions_of_each_role():
    assert {e.role for e in _examples().values()} == set(get_args(Role))


def test_a_session_on_no_card_leaves_both_off():
    review = _examples()["code-reviewer-on-a-pull-request-with-no-card"].sent()
    assert "card_id" not in review and "card" not in review
    story = _examples()["code-reviewer-on-a-story"].sent()
    assert story["card_id"] == "sprint-metrics#127" and story["card"]["created"] == "2026-09-23"


def test_a_session_on_no_card_is_allowed_only_with_no_card_at_all():
    good = _examples()["code-reviewer-on-a-story"]
    with pytest.raises(ValueError, match="card"):
        SessionEvent.model_validate({**good.model_dump(), "card": None})
    with pytest.raises(ValueError, match="card"):
        SessionEvent.model_validate({**good.model_dump(), "card_id": None})


def test_a_session_with_no_recorded_calls_leaves_its_measures_off_never_zero():
    unmeasured = _examples()["product-owner-with-no-recorded-calls"].sent()
    assert not {"model_seconds", "input_tokens", "output_tokens"} & set(unmeasured)


def test_a_session_cannot_finish_before_it_started_and_starts_at_its_timestamp():
    good = _examples()["developer-on-a-story"]
    with pytest.raises(ValueError, match="finish"):
        SessionEvent.model_validate({**good.model_dump(), "end": good.start.replace(year=2020)})
    with pytest.raises(ValueError, match="timestamp"):
        SessionEvent.model_validate({**good.model_dump(), "timestamp": good.end})


def test_the_shape_carries_no_totals_and_no_story_kind():
    """Each session's own measures only (the Sponsor, 2026-11-01): sprint-metrics adds
    them up. Nothing in the record says addition or change (crew#1000)."""
    schema = session_shape.schema()
    assert not {"tokens", "kind", "attempts"} & set(schema["properties"])
    assert "kind" not in schema["$defs"]["SessionCard"]["properties"]


def test_each_field_comes_from_a_field_the_record_has():
    fields = set(SessionEvent.model_fields) | {
        f"card.{name}" for name in session_shape.SessionCard.model_fields
    }
    assert set(session_shape.SOURCES) | session_shape.INTAKE_ONLY == fields
    for name, (model, field) in session_shape.SOURCES.items():
        assert field in model.model_fields, f"{name}: {model.__name__} has no {field}"


def _strings(schema: dict[str, Any], path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every string a value may be, with where: the free text the shape could carry."""
    found = []
    if schema.get("type") == "string":
        found.append((path, schema))
    for name, part in schema.get("properties", {}).items():
        found += _strings(part, f"{path}.{name}")
    for part in schema.get("anyOf", []):
        found += _strings(part, path)
    for name, part in schema.get("$defs", {}).items():
        found += _strings(part, f"${name}")
    return found


def test_it_carries_measures_and_names_only():
    """ADR 0020: every string is a time, a fixed name or a reference, never free text.

    A sprint's name is the board's own, as every event the feed sends has it.
    """
    strings = _strings(session_shape.schema())
    assert strings
    for path, schema in strings:
        fixed = {"const", "enum", "pattern", "format"} & set(schema)
        assert fixed or path == ".sprint", f"{path} could carry free text"


def test_a_changed_shape_without_a_new_version_is_refused(tmp_path, monkeypatch):
    session_shape.write(tmp_path)
    assert json.loads((tmp_path / session_shape.SCHEMA).read_text()) == session_shape.schema()
    assert sorted(p.name for p in (tmp_path / session_shape.EXAMPLES).iterdir()) == sorted(
        session_shape.example_files()
    )
    monkeypatch.setattr(session_shape, "schema", lambda: {"changed": True})
    with pytest.raises(contract.StaleVersion):
        session_shape.write(tmp_path)


def test_writing_again_removes_an_example_that_s_gone(tmp_path):
    session_shape.write(tmp_path)
    (tmp_path / session_shape.EXAMPLES / "gone.json").write_text("{}", encoding="utf-8")
    session_shape.write(tmp_path)
    assert not (tmp_path / session_shape.EXAMPLES / "gone.json").exists()
