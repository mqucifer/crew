"""sprint-metrics' answers for the sprints a package covers (crew#521, criterion 2).

sprint-metrics keeps and computes; the crew asks it for its answers and carries
them unchanged (ADR 0022). One `GET /range` from the package's first sprint to
its last returns each sprint's numbers with the sprint before it as `prior`.

**The release the crew reads is `RELEASE`,** and its answers follow
sprint-metrics' published schema at that tag. The service may run a later release
at the same MAJOR: mqucifer/infra's inventory names the crew as its reader at this
constant, so a release's deploy never moves it to another MAJOR before this does
(crew#829). `crew package schema` fetches that schema once into `upstream/`, and
the package's own schema embeds it (`contract.schemas()`): a consumer validates
sprint-metrics' part against sprint-metrics' own contract, through the crew's.
Raising `RELEASE` and regenerating changes the package's schema, so its version
must rise with it.

**The service's answer is checked against that schema before it's carried.** A
service still on an older release fails the check, and the package is built
without the part, which `release.refusal()` won't release.

**Only what the schema names is carried** (ADR 0020's allow-list): a field the
service adds later stays out until the pinned release names it. What it leaves
open (flags, failure causes and breakdowns, the superseded breakdown) keeps only
measures and names: numbers, true and false, and single words such as `VERIFY`,
never a sentence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# The sprint-metrics release the crew reads; the service's deploy keeps to its MAJOR.
RELEASE = "1.3.1"
UPSTREAM = Path(__file__).resolve().parent / "upstream"
SCHEMA_URL = (
    "https://raw.githubusercontent.com/mqucifer/sprint-metrics/v{release}/schemas/sprint-range.json"
)
# A name, such as a failure class or a role: one word, never a sentence.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")


class Unanswered(Exception):
    """sprint-metrics didn't answer for the package: why, in words a person can act on."""


def schema_path(release: str = RELEASE) -> Path:
    return UPSTREAM / f"sprint-metrics-v{release}" / "sprint-range.json"


def range_schema(release: str = RELEASE) -> dict[str, Any]:
    """sprint-metrics' `GET /range` schema at `release`, as published."""
    loaded: dict[str, Any] = json.loads(schema_path(release).read_text(encoding="utf-8"))
    return loaded


def entry_schema(release: str = RELEASE) -> dict[str, Any]:
    """The schema of one sprint's answer: an entry of `/range`'s `sprints`."""
    entry: dict[str, Any] = range_schema(release)["properties"]["sprints"]["additionalProperties"]
    return entry


def fetch_schema(release: str = RELEASE, *, timeout: float = 10.0) -> Path | None:
    """Fetch the schema at `release` if it isn't here yet. The path written, or None."""
    path = schema_path(release)
    if path.exists():
        return None
    import httpx  # noqa: PLC0415

    response = httpx.get(SCHEMA_URL.format(release=release), timeout=timeout)
    response.raise_for_status()
    json.loads(response.text)  # refuse anything that isn't JSON before keeping it
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    return path


def allowed(value: Any, schema: dict[str, Any]) -> Any:
    """`value` with only what `schema` names: the allow-list (ADR 0020)."""
    named = schema.get("properties")
    if isinstance(value, dict) and named is not None:
        return {k: allowed(v, named[k]) for k, v in value.items() if k in named}
    if isinstance(value, list) and "items" in schema:
        return [allowed(v, schema["items"]) for v in value]
    if named is None and "items" not in schema:
        return _open(value)
    return value


def _open(value: Any) -> Any:
    """What the schema leaves open, cut to measures and names."""
    if isinstance(value, dict):
        kept = {k: _open(v) for k, v in value.items() if isinstance(k, str) and _NAME.match(k)}
        return {k: v for k, v in kept.items() if v is not None}
    if isinstance(value, list):
        return [v for v in (_open(v) for v in value) if v is not None]
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and _NAME.match(value):
        return value
    return None


def checked(body: dict[str, Any], sprints: list[str], release: str = RELEASE) -> dict[str, Any]:
    """Each sprint's answer from a `/range` body, checked and cut to what the schema names.

    Raises `Unanswered` if the body doesn't follow the release's schema, or a
    sprint has no answer.
    """
    import jsonschema  # type: ignore[import-untyped]  # noqa: PLC0415

    try:
        jsonschema.validate(body, range_schema(release))
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "the answer"
        raise Unanswered(
            f"sprint-metrics' answer doesn't follow its {release} schema at {where}: "
            f"{exc.message}. Is the service running {release}?"
        ) from None
    answers = body["sprints"]
    missing = [s for s in sprints if s not in answers]
    if missing:
        raise Unanswered(f"sprint-metrics has no answer for {', '.join(missing)}")
    entry = entry_schema(release)
    return {s: allowed(answers[s], entry) for s in sprints}


def ask(url: str, sprints: list[str], *, timeout: float = 30.0) -> dict[str, Any]:
    """Each of `sprints`' answers from the service at `url`, oldest first."""
    if not sprints:
        return {}
    import httpx  # noqa: PLC0415

    try:
        response = httpx.get(
            f"{url.rstrip('/')}/range",
            params={"start": sprints[0], "end": sprints[-1]},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise Unanswered(f"sprint-metrics at {url} couldn't be reached: {exc}") from None
    if response.status_code != 200:
        raise Unanswered(f"sprint-metrics answered {response.status_code}: {response.text[:200]}")
    return checked(response.json(), sprints)
