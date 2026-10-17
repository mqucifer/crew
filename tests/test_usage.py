"""A record's `uses` is followed to the producer's own notes (ADR 0021; crew#690).

crew-presentation's record said "used as the crew's README says". Nothing followed
it, so the Architect guessed the package's layout: a top-level `"major"` in
`schema.json`, where the README says the manifest's `schema_version`.
"""

from __future__ import annotations

import httpx

from crew_org import usage
from crew_org.project import Use, brief, parse, render

README = """# crew

## Data packages: how projects use them

- **The manifest's `schema_version`** says which schema the package follows.

```bash
# not a heading
crew package build
```

### A release

Dated, not versioned.

## Running it

Not this.
"""

RECORD = """version: 2
intent:
  scope:
    purpose: Show how the crew works.
    uses:
      - repo: mqucifer/crew
        section: "Data packages: how projects use them"
  release:
    deploys: false
  done:
    bar: CI is green.
"""


def test_a_section_runs_to_the_next_heading_at_its_level():
    found = usage.section(README, "data packages: how projects use them")
    assert found is not None
    assert "schema_version" in found and "# not a heading" in found
    assert "### A release" in found and "Dated, not versioned." in found
    assert "Running it" not in found


def test_a_missing_section_is_none():
    assert usage.section(README, "Installing") is None


def test_the_brief_shows_the_producer_s_words(monkeypatch):
    monkeypatch.setattr(usage, "_file", lambda repo, file: README)
    shown = brief(parse(RECORD))
    assert '**How this project uses mqucifer/crew**, from its `README.md`, "Data packages' in shown
    assert "The manifest's `schema_version`" in shown
    assert "Running it" not in shown


def test_what_can_t_be_read_says_why(monkeypatch):
    def down(repo, file):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(usage, "_file", down)
    use = Use(repo="mqucifer/crew", section="Data packages")
    assert usage.notes(use) == "(Couldn't read `README.md` from mqucifer/crew: no route to host.)"
    monkeypatch.setattr(usage, "_file", lambda repo, file: README)
    assert "has no section" in usage.notes(Use(repo="mqucifer/crew", section="Installing"))


def test_the_pointer_survives_the_record_s_round_trip():
    record = parse(RECORD)
    assert parse(render(record)).intent.scope.uses == record.intent.scope.uses
    assert record.intent.scope.uses[0].file == "README.md"


SCHEMA = {
    "properties": {
        "sprints": {"type": "array", "items": {"$ref": "#/$defs/Sprint"}},
        "sprint_metrics": {
            "properties": {"answers": {"additionalProperties": {"$ref": "#/$defs/Answer"}}}
        },
    },
    "$defs": {
        "Sprint": {"properties": {"name": {"type": "string"}}},
        "Answer": {"properties": {"throughput": {"type": "integer"}}},
    },
}


def test_a_schema_is_shown_as_its_fields():
    assert usage.schema_fields(SCHEMA) == [
        "sprints",
        "sprints[].name",
        "sprint_metrics",
        "sprint_metrics.answers",
        "sprint_metrics.answers.<key>.throughput",
    ]


def test_a_field_is_shown_with_what_its_schema_says_of_it():
    """Shown only `cycle_time_days` beside `cycle_time_minutes`, the UX Designer picked
    days, and the site showed "0 days" for one-day sprints (crew#761)."""
    import json

    schema = {
        "properties": {
            "cycle_time_minutes": {
                "type": "integer",
                "description": "Mean cycle time in whole minutes.",
            },
            "cycle_time_days": {
                "type": "integer",
                "description": "Mean cycle time in whole days.",
                "deprecated": True,
            },
            "throughput": {"type": "integer"},
        }
    }
    shown = usage._provided("schema.json", json.dumps(schema))
    assert shown.splitlines() == [
        "Fields:",
        "- `cycle_time_minutes`: Mean cycle time in whole minutes.",
        "- `cycle_time_days`: deprecated. Mean cycle time in whole days.",
        "- `throughput`",
    ]


def test_the_crew_s_own_descriptions_reach_the_brief():
    """The delivery-history contract's own fields carry descriptions the brief dropped."""
    from crew_org.packages.contract import schemas

    fields = usage.described_fields(schemas()["schema.json"])
    assert fields["analysis.sprints[].as_of"].startswith("The moment it describes")
    assert "sprint_metrics.answers.<key>.cycle_time_days" in fields


def test_a_stylesheet_is_shown_as_its_tokens_and_classes():
    css = (
        ":root { --color-bg: #fff; } /* .not-a-class */ "
        ".card, .card--big > p { color: var(--color-bg); }"
    )
    assert usage.css_names(css) == (["--color-bg"], ["card", "card--big"])


def test_what_is_provided_is_read_at_the_pinned_release(monkeypatch, tmp_path):
    """crew-presentation#68 promised two numbers its package doesn't carry (crew#690)."""
    import json

    from crew_org.project import Use

    asked = []

    def fetched(repo, file, ref="HEAD"):
        asked.append((repo, file, ref))
        return json.dumps(SCHEMA)

    monkeypatch.setattr(usage, "_file", fetched)
    (tmp_path / "delivery-history.tag").write_text("delivery-history-2026-10-13\n")
    use = Use(
        repo="mqucifer/crew",
        section="Data packages",
        pin="delivery-history.tag",
        contract=["contracts/delivery-history/schema.json"],
    )

    shown = usage.contract(use, tmp_path)
    assert "at `delivery-history-2026-10-13`" in shown and "`sprints[].name`" in shown
    assert asked == [
        ("mqucifer/crew", "contracts/delivery-history/schema.json", "delivery-history-2026-10-13")
    ]
    provided = usage.provided_fields([use], tmp_path)
    assert {"throughput", "sprints[].name", "name"} <= provided
    assert "crew_issues_fixed" not in provided


def test_without_a_pinned_release_nothing_is_guessed(tmp_path):
    from crew_org.project import Use

    use = Use(repo="o/r", section="s", pin="x.tag", contract=["schema.json"])
    assert "names no release in `x.tag`" in usage.contract(use, tmp_path)
    assert usage.provided_fields([use], tmp_path) == frozenset()
    assert usage.contract(Use(repo="o/r", section="s"), tmp_path) == ""
