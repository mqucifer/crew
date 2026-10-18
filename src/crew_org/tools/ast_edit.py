"""Replacing code by name rather than by position: the session's `replace_definition`.

The Developer used to return whole files, which made every story a
transcription exercise: regenerate three hundred lines, change four, and leave
the rest byte-identical. It could not do that, and the failures were not
random — it redesigned what it was asked to extend, deleted tests, and drifted
in code nobody had asked it to touch.

Targeting by name removes the transcription entirely. The model names a
definition and supplies its new source; nothing else is reproduced, so nothing
else can be damaged. In the Developer's session (crew#778) this is one of its
edit tools, beside quoting lines (`replace_text`) and writing whole files.

Two findings shaped this. Aider measured a 30-50% rise in editing errors when
models were pushed toward surgical line edits instead of whole functions, and a
9x rise without permissive parsing — so edits are whole definitions and the
applier forgives imperfect indentation. A published comparison of edit formats
found name-addressed AST edits had zero format failures across four models, where
search/replace had eleven and unified diff thirty-one, because both of those
require reproducing existing text exactly.

Python only. The principle generalises, the parser does not.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from crew_org.rulebook import Refusal, refuse


class EditError(Refusal):
    """An edit that cannot be applied, described so the model can fix it."""


@dataclass(frozen=True)
class Edit:
    """Replace an existing top-level definition or method, by name."""

    target: str
    source: str = ""


def _definitions(tree: ast.Module) -> dict[str, ast.stmt]:
    """Every addressable definition, by qualified dotted name."""
    found: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found[node.name] = node
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                        found[f"{node.name}.{child.name}"] = child
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node
        # `DEFAULT_THRESHOLDS: dict[str, float] = {...}`: a constant with a type.
        # Left out, sprint-metrics#127 was told to change it by name and then
        # that the name didn't exist.
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = node
    return found


def definitions(source: str) -> dict[str, ast.stmt]:
    """Every addressable definition in a module, by qualified dotted name.

    Unparseable source has no addressable definitions, which is the useful
    answer for every caller: there is nothing here to edit or to compare.
    """
    try:
        return _definitions(ast.parse(source))
    except SyntaxError:
        return {}


def _span(node: ast.stmt) -> tuple[int, int]:
    """The line range a definition occupies, decorators included."""
    decorators = getattr(node, "decorator_list", None)
    start = (decorators[0].lineno if decorators else node.lineno) - 1
    return start, (node.end_lineno or node.lineno)


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(source: str, indent: str) -> str:
    """Put a definition at the indentation its destination needs.

    A model asked for a method will usually emit it unindented, or indented by
    a guess. Correcting that here is cheaper than rejecting the edit.
    """
    lines = source.strip("\n").splitlines()
    if not lines:
        return ""
    base = _indent_of(lines[0])
    out = []
    for line in lines:
        stripped = line[len(base) :] if line.startswith(base) else line.lstrip()
        out.append(indent + stripped if stripped.strip() else "")
    return "\n".join(out)


def apply_edit(source: str, edit: Edit) -> str:
    """Apply one edit. The file is re-parsed per edit, so spans never go stale."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        refuse("edit.ast_edit.apply_edit", "unparsed", kind=EditError, error=exc)

    lines = source.splitlines()
    definitions = _definitions(tree)

    node = definitions.get(edit.target)
    if node is None:
        known = ", ".join(sorted(definitions)[:12]) or "nothing"
        refuse(
            "edit.ast_edit.apply_edit",
            "no_definition",
            kind=EditError,
            target=repr(edit.target),
            known=known,
        )

    start, end = _span(node)
    indent = _indent_of(lines[start])
    body = _reindent(edit.source, indent)
    if not body.strip():
        refuse("edit.ast_edit.apply_edit", "empty", kind=EditError, target=repr(edit.target))
    return "\n".join(lines[:start] + body.splitlines() + lines[end:]) + "\n"
