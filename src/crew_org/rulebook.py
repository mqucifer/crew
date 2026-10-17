"""Every rule a role meets, declared once (crew#756, ADR 0031).

Managed the way linters manage large rule sets, ruff's and ESLint's catalogs: each
rule has an id, a category, and one declaration that says what a person decided
about it. The catalog is `config/rules/`, one file per category, so a category's
rules are read and changed in one place.

A declaration holds only what can't be read from the code: its category, the role
that owns it, the incidents it came from, whether it's guidance, and, for a rule
that refuses through `refuse()`, its message and how its form tells it. Where a
rule is checked and where it's told are read from the code by `rule_list`, and the
tests hold the two together:

- every rule the code has is declared, and every declaration is a rule the code has;
- a rule that's told is checked, or labelled guidance (the first policy);
- a rule that's checked is told, in the description of the form it checks, from the
  declaration its refusal comes from (the second policy). The call layer's checks
  are exempt: the model doesn't choose an empty answer or a cut field;
- each role's guidance stays within its budget.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

CATALOG = Path(__file__).resolve().parent / "config" / "rules"
BUDGET = CATALOG / "budget.yaml"
UNPLACED = "unplaced"

# What the rules are managed by: what a rule protects, not where it lives (the
# Sponsor, 2026-10-17, crew#756).
CATEGORIES = {
    "answer_shape": "The answer parses and is complete",
    "scope": "The work stays in the story",
    "edits": "A change applies cleanly",
    "evidence": "Tests prove the criteria",
    "guards": "Safety guards",
    "flow": "When a card moves",
    "contracts": "What other work relies on, and declared changes to it",
    "guidance": "Writing guidance",
}

# The categories whose checks refuse through `refuse()`, their message and their
# form's description from the declaration. The others move in their own cleanups.
MIGRATED: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RuleSpec:
    """One rule, as decided: everything about it the code can't say itself."""

    id: str
    category: str
    # The role it serves. Empty for a rule several roles share until its category's
    # cleanup gives it one.
    owner: str = ""
    # The incidents it came from.
    origin: tuple[str, ...] = ()
    # Told and deliberately not checked: counted against the role's budget.
    guidance: bool = False
    # What a refusal says, with `{name}` for each value `refuse()` is given.
    message: str = ""
    # What the form tells the role it fills, and where: `Form` or `Form.field`.
    description: str = ""
    told_in: str = ""

    def says(self, **values: Any) -> str:
        return self.message.format(**values)

    def fields(self) -> set[str]:
        """The values the message needs."""
        return {name for _, name, _, _ in string.Formatter().parse(self.message) if name}


def _read(path: Path) -> dict[str, RuleSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    category = "" if path.stem == UNPLACED else path.stem
    found = {}
    for rule, decided in raw.items():
        fields = decided or {}
        found[rule] = RuleSpec(
            id=rule,
            category=category,
            owner=str(fields.get("owner") or ""),
            origin=tuple(fields.get("origin") or ()),
            guidance=bool(fields.get("guidance")),
            message=str(fields.get("message") or ""),
            description=str(fields.get("description") or ""),
            told_in=str(fields.get("told_in") or ""),
        )
    return found


@cache
def catalog(directory: Path = CATALOG) -> dict[str, RuleSpec]:
    """Every declared rule, by id, from the category files."""
    found: dict[str, RuleSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name == BUDGET.name:
            continue
        for rule, spec in _read(path).items():
            if rule in found:
                raise ValueError(f"{rule} is declared twice: in {found[rule].category} and {path}")
            found[rule] = spec
    return found


@cache
def budget(path: Path = BUDGET) -> dict[str, int]:
    """Each role's guidance budget: how many lines it may be told and not checked."""
    if not path.exists():
        return {}
    return {str(k): int(v) for k, v in (yaml.safe_load(path.read_text("utf-8")) or {}).items()}


def told(form: str) -> dict[str, list[str]]:
    """What a form tells its role, from its rules' declarations, by field ("" the form)."""
    out: dict[str, list[str]] = {}
    for spec in catalog().values():
        here, _, field = spec.told_in.partition(".")
        if here == form and spec.description and spec.category in MIGRATED:
            out.setdefault(field, []).append(spec.description)
    return out


def write(declared: dict[str, dict[str, dict[str, Any]]], directory: Path = CATALOG) -> list[Path]:
    """The catalog's files, one per category, each rule's decided fields in order."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for category, rules in declared.items():
        path = directory / f"{category or UNPLACED}.yaml"
        title = CATEGORIES.get(category, "Rules no category holds yet")
        body = {
            rule: {k: v for k, v in fields.items() if v} or None for rule, fields in rules.items()
        }
        text = yaml.safe_dump(body, sort_keys=True, allow_unicode=True, width=100)
        path.write_text(
            f"# {title}. Declared once each (crew#756, ADR 0031): change a rule here.\n"
            + text.replace(": null\n", ":\n"),
            encoding="utf-8",
        )
        written.append(path)
    catalog.cache_clear()
    return written
