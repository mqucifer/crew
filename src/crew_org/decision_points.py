"""Every point where the tick decides a card's handling, held to the rules list (crew#756).

`rule_list` finds rules by the shapes they take: a refusal, a feedback, a guard, a
skip. A rule in a shape it doesn't know is missed: `refine_panel.builds_on_unsplit`
was, until crew#773. This is the independent check. It finds every branch in the
tick's code that ends or redirects a card's handling, whatever shape it takes,
and holds each to the catalog: it maps to a declared rule by evidence in the code,
or it's listed in `config/rules/decision_points.yaml` as a rule's or as not a rule,
with why. A test fails on a point in neither, or an entry whose point is gone.

A decision point is:
- a branch in a loop over cards, epics, stories, issues or pull requests that skips
  the item or stops the loop (`continue`, `break`, `return`);
- a branch in a step for one card that ends it early (`return`);
- any branch that adds to a tick's result lists, moves a card or changes a label;
- a comprehension that filters cards, as `approved_epics` and the label gates do.

In `tools/`, only the branches of a function a declared rule checks with are points,
and they are that rule's.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from crew_org import rule_list, rulebook
from crew_org.rule_list import Source, _enclosing, _parents, sources
from crew_org.rules import Rule

TABLE = rulebook.CATALOG / "decision_points.yaml"

# What a tick's results collect, by name: a branch adding to one decides something.
_RESULTS = (
    ("crew_org.flows.board_flow", "TickResult"),
    ("crew_org.flows.delivery", "DeliveryResult"),
    ("crew_org.flows.delivery", "DeliveryOutcome"),
    ("crew_org.flows.loop", "LoopResult"),
)
# The words that make a loop one over cards, or a step one for a card.
_CARDISH = re.compile(
    r"\b(card|cards|epic|epics|story|stories|child|children|issue|issues|pull|pulls|"
    r"pr|prs|goal|goals|bug|bugs|number|numbers|item|items)\b"
)
_STEP_ARGS = frozenset(
    ["card", "epic", "story", "pull", "pr", "issue", "goal", "number", "bug", "item"]
)
# The tick's code, by module: the flows, the crews, the call layer and the CLI.
_TICK = ("crew_org.flows.", "crew_org.crews.")
_TICK_MODULES = ("crew_org.calls", "crew_org.cli")


@dataclass
class Point:
    id: str
    path: str
    function: str
    line: int
    kind: str
    condition: str
    evidence: set[str] = field(default_factory=set)


@cache
def result_lists() -> frozenset[str]:
    names = set()
    for module, cls in _RESULTS:
        found = getattr(importlib.import_module(module), cls)
        names |= {f.name for f in dataclasses.fields(found) if "list" in str(f.type).lower()}
    return frozenset(names)


def _point_id(source: Source, function: str, kind: str, condition: str) -> str:
    """Module, function and the condition itself: stable while the condition is."""
    digest = hashlib.sha1(f"{kind}:{condition}".encode()).hexdigest()[:8]
    return f"{source.module.removeprefix('crew_org.')}.{function}:{kind}:{digest}"


def _walk_shallow(nodes: list[ast.stmt]) -> list[ast.AST]:
    """A block's nodes, not those of the functions and classes defined in it."""
    out: list[ast.AST] = []
    todo: list[ast.AST] = list(nodes)
    while todo:
        here = todo.pop()
        out.append(here)
        if isinstance(here, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        todo += list(ast.iter_child_nodes(here))
    return out


def _ends(nodes: list[ast.AST], loop: bool) -> str:
    """What a branch's body does to the handling: skip the item, end it, or neither."""
    for node in nodes:
        if loop and isinstance(node, ast.Continue | ast.Break):
            return "skip"
        if isinstance(node, ast.Return):
            return "end"
    return ""


def _acts(nodes: list[ast.AST]) -> str:
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if (
            name == "append"
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and func.value.attr in result_lists()
        ):
            return f"result:{func.value.attr}"
        if name in ("move_card", "set_status"):
            return "move"
        if name == "label" or name in ("add_labels", "remove_label", "remove_labels"):
            return "label"
    return ""


def _card_loop(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    loop = _enclosing(node, parents, ast.For | ast.AsyncFor)
    fn = _enclosing(node, parents, ast.FunctionDef)
    if loop is None or (fn is not None and _enclosing(loop, parents, ast.FunctionDef) is not fn):
        return False
    return bool(_CARDISH.search(ast.unparse(loop.target) + " " + ast.unparse(loop.iter)))


def _card_step(fn: ast.FunctionDef | None) -> bool:
    if fn is None:
        return False
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    annotated = any(
        a.annotation is not None and "Card" in ast.unparse(a.annotation)
        for a in fn.args.args + fn.args.kwonlyargs
    )
    return bool(names & _STEP_ARGS) or annotated


def _in_scope(source: Source) -> bool:
    return source.module.startswith(_TICK) or source.module in _TICK_MODULES


@cache
def points() -> tuple[Point, ...]:
    """Every decision point in the tick's code, with the rules its code shows."""
    checked_by = _checked_functions()
    found: dict[str, Point] = {}
    for source in sources():
        tools = source.module.startswith("crew_org.tools.")
        if not (_in_scope(source) or tools):
            continue
        parents = _parents(source.tree)
        for node in ast.walk(source.tree):
            fn = _enclosing(node, parents, ast.FunctionDef)
            name = fn.name if fn is not None else "<module>"
            where = f"{source.path}:{name}"
            if tools and where not in checked_by:
                continue
            kind, condition = "", ""
            if isinstance(node, ast.If):
                body = _walk_shallow(node.body)
                kind = _acts(body) or _ends(body, _card_loop(node, parents))
                if kind == "end" and not (_card_loop(node, parents) or _card_step(fn) or tools):
                    kind = ""
                condition = ast.unparse(node.test)
            elif isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
                gens = [g for g in node.generators if g.ifs]
                if gens and any(
                    _CARDISH.search(ast.unparse(g.iter) + " " + ast.unparse(g.target)) for g in gens
                ):
                    kind = "filter"
                    condition = " and ".join(ast.unparse(i) for g in gens for i in g.ifs)
            if not kind:
                continue
            condition = " ".join(condition.split())[:200]
            point_id = _point_id(source, name, kind, condition)
            point = found.setdefault(
                point_id,
                Point(point_id, source.path, name, getattr(node, "lineno", 0), kind, condition),
            )
            point.evidence |= _evidence(source, node, fn, where, checked_by)
    return tuple(sorted(found.values(), key=lambda p: (p.path, p.line)))


@cache
def _checked_functions() -> dict[str, set[str]]:
    """Each function a declared rule checks with, by `path:function`, and its rules."""
    out: dict[str, set[str]] = {}
    for entry in rule_list.entries():
        # The audit's own rules come from its table, point by point: a new branch in
        # their functions is reviewed, never taken as theirs by where it is.
        if entry.group == "decision":
            continue
        for site in entry.checked:
            out.setdefault(site, set()).add(entry.id)
    return out


@cache
def _by_message() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for entry in rule_list.entries():
        for message in entry.messages:
            out.setdefault(message, set()).add(entry.id)
    return out


def _evidence(
    source: Source,
    node: ast.AST,
    fn: ast.FunctionDef | None,
    where: str,
    checked_by: dict[str, set[str]],
) -> set[str]:
    """The declared rules a decision point's own code names, or calls on."""
    found: set[str] = set()
    known = {e.id for e in rule_list.entries()}
    parts = _walk_shallow([node]) if isinstance(node, ast.stmt) else list(ast.walk(node))
    for sub in parts:
        # A rules.Rule named, or a declared rule refused by.
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "Rule"
            and sub.attr in Rule.__members__
        ):
            found.add(Rule[sub.attr].value)
        if (named := rule_list._refused_by(sub)) is not None and named in known:
            found.add(named)
        raised = rule_list._refusal(sub)
        if raised is not None:
            found |= _by_message().get(rule_list._template(raised.args[0], source.tree), set())
        value = rule_list._is_feedback(sub)
        if value is not None:
            for text in rule_list._texts(value, source.tree):
                found |= _by_message().get(text, set())
        # A failure class recorded, as a guard's events do.
        if isinstance(sub, ast.Dict):
            klass = rule_list._failure_class(sub)
            if klass:
                found |= {
                    e.id
                    for e in rule_list.entries()
                    if e.failure_class == klass and e.where == where
                }
        # A hold said in words: the skip rule its reason makes.
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "append"
            and isinstance(sub.func.value, ast.Attribute)
            and sub.func.value.attr == "skipped"
            and sub.args
        ):
            given = sub.args[0]
            why = given.elts[1] if isinstance(given, ast.Tuple) and len(given.elts) > 1 else None
            if why is None and isinstance(given, ast.Call):
                why = next((k.value for k in given.keywords if k.arg == "skipped"), None)
            if why is not None:
                text = rule_list._template(why, source.tree).replace("{}", " ")
                rule = f"skip.{rule_list._short(source)}.{rule_list._slug(text, 6)}"
                if rule in known:
                    found.add(rule)
    # A branch that tests what a rule's check returned is that rule's.
    if isinstance(node, ast.If) and fn is not None:
        for checked_source, checked in rule_list._tested_calls(source, fn, node):
            found |= checked_by.get(f"{checked_source.path}:{checked.name}", set())
        for call in (n for n in ast.walk(node.test) if isinstance(n, ast.Call)):
            resolved = rule_list._resolve(call, source)
            if resolved is not None:
                found |= checked_by.get(f"{resolved[0].path}:{resolved[1].name}", set())
    # Inside a function a rule checks with, the point is part of that rule.
    found |= checked_by.get(where, set())
    return found


# --- the reviewed table -----------------------------------------------------------------


@cache
def table(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Each reviewed point: `rule: <id>` or `not_a_rule: <why>`, by point id."""
    path = path or TABLE
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): {str(a): str(b) for a, b in (v or {}).items()} for k, v in raw.items()}


def unresolved() -> list[Point]:
    """Points that map to no rule by evidence and have no entry in the table."""
    reviewed = table()
    return [p for p in points() if not p.evidence and p.id not in reviewed]


def stale() -> list[str]:
    """Table entries whose point is gone, or whose rule isn't declared."""
    ids = {p.id for p in points()}
    declared = rulebook.catalog()
    out = []
    for point, entry in table().items():
        if point not in ids:
            out.append(f"{point}: no such decision point")
        elif "rule" in entry and entry["rule"] not in declared:
            out.append(f"{point}: rule {entry['rule']} isn't declared")
    return out
