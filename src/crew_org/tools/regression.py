"""Detecting when an implementation destroys work that already exists.

The Developer returns whole files, which is what makes its output easy to
validate and repair. The cost is that touching an existing file means rewriting
it, and a model asked to "add throughput" will happily redesign the module it
is adding to — discarding functions that other stories, and other tests, depend
on.

Observed on story #7: it rewrote story #6's merged module from scratch, renamed
its public functions, and implemented three future stories along the way. The
existing tests then failed to import, which reads as a coding error rather than
as the regression it is.

This is checked mechanically because it is mechanically checkable, and because
a prompt asking the model not to do it is a request rather than a guarantee.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Callable
from pathlib import Path

# How a contract reads when its definition is deleted. A move reads the same
# until `without_moves` looks across files (#202).
REMOVED = "removed entirely"
# Not the project's code: a move is never judged against these.
_SKIP = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache"}


class Merged:
    """The default branch where this story's branch left it: what's merged.

    The guards protect merged work, not a story's own draft. A failed
    attempt's changes stay in the worktree for the repair to build on, and
    judged against that draft the guards went wrong both ways: sprint-metrics
    #126 couldn't delete a test its own first attempt had added, and #129's
    first attempt removed imports `__init__.py` needed, after which nothing
    was ever "lost" again and the tests failed without the guard saying why.
    """

    def __init__(self, worktree: Path, sha: str) -> None:
        self.worktree, self.sha = worktree, sha

    def text(self, path: str) -> str | None:
        """`path` as merged, or None if it isn't on the default branch."""
        import subprocess  # noqa: PLC0415

        shown = subprocess.run(
            ["git", "show", f"{self.sha}:{path}"],
            cwd=self.worktree,
            capture_output=True,
            text=True,
        )
        return shown.stdout if shown.returncode == 0 else None

    def changed(self) -> set[str]:
        """Python files the worktree has changed since, committed or not."""
        import subprocess  # noqa: PLC0415

        diff = subprocess.run(
            ["git", "diff", "--name-only", self.sha],
            cwd=self.worktree,
            capture_output=True,
            text=True,
        )
        return {p for p in diff.stdout.split() if p.endswith(".py")}


def merged_base(worktree: Path) -> Merged | None:
    """The merged state for `worktree`, or None where there's no git to ask."""
    import subprocess  # noqa: PLC0415

    found = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    sha = found.stdout.strip()
    return Merged(worktree, sha) if found.returncode == 0 and sha else None


def _before(worktree: Path, path: str, merged: Merged | None) -> str | None:
    """`path` as the guards compare against: merged, else the file as it stands."""
    if merged is not None:
        return merged.text(path)
    target = worktree / path
    return target.read_text(encoding="utf-8") if target.is_file() else None


def _parse_text(text: str | None) -> ast.Module | None:
    if text is None:
        return None
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def overwrites_existing(worktree: Path, new_files: list) -> list[str]:
    """New files that would overwrite something already there.

    Writing an existing path as a "new file" is a whole-file rewrite by another
    name — the exact thing editing by name exists to prevent — so it is refused
    rather than merged.
    """
    return [f.path for f in new_files if (worktree / f.path).exists()]


# --- what a replacement may not change -----------------------------------
#
# Editing by name made the change list visible, but nothing evaluated whether
# the listed scope was proportionate to the story. Six public definitions were
# replaced for a card that asked for one column, and every replacement changed
# a signature: a frozen dataclass gained fields, properties became methods, a
# return type went from `int` to `float | None`. The private helper that
# consumed those values was never looked at, so it went on summing integers
# over a list of None.
#
# Hence the line drawn here. A body is the author's business. A signature is a
# promise other code is already relying on, and a story that says "add a
# column" is not a story about renegotiating promises.

# Decorators that change how a definition is *called*, not merely what it does.
CALLING_DECORATORS = frozenset({"property", "staticmethod", "classmethod", "cached_property"})


def _decorator_name(node: ast.expr) -> str:
    """The bare name of a decorator, however it is spelled."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Parameter names, in the order a caller must supply them."""
    a = node.args
    names = [p.arg for p in (*a.posonlyargs, *a.args)]
    if a.vararg:
        names.append(f"*{a.vararg.arg}")
    names += [f"{p.arg}=" for p in a.kwonlyargs]
    if a.kwarg:
        names.append(f"**{a.kwarg.arg}")
    return names


