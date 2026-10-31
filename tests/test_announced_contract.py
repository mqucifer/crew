"""A breaking version's contract, announced beside the current one (crew#905).

A producer announces a breaking change and ships it a week later (ADR 0008 in
mqucifer/project-print). The announced version's schemas and mock sit in
`contracts/<package>/<version>/` while the package still follows the current
one, and `versions.json` names the announced schemas' fingerprint, so the switch
must ship exactly what was announced or its own tests refuse it.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import jsonschema
import pytest

from crew_org.packages import contract
from crew_org.packages import delivery_history as dh

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
ANNOUNCED = sorted(p for p in contract.CONTRACTS.iterdir() if p.is_dir() and _VERSION.match(p.name))


def _schemas(directory: Path) -> dict[str, dict]:
    return {
        name: json.loads((directory / name).read_text(encoding="utf-8"))
        for name in contract.schemas()
    }


def test_delivery_history_2_0_0_shipped_exactly_as_announced_and_2_1_0_only_adds_to_it():
    """Its announcement recorded the fingerprint; the switch removed the announced copy.

    2.1.0 is that, plus how each card was filed (crew#974): take `filed` out and the
    schemas are exactly 2.0.0's, so a reader of 2.0.0 loses nothing.
    """
    assert dh.SCHEMA_VERSION == "2.1.0"
    schemas = contract.schemas()
    defs = schemas["schema.json"]["$defs"]
    del defs["Filed"]
    del defs["Card"]["properties"]["filed"]
    defs["Card"]["required"].remove("filed")
    assert contract.read_versions()["2.0.0"] == contract.fingerprint(schemas)


def test_delivery_history_3_0_0_is_announced_while_the_package_stays_on_2_x():
    assert [p.name for p in ANNOUNCED] == ["3.0.0"]
    announced = _schemas(contract.CONTRACTS / "3.0.0")
    schema = announced["schema.json"]
    # What leaves: what the crew worked out from its events (the Sponsor, crew#963).
    assert "analysis" not in schema["properties"]
    assert "gaps" not in announced["manifest.schema.json"]["properties"]
    assert not {
        "Analysis",
        "SprintAnalysis",
        "Delivery",
        "StoryPattern",
        "Repeated",
        "StoryCost",
    } & set(schema["$defs"])
    # What stays: what happened, and sprint-metrics' answers.
    assert {"sprints", "cards", "periods", "sprint_metrics"} <= set(schema["properties"])
    # What stays: how each card was filed (crew#974). 3.0.0 is built on 2.1.0.
    current = contract.schemas()["schema.json"]["$defs"]
    assert schema["$defs"]["Filed"] == current["Filed"]
    assert schema["$defs"]["Card"] == current["Card"]


@pytest.mark.parametrize("directory", ANNOUNCED, ids=lambda p: p.name)
def test_an_announced_version_is_recorded_with_exactly_its_schemas(directory):
    assert contract.read_versions()[directory.name] == contract.fingerprint(_schemas(directory))


@pytest.mark.parametrize("directory", ANNOUNCED, ids=lambda p: p.name)
def test_an_announced_mock_follows_its_schemas(directory):
    schemas = _schemas(directory)
    mock = directory / "mock"
    index = json.loads((mock / "delivery-history.json").read_text(encoding="utf-8"))
    manifest = json.loads((mock / "manifest.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == manifest["schema_version"] == directory.name
    jsonschema.validate(index, schemas["schema.json"])
    jsonschema.validate(manifest, schemas["manifest.schema.json"])
    periods = sorted((mock / "events").glob("*.json"))
    assert periods
    for period in periods:
        jsonschema.validate(json.loads(period.read_text()), schemas["events.schema.json"])


def _announced() -> dict:
    return json.loads((contract.CONTRACTS / "announced.json").read_text(encoding="utf-8"))


def test_what_is_announced_is_published_for_readers_crews_to_file():
    # Read from what the crew publishes, by each reader's pass (crew#960).
    assert sorted(_announced()) == [p.name for p in ANNOUNCED]


@pytest.mark.parametrize("version", sorted(_announced()))
def test_an_announcement_lists_its_mock_whole(version):
    # A raw file host lists no folder, so a reader's crew fetches what this names (crew#969).
    folder = contract.CONTRACTS / version
    on_disk = sorted(
        p.relative_to(folder).as_posix() for p in (folder / "mock").rglob("*") if p.is_file()
    )
    assert sorted(_announced()[version]["mock"]) == on_disk


@pytest.mark.parametrize("version", sorted(_announced()))
def test_an_announcement_names_its_contract_date_and_changelog(version):
    from crew_org.usage import section  # noqa: PLC0415

    said = _announced()[version]
    assert said["replaces"] == f"{int(version.split('.')[0]) - 1}.x"
    assert date.fromisoformat(said["on"])
    assert (contract.ROOT / said["contract"]).resolve() == contract.CONTRACTS / version
    log = said["changelog"]
    text = section((contract.ROOT / log["file"]).read_text(encoding="utf-8"), log["section"])
    assert text and f"replaces {said['replaces']} on {said['on']}" in text
