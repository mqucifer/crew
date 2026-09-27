"""A test declares the pytest fixtures it uses (#196).

`F821 Undefined name tmp_path` failed four cards' first attempts (sprint-metrics
#11, #30, #75, and #93's variant): the Developer wrote a test that uses one of
pytest's own fixtures without taking it as a parameter. The fix is always the
same, so the crew makes it: a test function that uses a built-in fixture it
never binds gets it added to its signature, before anything runs. The text is
edited in place, not regenerated, so comments and formatting survive.
"""

from __future__ import annotations

import ast
import textwrap

# pytest's own fixtures: a name here, used and never bound, can only mean one.
BUILTIN = frozenset(
    {
        "tmp_path",
        "tmp_path_factory",
        "tmpdir",
        "capsys",
        "capsysbinary",
        "capfd",
        "capfdbinary",
        "caplog",
        "monkeypatch",
        "recwarn",
        "request",
        "pytestconfig",
    }
)


def _module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _missing(func: ast.FunctionDef | ast.AsyncFunctionDef, bound: set[str]) -> list[str]:
    args = func.args
    params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    params |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    local = {
        n.id for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    used = [
        n.id
        for n in ast.walk(func)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in BUILTIN
    ]
    return [
        name
        for name in dict.fromkeys(used)
        if name not in params and name not in local and name not in bound
    ]


def _close_of_signature(lines: list[str], start: int) -> tuple[int, int, bool, bool] | None:
    """Where the `)` closing the def on line `start` is: (line, column, has params,
    ends with a comma)."""
    depth, seen_open, inside = 0, False, []
    for row in range(start, len(lines)):
        line = lines[row]
        begin = line.index("def ") if row == start and "def " in line else 0
        for col in range(begin, len(line)):
            ch = line[col]
            if ch == "(":
                depth += 1
                seen_open = True
                if depth == 1:
                    continue
            elif ch == ")":
                depth -= 1
                if seen_open and depth == 0:
                    text = "".join(inside).strip()
                    return row, col, bool(text.strip(",").strip()), text.endswith(",")
            if seen_open and depth >= 1:
                inside.append(ch)
        if seen_open:
            inside.append("\n")
    return None


def declare_fixtures(source: str) -> str:
    """`source`, with each test function taking the built-in fixtures it uses."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return source
    bound = _module_names(tree)
    funcs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    ]
    lines = source.split("\n")
    # Bottom up, so an insertion never moves a def still to be edited.
    for func in sorted(funcs, key=lambda f: f.lineno, reverse=True):
        missing = _missing(func, bound)
        if not missing:
            continue
        found = _close_of_signature(lines, func.lineno - 1)
        if found is None:
            continue
        row, col, has, comma = found
        line = lines[row]
        names = ", ".join(missing)
        if comma and not line[:col].strip():
            # One parameter a line, closed on a line of its own: add a line.
            above = next((ln for ln in reversed(lines[:row]) if ln.strip()), line)
            indent = above[: len(above) - len(above.lstrip())]
            lines.insert(row, f"{indent}{names},")
            continue
        if has and not comma and not line[:col].strip():
            # Closed on a line of its own after the last parameter: extend that line.
            last = max(i for i in range(row) if lines[i].strip())
            lines[last] = f"{lines[last].rstrip()}, {names}"
            continue
        added = (" " if comma else ", " if has else "") + names
        lines[row] = line[:col] + added + line[col:]
    return "\n".join(lines)