def signature_of(node: ast.stmt) -> str | None:
    """What a caller depends on. None for definitions with no callable contract.

    Deliberately not the full source: renaming a local, rewriting a body or
    changing a docstring are the author's business and must stay allowed, or
    the check refuses the very edits stories are made of.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        calling = sorted(
            {
                name
                for d in node.decorator_list
                if (name := _decorator_name(d)) in CALLING_DECORATORS
            }
        )
        kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{' '.join(calling)} {kind}({', '.join(_parameters(node))})".strip()

    if isinstance(node, ast.ClassDef):
        # A dataclass's fields are its constructor, so an annotated attribute is
        # as much a part of the contract as a method is.
        members: list[str] = []
        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and (
                not child.name.startswith("_") or child.name == "__init__"
            ):
                members.append(f"{child.name} {signature_of(child)}")
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and not child.target.id.startswith("_")
            ):
                members.append(child.target.id)
        bases = [_decorator_name(b) for b in node.bases]
        decorators = sorted(_decorator_name(d) for d in node.decorator_list)
        return f"class({', '.join(bases)}) {' '.join(decorators)} {{{', '.join(sorted(members))}}}"

    return None


def _first_definition(source: str) -> ast.stmt | None:
    """The definition an edit carries, parsed on its own.

    An edit's source arrives at whatever indentation it had in the file, so it
    is dedented before parsing — a method replacement is otherwise a syntax
    error rather than a definition.
    """
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            return node
    return None


def broken_contracts(
    worktree: Path, edits: list, merged: Merged | None = None
) -> dict[str, tuple[str, str]]:
    """Edits that would change or remove a contract other code depends on.

    Keyed by `path::target`, valued by what the signature was and what it would
    become — the two strings are the whole explanation, so the message written
    from them needs no further reasoning.

    A replacement that leaves the signature alone is not reported, however much
    of the body it rewrites. That is the point: stories are made of body edits,
    and a check that refused those would refuse everything.
    """
    from crew_org.tools.ast_edit import definitions  # noqa: PLC0415

    found: dict[str, tuple[str, str]] = {}
    cache: dict[str, dict[str, ast.stmt]] = {}

    for edit in edits:
        operation = str(getattr(edit, "operation", ""))
        if operation not in {"replace", "delete"} or not edit.path.endswith(".py"):
            continue
        # A private helper is the author's business — nothing outside the module
        # can depend on it, and refusing to let a story reshape its own internals
        # is the kind of constraint that blocks correct work. `__init__` is the
        # exception: it is private in name only, and it is the constructor.
        leaf = edit.target.rpartition(".")[2]
        if leaf.startswith("_") and leaf != "__init__":
            continue

        if edit.path not in cache:
            # As merged: a definition only this story's draft added is its own
            # to change or delete (sprint-metrics#126).
            text = _before(worktree, edit.path, merged)
            if text is None:
                continue
            try:
                cache[edit.path] = definitions(text)
            except SyntaxError:
                continue

        old_node = cache[edit.path].get(edit.target)
        if old_node is None:
            # Naming something that is not there is a different failure, and
            # the edit machinery already reports it as one.
            continue
        was = signature_of(old_node)
        if was is None:
            continue

        key = f"{edit.path}::{edit.target}"
        if operation == "delete":
            found[key] = (was, REMOVED)
            continue

        new_node = _first_definition(edit.source)
        if new_node is None:
            continue
        broke = contract_break(old_node, new_node)
        if broke is not None:
            found[key] = (was, broke)

    return found


def describe_contracts(broken: dict[str, tuple[str, str]]) -> str:
    """What the Developer is told. Written so the repair is obvious."""
    lines = [
        "This changes what existing code promises its callers. Other stories "
        "have already been merged against these signatures and their tests "
        "still call them the old way.",
        "",
    ]
    for key, (was, broke) in sorted(broken.items()):
        path, target = key.split("::", 1)
        lines.append(f"`{target}` in `{path}` {broke}")
        lines.append(f"    it is currently: {was}")
    lines += [
        "",
        "Adding is fine and is usually the answer: a new field with a default, "
        "a new method, a new optional parameter, a whole new function. Rewrite "
        "a body as freely as the story needs — that is invisible to callers.",
        "",
        "What callers cannot survive is a promise being taken back. Keep every "
        "existing parameter, in order; keep a property a property; keep every "
        "field it already has, in the order it has them. If you need a new "
        "field, append it with a default.",
    ]
    if any(broke.startswith(REMOVED) for _was, broke in broken.values()):
        lines += [
            "",
            "Moving a definition to another module is fine, and is not removing it. "
            "Define it there with the same shape, delete it here, and import it back "
            "into this module so its callers still find it. Only when nothing imports "
            "this module any more may it stop providing the name, and then the "
            "package's `__init__.py` must import it from its new home. Never keep "
            "both copies.",
        ]
    return "\n".join(lines)


def render_signature(node: ast.stmt) -> str | None:
    """A definition's contract, written the way a caller reads it.

    Compact on purpose: this goes in the Developer's prompt, where it has to
    earn its tokens and stay identical between attempts so the prefix stays
    cacheable. `signature_of` is the exact form used for comparison; this is
    the same information written to be read.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        calling = [
            name for d in node.decorator_list if (name := _decorator_name(d)) in CALLING_DECORATORS
        ]
        rendered = f"({', '.join(_parameters(node))})"
        return f"{rendered} [{calling[0]}]" if calling else rendered
    if isinstance(node, ast.ClassDef):
        return ""
    return None


