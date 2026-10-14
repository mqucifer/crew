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
