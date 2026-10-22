"""Every kind of event the crew records, read from the code (crew#870).

The Sponsor, 2026-10-22: "we can't trim if we don't know what we even have." The
records reference listed the event kinds by name only, so nobody choosing what a Goal
measures could tell what one means, what it carries, or whether it leaves the crew.
This reads all of it from the code, as `rule_list` does for the rules, so the page
can't drift from it:

- **where each kind is written:** the calls that name it, module and function;
- **the fields a site writes by name,** which a test holds to `events.FIELDS`;
- **what `delivery-history` makes of it,** from the package's own mapping: the part
  `_event` builds for it, or the functions that read it, or nothing;
- **the notes,** by the rule each names, and the words of those that name none.
"""

from __future__ import annotations

import ast
import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from crew_org.events import EventKind

SRC = Path(__file__).resolve().parent
# The module that declares the kinds; its calls pass a kind through, they don't name one.
_DECLARING = "events.py"


@dataclass(frozen=True)
class Site:
    """A call that writes an event: where it is, as module:function."""

    module: str
    function: str

    def __str__(self) -> str:
        return f"{self.module}:{self.function}"


@dataclass
class Written:
    """One kind's writing calls, and the fields each names."""

    sites: set[Site] = field(default_factory=set)
    fields: dict[str, set[Site]] = field(default_factory=lambda: defaultdict(set))


@dataclass(frozen=True)
class Note:
    """A call that writes a `note`: its rule, its summary's fixed words, its fields."""

    site: Site
    rule: str | None
    words: str
    fields: tuple[str, ...] = ()


def _kind(node: ast.expr) -> EventKind | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "EventKind"
        and node.attr in EventKind.__members__
    ):
        return EventKind[node.attr]
    return None


def _sources() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name != _DECLARING:
            out.append((str(path.relative_to(SRC)), ast.parse(path.read_text("utf-8"))))
    return out


