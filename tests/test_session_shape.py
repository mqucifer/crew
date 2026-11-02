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
        {**good, "card_id": "127"},
        {**good, "type": "started"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


def test_there_are_sessions_of_each_role():
    assert {e.role for e in _examples().values()} == set(get_args(Role))


def test_a_review_of_a_pull_request_that_closes_no_card_says_so():
    review = _examples()["code-reviewer-on-a-pull-request-with-no-card"]
    assert review.card_id is None and review.card is None
    assert review.pull == "sprint-metrics#141"


def test_a_review_of_a_story_s_pull_request_has_both():
    review = _examples()["code-reviewer-on-a-story"]
    assert review.card_id == "sprint-metrics#127"
    assert review.pull == "sprint-metrics#140"


def test_a_session_on_no_card_is_allowed_only_with_no_card_at_all():
    good = _examples()["code-reviewer-on-a-story"]
    with pytest.raises(ValueError, match="card"):
        SessionEvent.model_validate({**good.model_dump(), "card": None})
    with pytest.raises(ValueError, match="card"):
        SessionEvent.model_validate({**good.model_dump(), "card_id": None})


def test_planning_and_building_can_be_told_apart():
    parts = {e.part for e in _examples().values()}
    assert {"refinement", "delivery"} <= parts
    split = _examples()["business-analyst-on-an-epic"]
    assert split.part == "refinement" and split.card is not None and split.card.type == "Epic"
    built = _examples()["developer-on-a-story"]
    assert built.part == "delivery" and built.epic == "sprint-metrics#120"


def test_a_session_with_no_recorded_finish_says_so_and_never_says_zero():
    open_ended = _examples()["developer-with-no-recorded-finish"]
    assert open_ended.finished is None
    unmeasured = _examples()["product-owner-with-no-recorded-calls"]
    assert unmeasured.part is None
    assert unmeasured.model_seconds is None and unmeasured.prompt_tokens is None


def test_a_session_cannot_finish_before_it_started():
    good = _examples()["developer-on-a-story"]
    with pytest.raises(ValueError, match="finish"):
        SessionEvent.model_validate(
            {**good.model_dump(), "finished": good.timestamp.replace(year=2020)}
        )


def test_the_shape_carries_no_story_kind():
    """Nothing in the record says addition or change (crew#1000): it's left out, not guessed."""
    schema = session_shape.schema()
    assert "kind" not in schema["properties"]
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
