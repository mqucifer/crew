"""Move a definition to another module in one step (#216).

A move was three steps the Developer had to get all of right: write the
definition into the new module, delete it from the old, import it back. In
the Architect's split of sprint-metrics it dropped or doubled one on every
story: both copies kept (#125, #126), the class then each method deleted
(#125), a helper moved with nothing left to import it (#126). Here the crew
does all three.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from crew_org.tools.ast_edit import Edit, EditError, Operation, apply_edits

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def module_of(path: str) -> str:
    """`src/sprint_metrics/card.py` -> `sprint_metrics.card`."""
    parts = path.removesuffix(".py").split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _top(tree: ast.Module, name: str) -> ast.stmt | None:
    for node in tree.body:
        if isinstance(node, DEFINITIONS) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node
    return None


def _defined(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, DEFINITIONS):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _used(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _imports_needed(
    tree: ast.Module, used: set[str], *, target: str, target_names: set[str]
) -> list[str]:
    """The old module's imports the moved definition uses, one statement each.

    Never one from the target itself, nor of a name it defines: after the first
    of several moves, the old module imports that one back from the target.
    """
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            source = "." * node.level + (node.module or "")
            if node.level == 0 and node.module == target:
                continue
            for alias in node.names:
                if (alias.asname or alias.name) in target_names:
                    continue
                bound = alias.asname or alias.name
                if bound in used and alias.name != "*":
                    rename = f" as {alias.asname}" if alias.asname else ""
                    out.append(f"from {source} import {alias.name}{rename}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound in used:
                    rename = f" as {alias.asname}" if alias.asname else ""
                    out.append(f"import {alias.name}{rename}")
    return out


def apply_moves(root: Path, moves: list) -> list[str]:
    """Carry out each move. Returns the paths written. Raises EditError on one it can't do."""
    written: list[str] = []
    batch: dict[tuple[str, str], set[str]] = {}
    for move in moves:
        batch.setdefault((move.from_path, move.to_path), set()).add(move.name)
    for move in moves:
        written += _move_one(root, move, batch[(move.from_path, move.to_path)])
    return list(dict.fromkeys(written))


def _move_one(root: Path, move, together: set[str]) -> list[str]:
    source_file, target_file = root / move.from_path, root / move.to_path
    if not source_file.is_file():
        raise EditError(f"can't move `{move.name}`: {move.from_path!r} doesn't exist")
    text = source_file.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = _top(tree, move.name)
    if node is None:
        raise EditError(
            f"can't move `{move.name}`: {move.from_path!r} has no top-level definition by that name"
        )
    target_text = target_file.read_text(encoding="utf-8") if target_file.is_file() else ""
    if target_text and _top(ast.parse(target_text), move.name) is not None:
        raise EditError(f"can't move `{move.name}`: {move.to_path!r} already defines it")

    used = _used(node)
    # A definition that leans on others still in the old module would have the
    # two modules import each other, which fails at import time. They move too.
    left_behind = sorted((used & _defined(tree)) - together - {move.name})
    if left_behind:
        raise EditError(
            f"can't move `{move.name}` alone: it uses "
            + ", ".join(f"`{n}`" for n in left_behind)
            + f" from {move.from_path!r}. Move those to {move.to_path!r} in the same change."
        )

    lines = text.splitlines()
    decorators = getattr(node, "decorator_list", None) or []
    start = (decorators[0].lineno if decorators else node.lineno) - 1
    definition = "\n".join(lines[start : node.end_lineno or node.lineno])

    # Into the new module, with the imports it uses.
    imports = _imports_needed(
        tree,
        used,
        target=module_of(move.to_path),
        target_names=_defined(ast.parse(target_text)) if target_text else set(),
    )
    if target_text:
        edits = [Edit(operation=Operation.ADD_IMPORT, target="i", source=i) for i in imports]
        edits.append(Edit(operation=Operation.ADD, target=move.name, source=definition))
        target_file.write_text(apply_edits(target_text, edits), encoding="utf-8")
    else:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        head = "\n".join(imports)
        target_file.write_text(
            (f"{head}\n\n\n" if head else "") + definition + "\n", encoding="utf-8"
        )

    # Out of the old one, imported back when anything still asks it for the name.
    remaining = apply_edits(text, [Edit(operation=Operation.DELETE, target=move.name)])
    # And without the imports only it used: left behind, lint calls them unused
    # (F401), the squeeze sprint-metrics#129 spent the night in. Unless another
    # file gets that name through this module, when it's a pass-through.
    remaining = _drop_unused_imports(root, move.from_path, remaining, used)
    if _still_asked(root, move, remaining):
        remaining = apply_edits(
            remaining,
            [
                Edit(
                    operation=Operation.ADD_IMPORT,
                    target="back",
                    source=f"from {module_of(move.to_path)} import {move.name} as {move.name}",
                )
            ],
        )
    source_file.write_text(remaining, encoding="utf-8")
    return [move.to_path, move.from_path]


def _drop_unused_imports(root: Path, path: str, text: str, candidates: set[str]) -> str:
    """Remove the names in `candidates` that `text`'s imports bind and nothing uses now."""
    tree = ast.parse(text)
    used = _used(tree)
    lines = text.splitlines()
    for node in sorted(
        (n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)),
        key=lambda n: -n.lineno,
    ):
        keep = [
            a
            for a in node.names
            if (bound := a.asname or a.name.split(".")[0]) not in candidates
            or bound in used
            or _asked_through(root, path, bound)
        ]
        if len(keep) == len(node.names):
            continue
        start, end = node.lineno - 1, node.end_lineno or node.lineno
        replacement = []
        if keep:
            node.names = keep
            replacement = ast.unparse(node).splitlines()
        lines[start:end] = replacement
    # What deletions leave behind: no more than two blank lines in a row.
    return re.sub(r"\n{4,}", "\n\n\n", "\n".join(lines)) + "\n"


def _asked_through(root: Path, path: str, name: str) -> bool:
    """Does another file import `name` from the module at `path`?"""
    module = module_of(path)
    for other in root.rglob("*.py"):
        if {".git", ".venv", "__pycache__"} & set(other.relative_to(root).parts):
            continue
        if other == root / path:
            continue
        try:
            tree = ast.parse(other.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == module
                and any(a.name == name for a in node.names)
            ):
                return True
    return False


def _still_asked(root: Path, move, remaining: str) -> bool:
    """Does the old module still use the name, or another file import it from there?"""
    if move.name in _used(ast.parse(remaining)):
        return True
    old = module_of(move.from_path)
    for path in root.rglob("*.py"):
        if {".git", ".venv", "__pycache__"} & set(path.relative_to(root).parts):
            continue
        if path == root / move.from_path:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == old:
                if any(a.name == move.name for a in node.names):
                    return True
            elif isinstance(node, ast.Import) and any(a.name == old for a in node.names):
                return True
    return False