def signatures_for_context(source: str) -> dict[str, str]:
    """Every public definition, mapped to how it must be called.

    Shown to the Developer before it writes, rather than only quoted back at it
    after it has broken something. A constraint the model is never told is not
    a constraint it can respect, and discovering one by being refused costs a
    repair attempt that was reserved for real failures.
    """
    from crew_org.tools.ast_edit import definitions  # noqa: PLC0415

    out: dict[str, str] = {}
    for name, node in definitions(source).items():
        if name.rpartition(".")[2].startswith("_"):
            continue
        rendered = render_signature(node)
        if rendered is not None:
            out[name] = rendered
    return out


# --- additive is not breaking --------------------------------------------
#
# The first version of this check compared the whole member set for equality,
# which made every addition a break. Story #9 needs `Card.blocked_since` — the
# aging it reports is measured from it — so the check refused the story it was
# meant to protect. A model that had listened, kept every property a property
# and touched nothing else, was told no three times.
#
# Growth is how a module serves a new story. What callers cannot survive is a
# promise being withdrawn or changed: a parameter removed or reordered, a new
# required argument, a property becoming a method, a field appearing before the
# ones already being passed positionally.


def _params_with_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, bool]]:
    """Positional parameters in order, each with whether it has a default."""
    a = node.args
    positional = [*a.posonlyargs, *a.args]
    # defaults align to the tail of the positional list.
    first_default = len(positional) - len(a.defaults)
    return [(p.arg, i >= first_default) for i, p in enumerate(positional)]


def _fields(node: ast.ClassDef) -> list[tuple[str, bool]]:
    """Public annotated attributes in declaration order, with whether each has a default.

    Order matters: a dataclass's field order is its positional constructor, so
    a new field appearing before an existing one silently reassigns arguments
    at every call site that never changed.
    """
    return [
        (child.target.id, child.value is not None)
        for child in node.body
        if isinstance(child, ast.AnnAssign)
        and isinstance(child.target, ast.Name)
        and not child.target.id.startswith("_")
    ]


def _methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        child.name: child
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        and (not child.name.startswith("_") or child.name == "__init__")
    }


