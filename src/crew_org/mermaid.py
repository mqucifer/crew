"""Whether a Mermaid diagram the Architect draws is well formed (crew#858).

The Goal's target diagram is C4 (Simon Brown's model: context, containers and
components) in Mermaid's C4 syntax, which GitHub renders in the Goal's comment. A
diagram GitHub can't render is a comment of source text nobody reads, and nothing in
the crew runs Mermaid, so this checks the grammar the Architect is asked for, in code:

- the first line is a C4 diagram type;
- every other line is a C4 macro the syntax has, a boundary's closing brace, a title
  or a comment;
- quotes and parentheses balance, each macro has the arguments it needs, and a
  boundary opens its brace;
- each alias is defined once, and each relation names aliases the diagram defines.

A sequence diagram, drawn only when a Goal adds an interaction, is checked the same
way for its own lines and for blocks that open and end.

What it can't show is that GitHub draws it as intended: layout and Mermaid's own
quirks. Its tests' diagrams were run once through Mermaid's parser (`tests/test_mermaid.py`).
"""

from __future__ import annotations

import re

C4_TYPES = ("C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment")
# Each macro, and how many arguments it needs before the optional ones.
_ELEMENTS = {
    f"{kind}{variant}{ext}": 2
    for kind in ("Person", "System", "Container", "Component")
    for variant in (("",) if kind == "Person" else ("", "Db", "Queue"))
    for ext in ("", "_Ext")
}
_BOUNDARIES = {
    "Boundary": 2,
    "Enterprise_Boundary": 2,
    "System_Boundary": 2,
    "Container_Boundary": 2,
    "Deployment_Node": 2,
    "Node": 2,
    "Node_L": 2,
    "Node_R": 2,
}
_RELATIONS = {
    name: 3
    for name in (
        "Rel",
        "BiRel",
        "Rel_U",
        "Rel_Up",
        "Rel_D",
        "Rel_Down",
        "Rel_L",
        "Rel_Left",
        "Rel_R",
        "Rel_Right",
        "Rel_Back",
    )
}
_STYLES = {
    "UpdateElementStyle": 1,
    "UpdateRelStyle": 2,
    "UpdateBoundaryStyle": 1,
    "UpdateLayoutConfig": 0,
}
_MACRO = re.compile(r"^(\w+)\s*\((.*)\)\s*(\{)?\s*$")
_ALIAS = re.compile(r"^[A-Za-z_][\w.]*$")


def _unfenced(text: str) -> list[str]:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return [line.strip() for line in lines]


def _arguments(inner: str) -> list[str] | None:
    """A macro's arguments, split at commas outside quotes, or None if a quote is open."""
    out, current, quoted = [], "", False
    for ch in inner:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            out.append(current.strip())
            current = ""
        else:
            current += ch
    if quoted:
        return None
    if current.strip() or out:
        out.append(current.strip())
    return out


def c4_problems(text: str) -> list[str]:
    """What keeps a Mermaid C4 diagram from rendering, in its own words. Empty if nothing."""
    lines = _unfenced(text)
    if not lines or lines[0] not in C4_TYPES:
        first = lines[0] if lines else "nothing"
        return [f"the first line is `{first[:40]}`, not one of {', '.join(C4_TYPES)}"]
    problems: list[str] = []
    defined: dict[str, int] = {}
    relations: list[tuple[int, list[str]]] = []
    depth = 0
    for n, line in enumerate(lines[1:], 2):
        if not line or line.startswith("%%") or line.startswith("title "):
            continue
        if line == "}":
            depth -= 1
            if depth < 0:
                problems.append(f"line {n}: a `}}` closes no boundary")
                depth = 0
            continue
        found = _MACRO.match(line)
        if not found:
            problems.append(f"line {n}: `{line[:60]}` isn't a C4 macro, a `}}` or a title")
            continue
        name, inner, brace = found.groups()
        need = {**_ELEMENTS, **_BOUNDARIES, **_RELATIONS, **_STYLES}.get(name)
        if need is None:
            problems.append(f"line {n}: `{name}` isn't a C4 macro")
            continue
        args = _arguments(inner)
        if args is None or inner.count("(") != inner.count(")"):
            problems.append(f"line {n}: a quote or a parenthesis is left open")
            continue
        positional = [a for a in args if not a.startswith("$")]
        if len(positional) < need:
            problems.append(f"line {n}: `{name}` needs {need} arguments, and has {len(positional)}")
            continue
        if name in _BOUNDARIES:
            if not brace:
                problems.append(f"line {n}: `{name}` opens no `{{` for what it holds")
            depth += 1
        elif brace:
            problems.append(f"line {n}: `{name}` can't hold anything")
        if name in _ELEMENTS or name in _BOUNDARIES:
            alias = positional[0]
            if not _ALIAS.match(alias):
                problems.append(f"line {n}: `{alias}` isn't an alias: letters, digits, `_`")
            elif alias in defined:
                problems.append(
                    f"line {n}: `{alias}` is defined twice, on lines {defined[alias]} and {n}"
                )
            else:
                defined[alias] = n
        elif name in _RELATIONS:
            relations.append((n, positional[:2]))
    if depth:
        problems.append(f"{depth} boundary left open: close each with `}}`")
    for n, ends in relations:
        problems += [
            f"line {n}: the relation names `{end}`, which the diagram doesn't define"
            for end in ends
            if end not in defined
        ]
    return problems


def aliases(text: str) -> set[str]:
    """The aliases a C4 diagram defines."""
    found = set()
    for line in _unfenced(text):
        macro = _MACRO.match(line)
        if macro and (macro.group(1) in _ELEMENTS or macro.group(1) in _BOUNDARIES):
            args = _arguments(macro.group(2)) or []
            if args:
                found.add(args[0])
    return found


_ARROW = r"(?:->>|-->>|->|-->|-x|--x|-\)|--\))"
_SEQUENCE_LINES = (
    re.compile(r"^(?:participant|actor)\s+\S+(?:\s+as\s+.+)?$"),
    re.compile(rf"^[^\s:>-]+\s*{_ARROW}[+-]?\s*[^\s:]+\s*:.*$"),
    re.compile(r"^Note\s+(?:over|left of|right of)\s+[^:]+:.*$"),
    re.compile(r"^(?:activate|deactivate)\s+\S+$"),
    re.compile(r"^(?:autonumber|title\s.*|%%.*)$"),
)
_OPENS = ("loop", "alt", "opt", "par", "critical", "break", "rect", "box")
_INSIDE = ("else", "and", "option")


def sequence_problems(text: str) -> list[str]:
    """What keeps a Mermaid sequence diagram from rendering. Empty if nothing."""
    lines = _unfenced(text)
    if not lines or lines[0] != "sequenceDiagram":
        first = lines[0] if lines else "nothing"
        return [f"the first line is `{first[:40]}`, not `sequenceDiagram`"]
    problems: list[str] = []
    depth = 0
    for n, line in enumerate(lines[1:], 2):
        word = line.split(" ", 1)[0]
        if not line:
            continue
        if word in _OPENS:
            depth += 1
        elif word == "end":
            depth -= 1
            if depth < 0:
                problems.append(f"line {n}: an `end` closes no block")
                depth = 0
        elif word in _INSIDE:
            if not depth:
                problems.append(f"line {n}: `{word}` is outside any block")
        elif not any(form.match(line) for form in _SEQUENCE_LINES):
            problems.append(f"line {n}: `{line[:60]}` isn't a sequence diagram line")
    if depth:
        problems.append(f"{depth} block left open: close each with `end`")
    return problems
