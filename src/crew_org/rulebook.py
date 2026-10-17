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
from typing import Any, NoReturn

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
MIGRATED: frozenset[str] = frozenset({"answer_shape", "evidence", "scope"})


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
    # What a refusal says, by name, as ESLint's `meta.messages`: `{value}` for each
    # value `refuse()` is given. A rule that refuses one way has one, `default`.
    messages: tuple[tuple[str, str], ...] = ()
    # What the form tells the role it fills, and where: `Form` or `Form.field`, or
    # several, comma-separated, for a check every form of a role's answer meets.
    description: str = ""
    told_in: str = ""
    # Why a check refuses nothing a role chose, so it has nothing to tell: the call
    # layer's kind of exemption from the second policy, declared with its reason.
    exempt: str = ""
    # The rules merged into this one, by the names the code and the event log still
    # give them: each is one of its cases, as ESLint's message ids are (crew#756). A
    # line a role is told is a case of the check that enforces it.
    cases: tuple[str, ...] = ()

    def says(self, which: str = "default", **values: Any) -> str:
        return dict(self.messages)[which].format(**values)

    def fields(self, which: str = "default") -> set[str]:
        """The values a message needs."""
        text = dict(self.messages)[which]
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class Refusal(ValueError):
    """An answer refused by a declared rule, which the refusal names.

    A ValueError, so a validator raising it is read by pydantic as any refusal is,
    and pydantic keeps it in the error's context for the call layer to name. A
    step's own kind of refusal, such as the settle step's `Unsettled`, is one too.
    """

    def __init__(self, message: str, rule: str = "") -> None:
        super().__init__(message)
        self.rule = rule


def says(rule: str, which: str = "default", **values: Any) -> str:
    """A declared rule's message `which`, with its values: for a refusal said in parts."""
    return catalog()[rule].says(which, **values)


def refuse(
    rule: str, which: str = "default", *, kind: type[Refusal] = Refusal, **values: Any
) -> NoReturn:
    """Refuse an answer by the declared rule, with its message `which`, as declared."""
    raise kind(says(rule, which, **values), rule=rule)


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
            messages=tuple((str(k), str(v)) for k, v in (fields.get("messages") or {}).items()),
            description=str(fields.get("description") or ""),
            told_in=str(fields.get("told_in") or ""),
            exempt=str(fields.get("exempt") or ""),
            cases=tuple(str(c) for c in fields.get("cases") or ()),
        )
    return found


@cache
def catalog(directory: Path | None = None) -> dict[str, RuleSpec]:
    """Every declared rule, by id, from the category files."""
    found: dict[str, RuleSpec] = {}
    for path in sorted((directory or CATALOG).glob("*.yaml")):
        # Only the category files: the budget and the decision-point table live beside them.
        if path.stem not in {*CATEGORIES, UNPLACED}:
            continue
        for rule, spec in _read(path).items():
            if rule in found:
                raise ValueError(f"{rule} is declared twice: in {found[rule].category} and {path}")
            found[rule] = spec
    return found


@cache
def _cases(directory: Path | None = None) -> dict[str, str]:
    return {case: spec.id for spec in catalog(directory).values() for case in spec.cases}


def rule_of(name: str) -> str:
    """The declared rule a name is: itself, or the rule it's a case of since a merge."""
    return _cases().get(name, name)


@cache
def budget(path: Path | None = None) -> dict[str, int]:
    """Each role's guidance budget: how many lines it may be told and not checked."""
    path = path or BUDGET
    if not path.exists():
        return {}
    return {str(k): int(v) for k, v in (yaml.safe_load(path.read_text("utf-8")) or {}).items()}


def told(form: str) -> dict[str, list[str]]:
    """What a form tells its role, from its rules' declarations, by field ("" the form)."""
    out: dict[str, list[str]] = {}
    for spec in catalog().values():
        if not (spec.description and spec.category in MIGRATED):
            continue
        for place in (p.strip() for p in spec.told_in.split(",")):
            here, _, field = place.partition(".")
            if here == form:
                out.setdefault(field, []).append(spec.description)
    return out


def write(
    declared: dict[str, dict[str, dict[str, Any]]], directory: Path | None = None
) -> list[Path]:
    """The catalog's files, one per category, each rule's decided fields in order."""
    directory = directory or CATALOG
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
    _cases.cache_clear()
    return written
