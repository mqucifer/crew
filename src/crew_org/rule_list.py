"""Every rule a role meets, listed from the code (crew#756).

The rules were added one incident at a time: a form's refusal, a guard on a
change, a gate, a line in a prompt. Nobody could list the ones a role meets, and
some were told and never checked, or checked and never told. This module reads
them out of the code, so the list can't drift from it: `crew rules` writes
`docs/reference/rules.md`, and a test regenerates it and compares, as it does for
`docs/reference/records.md`.

A rule is one of seven kinds:

- **decides:** a `rules.Rule` the crew's own code records when it retries,
  returns, blocks or holds;
- **form:** a validator on an answer's form, which refuses the answer (or, in a
  few, repairs it without saying);
- **call:** the call layer's refusals, for every form (empty, runaway, blocks);
- **check:** a step's own check, or a flow's, whose finding is sent back to the
  role as the reason it's asked again;
- **guard:** a check on a change in delivery, whose failure class is recorded
  and whose reason is sent back;
- **edit:** an edit that can't be applied, and why, sent back to the Developer;
- **told:** a line a role is told (the constitution, its entry in `agents.yaml`,
  its step's prompt, a field's description in its form) that nothing above checks.

Where a rule is told, and what checks a line that's told, are linked by name
where the code allows it: a field's description to that field's validator, a
constitution rule to code that cites it as §N.M or by the incident it came from,
and a rule's *Enforced:* line to the code it names. A link found only by shared
wording is marked "maybe": it's for a person to confirm, not a fact.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any, get_args

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from crew_org import constitution, rulebook
from crew_org.permissions import load_agents
from crew_org.rules import Rule, meanings

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
PAGE = ROOT / "docs" / "reference" / "rules.md"
CONSTITUTION = ROOT / "docs" / "ways-of-working.md"

GROUPS = {
    "decides": "The crew's own decisions (`rules.Rule`)",
    "form": "An answer's form refuses it",
    "call": "The call layer refuses any answer",
    "check": "A flow's own check sends the answer back",
    "guard": "A guard on a change sends it back",
    "edit": "An edit can't be applied, and why is sent back",
    "tool": "A session's tool refuses a call, and says why in its reply",
    "repair": "The crew changes the work itself, and records it",
    "skip": "The crew holds work back, said only in words",
    "decision": "The tick decides a card's handling, found by the decision-point audit",
    "config": "A setting that changes what the tick does",
    "told": "Told, and not checked by anything above",
}

# An incident or decision a rule cites. A bare #n in the crew's code is the crew's
# own issue: the habit before references were made explicit.
_REF = re.compile(
    r"(?:mqucifer/)?\b(crew|sprint-metrics|crew-presentation)#(\d+)"
    r"|(?<![\w/#-])#(\d{2,4})\b"
    r"|\bADR (\d{4})\b"
)
# A section of the constitution, as code and prose cite it.
_SECTION = re.compile(r"§(\d+)(?:\.(\d+))?")
# The words that make a sentence a rule rather than a description.
_RULE_WORDS = re.compile(
    r"\b(must|never|only|always|exactly|at least|at most|no more than|don't|do not|"
    r"cannot|can't|is refused|are refused|refuse[sd]?|may not|not allowed)\b",
    re.IGNORECASE,
)
_STOP = frozenset(
    re.findall(
        r"\S+",
        "a an the and or of to in on is are be it its it's this that for with as by at from "
        "not no never only must each every any one if when then than so but do does don't "
        "which what who where how your you can may into out up has have was were their them "
        "they there here also more most less such these those other same all some",
    )
)


@dataclass
class Entry:
    """One rule a role meets: where it lives, who's told it, what checks it, and why."""

    id: str
    group: str
    where: str
    says: str
    roles: set[str] = field(default_factory=set)
    told: set[str] = field(default_factory=set)
    checked: set[str] = field(default_factory=set)
    origin: set[str] = field(default_factory=set)
    # The messages it refuses with, as patterns, for reading the event log.
    messages: list[str] = field(default_factory=list)
    # For a form's rule: the form and the fields it checks, matched by name.
    form: str = ""
    fields: set[str] = field(default_factory=set)
    # For a form's rule: "field" or "model", the validator's kind.
    mode: str = ""
    # A form's rule that changes the answer and never refuses it.
    repairs: bool = False
    # The declared rules its code refuses by, through `rulebook.refuse()`.
    named: set[str] = field(default_factory=set)
    # What it protects, one of CATEGORIES: as declared, or as PLACEMENT proposes for a
    # rule not yet declared. "" where neither places it.
    category: str = ""
    # Its declaration in the rulebook, if it has one.
    declared: rulebook.RuleSpec | None = None
    # For a guard: the failure class its events record.
    failure_class: str = ""
    # For a told line: the constitution sections it is, for linking by citation.
    sections: set[str] = field(default_factory=set)
    # The text it was linked by, for wording.
    text: str = ""
    # For a merged rule: each case, by the name the code still gives it, and where.
    cases: dict[str, str] = field(default_factory=dict)
    # For a merged form rule: each case's form, mode and fields, to read refusals by.
    places: list[tuple[str, str, frozenset[str]]] = field(default_factory=list)


# --- reading the source -----------------------------------------------------------------


# Compared by identity: each module is read once.
@dataclass(frozen=True, eq=False)
class Source:
    path: str  # relative to the repository root
    module: str
    tree: ast.Module
    lines: tuple[str, ...]


@cache
def sources() -> tuple[Source, ...]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        module = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        text = path.read_text(encoding="utf-8")
        found.append(Source(rel, module, ast.parse(text), tuple(text.splitlines())))
    return tuple(found)


def refs(text: str) -> set[str]:
    """The incidents and decisions a text cites, written one way."""
    out = set()
    for repo, number, bare, adr in _REF.findall(text):
        if adr:
            out.add(f"ADR {adr}")
        else:
            out.add(f"{repo or 'crew'}#{number or bare}")
    return out


def _comments_above(lines: tuple[str, ...], lineno: int) -> str:
    """The comment lines directly above a line (1-based), joined."""
    out: list[str] = []
    i = lineno - 2
    while i >= 0 and lines[i].strip().startswith(("#", "@")):
        if lines[i].strip().startswith("#"):
            out.insert(0, lines[i].strip().lstrip("#").strip())
        i -= 1
    return " ".join(out)


def _comments_in(lines: tuple[str, ...], node: ast.AST) -> str:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start) or start
    found = [line.split("#", 1)[1].strip() for line in lines[start - 1 : end] if "#" in line]
    return " ".join(found)


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    head = re.split(r"(?<=\.)\s+(?=[A-Z`'\"(])", text, maxsplit=1)[0]
    return head if len(head) <= 200 else head[:199] + "…"


@cache
def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST], kind: Any) -> Any:
    while node in parents:
        node = parents[node]
        if isinstance(node, kind):
            return node
    return None