def _calls(tree: ast.Module) -> list[tuple[str, ast.Call]]:
    """Every call, with the function it's in."""
    found: list[tuple[str, ast.Call]] = []

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else where
            )
            if isinstance(child, ast.Call):
                found.append((where, child))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def _named_keys(call: ast.Call) -> list[str]:
    """The detail fields a call writes by name: `detail={...}`'s keys, or its keywords."""
    keys: list[str] = []
    for kw in call.keywords:
        if kw.arg == "detail" and isinstance(kw.value, ast.Dict):
            keys += [
                k.value
                for k in kw.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
        elif kw.arg not in (None, "detail", "kind", "role", "card", "summary", "at"):
            keys.append(kw.arg)
    return [k for k in keys if isinstance(k, str)]


# The calls that take an event's detail as keywords, beside `CrewEvent(detail=...)`.
_KEYWORD_WRITERS = ("note", "record", "emit")
# `move_card`'s own parameters, which aren't detail fields; its other keywords are.
_MOVE_PARAMS = ("item_id", "to", "by", "card", "frm", "summary", "repo", "kind")


@cache
def written() -> dict[EventKind, Written]:
    """Each kind's writing calls in the crew's code, and the fields each names."""
    out: dict[EventKind, Written] = defaultdict(Written)
    for module, tree in _sources():
        for function, call in _calls(tree):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else ""
            name = name or (call.func.id if isinstance(call.func, ast.Name) else "")
            site = Site(module, function)
            kinds = [k for a in call.args if (k := _kind(a))]
            kinds += [k for kw in call.keywords if kw.value is not None and (k := _kind(kw.value))]
            if name == "move_card":
                kind = kinds[0] if kinds else EventKind.CARD_MOVED
                out[kind].sites.add(site)
                for kw in call.keywords:
                    if kw.arg is not None and kw.arg not in _MOVE_PARAMS:
                        out[kind].fields[kw.arg].add(site)
                continue
            for kind in kinds:
                out[kind].sites.add(site)
                if name == "CrewEvent" or name in _KEYWORD_WRITERS:
                    for key in _named_keys(call):
                        out[kind].fields[key].add(site)
    return dict(out)


def _said(node: ast.expr | None) -> str:
    """A summary's fixed words, with `…` for each part filled in."""
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(str(p.value) if isinstance(p, ast.Constant) else "…" for p in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _said(node.left) + _said(node.right)
    return "…"


def _summary_words(call: ast.Call) -> str:
    summary = call.args[1] if len(call.args) > 1 else None
    for kw in call.keywords:
        if kw.arg == "summary":
            summary = kw.value
    words = " ".join(_said(summary).split())
    while "……" in words:
        words = words.replace("……", "…")
    return words


def _rule_of(call: ast.Call) -> str | None:
    found: ast.expr | None = None
    for kw in call.keywords:
        if kw.arg == "rule":
            found = kw.value
        if kw.arg == "detail" and isinstance(kw.value, ast.Dict):
            for key, value in zip(kw.value.keys, kw.value.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "rule":
                    found = value
    if isinstance(found, ast.Constant) and isinstance(found.value, str):
        return found.value
    if isinstance(found, ast.Attribute) and isinstance(found.value, ast.Name):
        from crew_org.rules import Rule  # noqa: PLC0415

        if found.value.id == "Rule" and found.attr in Rule.__members__:
            return Rule[found.attr].value
    return None


@cache
def notes() -> list[Note]:
    """Every call that writes a `note`, by its rule or the words it starts with."""
    out = []
    for module, tree in _sources():
        for function, call in _calls(tree):
            kinds = [k for a in call.args if (k := _kind(a))]
            kinds += [k for kw in call.keywords if kw.value is not None and (k := _kind(kw.value))]
            if EventKind.NOTE in kinds:
                named = tuple(sorted(set(_named_keys(call)) - {"rule"}))
                out.append(
                    Note(Site(module, function), _rule_of(call), _summary_words(call), named)
                )
    return sorted(out, key=lambda n: (n.rule or "", str(n.site), n.words))


# --- what delivery-history makes of each kind ---------------------------------------------


def _strings(node: ast.AST, constants: dict[str, set[str]]) -> set[str]:
    """The event kinds a test names: its strings, and module constants of strings."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
        elif isinstance(n, ast.Name) and n.id in constants:
            out |= constants[n.id]
    return out & {k.value for k in EventKind}


@cache
def published() -> dict[EventKind, str]:
    """What `delivery-history` makes of each kind, from its own code: a part, or a reader."""
    from crew_org.packages import delivery_history as dh  # noqa: PLC0415

    tree = ast.parse(inspect.getsource(dh))
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    constants: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
            for t in targets:
                if isinstance(t, ast.Name):
                    constants[t.id] = names
    parts = {
        name: obj.model_fields["kind"].default
        for name, obj in vars(dh).items()
        if isinstance(obj, type) and issubclass(obj, dh._Part) and "kind" in obj.model_fields
    }
    found: dict[str, set[str]] = defaultdict(set)
    seen: set[tuple[str, frozenset[str]]] = set()
    # A part picked by kind into a name, `cls = WorkStarted if kind == ... else ...`: for
    # each name, which part each kind gives it.
    chosen: dict[str, dict[str, str]] = {}

    def _picks(statement: ast.Assign) -> tuple[str, dict[str, str]] | None:
        value, target = statement.value, statement.targets[0]
        if not (isinstance(value, ast.IfExp) and isinstance(target, ast.Name)):
            return None
        named = _strings(value.test, constants)
        body, orelse = value.body, value.orelse
        if not (named and isinstance(body, ast.Name) and isinstance(orelse, ast.Name)):
            return None
        every = {k.value for k in EventKind}
        return target.id, {k: (body.id if k in named else orelse.id) for k in every}

    def _chosen(test: ast.expr, left: frozenset[str]) -> set[str]:
        """The kinds `if cls is WorkFinished` keeps, where `cls` was picked by kind."""
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id in chosen
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Name)
        ):
            return set()
        part = test.comparators[0].id
        return {k for k in left if chosen[test.left.id].get(k) == part}

    def walk(statements: list[ast.stmt], kinds: frozenset[str]) -> None:
        """Each part built, under the kinds that reach it: a branch on the kind narrows
        them, and one that returns leaves the rest for what follows it."""
        left = kinds
        for statement in statements:
            named = _strings(statement.test, constants) if isinstance(statement, ast.If) else set()
            if isinstance(statement, ast.If) and (chose := _chosen(statement.test, left)):
                named = chose
            if isinstance(statement, ast.Assign) and (picked := _picks(statement)):
                chosen[picked[0]] = picked[1]
            if isinstance(statement, ast.If) and named:
                walk(statement.body, left & named)
                walk(statement.orelse, left - named)
                if any(isinstance(n, ast.Return) for n in statement.body):
                    left = left - named
                continue
            if isinstance(statement, ast.If):
                walk(statement.body, left)
                walk(statement.orelse, left)
                continue
            scan(statement, left)

    def scan(node: ast.AST, kinds: frozenset[str]) -> None:
        if isinstance(node, ast.Compare):
            return  # `cls is WorkFinished` names a part it doesn't build
        if isinstance(node, ast.If):
            walk([node], kinds)
            return
        if isinstance(node, ast.IfExp) and (named := _strings(node.test, constants)):
            scan(node.body, kinds & named)
            scan(node.orelse, kinds - named)
            return
        if isinstance(node, ast.Name) and node.id in parts:
            for kind in kinds:
                found[kind].add(parts[node.id])
        if isinstance(node, ast.Name) and node.id in chosen:
            for kind in kinds:
                if (name := chosen[node.id].get(kind)) in parts:
                    found[kind].add(parts[name])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            callee = functions.get(node.func.id)
            if callee is not None and (callee.name, kinds) not in seen:
                seen.add((callee.name, kinds))
                walk(callee.body, kinds)
        for child in ast.iter_child_nodes(node):
            scan(child, kinds)

    every = frozenset(k.value for k in EventKind)
    walk(functions["_event"].body, every)
    out: dict[EventKind, str] = {}
    for kind in EventKind:
        if kind.value in found:
            shown = ", ".join(f"`{p}`" for p in sorted(found[kind.value]))
            out[kind] = f"as {shown}"
    # Read elsewhere in the package: for a card's fields or the analysis, not as a part.
    for name, function in functions.items():
        if name == "_event":
            continue
        for kind in EventKind:
            if kind not in out and kind.value in _strings(function, {}):
                out[kind] = f"read by `{name}`"
    return out


def rule_meaning(rule: str) -> str | None:
    """What a rule means: its `rules.Rule` docstring, its declaration in the catalog, or,
    for a case of a declared rule, that rule's."""
    from crew_org import rulebook  # noqa: PLC0415
    from crew_org.rules import Rule, meanings  # noqa: PLC0415

    said = meanings(Rule)
    for member in Rule:
        if member.value == rule:
            return said[member.name]
    catalog = rulebook.catalog()
    spec = catalog.get(rule)
    if spec is not None and spec.description:
        return " ".join(spec.description.split())
    for parent in catalog.values():
        if rule in (parent.cases or ()) and parent.description:
            return " ".join(parent.description.split())
    return None


def undescribed(kinds: type[EventKind] = EventKind) -> list[str]:
    """The kinds with no meaning written under them, by name."""
    from crew_org.rules import meanings  # noqa: PLC0415

    said = meanings(kinds)
    return [k.name for k in kinds if not said.get(k.name)]


def parts_from_elsewhere() -> list[str]:
    """The package's event parts no event kind becomes: built from GitHub, say."""
    from typing import get_args  # noqa: PLC0415

    from crew_org.packages import delivery_history as dh  # noqa: PLC0415

    parts = {m.model_fields["kind"].default for m in get_args(get_args(dh.Event)[0])}
    reached = {
        part.strip("`")
        for said in published().values()
        if said.startswith("as ")
        for part in said.removeprefix("as ").split(", ")
    }
    return sorted(parts - reached)