def _function_break(old: ast.stmt, new: ast.stmt) -> str | None:
    """How `new` breaks `old`'s promise to callers, or None if it only adds."""
    if not isinstance(old, ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    if not isinstance(new, ast.FunctionDef | ast.AsyncFunctionDef):
        return "is no longer a function"

    old_calling = {n for d in old.decorator_list if (n := _decorator_name(d)) in CALLING_DECORATORS}
    new_calling = {n for d in new.decorator_list if (n := _decorator_name(d)) in CALLING_DECORATORS}
    if old_calling != new_calling:
        was = ", ".join(sorted(old_calling)) or "a plain method"
        now = ", ".join(sorted(new_calling)) or "a plain method"
        return f"was {was} and is now {now}, so every caller has to change"

    old_params, new_params = _params_with_defaults(old), _params_with_defaults(new)
    old_names = [n for n, _ in old_params]
    new_names = [n for n, _ in new_params]
    if new_names[: len(old_names)] != old_names:
        return f"parameters ({', '.join(old_names)}) became ({', '.join(new_names)})"
    required = [n for n, has_default in new_params[len(old_params) :] if not has_default]
    if required:
        return f"adds required parameter(s): {', '.join(required)}"
    return None


def _class_break(old: ast.ClassDef, new: ast.stmt) -> str | None:
    if not isinstance(new, ast.ClassDef):
        return "is no longer a class"

    old_fields, new_fields = _fields(old), _fields(new)
    old_names = [n for n, _ in old_fields]
    new_names = [n for n, _ in new_fields]
    if new_names[: len(old_names)] != old_names:
        return (
            f"fields ({', '.join(old_names)}) became ({', '.join(new_names)}) — "
            "existing fields must keep their names and their order"
        )
    required = [n for n, has_default in new_fields[len(old_fields) :] if not has_default]
    if required:
        return f"adds field(s) with no default: {', '.join(required)}"

    old_methods, new_methods = _methods(old), _methods(new)
    for name, node in old_methods.items():
        if name not in new_methods:
            return f"removes `{name}`"
        if (broke := _function_break(node, new_methods[name])) is not None:
            return f"`{name}` {broke}"
    return None


def contract_break(old: ast.stmt, new: ast.stmt) -> str | None:
    """How a replacement breaks what callers already rely on, or None if it grows.

    Adding is always allowed: a new field with a default, a new method, a new
    optional parameter. Those are how a module serves a story it did not
    originally have. Withdrawing or reshaping is not.
    """
    if isinstance(old, ast.ClassDef):
        return _class_break(old, new)
    return _function_break(old, new)


# --- a definition moved, not removed (#202) ------------------------------------------------------
#
# The contract check reads one file at a time, so moving a definition to
# another module looked exactly like deleting it. The Architect's first
# refactor, sprint-metrics#123, is nothing but moves, and its first story was
# refused twice for "removing" `Card`. What callers rely on is that the name
# still reaches them with the same shape, not which file defines it.


def without_moves(
    worktree: Path,
    implementation,
    broken: dict[str, tuple[str, str]],
    merged: Merged | None = None,
) -> dict[str, tuple[str, str]]:
    """`broken`, less the removals that are really moves.

    A top-level definition deleted from module A is kept when, once the change
    is applied, a definition of the same name and a compatible shape exists in
    another module B, and either A still binds the name by importing it from
    B, or nothing in the repository asks A for the name any more and A's
    package binds it from B. Judged on a scratch copy with the change applied, because
    a move is spread across files: the new module, the deletion, the import.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from crew_org.tools.workspace import apply_implementation  # noqa: PLC0415

    removals = {
        key: value
        for key, value in broken.items()
        if value[1] == REMOVED and "." not in key.partition("::")[2]
    }
    if not removals:
        return broken
    with tempfile.TemporaryDirectory() as scratch:
        after = Path(scratch) / "after"
        shutil.copytree(worktree, after, ignore=shutil.ignore_patterns(*_SKIP), symlinks=True)
        try:
            apply_implementation(after, implementation)
        except Exception as exc:  # noqa: BLE001
            # It won't apply, so nothing here can show a move. Say so: reported
            # only as "removed entirely", the edit error that is the real fault
            # was never seen.
            why = f"the change doesn't apply, so it couldn't be checked as a move: {exc}"
            return {
                key: (value[0], f"{REMOVED}; {why}") if key in removals else value
                for key, value in broken.items()
            }
        modules = _modules(after)
        before = {
            path: _parse_text(_before(worktree, path, merged))
            for path in {k.partition("::")[0] for k in removals}
        }
        why = {key: _moved(key, before, modules) for key in removals}
    kept = {key for key, reason in why.items() if not reason}
    # A method goes where its class went: `Card.is_completed` removed with
    # `Card`, and `Card` moved whole, is part of that move. The class's own
    # check already compared every method.
    kept |= {key for key in broken if _owner(key) in kept}
    return {
        key: (value[0], f"{REMOVED}; not a move: {why[key]}") if why.get(key) else value
        for key, value in broken.items()
        if key not in kept
    }


def _owner(key: str) -> str:
    """`path::Class.method` -> `path::Class`; a top-level key is its own owner."""
    path, _, target = key.partition("::")
    return f"{path}::{target.split('.')[0]}"


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None


def _modules(root: Path) -> dict[str, ast.Module]:
    """Every parseable Python file under `root`, by repository-relative path."""
    found: dict[str, ast.Module] = {}
    for path in root.rglob("*.py"):
        if _SKIP & set(path.relative_to(root).parts):
            continue
        tree = _parse(path)
        if tree is not None:
            found[path.relative_to(root).as_posix()] = tree
    return found


def _top_level(tree: ast.Module, name: str) -> ast.stmt | None:
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name == name
        ):
            return node
    return None


def _resolve(importer: str, module: str | None, level: int, files: set[str]) -> str | None:
    """The repository file an import names, or None if it isn't one of ours."""
    parts = [p for p in (module or "").split(".") if p]
    if level:
        base = Path(importer).parent
        for _ in range(level - 1):
            base = base.parent
        stem = base.joinpath(*parts) if parts else base
        candidates = [f"{stem.as_posix()}.py", f"{stem.as_posix()}/__init__.py"]
        return next((c for c in candidates if c in files), None)
    if not parts:
        return None
    tail = "/".join(parts)
    for suffix in (f"{tail}.py", f"{tail}/__init__.py"):
        matches = [f for f in files if f == suffix or f.endswith(f"/{suffix}")]
        if len(matches) == 1:
            return matches[0]
    return None


def _binds_from(tree: ast.Module, importer: str, name: str, files: set[str]) -> set[str]:
    """The files `importer` imports `name` from, under its own name."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == name and (alias.asname in (None, name)):
                target = _resolve(importer, node.module, node.level, files)
                if target is not None:
                    found.add(target)
    return found


def _imports_whole(tree: ast.Module, importer: str, module_file: str, files: set[str]) -> bool:
    """Does `importer` import `module_file` as a module, so any name in it may be used?"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_resolve(importer, a.name, 0, files) == module_file for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            # `from pkg import module` names the module as an attribute.
            package = _resolve(importer, node.module, node.level, files)
            if package and package.endswith("__init__.py"):
                folder = package.rpartition("/")[0]
                if any(f"{folder}/{a.name}.py" == module_file for a in node.names):
                    return True
    return False


def _moved(key: str, before: dict[str, ast.Module | None], after: dict[str, ast.Module]) -> str:
    """'' if the removal at `key` is a move callers survive; otherwise why it isn't.

    The why names the one step that's missing, so a repair can make it: a
    bare "removed entirely" left sprint-metrics#127 repeating the same move
    three times without learning which part was wrong.
    """
    path, _, name = key.partition("::")
    old_tree = before.get(path)
    old = _top_level(old_tree, name) if old_tree is not None else None
    if old is None:
        return f"`{path}` had no top-level `{name}` to move"
    files = set(after)
    elsewhere = {
        file: new
        for file, tree in after.items()
        if file != path and (new := _top_level(tree, name)) is not None
    }
    if not elsewhere:
        return f"no other module defines `{name}`, so this deletes it rather than moving it"
    homes = {file for file, new in elsewhere.items() if contract_break(old, new) is None}
    if not homes:
        file, new = sorted(elsewhere.items())[0]
        return f"`{file}` defines `{name}`, but it {contract_break(old, new)}"
    home = sorted(homes)[0]
    module = home.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
    module = module.removeprefix("src.")
    # The old module still hands it out: callers of A.name are untouched.
    if path in after and _binds_from(after[path], path, name, files) & homes:
        return ""
    askers = sorted(
        file
        for file, tree in after.items()
        if file != path
        and (
            path in _binds_from(tree, file, name, files) or _imports_whole(tree, file, path, files)
        )
    )
    if askers:
        return (
            f"`{home}` has it, but `{path}` doesn't import it back "
            f"(add `from {module} import {name}` to `{path}`), and "
            f"`{askers[0]}` still gets it from `{path}`"
        )
    package = f"{path.rpartition('/')[0]}/__init__.py" if "/" in path else "__init__.py"
    if package in after and _binds_from(after[package], package, name, files) & homes:
        return ""
    return (
        f"`{home}` has it and nothing asks `{path}` for it any more, but `{package}` "
        f"doesn't import it from `{home}`"
    )


# --- a name a module passes along is a promise too ---------------------------------------------
#
# `crew_performance.py` imported `calculate_blocked_aging` from `metrics.py`
# and the package's `__init__.py` imported it from there. sprint-metrics#129
# removed that import as unused (lint's F401), the package broke, and putting
# it back failed lint: the Developer went round between the two until it
# blocked. The contract check only knew definitions, so it never said which
# file still asked, or how to keep the name and satisfy lint.


def lost_names(
    worktree: Path,
    implementation,
    merged: Merged | None = None,
    on_skip: Callable[[str], None] | None = None,
) -> dict[str, tuple[str, str]]:
    """Names a changed module stops providing that another file still imports from it.

    Covers what `broken_contracts` doesn't: a name the module imported and
    passed along, or a constant it assigned. Keyed `path::name` like a broken
    contract, valued (what it was, what goes wrong). Judged with the change
    applied to a scratch copy; a change that won't apply reports nothing here,
    since `without_moves` already says so.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from crew_org.tools.workspace import apply_implementation  # noqa: PLC0415

    changed = {e.path for e in implementation.all_edits if e.path.endswith(".py")} | {
        t.path for t in implementation.text_edits if t.path.endswith(".py")
    }
    if merged is not None:
        # Including what an earlier attempt changed: its damage still counts.
        changed |= merged.changed()
    before = {path: _parse_text(_before(worktree, path, merged)) for path in changed}
    before = {path: tree for path, tree in before.items() if tree is not None}
    if not before:
        return {}
    changed = set(before)
    with tempfile.TemporaryDirectory() as scratch:
        after_root = Path(scratch) / "after"
        shutil.copytree(worktree, after_root, ignore=shutil.ignore_patterns(*_SKIP), symlinks=True)
        try:
            apply_implementation(after_root, implementation)
        except Exception as exc:  # noqa: BLE001
            # Said out loud: returning nothing reads as "nothing lost", and
            # sprint-metrics#129 went through with a broken import that way.
            if on_skip is not None:
                on_skip(f"couldn't check what the change stops providing: {exc}")
            return {}
        after = _modules(after_root)
    files = set(after)
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(changed):
        old = before.get(path)
        if old is None:
            continue
        had = _passed_along(old)
        has = _provided(after[path]) if path in after else set()
        for name, was in sorted(had.items()):
            if name in has:
                continue
            askers = sorted(
                file
                for file, tree in after.items()
                if file != path and path in _binds_from(tree, file, name, files)
            )
            if not askers:
                continue
            home = was.removeprefix("imported from ")
            found[f"{path}::{name}"] = (
                was,
                f"is no longer provided, and `{askers[0]}` still imports it from `{path}`. "
                f"Keep it in `{path}` as `from {home} import {name} as {name}` (the `as` "
                f"marks it as passed along, so lint doesn't call it unused), or change "
                f"`{askers[0]}` to import it from where it lives now",
            )
    return found


def _passed_along(tree: ast.Module) -> dict[str, str]:
    """Top-level names bound by an import or an assignment, and how."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            source = "." * node.level + (node.module or "")
            for alias in node.names:
                if alias.name != "*":
                    found[alias.asname or alias.name] = f"imported from {source}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name.split(".")[0]] = f"imported as {alias.name}"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = "a constant assigned here"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = "a constant assigned here"
    return found


def _provided(tree: ast.Module) -> set[str]:
    """Every top-level name the module binds: definitions, imports, assignments."""
    names = set(_passed_along(tree))
    names |= {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    return names


def draft_breaks(
    worktree: Path,
    implementation,
    merged: Merged | None,
    on_skip: Callable[[str], None] | None = None,
) -> dict[str, tuple[str, str]]:
    """Merged definitions an earlier attempt already broke, and this one leaves broken.

    `broken_contracts` reads this attempt's edits; a definition the draft
    deleted or reshaped before it isn't in them, so it was never reported.
    Keyed and valued like a broken contract, and judged like one: a removal
    here is still a move if `without_moves` finds its new home.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from crew_org.tools.workspace import apply_implementation  # noqa: PLC0415

    if merged is None:
        return {}
    paths = merged.changed()
    if not paths:
        return {}
    with tempfile.TemporaryDirectory() as scratch:
        after_root = Path(scratch) / "after"
        shutil.copytree(worktree, after_root, ignore=shutil.ignore_patterns(*_SKIP), symlinks=True)
        try:
            apply_implementation(after_root, implementation)
        except Exception as exc:  # noqa: BLE001
            # Said out loud: returning nothing reads as "nothing lost", and
            # sprint-metrics#129 went through with a broken import that way.
            if on_skip is not None:
                on_skip(f"couldn't check what the change stops providing: {exc}")
            return {}
        after = _modules(after_root)
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(paths):
        old_tree = _parse_text(merged.text(path))
        if old_tree is None:
            continue
        for node in old_tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if node.name.startswith("_"):
                continue
            was = signature_of(node)
            if was is None:
                continue
            new = _top_level(after[path], node.name) if path in after else None
            if new is None:
                found[f"{path}::{node.name}"] = (was, REMOVED)
            elif (broke := contract_break(node, new)) is not None:
                found[f"{path}::{node.name}"] = (was, broke)
    return found
