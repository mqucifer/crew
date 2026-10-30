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


def test_delivery_history_2_0_0_shipped_exactly_as_announced():
    """Its announcement recorded the fingerprint; the switch removed the announced copy."""
    assert ANNOUNCED == []
    assert dh.SCHEMA_VERSION == "2.0.0"
    assert contract.read_versions()["2.0.0"] == contract.fingerprint(contract.schemas())


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
