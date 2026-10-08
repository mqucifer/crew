"""The contract a package's consumers build against, generated from its model (ADR 0021).

`contracts/<package>/` holds the JSON Schemas, written from the model by
`crew package schema`, and `versions.json`: each schema version and a
fingerprint of the schemas it named. The tests regenerate the schemas and
compare. So the model, the committed schema and the version can't drift apart:
change the model and the schema is stale; regenerate it without a new version
and the fingerprint no longer matches the version's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from crew_org.packages import delivery_history as dh

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "contracts" / dh.NAME
VERSIONS = "versions.json"


class StaleVersion(Exception):
    """The schema changed and its version didn't."""


def schemas() -> dict[str, dict[str, Any]]:
    """Each schema file's content: what the package writes, as a consumer reads it."""
    return {
        "schema.json": dh.DeliveryHistory.model_json_schema(mode="serialization"),
        "events.schema.json": dh.PeriodEvents.model_json_schema(mode="serialization"),
        "manifest.schema.json": dh.Manifest.model_json_schema(mode="serialization"),
    }


def fingerprint(files: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def read_versions(directory: Path = CONTRACTS) -> dict[str, str]:
    path = directory / VERSIONS
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def check_version(versions: dict[str, str], version: str, print_: str) -> None:
    """Raise unless `version` names exactly this schema, or is new and later than the rest."""
    known = versions.get(version)
    if known == print_:
        return
    if known is not None:
        raise StaleVersion(
            f"the schema changed but SCHEMA_VERSION is still {version}: raise it, MAJOR "
            "if a consumer could break on the change, MINOR if it only adds (ADR 0021)"
        )
    later = [v for v in versions if _version(v) >= _version(version)]
    if later:
        raise StaleVersion(f"SCHEMA_VERSION {version} isn't later than {max(later, key=_version)}")


def write(directory: Path = CONTRACTS) -> list[Path]:
    """Write the schemas, and record this version's fingerprint if it's new."""
    files = schemas()
    print_ = fingerprint(files)
    versions = read_versions(directory)
    check_version(versions, dh.SCHEMA_VERSION, print_)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = directory / name
        path.write_text(_dump(content), encoding="utf-8")
        written.append(path)
    versions[dh.SCHEMA_VERSION] = print_
    path = directory / VERSIONS
    path.write_text(_dump(dict(sorted(versions.items(), key=lambda kv: _version(kv[0])))))
    written.append(path)
    return written
