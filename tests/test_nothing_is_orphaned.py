"""Code nothing can reach, found mechanically rather than by a person reading.

`find_regressions` was orphaned when the Developer switched from returning whole
files to editing by name. It stayed in the tree for two months with a green test
file, which is what made it look alive — and crew#9 cites it as the example of a
failure the crew cannot see about itself. A person found it, twice: once to file
that card, once to act on it.

This is that check, so the next one announces itself. It is deliberately a test
rather than a sentence in a document: a claim about what the code does goes
stale silently, and a failing test does not.

**What it does not claim.** Reachable is not the same as correct, and unreached
is not always wrong — a deliberate test helper is neither. The exemptions below
carry their reason, and adding one is a decision rather than a workaround.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "crew_org"

# Reached by something other than production code, with the reason. A name here
# is a claim that nothing *should* call it, which is worth stating out loud.
EXEMPT = {
    "bridged_sinks": "test-only: asserts the CrewAI bridge registered a sink",
    "reset_bridge": "test-only: the global bus handler cannot be unregistered",
}


def _module_functions() -> dict[Path, dict[str, ast.FunctionDef]]:
    out: dict[Path, dict[str, ast.FunctionDef]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Private functions are in the graph but never reported: a public
        # function called only by a private one is reachable, and leaving them
        # out made `deliver_story` look dead because `_work_one_card` calls it.
        out[path] = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    return out


def _names(node: ast.AST) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def _is_entry_point(node: ast.FunctionDef) -> bool:
    """A typer command. Nothing in the codebase calls it; the CLI does."""
    return any(
        ".command(" in ast.unparse(d) or ast.unparse(d).endswith(".command")
        for d in node.decorator_list
    )


def orphans() -> dict[str, str]:
    """Public module-level functions nothing outside their own module can reach.

    Two passes, because a single "is this name mentioned anywhere" check gets
    both answers wrong. It calls a whole dead cluster alive when its members
    only call each other — which is exactly how `find_regressions` survived,
    each of its five helpers referenced by the one above it and none of them
    by anything real.

    So: a function referenced from *another* module is a root, and everything a
    root reaches within its own module is live. Cross-module references are
    used rather than bare name matching, because two modules may define `run`
    and matching on the name alone declares both reachable.
    """
    by_file = _module_functions()

    referenced_from_elsewhere: dict[Path, set[str]] = defaultdict(set)
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used = _names(tree)
        for other, functions in by_file.items():
            if other == path:
                continue
            referenced_from_elsewhere[other] |= used & set(functions)

    found: dict[str, str] = {}
    for path, functions in by_file.items():
        roots = set(referenced_from_elsewhere[path])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                if _is_entry_point(node):
                    roots.add(node.name)
            else:
                # Module scope and class bodies run on import.
                roots |= _names(node) & set(functions)

        reached: set[str] = set()
        stack = list(roots)
        while stack:
            name = stack.pop()
            if name in reached or name not in functions:
                continue
            reached.add(name)
            stack.extend(_names(functions[name]) & set(functions))

        for name in set(functions) - reached:
            if not name.startswith("_"):
                found[name] = str(path.relative_to(SRC))
    return found


def test_nothing_in_src_is_unreachable():
    """A function nothing can reach is dead whatever its tests say.

    crew#9's argument in miniature: `tests/test_regression.py` was green over
    ninety lines that nothing called, and green tests are what made it invisible.
    """
    unreachable = {n: where for n, where in orphans().items() if n not in EXEMPT}
    assert not unreachable, "nothing reaches these; delete them or call them:\n" + "\n".join(
        f"  {n}  ({where})" for n, where in sorted(unreachable.items())
    )


def test_every_exemption_is_still_needed():
    """An exemption that stops being true is its own kind of stale claim."""
    found = orphans()
    stale = sorted(name for name in EXEMPT if name not in found)
    assert not stale, f"these are reachable now and need no exemption: {stale}"