def _qualname(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    while node in parents:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.insert(0, node.name)
        node = parents[node]
    if isinstance(node, ast.FunctionDef | ast.ClassDef):
        names.insert(0, node.name)
    return ".".join(names) or "<module>"


@cache
def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


@cache
def _constants(tree: ast.Module) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out[target.id] = node.value
    return out


def _template(node: ast.expr | None, tree: ast.Module, depth: int = 0) -> str:
    """A message's text, its computed parts as {}: what a refusal reads like."""
    if node is None or depth > 3:
        return "{}"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(str(v.value) if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _template(node.left, tree, depth + 1) + _template(node.right, tree, depth + 1)
    if isinstance(node, ast.IfExp):
        return "{}"
    if isinstance(node, ast.Name):
        value = _constants(tree).get(node.id)
        return _template(value, tree, depth + 1) if value is not None else "{}"
    if isinstance(node, ast.Call):
        func = node.func
        # "...".format(...) and NAME.format(...): the named parts are computed.
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return re.sub(r"\{[^{}]*\}", "{}", _template(func.value, tree, depth + 1))
        # A same-module function that builds the message: what it returns.
        if isinstance(func, ast.Name) and func.id in _functions(tree):
            returns = [
                r.value
                for r in ast.walk(_functions(tree)[func.id])
                if isinstance(r, ast.Return) and r.value is not None
            ]
            return _template(returns[0], tree, depth + 1) if returns else "{}"
    return "{}"


@cache
def refusals() -> frozenset[str]:
    """What a refusal is raised as: `ValueError`, and what a flow catches to send back.

    Each counts with the crew's own kinds of it, such as the edit tools' `EditError`.
    """
    names = {"ValueError"}
    for source in sources():
        for handler in (n for n in ast.walk(source.tree) if isinstance(n, ast.ExceptHandler)):
            if handler.type is not None and any(
                _is_feedback(n) is not None for n in ast.walk(handler)
            ):
                names |= {
                    n.id
                    for n in ast.walk(handler.type)
                    if isinstance(n, ast.Name) and n.id[0].isupper() and n.id != "Exception"
                }
    while True:
        more = (
            {
                node.name
                for source in sources()
                for node in ast.walk(source.tree)
                if isinstance(node, ast.ClassDef)
                and any(isinstance(b, ast.Name) and b.id in names for b in node.bases)
            }
            - names
            - {"Refused"}
        )
        if not more:
            return frozenset(names)
        names |= more


def _refusal(node: ast.AST) -> ast.Call | None:
    """The refusal a `raise` statement raises, if it raises one with a message."""
    if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
        exc = node.exc.func
        if isinstance(exc, ast.Name) and exc.id in refusals() and node.exc.args:
            return node.exc
    return None


def _refused_by(node: ast.AST) -> str | None:
    """The declared rule a `refuse("<id>", ...)` or `says("<id>", ...)` call names (ADR 0031)."""
    if not (isinstance(node, ast.Call) and node.args):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    first = node.args[0]
    if (
        name in ("refuse", "says")
        and isinstance(first, ast.Constant)
        and isinstance(first.value, str)
    ):
        return first.value
    return None


def _declared(rule: str) -> list[str]:
    """A declared rule's messages, each as a template: its values as {}."""
    spec = rulebook.catalog().get(rule)
    return [re.sub(r"\{[^{}]*\}", "{}", text) for _, text in spec.messages] if spec else []


def _named(node: ast.AST) -> set[str]:
    """The declared rules a function refuses by, itself."""
    return {rule for sub in ast.walk(node) if (rule := _refused_by(sub))}


def _raises(node: ast.AST, tree: ast.Module, depth: int = 0) -> list[str]:
    """The messages a function refuses with, its helpers' included.

    A helper is a module function, or a method called on a class, this module's or
    one it imports: `PlannedFile` checks its path with
    `FileWrite._stays_in_the_repository(value)`.
    """
    found: list[str] = []
    for sub in ast.walk(node):
        raised = _refusal(sub)
        if raised is not None:
            found.append(_template(raised.args[0], tree))
        elif (rule := _refused_by(sub)) is not None:
            found += _declared(rule)
        elif depth < 2 and isinstance(sub, ast.Call):
            helper = _helper(sub, tree)
            if helper is not None and helper[0] is not node:
                found += _raises(helper[0], helper[1], depth + 1)
    return list(dict.fromkeys(found))


def _refused_through(node: ast.AST, tree: ast.Module, depth: int = 0) -> set[str]:
    """The declared rules a function refuses by, its helpers' included, as `_raises` reads
    their messages: a check that refuses in a helper is still that rule (ADR 0031)."""
    found = set(_named(node))
    if depth < 2:
        for sub in ast.walk(node):
            helper = _helper(sub, tree) if isinstance(sub, ast.Call) else None
            if helper is not None and helper[0] is not node:
                found |= _refused_through(helper[0], helper[1], depth + 1)
    return found


def _helper(call: ast.Call, tree: ast.Module) -> tuple[ast.FunctionDef, ast.Module] | None:
    """The function a call runs, with its module's tree, if the crew defines it."""
    func = call.func
    if isinstance(func, ast.Name):
        found = _functions(tree).get(func.id)
        return (found, tree) if found is not None else None
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return None
    here = tree
    source = next((s for s in sources() if s.tree is tree), None)
    imported = _imports(source).get(func.value.id) if source is not None else None
    if imported is not None and imported[1] and imported[0] in _by_module():
        here = _by_module()[imported[0]].tree
    for cls in here.body:
        if isinstance(cls, ast.ClassDef) and cls.name == func.value.id:
            method = next(
                (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == func.attr),
                None,
            )
            return (method, here) if method is not None else None
    return None


@dataclass(frozen=True)
class Pattern:
    """A message template's fixed parts, found in order in a refusal's text.

    A regex of `.*?` between the parts backtracks without end on a long refusal;
    finding each part after the last is the same test, in one pass.
    """

    parts: tuple[str, ...]

    @property
    def fixed(self) -> int:
        return sum(len(p) for p in self.parts)

    def search(self, text: str) -> tuple[bool, int]:
        """Whether the text reads as this message, whole, and how much fixed text it has.

        A text can hold only the start of the message: the log keeps a refusal to a few
        hundred characters, and older events have older wording. The parts it does have
        must be in order.
        """
        at, matched = 0, 0
        for part in self.parts:
            found = text.find(part, at)
            if found < 0:
                return False, matched
            at, matched = found + len(part), matched + len(part.strip())
        return True, matched


def pattern(template: str) -> Pattern | None:
    """A message template as a pattern, or None when too little of it is fixed text."""
    parts = tuple(p for p in template.split("{}") if p)
    if sum(len(p.strip()) for p in parts) < 12:
        return None
    return Pattern(parts)


# --- who answers which form -------------------------------------------------------------


@cache
def _module(name: str) -> ModuleType:
    return importlib.import_module(name)


@cache
def role_names() -> dict[str, str]:
    """Each role's key in agents.yaml, as its name."""
    return {key: spec["role"] for key, spec in load_agents().items() if isinstance(spec, dict)}


@dataclass(frozen=True)
class Ask:
    role: str
    source: Source
    function: str
    forms: frozenset[type[BaseModel]]
    check: str | None


def _is_ask(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ask"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "calls"
    )


@cache
def asks() -> tuple[Ask, ...]:
    """Every step's call to `calls.ask`: the role, and the forms it may answer in.

    A form is passed as a name, or through a variable or a parameter, so every form
    named in the asking function, or in a function of the same module that calls it,
    is counted as one that role answers.
    """
    roles = role_names()
    found: list[Ask] = []
    for source in sources():
        if "calls.ask(" not in "\n".join(source.lines):
            continue
        module = _module(source.module)
        parents = _parents(source.tree)
        callers: dict[str, set[str]] = {}
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                outer = _enclosing(node, parents, ast.FunctionDef)
                if outer is not None:
                    callers.setdefault(node.func.id, set()).add(outer.name)
        for node in ast.walk(source.tree):
            if not _is_ask(node):
                continue
            assert isinstance(node, ast.Call)
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys = [first.value]
            else:
                # A role passed in: every role the module names, as the panel's members.
                keys = sorted(
                    {
                        n.value
                        for n in ast.walk(source.tree)
                        if isinstance(n, ast.Constant) and n.value in roles
                    }
                )
            outer = _enclosing(node, parents, ast.FunctionDef)
            scope = [outer] if outer is not None else []
            seen = {outer.name} if outer is not None else set()
            while scope:
                here = scope.pop()
                for name in callers.get(here.name, ()):
                    fn = _functions(source.tree).get(name)
                    if fn is not None and name not in seen:
                        seen.add(name)
                        scope.append(fn)
            forms = set()
            for name in seen:
                fn = _functions(source.tree).get(name) or outer
                for sub in ast.walk(fn):
                    if isinstance(sub, ast.Name):
                        value = getattr(module, sub.id, None)
                        if (
                            isinstance(value, type)
                            and issubclass(value, BaseModel)
                            and value is not BaseModel
                        ):
                            forms.add(value)
            check = next((k.value for k in node.keywords if k.arg == "check"), None)
            found += [
                Ask(
                    roles.get(key, key),
                    source,
                    outer.name if outer is not None else "<module>",
                    frozenset(forms),
                    ast.unparse(check) if check is not None else None,
                )
                for key in keys
            ]
    return tuple(found)


def _nested(form: type[BaseModel]) -> Iterator[type[BaseModel]]:
    def walk(annotation: Any) -> Iterator[type[BaseModel]]:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
        for arg in get_args(annotation):
            yield from walk(arg)

    for info in form.model_fields.values():
        yield from walk(info.annotation)


@cache
def answered() -> dict[type[BaseModel], frozenset[str]]:
    """Every form a role's answer is held to, nested forms included, with its roles."""
    out: dict[type[BaseModel], set[str]] = {}
    for ask in asks():
        todo = list(ask.forms)
        while todo:
            form = todo.pop()
            if ask.role in out.setdefault(form, set()):
                continue
            out[form].add(ask.role)
            todo += list(_nested(form))
    return {form: frozenset(roles) for form, roles in out.items()}


def _where(obj: Any) -> str:
    path = Path(inspect.getsourcefile(obj) or "").resolve()
    return f"{path.relative_to(ROOT).as_posix()}:{obj.__qualname__}"


def _owner(form: type[BaseModel], name: str) -> type[BaseModel]:
    """The form that declares a field, of those that inherit it."""
    for cls in reversed(form.__mro__):
        if (
            isinstance(cls, type)
            and issubclass(cls, BaseModel)
            and cls is not BaseModel
            and name in cls.model_fields
        ):
            return cls
    return form


# --- the rows ---------------------------------------------------------------------------


def _form_rules() -> list[Entry]:
    out: dict[str, Entry] = {}
    for form, roles in answered().items():
        decorators = form.__pydantic_decorators__
        for kind, items in (
            ("field", decorators.field_validators),
            ("model", decorators.model_validators),
        ):
            for dec in items.values():
                func = getattr(dec.func, "__func__", dec.func)
                where = _where(func)
                owner, _, name = func.__qualname__.rpartition(".")
                rule_id = f"form.{owner}.{name.lstrip('_')}"
                entry = out.get(rule_id)
                if entry is None:
                    source = next(s for s in sources() if where.startswith(s.path + ":"))
                    text = textwrap.dedent(inspect.getsource(func))
                    tree = ast.parse(text)
                    lines = source.lines
                    start = inspect.getsourcelines(func)[1]
                    doc = ast.get_docstring(tree.body[0]) or ""  # type: ignore[arg-type]
                    comments = (
                        _comments_above(lines, start)
                        + " "
                        + _comments_in(lines, _shift(tree.body[0], start - 1))
                    )
                    messages = _raises(tree.body[0], source.tree)
                    fields = (
                        set(getattr(dec.info, "fields", ()))
                        if kind == "field"
                        else _reads(tree, form)
                    )
                    entry = Entry(
                        rule_id,
                        "form",
                        where,
                        _first_sentence(doc) or _says(messages, name),
                        origin=refs(doc + " " + comments),
                        messages=messages,
                        form=owner,
                        fields=fields,
                        mode=kind,
                        text=doc + " " + " ".join(messages) + " " + " ".join(fields),
                        sections=_cited_sections(doc + " " + comments + " " + text),
                    )
                    entry.checked.add(where)
                    entry.named = _named(tree.body[0])
                    if not messages:
                        entry.says += " (repairs the answer, never refuses it)"
                        entry.repairs = True
                    out[rule_id] = entry
                entry.roles |= roles
    return list(out.values())


def _shift(node: ast.AST, by: int) -> ast.AST:
    for sub in ast.walk(node):
        if hasattr(sub, "lineno"):
            setattr(sub, "lineno", getattr(sub, "lineno", 0) + by)  # noqa: B010
            if getattr(sub, "end_lineno", None) is not None:
                setattr(sub, "end_lineno", getattr(sub, "end_lineno", 0) + by)  # noqa: B010
    return node


def _reads(tree: ast.AST, form: type[BaseModel]) -> set[str]:
    """The fields a model validator reads: `self.x`, or `data.get("x")` before parsing."""
    names = set(form.model_fields)
    found = set()
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            if sub.value.id == "self" and sub.attr in names:
                found.add(sub.attr)
        elif isinstance(sub, ast.Constant) and sub.value in names:
            found.add(sub.value)
    return found


def _says(messages: list[str], name: str) -> str:
    if messages:
        return _first_sentence(messages[0].replace("{}", "…"))
    return name.lstrip("_").replace("_", " ")


def _cited_sections(text: str) -> set[str]:
    return {f"§{a}.{b}" if b else f"§{a}" for a, b in _SECTION.findall(text)}


def _slug(text: str, words: int = 6) -> str:
    """A rule's name from its first words, as code names are written: `ask_the_sponsor`.

    Not hyphens: "ask", a hyphen and sixteen more characters reads as a key to the
    repository's credential check.
    """
    found = re.findall(r"[a-z0-9]+", re.sub(r"[*`']", "", text).lower())
    return "_".join(found[:words])


def _inline(source: Source, within: ast.AST, group: str, prefix: str) -> list[Entry]:
    """Each refusal raised inline in a long function, as its own rule, named by its words."""
    parents = _parents(source.tree)
    fn: Any = within
    if not isinstance(within, ast.FunctionDef):
        fn = _enclosing(within, parents, ast.FunctionDef)
    where = f"{source.path}:{fn.name if fn is not None else '<module>'}"
    out: list[Entry] = []
    for sub in ast.walk(within):
        raised = _refusal(sub)
        rule = _refused_by(sub)
        if raised is None and rule is None:
            continue
        message = _template(raised.args[0], source.tree) if raised is not None else ""
        comments = _comments_above(source.lines, getattr(sub, "lineno", 1))
        statement = _enclosing(sub, parents, ast.If)
        if statement is not None:
            comments = _comments_above(source.lines, statement.lineno) + " " + comments
        messages = _declared(rule) if rule is not None else [message]
        out.append(
            Entry(
                rule or f"{prefix}.{_slug(message.replace('{}', ' '), 5) or 'refused'}",
                group,
                where,
                _first_sentence(messages[0].replace("{}", "…")) if messages else rule or "",
                checked={where},
                origin=refs(comments),
                messages=messages,
                text=" ".join(messages) + " " + comments,
                sections=_cited_sections(comments),
                named={rule} if rule else set(),
            )
        )
    return out


def _check_rules() -> list[Entry]:
    out: dict[str, Entry] = {}
    for ask in asks():
        if ask.check is None:
            continue
        name = ask.check.rsplit(".", 1)[-1]
        # A check is a module's function, or a closure in the asking function.
        outer = _functions(ask.source.tree).get(ask.function)
        nested = (
            [n for n in ast.walk(outer) if isinstance(n, ast.FunctionDef) and n.name == name]
            if outer is not None
            else []
        )
        fn = nested[0] if nested else _functions(ask.source.tree).get(name)
        short = ask.source.module.rsplit(".", 1)[-1]
        rule_id = f"check.{short}.{name.lstrip('_')}"
        if rule_id not in out:
            if fn is None:
                out[rule_id] = Entry(rule_id, "check", f"{ask.source.path}:{ask.check}", name)
            else:
                doc = ast.get_docstring(fn) or ""
                comments = (
                    _comments_above(ask.source.lines, fn.lineno)
                    + " "
                    + _comments_in(ask.source.lines, fn)
                )
                messages = _raises(fn, ask.source.tree)
                where = f"{ask.source.path}:{fn.name}"
                out[rule_id] = Entry(
                    rule_id,
                    "check",
                    where,
                    _first_sentence(doc) or _says(messages, name),
                    checked={where},
                    origin=refs(doc + " " + comments),
                    messages=messages,
                    named=_refused_through(fn, ask.source.tree),
                    text=doc + " " + " ".join(messages),
                    sections=_cited_sections(doc + " " + comments),
                )
        out[rule_id].roles.add(ask.role)
    return list(out.values())


# The call layer's own refusals: what every answer is held to before its step's rules.
_CALL_LAYER = (
    ("crew_org.calls", "ask"),
    ("crew_org.calls", "_runaway"),
    ("crew_org.calls", "split_blocks"),
    ("crew_org.calls", "join_blocks"),
    ("crew_org.cut_text", "check_not_cut_at_a_quote"),
)


def _call_rules() -> list[Entry]:
    everyone = {ask.role for ask in asks()}
    out = [
        Entry(
            "call.schema",
            "call",
            "pydantic, from the form's JSON schema",
            "The answer is valid JSON with every field of its form, each of its type.",
            roles=set(everyone),
            checked={"calls.ask: `answer.model_validate`"},
            origin={"ADR 0025"},
        )
    ]
    for module, name in _CALL_LAYER:
        source = next(s for s in sources() if s.module == module)
        fn = _functions(source.tree)[name]
        where = f"{source.path}:{name}"
        doc = ast.get_docstring(fn) or ""
        if name == "ask":
            # One function, several rules: each refusal it raises itself is its own.
            for entry in _inline(source, fn, "call", "call"):
                entry.roles |= everyone
                out.append(entry)
            continue
        comments = _comments_above(source.lines, fn.lineno)
        messages = _raises(fn, source.tree)
        # A function that refuses by one declared rule is that rule.
        named = _named(fn)
        out.append(
            Entry(
                next(iter(named)) if len(named) == 1 else f"call.{name.lstrip('_')}",
                "call",
                where,
                _first_sentence(doc) or _says(messages, name),
                roles=set(everyone),
                checked={where},
                origin=refs(doc + " " + comments + " " + "\n".join(source.lines[:20])),
                messages=messages,
                named=named,
                text=doc + " " + " ".join(messages),
            )
        )
    return out


# Modules whose refusals are listed as their own kinds (a form's validators, the call
# layer), or aren't a role's at all: the rulebook's own errors.
_LISTED_ELSEWHERE = ("crew_org.calls", "crew_org.cut_text", "crew_org.rulebook")
_FEEDBACK = "feedback"


@cache
def _by_module() -> dict[str, Source]:
    return {s.module: s for s in sources()}


@cache
def _imports(source: Source) -> dict[str, tuple[str, str]]:
    """Each name a module imports from the crew: (module, function), or (module, "")."""
    modules = _by_module()
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(source.tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("crew_org"):
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                local = alias.asname or alias.name
                out[local] = (full, "") if full in modules else (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules and alias.asname:
                    out[alias.asname] = (alias.name, "")
    return out


def _resolve(call: ast.Call, source: Source) -> tuple[Source, ast.FunctionDef] | None:
    """The crew function a call reaches, outside the modules listed by their own kinds."""
    module, name = source.module, ""
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in _functions(source.tree):
            name = func.id
        elif func.id in _imports(source) and _imports(source)[func.id][1]:
            module, name = _imports(source)[func.id]
    elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        target = _imports(source).get(func.value.id)
        if target is not None and not target[1]:
            module, name = target[0], func.attr
    if not name or module.startswith(_LISTED_ELSEWHERE):
        return None
    found = _by_module().get(module)
    fn = _functions(found.tree).get(name) if found is not None else None
    return (found, fn) if found is not None and fn is not None else None


def _calls_in(node: ast.AST) -> Iterator[ast.Call]:
    """The calls a block makes itself, not those of functions defined inside it."""
    todo = list(ast.iter_child_nodes(node))
    while todo:
        here = todo.pop()
        if isinstance(here, ast.FunctionDef | ast.Lambda | ast.ClassDef):
            continue
        if isinstance(here, ast.Call):
            yield here
        todo += list(ast.iter_child_nodes(here))


def _reached(source: Source, node: ast.AST, depth: int = 5) -> list[tuple[Source, ast.FunctionDef]]:
    """Every crew function a block reaches by its calls, a few calls deep.

    A function passed to another counts as called: `attributed(stepped.implement_file,
    ...)(...)` runs it.
    """
    out: dict[tuple[str, str], tuple[Source, ast.FunctionDef]] = {}
    # Breadth first, so each function is reached by its shortest path and its own
    # calls are followed as deep as that allows, whatever order the code is in.
    todo = deque([(source, node, 0)])
    while todo:
        here, block, level = todo.popleft()
        called = [
            ast.Call(func=f, args=[], keywords=[])
            for call in _calls_in(block)
            for f in (call.func, *call.args)
            if isinstance(f, ast.Name | ast.Attribute)
        ]
        for call in called:
            found = _resolve(call, here)
            if found is None or (found[0].module, found[1].name) in out:
                continue
            out[(found[0].module, found[1].name)] = found
            if level + 1 < depth:
                todo.append((found[0], found[1], level + 1))
    return list(out.values())


def _own_raises(fn: ast.FunctionDef, tree: ast.Module) -> list[str]:
    raised = [_template(r.args[0], tree) for r in (_refusal(n) for n in ast.walk(fn)) if r]
    declared = [m for rule in sorted(_named(fn)) for m in _declared(rule)]
    if declared:
        # A refusal said in its declared parts has no text of its own.
        raised = [t for t in raised if re.search(r"[A-Za-z]", t)]
    return list(dict.fromkeys(raised + declared))


def _findings(fn: ast.FunctionDef, tree: ast.Module) -> list[str]:
    """What a check function finds, as text: what it raises, appends, returns or yields."""
    found = _own_raises(fn, tree)
    for node in ast.walk(fn):
        value: ast.expr | None = None
        if isinstance(node, ast.Return | ast.Yield | ast.Assign | ast.AugAssign):
            value = node.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
        ):
            value = node.args[0]
        found += _texts(value, tree)
    return list(dict.fromkeys(found))


def _texts(value: ast.AST | None, tree: ast.Module) -> list[str]:
    """The messages an expression writes, inside a list or a comprehension too."""
    if value is None:
        return []
    if isinstance(value, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Name | ast.Call):
        text = _template(value, tree)
        if pattern(text) is not None:
            return [text]
    if isinstance(value, ast.Constant | ast.JoinedStr):
        return []
    return [t for child in ast.iter_child_nodes(value) for t in _texts(child, tree)]


def _is_feedback(node: ast.AST) -> ast.expr | None:
    """The value a statement sends back to a role as `feedback`, if it does."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == _FEEDBACK:
                return node.value
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == _FEEDBACK
            ):
                return node.value
    if (
        isinstance(node, ast.keyword)
        and node.arg == _FEEDBACK
        and not (isinstance(node.value, ast.Name) and node.value.id == _FEEDBACK)
    ):
        return node.value
    return None


def _failure_class(node: ast.AST) -> str | None:
    """The failure class a block records, from its event's `failure_class`."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for key, value in zip(sub.keys, sub.values, strict=False):
                if isinstance(key, ast.Constant) and key.value == "failure_class":
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
                    if isinstance(value, ast.Attribute):
                        return value.attr
    return None


def _tested_calls(
    source: Source, fn: ast.FunctionDef, branch: ast.If
) -> list[tuple[Source, ast.FunctionDef]]:
    """The crew functions whose results a branch tests: `found = f(...)`, `if found:`."""
    tested = {n.id for n in ast.walk(branch.test) if isinstance(n, ast.Name)}
    tested |= {
        n.value.id
        for n in ast.walk(branch.test)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    out: list[tuple[Source, ast.FunctionDef]] = []
    for sub in ast.walk(fn):
        if (
            isinstance(sub, ast.Assign | ast.AugAssign | ast.AnnAssign)
            and sub.lineno < branch.lineno
            and sub.value is not None
        ):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            names = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
            if names & tested:
                out += [f for c in _calls_in(sub) if (f := _resolve(c, source)) is not None]
    return list({(s.module, f.name): (s, f) for s, f in out}.values())


def _short(source: Source) -> str:
    return source.module.rsplit(".", 1)[-1]


def _roles_near(source: Source, scope: ast.AST) -> set[str]:
    """The roles asked in a block: a crew step it calls, or the role its events name."""
    steps: dict[tuple[str, str], set[str]] = {}
    for ask in asks():
        steps.setdefault((ask.source.module, ask.function), set()).add(ask.role)
    # A step passed in as a parameter keeps its name: `propose_design=propose_design`.
    named: dict[str, set[tuple[str, str]]] = {}
    for key in steps:
        named.setdefault(key[1], set()).add(key)
    found: set[str] = set()
    # A step called, or passed to a wrapper: `attributed(split_epic, ...)`.
    called = [
        node
        for call in ast.walk(scope)
        if isinstance(call, ast.Call)
        for node in (call.func, *call.args)
    ]
    local = {
        n.id for n in ast.walk(scope) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    roles = set(role_names().values())
    found |= {
        str(k.value.value)
        for call in ast.walk(scope)
        if isinstance(call, ast.Call)
        for k in call.keywords
        if k.arg == "role" and isinstance(k.value, ast.Constant) and k.value.value in roles
    }
    for node in called:
        if isinstance(node, ast.Name):
            if (source.module, node.id) in steps:
                found |= steps[(source.module, node.id)]
            elif node.id in _imports(source):
                found |= steps.get(_imports(source)[node.id], set())
            elif len(named.get(node.id, ())) == 1 and node.id not in local:
                found |= steps[next(iter(named[node.id]))]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = _imports(source).get(node.value.id, ("", "x"))
            if not module[1]:
                found |= steps.get((module[0], node.attr), set())
    return found


def _guarded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether a statement is inside a branch that records a guard's failure class."""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.If) and any(
            _failure_class(stmt) for stmt in node.body if not isinstance(stmt, ast.If)
        ):
            return True
    return False


def _fed_roles(source: Source, fn: ast.FunctionDef) -> set[str]:
    """The roles a function sends feedback to: the steps it calls with `feedback=`."""
    spread = {
        t.value.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Subscript)
        and isinstance(t.value, ast.Name)
        and isinstance(t.slice, ast.Constant)
        and t.slice.value == _FEEDBACK
    }
    steps = [
        call
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and any(
            k.arg == _FEEDBACK
            or (k.arg is None and isinstance(k.value, ast.Name) and k.value.id in spread)
            for k in call.keywords
        )
    ]
    return set().union(
        *(
            _roles_near(source, ast.Expr(value=ast.Call(func=c.func, args=[], keywords=[])))
            for c in steps
        )
    )


def _fed_back_rules() -> list[Entry]:
    """What the flows check in an answer or a change and send back as the reason.

    Two shapes: a refusal raised in a `try` whose handler sends it back or records
    its failure class; and a finding a flow puts in `feedback` from a function it
    calls. An edit that can't be applied is its own kind: the Developer's edits.
    """
    out: dict[str, Entry] = {}

    def add(entry: Entry) -> None:
        here = out.setdefault(entry.id, entry)
        if here is not entry:
            here.roles |= entry.roles
            here.checked |= entry.checked

    for source in sources():
        if not source.module.startswith("crew_org.flows."):
            continue
        parents = _parents(source.tree)
        for fn in (n for n in ast.walk(source.tree) if isinstance(n, ast.FunctionDef)):
            for attempt in (n for n in ast.walk(fn) if isinstance(n, ast.Try)):
                if _enclosing(attempt, parents, ast.FunctionDef) is not fn:
                    continue
                handlers = [
                    h
                    for h in attempt.handlers
                    if any(_is_feedback(n) is not None for n in ast.walk(h))
                    or _failure_class(h) is not None
                ]
                if not handlers:
                    continue
                klass = next((c for h in handlers if (c := _failure_class(h))), "")
                group = "edit" if klass == "EDIT" else "check"
                # Whose answer it is: the role its failure names, or the step it asks.
                roles = set().union(*(_roles_near(source, h) for h in handlers))
                roles = roles or _fed_roles(source, fn)
                if not roles:
                    loop = _enclosing(attempt, parents, ast.For | ast.While)
                    roles = _roles_near(source, ast.Module(body=attempt.body, type_ignores=[]))
                    roles = roles or _roles_near(source, loop or fn)
                body = ast.Module(body=attempt.body, type_ignores=[])
                for entry in _inline(source, body, group, f"{group}.{_short(source)}"):
                    branch = next(
                        (
                            b
                            for b in ast.walk(body)
                            if isinstance(b, ast.If)
                            and any(_refusal(r) for r in ast.walk(b))
                            and entry.messages[0]
                            in [
                                _template(r.args[0], source.tree)
                                for r in (_refusal(x) for x in ast.walk(b))
                                if r
                            ]
                        ),
                        None,
                    )
                    if branch is not None:
                        entry.checked |= {
                            f"{s.path}:{f.name}" for s, f in _tested_calls(source, fn, branch)
                        }
                    entry.where = f"{source.path}:{fn.name}"
                    entry.roles |= roles
                    entry.failure_class = klass
                    add(entry)
                for reached, callee in _reached(source, body):
                    messages = _own_raises(callee, reached.tree)
                    if not messages:
                        continue
                    where = f"{reached.path}:{callee.name}"
                    doc = ast.get_docstring(callee) or ""
                    comments = _comments_above(reached.lines, callee.lineno)
                    # A function that refuses by one declared rule is that rule.
                    named = _named(callee)
                    add(
                        Entry(
                            next(iter(named))
                            if len(named) == 1
                            else f"{group}.{_short(reached)}.{callee.name.lstrip('_')}",
                            group,
                            where,
                            _first_sentence(doc) or _says(messages, callee.name),
                            roles=set(roles),
                            checked={where},
                            origin=refs(doc + " " + comments),
                            messages=messages,
                            named=_named(callee),
                            failure_class=klass,
                            text=doc + " " + " ".join(messages),
                            sections=_cited_sections(doc + " " + comments),
                        )
                    )
            fed = _fed_roles(source, fn) or _roles_near(source, fn)
            for node in ast.walk(fn):
                value = _is_feedback(node)
                if value is None or _enclosing(node, parents, ast.FunctionDef) is not fn:
                    continue
                if _enclosing(node, parents, ast.ExceptHandler) is not None:
                    continue
                branch = _enclosing(node, parents, ast.If)
                if _guarded(node, parents):
                    continue  # a guard: listed with its failure class
                # What the flow writes itself, by a declared rule's message (ADR 0031).
                for rule in sorted(_named(value)):
                    comments = _comments_above(source.lines, getattr(node, "lineno", 1))
                    if branch is not None:
                        comments = _comments_above(source.lines, branch.lineno) + " " + comments
                    messages = _declared(rule)
                    add(
                        Entry(
                            rule,
                            "check",
                            f"{source.path}:{fn.name}",
                            _first_sentence(messages[0].replace("{}", "…")) if messages else rule,
                            roles=set(fed),
                            checked={f"{source.path}:{fn.name}"},
                            origin=refs(comments),
                            messages=messages,
                            text=" ".join(messages) + " " + comments,
                            sections=_cited_sections(comments),
                            named={rule},
                        )
                    )
                # What the flow writes itself, such as the limit on asking for files.
                for message in [] if _named(value) else _texts(value, source.tree):
                    rule_id = f"check.{_short(source)}.{_slug(message.replace('{}', ' '), 5)}"
                    comments = _comments_above(source.lines, getattr(node, "lineno", 1))
                    if branch is not None:
                        comments = _comments_above(source.lines, branch.lineno) + " " + comments
                    add(
                        Entry(
                            rule_id,
                            "check",
                            f"{source.path}:{fn.name}",
                            _first_sentence(message.replace("{}", "…")),
                            roles=set(fed),
                            checked={f"{source.path}:{fn.name}"},
                            origin=refs(comments),
                            messages=[message],
                            text=message + " " + comments,
                            sections=_cited_sections(comments),
                        )
                    )
                names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
                callees = [f for c in _calls_in(value) if (f := _resolve(c, source))]
                for sub in ast.walk(fn):
                    targets = (
                        sub.targets
                        if isinstance(sub, ast.Assign)
                        else [sub.target]
                        if isinstance(sub, ast.AugAssign)
                        else []
                    )
                    if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                        callees += [f for c in _calls_in(sub) if (f := _resolve(c, source))]
                roles = set(fed)
                for reached, callee in {(s.module, f.name): (s, f) for s, f in callees}.values():
                    messages = _findings(callee, reached.tree)
                    if not messages:
                        continue
                    where = f"{reached.path}:{callee.name}"
                    doc = ast.get_docstring(callee) or ""
                    comments = _comments_above(reached.lines, callee.lineno)
                    # A function that writes by one declared rule is that rule.
                    named = _named(callee)
                    add(
                        Entry(
                            next(iter(named))
                            if len(named) == 1
                            else f"check.{_short(reached)}.{callee.name.lstrip('_')}",
                            "check",
                            where,
                            _first_sentence(doc) or _says(messages, callee.name),
                            roles=set(roles),
                            checked={where},
                            origin=refs(doc + " " + comments),
                            messages=messages,
                            named=named,
                            text=doc + " " + " ".join(messages),
                            sections=_cited_sections(doc + " " + comments),
                        )
                    )
    return list(out.values())


def _changed_answers() -> list[Entry]:
    """Each flow function that takes a role's answer and returns it changed.

    Neither a refusal nor feedback: the role is never told. `held_to_what_it_read`
    holds a criterion unproven when QA cites a test it wasn't shown (crew#770).
    """
    forms = {form.__name__: roles for form, roles in answered().items()}
    out: list[Entry] = []
    for source in sources():
        if not source.module.startswith("crew_org.flows."):
            continue
        for fn in (n for n in source.tree.body if isinstance(n, ast.FunctionDef)):
            params = {
                a.annotation.id
                for a in fn.args.args + fn.args.kwonlyargs
                if isinstance(a.annotation, ast.Name) and a.annotation.id in forms
            }
            if not (isinstance(fn.returns, ast.Name) and fn.returns.id in params):
                continue
            doc = ast.get_docstring(fn) or ""
            comments = (
                _comments_above(source.lines, fn.lineno) + " " + _comments_in(source.lines, fn)
            )
            where = f"{source.path}:{fn.name}"
            out.append(
                Entry(
                    f"check.{_short(source)}.{fn.name.lstrip('_')}",
                    "check",
                    where,
                    (_first_sentence(doc) or fn.name) + " (changes the answer, never refuses it)",
                    roles=set(forms[fn.returns.id]),
                    checked={where},
                    origin=refs(doc + " " + comments),
                    messages=_findings(fn, source.tree),
                    form=fn.returns.id,
                    repairs=True,
                    text=doc,
                    sections=_cited_sections(doc + " " + comments),
                )
            )
    return out


def _repairs() -> list[Entry]:
    """What the crew changes in the work by itself, by the names the code gives it.

    Each is on the attempt's record, and none is refused or sent back:
    - a project's fixers, `AUTOFIX`, with what they change outside the attempt's
      files undone (crew#758);
    - each allowed unsafe fix, a key of a `*_FIXES` dict, with why it may run;
    - each looser match an edit or a block name is taken by, recorded as its
      `fallback` (crew#734).
    """
    out: dict[str, Entry] = {}
    for source in sources():
        parents = _parents(source.tree)
        for node in source.tree.body:
            if not (isinstance(node, ast.Assign | ast.AnnAssign)):
                continue
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if not isinstance(target, ast.Name) or node.value is None:
                continue
            comments = _comments_above(source.lines, node.lineno)
            where = f"{source.path}:{target.id}"
            if target.id == "AUTOFIX":
                rule_id = f"repair.{_short(source)}.autofix"
                out[rule_id] = Entry(
                    rule_id,
                    "repair",
                    where,
                    _first_sentence(comments) or "the project's fixers run before the checks",
                    checked={where},
                    origin=refs(comments + " " + _comments_in(source.lines, node)),
                    repairs=True,
                    text=comments,
                )
            elif target.id.endswith("_FIXES") and isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values, strict=False):
                    if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                        continue
                    why = _template(value, source.tree)
                    above = _comments_above(source.lines, key.lineno)
                    rule_id = f"repair.unsafe_fix.{key.value}"
                    out[rule_id] = Entry(
                        rule_id,
                        "repair",
                        where,
                        f"{key.value}: {_first_sentence(why)}",
                        checked={where},
                        origin=refs(comments + " " + above),
                        repairs=True,
                        text=why + " " + comments,
                    )
        for found in ast.walk(source.tree):
            if not isinstance(found, ast.Dict):
                continue
            for key, value in zip(found.keys, found.values, strict=False):
                if not (isinstance(key, ast.Constant) and key.value == "fallback"):
                    continue
                fn = _enclosing(found, parents, ast.FunctionDef)
                for name in _fallback_names(value, fn, source):
                    rule_id = f"repair.fallback.{name}"
                    where = f"{source.path}:{fn.name if fn else '<module>'}"
                    doc = ""
                    if fn is not None:
                        called = [f for c in _calls_in(fn) if (f := _helper(c, source.tree))]
                        doc = " ".join(ast.get_docstring(f[0]) or "" for f in called)
                    out.setdefault(
                        rule_id,
                        Entry(
                            rule_id,
                            "repair",
                            where,
                            f"an edit or a block taken by the looser match `{name}`",
                            checked={where},
                            origin=refs(
                                _comments_above(source.lines, found.lineno)
                                + " "
                                + (ast.get_docstring(fn) or "" if fn else "")
                                + " "
                                + doc
                            ),
                            repairs=True,
                            text=name,
                        ),
                    )
    return list(out.values())


def _fallback_names(value: ast.expr, fn: Any, source: Source) -> list[str]:
    """The names a `fallback` is recorded as: given there, or returned by what it calls."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return [value.value]
    if fn is None:
        return []
    names = []
    for call in _calls_in(fn):
        helper = _helper(call, source.tree)
        if helper is None:
            continue
        for ret in (n for n in ast.walk(helper[0]) if isinstance(n, ast.Return)):
            last = ret.value.elts[-1] if isinstance(ret.value, ast.Tuple) else None
            if isinstance(last, ast.Constant) and isinstance(last.value, str):
                names.append(last.value)
    return names


def _skips() -> list[Entry]:
    """Each place a flow holds a card or a pull request back, with only a sentence.

    A decision like a `rules.Rule`, with no name for the event log: `result.skipped`
    gets the card and why, as `refine_panel.builds_on_unsplit`'s hold does (crew#773).
    What decides it is the call its branch tests.
    """
    out: dict[str, Entry] = {}
    for source in sources():
        if not source.module.startswith("crew_org.flows."):
            continue
        parents = _parents(source.tree)
        for call in (n for n in ast.walk(source.tree) if isinstance(n, ast.Call)):
            func = call.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "append"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "skipped"
                and call.args
            ):
                continue
            given = call.args[0]
            why: ast.expr | None = None
            if isinstance(given, ast.Tuple) and len(given.elts) > 1:
                why = given.elts[1]
            elif isinstance(given, ast.Call):
                why = next((k.value for k in given.keywords if k.arg == "skipped"), None)
            text = _template(why, source.tree) if why is not None else ""
            if not re.search(r"[A-Za-z]", text):
                continue
            fn = _enclosing(call, parents, ast.FunctionDef)
            branch = _enclosing(call, parents, ast.If)
            checks = (
                _tested_calls(source, fn, branch) if fn is not None and branch is not None else []
            )
            comments = _comments_above(source.lines, getattr(call, "lineno", 1))
            if branch is not None:
                comments = _comments_above(source.lines, branch.lineno) + " " + comments
            rule_id = f"skip.{_short(source)}.{_slug(text.replace('{}', ' '), 6)}"
            where = f"{source.path}:{fn.name if fn is not None else '<module>'}"
            docs = " ".join(ast.get_docstring(f) or "" for _, f in checks)
            entry = out.setdefault(
                rule_id,
                Entry(
                    rule_id,
                    "skip",
                    where,
                    _first_sentence(text.replace("{}", "…")),
                    origin=refs(comments + " " + docs),
                    messages=[text],
                    text=text + " " + comments,
                    sections=_cited_sections(comments),
                ),
            )
            entry.checked |= {f"{s.path}:{f.name}" for s, f in checks} or {where}
    return list(out.values())


def _decisions() -> list[Entry]:
    """The rules the decision-point audit found that no other shape does (crew#756).

    Read from the reviewed table, `config/rules/decision_points.yaml`: a `decision.*`
    rule is checked where its points are, and says what its first function does.
    """
    path = rulebook.CATALOG / "decision_points.yaml"
    if not path.exists():
        return []
    table = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sites: dict[str, set[str]] = {}
    for entry in table.values():
        rule = (entry or {}).get("rule", "")
        if rule.startswith("decision."):
            sites.setdefault(rule, set()).add(str(entry["at"]))
    out = []
    for rule, ats in sorted(sites.items()):
        first = sorted(ats)[0]
        module_path, _, name = first.partition(":")
        source = next(s for s in sources() if s.path == f"src/crew_org/{module_path}")
        fn = _functions(source.tree).get(name)
        doc = (ast.get_docstring(fn) or "") if fn is not None else ""
        comments = _comments_above(source.lines, fn.lineno) if fn is not None else ""
        out.append(
            Entry(
                rule,
                "decision",
                f"src/crew_org/{first}",
                _first_sentence(doc) or name.replace("_", " "),
                checked={f"src/crew_org/{at}" for at in ats},
                origin=refs(doc + " " + comments),
                text=doc,
            )
        )
    return out


ORG = PACKAGE / "config" / "org.yaml"
# Agents' settings that change what a role does or may do.
_AGENT_SETTINGS = ("llm", "llm_params", "can", "constitution")
# Modules whose numeric constants set the tick's behaviour: its flows, crews and tools,
# and the Developer's session (crew#778).
_CONFIG_MODULES = ("crew_org.flows.", "crew_org.crews.", "crew_org.tools.", "crew_org.session.")
_CONFIG_EXTRA = ("crew_org.calls", "crew_org.cut_text", "crew_org.escalation", "crew_org.llm")


def org_settings() -> list[tuple[str, Any, bool]]:
    """Each org.yaml setting, its value, and whether any code reads its key.

    A section of named entries (`wip_limits`, a column each) is one setting.
    """
    org = yaml.safe_load(ORG.read_text(encoding="utf-8")) or {}
    code = "\n".join("\n".join(s.lines) for s in sources())
    out = []
    for section, value in org.items():
        named = isinstance(value, dict) and not any(" " in str(k) for k in value)
        for path, key, setting in (
            [(f"{section}.{k}", k, v) for k, v in value.items()]
            if named
            else [(section, section, value)]
        ):
            read = re.search(rf"[\"']{re.escape(str(key))}[\"']", code) is not None
            out.append((path, setting, read))
    return out


def _config() -> list[Entry]:
    """Every setting the tick reads that changes what it does (crew#756).

    org.yaml's settings the code reads (the board, the WIP limits and the sprint are
    also `ProcessRules`), each role's model, its parameters, what it may do and its
    constitution sections, and the numeric constants of the tick's modules.
    """
    out: list[Entry] = []
    for path, value, read in org_settings():
        if not read:
            continue
        out.append(
            Entry(
                f"config.org.{path}",
                "config",
                f"src/crew_org/config/org.yaml:{path}",
                f"{path} = {value}"[:200],
                checked={"src/crew_org/config/org.yaml"},
            )
        )
    for key, spec in load_agents().items():
        steps = [(key, spec)] + [
            (f"{key}.{step}", sub) for step, sub in spec.items() if isinstance(sub, dict)
        ]
        for where, body in steps:
            for setting in _AGENT_SETTINGS:
                if setting in body:
                    out.append(
                        Entry(
                            f"config.agents.{where}.{setting}",
                            "config",
                            f"src/crew_org/config/agents.yaml:{where}.{setting}",
                            f"{setting} = {body[setting]}"[:200],
                            roles={spec["role"]},
                            checked={"src/crew_org/config/agents.yaml"},
                        )
                    )
    for source in sources():
        if not (source.module.startswith(_CONFIG_MODULES) or source.module in _CONFIG_EXTRA):
            continue
        for node in source.tree.body:
            if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
                continue
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if not (isinstance(target, ast.Name) and target.id.isupper()):
                continue
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                try:
                    value = eval(compile(ast.Expression(node.value), "<const>", "eval"), {})  # noqa: S307
                except Exception:  # noqa: BLE001 - only literal arithmetic counts
                    continue
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            comments = _comments_above(source.lines, node.lineno)
            where = f"{source.path}:{target.id}"
            out.append(
                Entry(
                    f"config.const.{_short(source)}.{target.id}",
                    "config",
                    where,
                    _first_sentence(comments) or f"{target.id} = {value}",
                    checked={where},
                    origin=refs(comments),
                    text=comments,
                )
            )
    return out


def _guard_rules() -> list[Entry]:
    """Each place delivery records a failure class for a change and sends a reason back.

    The class is what its events record. What checks it is the functions whose result
    the branch tests: `overwrites = regression.overwrites_existing(...)`, `if overwrites:`.
    """
    out: dict[str, Entry] = {}
    for source in sources():
        if not source.module.startswith("crew_org.flows."):
            continue
        parents = _parents(source.tree)
        for branch in (n for n in ast.walk(source.tree) if isinstance(n, ast.If)):
            klass = None
            # The branch's own event, not one in a branch nested inside it.
            for stmt in branch.body:
                if not isinstance(stmt, ast.If):
                    klass = klass or _failure_class(stmt)
            fn = _enclosing(branch, parents, ast.FunctionDef)
            if klass is None or fn is None:
                continue
            checks = _tested_calls(source, fn, branch)
            first = checks[0][1].name if checks else fn.name
            rule_id = f"guard.{klass.lower()}.{first.lstrip('_')}"
            if rule_id in out:
                continue
            comments = _comments_above(source.lines, branch.lineno)
            messages = [
                _template(value, source.tree)
                for sub in ast.walk(branch)
                if (value := _is_feedback(sub)) is not None
            ]
            messages = [m for m in messages if pattern(m) is not None]
            for _, check in checks:
                messages += [m for m in _findings(check, source.tree) if m not in messages]
            out[rule_id] = Entry(
                rule_id,
                "guard",
                f"{source.path}:{fn.name}",
                _first_sentence(comments)
                or f"{klass}: {', '.join(f.name for _, f in checks) or fn.name}",
                roles=_roles_near(source, branch),
                checked={f"{s.path}:{f.name}" for s, f in checks} or {f"{source.path}:{fn.name}"},
                origin=refs(comments + " " + _comments_in(source.lines, branch)),
                messages=messages,
                failure_class=klass,
                text=comments + " " + " ".join(messages),
                sections=_cited_sections(comments),
            )
    return list(out.values())


# The Developer's session (crew#778): its tools refuse a call in their reply.
_SESSION = "crew_org.session."


def _tool_rules() -> list[Entry]:
    """Each declared rule a session's tool refuses a call by, said in its reply.

    A refusal is the tool's reply, and the session goes on: never a discarded answer
    or a counted failure (decision 5's feedback). Its message is its declaration's.
    """
    out: dict[str, Entry] = {}
    developer = role_names().get("developer", "Developer")
    for source in sources():
        if not source.module.startswith(_SESSION):
            continue
        parents = _parents(source.tree)
        for node in ast.walk(source.tree):
            rule = _refused_by(node)
            if rule is None:
                continue
            where = f"{source.path}:{_qualname(node, parents)}"
            entry = out.get(rule)
            if entry is None:
                messages = _declared(rule)
                spec = rulebook.catalog().get(rule)
                entry = out[rule] = Entry(
                    rule,
                    "tool",
                    where,
                    (spec.description if spec else "") or (messages[0] if messages else rule),
                    roles={developer},
                    origin={"crew#778"},
                    messages=messages,
                    named={rule},
                    text=" ".join(messages),
                )
            entry.checked.add(where)
    return list(out.values())


def _decided_rules() -> list[Entry]:
    told = meanings(Rule)
    out = {
        rule.value: Entry(rule.value, "decides", "src/crew_org/rules.py:Rule", told[rule.name])
        for rule in Rule
    }
    for entry in out.values():
        entry.origin = refs(entry.says)
    for source in sources():
        parents = _parents(source.tree)
        for node in ast.walk(source.tree):
            if source.module == "crew_org.rules" and _enclosing(node, parents, ast.ClassDef):
                continue
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "Rule"
                and node.attr in Rule.__members__
            ):
                entry = out[Rule[node.attr].value]
                entry.checked.add(f"{source.path}:{_qualname(node, parents)}")
                statement: ast.AST = node
                while statement in parents and not isinstance(statement, ast.stmt):
                    statement = parents[statement]
                comments = _comments_above(source.lines, getattr(statement, "lineno", 1))
                entry.origin |= refs(comments)
                entry.sections |= _cited_sections(comments)
                entry.text += " " + comments
    for entry in out.values():
        entry.text += " " + entry.says
    return list(out.values())


# --- what a role is told ----------------------------------------------------------------


def _section_roles() -> dict[int, set[str]]:
    """Which roles are given each section of the constitution, by agents.yaml."""
    out: dict[int, set[str]] = {}
    for spec in load_agents().values():
        for number in spec.get("constitution", []):
            out.setdefault(number, set()).add(spec["role"])
        for step, sub in spec.items():
            if isinstance(sub, dict) and "constitution" in sub:
                for number in sub["constitution"]:
                    out.setdefault(number, set()).add(f"{spec['role']} ({step})")
    return out


_ITEM = re.compile(r"^(?:(\d+)\.\s+|-\s+)?\*\*(.+?)\*\*")


def _role(name: str) -> str:
    """A role's name without its step: `Product Owner (onboarding)` is the Product Owner."""
    return name.split(" (")[0]


def _told_constitution() -> list[Entry]:
    """Each rule of the constitution's sections a role is given: a numbered item, or a
    paragraph or item that opens in bold."""
    given = _section_roles()
    text = CONSTITUTION.read_text(encoding="utf-8")
    out: list[Entry] = []
    for chunk in re.split(r"(?m)^(?=## \d+\. )", text):
        heading = re.match(r"## (\d+)\. (.+)", chunk)
        if not heading:
            continue
        number = int(heading.group(1))
        roles = given.get(number, set())
        # Paragraphs and top-level list items, each with its continuation lines, and
        # the roles a marked passage is told to (None: every role given the section).
        items: list[tuple[list[str], set[str] | None]] = []
        fenced = False
        only: set[str] | None = None
        for line in chunk.splitlines()[1:]:
            if marker := re.match(r"<!-- told to: (.+?) -->$", line):
                only = constitution.told_to(marker.group(1))
                items.append(([], only))
                continue
            if line == "<!-- /told to -->":
                only = None
                items.append(([], only))
                continue
            if line.startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            starts = (
                not line.strip() or re.match(r"^(\d+\.\s|-\s|\*\*)", line) or line.startswith("#")
            )
            if starts or not items:
                items.append(([], only))
            if line.strip():
                items[-1][0].append(line.strip())
        for item, told_only in items:
            if not item:
                continue
            here = roles if told_only is None else {r for r in roles if _role(r) in told_only}
            # Kept in the document for people, and told to no role: not a rule a role meets.
            if told_only is not None and not here:
                continue
            body = " ".join(item)
            numbered = re.match(r"^(\d+)\.\s+(.*)", body)
            bold = _ITEM.match(body)
            if numbered:
                rule_id = f"§{number}.{numbered.group(1)}"
            elif bold:
                rule_id = f"§{number} {_slug(bold.group(2))}"
            elif body.startswith("- ") and _RULE_WORDS.search(body):
                rule_id = f"§{number} {_slug(body[2:])}"
            else:
                continue
            enforced = body.partition("*Enforced:*")[2]
            out.append(
                Entry(
                    rule_id,
                    "told",
                    f"docs/ways-of-working.md:§{number}",
                    _first_sentence(re.sub(r"[*`]", "", re.sub(r"^\d+\.\s+|^-\s+", "", body))),
                    roles=set(here),
                    told={f"§{number}"} if here else set(),
                    checked={f"named in the text: {_first_sentence(enforced)}"}
                    if enforced
                    else set(),
                    origin=refs(body),
                    sections={rule_id.split(" ")[0]} if numbered else set(),
                    text=body,
                )
            )
    return out


def _sentences(text: str) -> list[str]:
    """A text's sentences, its headings, code fences and placeholders left out."""
    kept = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(("#", "```")) and line.strip(" …")
    ]
    paragraphs = re.split(r"\n\s*\n", "\n".join(kept))
    return [
        s.strip(" …")
        for p in paragraphs
        for s in re.split(r"(?<=[.!?])\s+", " ".join(p.split()))
        if s.strip(" …")
    ]


def _told_agents() -> list[Entry]:
    """Each sentence of a role's goal and backstory that states a rule."""
    out: list[Entry] = []
    for key, spec in load_agents().items():
        parts = [(spec["role"], key, spec)]
        parts += [
            (f"{spec['role']} ({step})", f"{key}.{step}", sub)
            for step, sub in spec.items()
            if isinstance(sub, dict) and ("goal" in sub or "backstory" in sub)
        ]
        for role, where, body in parts:
            for part in ("goal", "backstory"):
                for sentence in _sentences(str(body.get(part, ""))):
                    if not _RULE_WORDS.search(sentence):
                        continue
                    out.append(
                        Entry(
                            f"told.{where}.{_slug(sentence)}",
                            "told",
                            f"src/crew_org/config/agents.yaml:{where}.{part}",
                            sentence,
                            roles={role},
                            told={f"agents.yaml {where}.{part}"},
                            origin=refs(sentence),
                            text=sentence,
                        )
                    )
    return out


def _prompt_texts(source: Source) -> Iterator[tuple[ast.AST, str]]:
    """Each text a crew module writes into a prompt, with where it is.

    A text is a whole string expression: a literal, an f-string, or a sum of them.
    Docstrings, refusals and field descriptions are left out: each is listed as its
    own kind.
    """
    parents = _parents(source.tree)
    skip: set[int] = set()
    for node in ast.walk(source.tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            skip.add(id(body[0].value))
        raised = _refusal(node)
        if raised is not None:
            skip |= {id(n) for n in ast.walk(raised)}
        if isinstance(node, ast.keyword) and node.arg == "description":
            skip |= {id(n) for n in ast.walk(node.value)}
        # A pattern a check matches with is the check's, not a line a role reads.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "compile"
        ):
            skip |= {id(n) for a in node.args for n in ast.walk(a)}
    for node in ast.walk(source.tree):
        if id(node) in skip or not isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.BinOp | ast.JoinedStr | ast.FormattedValue):
            continue
        text = _template(node, source.tree).replace("{}", "…")
        if (
            len(text) >= 40
            and isinstance(node, ast.Constant | ast.JoinedStr)
            or (isinstance(node, ast.BinOp) and len(text) >= 40)
        ):
            yield node, text


def _told_prompts() -> list[Entry]:
    """Each sentence of a crew step's prompt that states a rule."""
    out: dict[str, Entry] = {}
    for source in sources():
        if not source.module.startswith("crew_org.crews."):
            continue
        parents = _parents(source.tree)
        mine = [a for a in asks() if a.source is source]
        everyone = {a.role for a in mine}
        for node, text in _prompt_texts(source):
            fn = _enclosing(node, parents, ast.FunctionDef)
            name = fn.name if fn is not None else _assigned_to(node, parents)
            asked = {a.role for a in mine if fn is not None and a.function == fn.name}
            for sentence in _sentences(text):
                if not _RULE_WORDS.search(sentence) or len(sentence) < 25:
                    continue
                rule_id = f"told.{_short(source)}.{_slug(sentence.replace('…', ' '))}"
                entry = out.setdefault(
                    rule_id,
                    Entry(
                        rule_id,
                        "told",
                        f"{source.path}:{name}",
                        sentence,
                        told={f"the prompt, {source.path}:{name}"},
                        origin=refs(sentence),
                        sections=_cited_sections(sentence),
                        text=sentence,
                    ),
                )
                entry.roles |= asked or everyone
    return list(out.values())


def _assigned_to(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    while node in parents and not isinstance(node, ast.Assign):
        node = parents[node]
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return "<module>"


def _told_fields(checks: list[Entry]) -> list[Entry]:
    """Each field description in a form that states a rule: told, where the form is shown.

    A field a validator checks is told in that validator's row; one none checks is its
    own row.
    """
    by_field: dict[tuple[str, str], list[Entry]] = {}
    for entry in checks:
        for name in entry.fields:
            by_field.setdefault((entry.form, name), []).append(entry)
    out: dict[str, Entry] = {}
    for form, roles in answered().items():
        for name, info in form.model_fields.items():
            owner = _owner(form, name)
            text = " ".join((info.description or "").split())
            if not text or not _RULE_WORDS.search(text):
                continue
            told = f"`{owner.__name__}.{name}`'s description"
            linked = by_field.get((owner.__name__, name)) or by_field.get((form.__name__, name))
            if linked:
                for entry in linked:
                    entry.told.add(told)
                continue
            rule_id = f"told.form.{owner.__name__}.{name}"
            entry = out.setdefault(
                rule_id,
                Entry(
                    rule_id,
                    "told",
                    f"{_where(owner).rpartition(':')[0]}:{owner.__name__}.{name}",
                    _first_sentence(text),
                    told={told},
                    origin=refs(text),
                    text=text,
                    form=owner.__name__,
                    fields={name},
                ),
            )
            entry.roles |= roles
    return list(out.values())


def _words(text: str) -> set[str]:
    return {
        w.rstrip("s")
        for w in re.findall(r"[a-z][a-z_']+", text.lower())
        if w not in _STOP and len(w) > 2
    }


def _link(checks: list[Entry], told: list[Entry]) -> list[Entry]:
    """Join what's told to what checks it; what's told and linked by name leaves the list.

    By name: a check citing the told rule's section (§N.M), or both citing the same
    incident. By wording only, it's a "maybe" on the told rule's row.
    """
    unlinked: list[Entry] = []
    words = {c.id: _words(c.text) for c in checks}
    for line in told:
        cited = []
        for check in checks:
            sections = line.sections & check.sections
            # Not the audit's decisions: their origins come from long docstrings, and a
            # shared incident there is a coincidence more often than a link.
            if check.group == "config":
                # A setting is a parameter, not a check a role is told of.
                continue
            incidents = (
                set()
                if check.group == "decision"
                else {r for r in line.origin & check.origin if not r.startswith("ADR")}
            )
            if sections or incidents:
                cited.append(check)
                how = f"both cite {min(sections or incidents)}"
                given = "in " + ", ".join(sorted(line.told)) if line.roles else "given to no role"
                check.told.add(f"{line.id} ({how}; {given})")
                line.checked.add(check.id)
        if cited:
            continue
        mine = _words(line.text)
        best = max(
            checks,
            key=lambda c: (len(mine & words[c.id]) / (len(mine | words[c.id]) or 1), c.id),
            default=None,
        )
        if best is not None:
            shared = mine & words[best.id]
            if len(shared) >= 4 and len(shared) / (len(mine | words[best.id]) or 1) >= 0.12:
                line.checked.add(f"maybe {best.id}")
        unlinked.append(line)
    return unlinked


@cache
def entries() -> tuple[Entry, ...]:
    """Every rule a role meets, in the order the page lists them."""
    checks = (
        _decided_rules()
        + _form_rules()
        + _check_rules()
        + _call_rules()
        + _fed_back_rules()
        + _changed_answers()
        + _repairs()
        + _skips()
        + _decisions()
        + _config()
        + _guard_rules()
        + _tool_rules()
    )
    told = _told_fields(checks) + _told_prompts() + _told_constitution() + _told_agents()
    told = _link(checks, told)
    declared = rulebook.catalog()
    found = _merged(checks + told, declared)
    for entry in found:
        entry.declared = declared.get(entry.id)
        if entry.declared is not None:
            entry.category = entry.declared.category
            entry.origin |= set(entry.declared.origin)
        else:
            entry.category = place(entry)
    order = list(GROUPS)
    return tuple(sorted(found, key=lambda e: (order.index(e.group), e.id)))


def _merged(found: list[Entry], declared: dict[str, rulebook.RuleSpec]) -> list[Entry]:
    """Each merged rule as one entry, its cases folded in (crew#756).

    A case keeps the name the code and the event log give it, so nothing that
    records one changes; it's listed, counted and declared as its rule. A line a
    role is told is a case of the check that enforces it: one rule, told there and
    checked here.
    """
    case_of = {case: spec.id for spec in declared.values() for case in spec.cases}
    out: list[Entry] = []
    # A merged rule the code names by its own id too takes its cases in.
    into: dict[str, Entry] = {e.id: e for e in found if declared.get(e.id) and declared[e.id].cases}
    for entry in found:
        entry.checked = {
            c.replace(i, case_of[i]) if (i := c.removeprefix("maybe ")) in case_of else c
            for c in entry.checked
        }
        rule = case_of.get(entry.id)
        if rule is None:
            out.append(entry)
            continue
        merged = into.get(rule)
        if merged is None:
            spec = declared[rule]
            merged = into[rule] = Entry(
                id=rule,
                group=entry.group,
                where=entry.where,
                says=spec.description or entry.says,
                failure_class=entry.failure_class,
            )
            out.append(merged)
        merged.cases[entry.id] = entry.where
        if entry.form:
            merged.places.append((entry.form, entry.mode, frozenset(entry.fields)))
        for name in ("roles", "told", "checked", "origin", "named", "sections"):
            getattr(merged, name).update(getattr(entry, name))
        merged.messages += [m for m in entry.messages if m not in merged.messages]
        merged.failure_class = merged.failure_class or entry.failure_class
    return out


# --- what each rule protects ------------------------------------------------------------

# The categories the rules are managed by, as a linter's are (the Sponsor, 2026-10-17,
# on crew#756): by what a rule protects, not where it lives.
CATEGORIES = rulebook.CATEGORIES

# The forms whose rules are about the proof a criterion has.
_EVIDENCE_FORMS = frozenset(
    [
        "AcceptanceCriterion",
        "CriterionMet",
        "CriterionTest",
        "CriterionVerdict",
        "ExistingProof",
        "FirstAttempt",
        "FirstOrDone",
        "QAVerdict",
        "ReaderCriterion",
        "StoryCriteria",
        "Triage",
    ]
)
# The forms that carry a change to the files.
_EDIT_FORMS = frozenset(["FileEdit", "FileWrite", "Implementation", "Move", "TextEdit"])
# The forms that decide what work there is, and where it belongs.
_SCOPE_FORMS = frozenset(
    [
        "Change",
        "Epic",
        "EpicProposal",
        "MissingEpics",
        "Placement",
        "Plan",
        "PlannedFile",
        "SkeletonEpic",
        "StoryProposal",
    ]
)
# Fields that hold a story's criteria or their proof.
_EVIDENCE_FIELDS = frozenset(
    [
        "acceptance_criteria",
        "already_done",
        "ci_step",
        "criteria",
        "criteria_tests",
        "proven_by_existing",
        "then",
    ]
)
# Words in a rule's name that say the answer must name, say or finish something.
_SHAPE_WORDS = re.compile(
    r"(^|_)(an_id|row_ids?|test_ids|one_or_the_other|stripped|says_(something|what|why)|"
    r"is_specific|is_actionable|short|few|a_name_not_its_code|a_bare_path|stated|"
    r"covers|cut|a_verdict_or_an_ask|must_say_why|amendment_says_what|has_a_sample|"
    r"enforces_something|is_a_change)($|_)"
)
# Constitution sections, by what their rules protect. A section not here is unplaced.
_SECTIONS = {
    3: "evidence",
    4: "scope",
    5: "flow",
    6: "scope",
    7: "evidence",
    8: "guidance",
    9: "flow",
    11: "flow",
    1: "flow",
    2: "scope",
    10: "guidance",
    12: "flow",
    13: "scope",
    14: "guards",
    15: "contracts",
    17: "flow",
    18: "flow",
    19: "guards",
    20: "guards",
}

# Where a told line sits, by what the lines there protect: an agents.yaml section (a
# role, or a role's step), or a crew module's prompt, a function, constant or form in
# it. A line is placed by the most particular site listed; one at a site not listed
# stays unplaced. Left out on purpose, as no one category holds them: the onboarding
# interview, the Developer's instructions for reading files, and §16, what a role is
# shown.
_TOLD_SITES = {
    "agents.yaml:architect": "scope",
    "agents.yaml:architect.design_placement": "scope",
    "agents.yaml:business_analyst": "evidence",
    "agents.yaml:business_analyst.bug_triage": "evidence",
    "agents.yaml:code_reviewer": "guidance",
    "agents.yaml:developer": "scope",
    "agents.yaml:devops_engineer": "guards",
    "agents.yaml:product_owner": "scope",
    "agents.yaml:qa_engineer": "evidence",
    "agents.yaml:qa_engineer.criteria_check": "evidence",
    "agents.yaml:scrum_master": "guidance",
    "agents.yaml:senior_engineer": "guidance",
    "agents.yaml:ux_designer": "scope",
    "bug_triage_crew": "scope",
    "contract_crew": "contracts",
    "criteria_crew": "evidence",
    "delivery_crew:STANDING_INSTRUCTIONS": "edits",
    "delivery_crew:implement_story": "scope",
    "delivery_crew:Rework": "evidence",
    "deploy_review_crew": "guards",
    "design_crew:DesignProposal": "guards",
    "design_crew:propose_design": "guards",
    "design_crew:review_design": "guards",
    "design_crew:place_changes": "scope",
    "design_note_crew": "scope",
    "panel_crew": "scope",
    "presentation_note_crew": "evidence",
    "qa_crew": "evidence",
    "refinement_crew:AnswerRow": "answer_shape",
    "refinement_crew:RepairedStory": "answer_shape",
    "refinement_crew:RowNotForStories": "answer_shape",
    "refinement_crew:propose_epics": "scope",
    "refinement_crew:propose_missing_epics": "scope",
    "refinement_crew:_delivered_block": "scope",
    "refinement_crew:split_epic": "scope",
    "refinement_crew:repair_criteria": "evidence",
    "retro_crew": "guidance",
    "review_crew": "flow",
    "settle_crew": "scope",
}


def _told_site(entry: Entry) -> str:
    """The category of the site a told line sits at, from _TOLD_SITES, or ""."""
    path, _, name = entry.where.partition(":")
    if path.endswith("agents.yaml"):
        return _TOLD_SITES.get(f"agents.yaml:{name.rpartition('.')[0]}", "")
    if not path.startswith("src/crew_org/crews/"):
        return ""
    module = Path(path).stem
    for site in (f"{module}:{name}", f"{module}:{name.partition('.')[0]}", module):
        if site in _TOLD_SITES:
            return _TOLD_SITES[site]
    return ""


def _name(entry: Entry) -> str:
    return entry.id.rsplit(".", 1)[-1]


def _file(entry: Entry) -> str:
    return entry.where.partition(":")[0]


def _section(entry: Entry) -> int | None:
    found = re.match(r"docs/ways-of-working\.md:§(\d+)", entry.where)
    return int(found.group(1)) if found else None


# How a rule is placed: each line a category, why, and the test. The first that holds
# places it; a rule none holds for is left unplaced and listed, never guessed. This
# table is what's reviewed, not the rows it places.
PLACEMENT: list[tuple[str, str, Any]] = [
    (
        "flow",
        "the crew's own decisions (`rules.Rule`): retry, return, block, hold; and the "
        "holds said only in words",
        lambda e: e.group in ("decides", "skip", "decision"),
    ),
    ("answer_shape", "the call layer's refusals, for every form", lambda e: e.group == "call"),
    (
        "edits",
        "an edit that can't be applied, or rewrites a file whole; what the crew changes "
        "in the work by itself",
        lambda e: e.group in ("edit", "repair") or e.failure_class in ("EDIT", "OVERWRITE"),
    ),
    ("guards", "a path outside the repository or inside .git", lambda e: "repository" in _name(e)),
    (
        "guards",
        "what the project protects, and what the crew may push",
        lambda e: e.failure_class == "BOUNDS" or _name(e) == "workflows_not_permitted",
    ),
    (
        "guards",
        "the engineering guidelines a design is held to (§19)",
        lambda e: _file(e) == "src/crew_org/flows/design.py",
    ),
    (
        "contracts",
        "merged code and tests other work relies on, and a story's declared changes to them (§15)",
        lambda e: (
            e.failure_class in ("REGRESSION", "SCOPE")
            or e.form in ("ContractRuling", "RetiredTest")
            or "contract" in _name(e)
            or _file(e) == "src/crew_org/flows/record.py"
        ),
    ),
    (
        "evidence",
        "tests that prove the criteria, and their runs",
        lambda e: (
            e.failure_class == "REPRODUCE"
            or e.form in _EVIDENCE_FORMS
            or bool(e.fields & _EVIDENCE_FIELDS)
            and e.form not in _SCOPE_FORMS
            or re.search(r"tests|evidence|criteria|docs", _name(e)) is not None
            or _file(e)
            in (
                "src/crew_org/flows/attempts.py",
                "src/crew_org/flows/criteria_check.py",
                "src/crew_org/flows/presentation_notes.py",
            )
        ),
    ),
    (
        "answer_shape",
        "the answer names what it must, gives one answer, and finishes it",
        lambda e: (
            e.group in ("form", "check", "told")
            and e.form != ""
            and _SHAPE_WORDS.search(_name(e)) is not None
            or e.group == "check"
            and _SHAPE_WORDS.search(_name(e)) is not None
        ),
    ),
    ("edits", "the forms that carry a change", lambda e: e.form in _EDIT_FORMS),
    (
        "scope",
        "what work there is, and where it belongs",
        lambda e: (
            e.form in _SCOPE_FORMS
            or e.form in ("Row", "Story")
            or _file(e)
            in (
                "src/crew_org/flows/conclusion.py",
                "src/crew_org/flows/backlog_order.py",
                "src/crew_org/crews/stepped.py",
            )
            or _name(e) in ("deliver_in_steps", "check_grounded", "check_product_calls", "check")
        ),
    ),
    (
        "scope",
        "a step that asks to see more than it may",
        lambda e: _name(e).startswith("do_the_work_now"),
    ),
    (
        "flow",
        "the review gates' one decision from both",
        lambda e: _file(e) == "src/crew_org/flows/review.py",
    ),
]


# Where a setting sits, by what it changes: an org.yaml section, an agents.yaml setting,
# or the module a constant lives in. The longest prefix of its id that's listed places
# it; one at a place not listed stays unplaced: a platform's limits, the context a role
# is shown, a report's sizes.
_CONFIG_PLACES = {
    "config.org.board.": "flow",
    "config.org.wip_limits": "flow",
    "config.org.sprint.": "flow",
    "config.org.design.": "scope",
    "config.org.refinement.": "flow",
    "config.org.delivery.": "flow",
    "config.org.sandbox.": "guards",
    "config.org.execution.": "flow",
    "config.org.escalation.": "flow",
    "config.org.trust.": "guards",
    "config.const.calls.": "answer_shape",
    "config.const.cut_text.": "answer_shape",
    "config.const.llm.": "answer_shape",
    "config.const.escalation.": "flow",
    "config.const.claude_code.": "flow",
    "config.const.delivery.": "flow",
    "config.const.merge.": "flow",
    "config.const.board_flow.": "flow",
    "config.const.loop.": "flow",
    "config.const.story_problem.": "flow",
    "config.const.strain.": "flow",
    "config.const.settle.": "flow",
    "config.const.backlog_order.": "flow",
    "config.const.acceptance.": "evidence",
    "config.const.qa_crew.": "evidence",
    "config.const.criteria_check.": "evidence",
    "config.const.review_crew.": "evidence",
    "config.const.review_evidence.": "evidence",
    "config.const.ci.": "evidence",
    "config.const.deploy_evidence.": "evidence",
    "config.const.workspace.": "edits",
    "config.const.regression.": "edits",
    "config.const.pinning.": "edits",
    "config.const.delivery_crew.": "edits",
    "config.const.sandbox.": "guards",
    "config.const.base_images.": "guards",
    "config.const.design.": "scope",
    "config.const.design_notes.": "scope",
    "config.const.presentation_notes.": "scope",
    "config.const.refinement_crew.": "scope",
    "config.const.delivered.": "scope",
    "config.const.settle_crew.": "answer_shape",
    "config.const.retro_crew.": "answer_shape",
    "config.const.stepped.": "answer_shape",
}
_AGENT_PLACES = {
    "llm": "answer_shape",
    "llm_params": "answer_shape",
    "can": "guards",
    "constitution": "guidance",
}


def _config_place(entry: Entry) -> str:
    if entry.id.startswith("config.agents."):
        return _AGENT_PLACES.get(entry.id.rsplit(".", 1)[-1], "")
    found = [p for p in _CONFIG_PLACES if entry.id.startswith(p)]
    return _CONFIG_PLACES[max(found, key=len)] if found else ""


def place(entry: Entry) -> str:
    """The category a rule protects, by PLACEMENT, or by its constitution section."""
    if entry.group == "config":
        return _config_place(entry)
    section = _section(entry)
    if section is not None:
        return _SECTIONS.get(section, "")
    for category, _, holds in PLACEMENT:
        if holds(entry):
            return category
    return _told_site(entry) if entry.group == "told" else ""


# --- the page ---------------------------------------------------------------------------


def _cell(values: set[str] | str) -> str:
    if isinstance(values, str):
        return values.replace("|", "\\|")
    return "<br>".join(sorted(v.replace("|", "\\|") for v in values)) or "—"


def _guidance(entry: Entry) -> str:
    """An empty "checked by": guidance, as declared, or a gap the first policy names."""
    return "guidance" if entry.declared is not None and entry.declared.guidance else "—"


def _cases(entry: Entry) -> str:
    if not entry.cases:
        return ""
    return "<br>Cases: " + ", ".join(f"`{c}` ({_cell(w)})" for c, w in sorted(entry.cases.items()))


def render() -> str:
    """The rules list, as committed."""
    rows = entries()
    lines = [
        "# Every rule a role meets",
        "",
        "Generated by `crew rules` from the code (crew#756). Don't edit it by hand: a test",
        "regenerates it and fails when it differs, so a rule added anywhere shows up here.",
        "",
        "Each rule says where it lives, which roles meet it, where they're told it, what",
        "checks it, and the incidents it cites. A **Told** cell that's empty is a rule",
        "checked and never told; a **Checked by** cell that's empty is one told and never",
        'checked. "maybe" is a link found only by shared wording, for a person to confirm.',
        "How often each fired, and the model time it cost, is `crew rules --events`.",
        "",
        "| Kind | Rules |",
        "|---|---|",
        *(f"| {GROUPS[g]} | {sum(1 for r in rows if r.group == g)} |" for g in GROUPS),
        "",
        "## Categories",
        "",
        "What each rule protects, as a linter's rules are grouped (crew#756). A rule is",
        "placed by the first line of this table that holds for it, or by its constitution",
        "section; one no line holds for is unplaced, never guessed.",
        "",
        "| Category | Placed by | Rules |",
        "|---|---|---|",
        *(
            f"| {CATEGORIES[c]} (`{c}`) | "
            + "<br>".join(why for cat, why, _ in PLACEMENT if cat == c)
            + (
                f"<br>constitution {', '.join(f'§{n}' for n, s in _SECTIONS.items() if s == c)}"
                if c in _SECTIONS.values()
                else ""
            )
            + (
                "<br>told at " + ", ".join(f"`{s}`" for s, t in _TOLD_SITES.items() if t == c)
                if c in _TOLD_SITES.values()
                else ""
            )
            + f" | {sum(1 for r in rows if r.category == c)} |"
            for c in CATEGORIES
        ),
        f"| Unplaced | | {sum(1 for r in rows if not r.category)} |",
        "",
    ]
    for group, title in GROUPS.items():
        lines += [
            f"## {title}",
            "",
            "| Rule | Category | Where | Roles | Told | Checked by | From |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in (r for r in rows if r.group == group):
            lines.append(
                f"| `{_cell(r.id)}`<br>{_cell(r.says)}{_cases(r)} | {r.category or '—'} "
                f"| `{_cell(r.where)}` | {_cell(r.roles)} "
                f"| {_cell(r.told)} "
                f"| {_cell(r.checked) if r.checked else _guidance(r)} | {_cell(r.origin)} |"
            )
        lines.append("")
    return "\n".join(lines)


def write(path: Path = PAGE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8")
    return path


def declarations() -> dict[str, dict[str, dict[str, Any]]]:
    """The catalog as it should be: each rule's declaration, kept where it has one.

    A rule with none is proposed one: its category by PLACEMENT, its owner when one
    role meets it, its origin from what its code cites, and the guidance label when
    it's told and nothing checks it by name. A declaration the code no longer has
    is left out, so writing this removes it.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {c: {} for c in [*CATEGORIES, ""]}
    for entry in entries():
        spec = entry.declared
        if spec is not None:
            fields: dict[str, Any] = {
                "owner": spec.owner,
                "origin": list(spec.origin),
                "guidance": spec.guidance,
                "messages": dict(spec.messages),
                "description": spec.description,
                "told_in": spec.told_in,
                "exempt": spec.exempt,
                "cases": list(spec.cases),
            }
        else:
            fields = {
                "owner": next(iter(entry.roles)) if len(entry.roles) == 1 else "",
                "origin": sorted(entry.origin),
                "guidance": entry.group == "told"
                and not any(not c.startswith("maybe ") for c in entry.checked),
            }
        out[entry.category][entry.id] = fields
    return {c: rules for c, rules in out.items() if rules}


# The declarations the last `write_declarations` dropped: said, never silently lost.
dropped: list[str] = []


def write_declarations() -> list[Path]:
    """Declare every rule that has no declaration yet, and drop those the code lost.

    What's declared is read before any file is rewritten: a decision is never lost
    to the rewrite.
    """
    declared = declarations()
    kept = {rule for rules in declared.values() for rule in rules}
    kept |= {
        case for rules in declared.values() for f in rules.values() for case in f.get("cases", ())
    }
    dropped.extend(sorted(set(rulebook.catalog()) - kept))
    for path in rulebook.CATALOG.glob("*.yaml"):
        if path.stem in {*CATEGORIES, rulebook.UNPLACED}:
            path.unlink()
    return rulebook.write(declared)


def guidance_counts() -> dict[str, int]:
    """How many guidance lines each role is told: what its budget is held to."""
    counts: dict[str, int] = {}
    for entry in entries():
        if entry.declared is not None and entry.declared.guidance:
            for role in entry.roles:
                counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items()))


# --- what a prompt gives ----------------------------------------------------------------

# A told line's place, as `_told_*` write it: a prompt site, a constitution section, an
# agents.yaml field, or a form's description.
_SECTION_PLACE = re.compile(r"§(\d+)")


@cache
def _told_index() -> dict[str, frozenset[str]]:
    """Each place a rule is told, with the rules told there (crew#778).

    Built from `entries()` once a process. A constitution section is keyed with each
    role it's given to, since a passage can be told to some roles only.
    """
    index: dict[str, set[str]] = {}
    for entry in entries():
        if entry.form:
            index.setdefault(f"form:{entry.form}", set()).add(entry.id)
        for place in entry.told:
            if place.startswith("the prompt, "):
                keys = [f"site:{place.removeprefix('the prompt, ')}"]
            elif place.startswith("agents.yaml "):
                keys = [place]
            elif place.startswith("`"):
                keys = [f"form:{place[1:].split('`')[0].split('.')[0]}"]
            elif section := _SECTION_PLACE.match(place):
                keys = [f"§{section.group(1)}:{role}" for role in entry.roles]
            else:
                keys = []
            for key in keys:
                index.setdefault(key, set()).add(entry.id)
    return {key: frozenset(ids) for key, ids in index.items()}


@cache
def _site_lines() -> tuple[tuple[str, str], ...]:
    """Each line a prompt site tells, as (rule id, the text that shows it was given)."""
    out = []
    for entry in entries():
        if not any(p.startswith("the prompt, ") for p in entry.told):
            continue
        # The fixed text before any value the line is written with.
        shown = " ".join(entry.text.split("{", 1)[0].split())[:80]
        if len(shown) >= SITE_TEXT_MIN:
            out.append((entry.id, shown))
    return tuple(out)


# Shorter than this, a line's text could turn up in a prompt that never told it.
SITE_TEXT_MIN = 24


def given(
    *,
    role: str,
    sections: Iterable[int],
    agents: Iterable[str],
    forms: Iterable[str],
    prompt: str,
) -> dict[str, list[str]]:
    """The rule ids a prompt gives, by where they sit (crew#778).

    `role` is the name the constitution's sections are given to (`Product Owner
    (onboarding)` for a step with its own), `agents` the agents.yaml places its system
    prompt is built from, and `forms` the answer's form and the forms inside it. A
    prompt site's line counts where its text is in `prompt`: a site chooses some of
    its lines per call, as the Developer's reading instructions do.
    """
    index = _told_index()

    def ids(keys: Iterable[str]) -> list[str]:
        return sorted({i for key in keys for i in index.get(key, ())})

    said = " ".join(prompt.split())
    return {
        "system": ids([*(f"§{n}:{role}" for n in sections), *agents]),
        "form": ids(f"form:{form}" for form in forms),
        "site": sorted({rule for rule, shown in _site_lines() if shown in said}),
    }


@cache
def catalog_version() -> str:
    """A fingerprint of what the rule ids mean: the catalog, the constitution, agents.yaml."""
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256()
    meaning = [*sorted(rulebook.CATALOG.glob("*.yaml")), CONSTITUTION, ORG.parent / "agents.yaml"]
    for path in meaning:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
