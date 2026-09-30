"""What the Code Reviewer is shown beside a diff (#160).

A diff alone can't say whether the code it changes works, or what it leans on.
On sprint-metrics PR #88 that let the Reviewer claim tests "would fail" without
an implementation that was already on `main`, while CI had passed. Per §16 the
answer is what it is shown, not another rule: the checks GitHub ran on this
head, what delivery reported from its own sandbox run, and the code the diff
imports, as it stands on the base branch.
"""

from __future__ import annotations

import re
import sys
from typing import Any

# The code a diff leans on can be large; this is a guard, not a budget (§16).
# Past it, whole modules are left out and named, never cut mid-file.
MAX_IMPORTED_CHARS = 120_000

_IMPORT = re.compile(r"^\+?\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)
_CHANGED = re.compile(r"^\+\+\+ b/(\S+\.py)$", re.MULTILINE)


def checks_section(runs: list[dict[str, Any]], pull_body: str) -> str:
    """The checks on this head, and what delivery said it ran before opening it."""
    lines = ["## The checks on this pull request's head", ""]
    if runs:
        for run in runs:
            outcome = run.get("conclusion") or run.get("status") or "unknown"
            lines.append(f"- `{run.get('name')}`: {outcome}")
    else:
        lines.append("- none reported yet")
    verification = _section(pull_body or "", "Verification")
    if verification:
        lines += [
            "",
            "What delivery reported from its own run in the sandbox, before opening it:",
            "",
            verification,
        ]
    return "\n".join(lines)


def _section(body: str, heading: str) -> str:
    match = re.search(rf"^## {heading}\s*$(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def imported_modules(text: str) -> list[str]:
    """The modules `text` imports, in the order they appear: a file, or a diff's lines."""
    found = [m.group(1) or m.group(2) for m in _IMPORT.finditer(text)]
    return list(dict.fromkeys(found))


# An option added to an argparse parser: `add_argument("--thresholds", …)` (#191).
_OPTION = re.compile(r"""add_argument\(\s*(?:["']-\w["']\s*,\s*)?["'](--[A-Za-z0-9][\w-]*)["']""")
_FILE = re.compile(r"^\+\+\+ b/(\S+)$")


def _is_test(path: str) -> bool:
    from crew_org.profiles import profile_for  # noqa: PLC0415

    return profile_for(path).is_test_path(path)


def added_options(diff: str) -> list[str]:
    """Command-line options a diff adds, outside its tests. Hidden ones are left out."""
    found: list[str] = []
    path = ""
    for line in diff.splitlines():
        header = _FILE.match(line)
        if header:
            path = header.group(1)
            continue
        if not line.startswith("+") or line.startswith("+++") or _is_test(path):
            continue
        if "SUPPRESS" in line:
            continue
        found += [o for o in _OPTION.findall(line) if o not in found]
    return found


def undocumented_options(diff: str, docs: list[str], read_head) -> list[str]:
    """Options the diff adds that no user doc mentions at the pull request's head (#191).

    `read_head(path)` returns a file's text at the head, or None. A doc that
    doesn't exist documents nothing.
    """
    options = added_options(diff)
    if not options:
        return []
    text = "\n".join(t for path in docs if (t := read_head(path)))
    return [o for o in options if o not in text]


_DEF = re.compile(r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)")
_ASSIGN = re.compile(r"^(\w+)\s*(?::[^=]+)?=(?!=)")
_FROM = re.compile(r"^from\s+\S+\s+import\s+(.*)$")
_IMPORT_LINE = re.compile(r"^import\s+(.*)$")


def _imported_names(text: str) -> list[str]:
    names = []
    for part in text.replace("(", " ").replace(")", " ").split(","):
        words = part.split()
        if not words or words[0].startswith("#"):
            continue
        # `a as b` binds b; `import a.b` binds a.
        names.append(words[-1] if len(words) == 3 and words[1] == "as" else words[0].split(".")[0])
    return names


def changed_names(diff: str) -> dict[str, set[str]]:
    """The top-level names each changed Python file's diff adds or removes (#215).

    Read from the diff's own lines: definitions, assignments and imports at
    column 0, and the names inside a parenthesised import the diff touches.
    """
    found: dict[str, set[str]] = {}
    path = ""
    in_import = False
    for line in diff.splitlines():
        header = _FILE.match(line)
        if header:
            path, in_import = header.group(1), False
            continue
        if not path.endswith(".py") or line.startswith(("---", "+++", "@@", "diff ")):
            continue
        sign, text = line[:1], line[1:]
        if in_import:
            if sign in "+-" and text.strip():
                found.setdefault(path, set()).update(_imported_names(text))
            in_import = ")" not in text
            continue
        if text.startswith("from ") and "(" in text and ")" not in text:
            in_import = True
        if sign not in "+-":
            continue
        names: list[str] = []
        if match := _DEF.match(text):
            names = [match.group(1) or match.group(2)]
        elif (match := _FROM.match(text)) or (match := _IMPORT_LINE.match(text)):
            names = _imported_names(match.group(1))
        elif match := _ASSIGN.match(text):
            names = [match.group(1)]
        if names:
            found.setdefault(path, set()).update(n for n in names if n.isidentifier())
    return found


def importers_section(root, diff: str) -> str:
    """Who imports the names this diff changes, from the clone at the base branch (#215).

    On sprint-metrics PR #139 the reviewer called nine imports "unused". They
    were pass-throughs `__init__.py` imports from there, and a diff can't show
    that.
    """
    from crew_org.tools.regression import importers, passed_along  # noqa: PLC0415

    found = importers(root, changed_names(diff))
    if not found:
        return ""
    lines = [
        "## Who imports the names this diff changes, on the base branch",
        "",
        "A name another file imports from a module is part of that module's interface: "
        "removing or renaming it breaks them.",
        "",
    ]
    for path, names in sorted(found.items()):
        for name, users in names.items():
            by = ", ".join(f"`{u}`" for u in users)
            if passed_along(root, path, name):
                verb = "imports" if len(users) == 1 else "import"
                lines.append(
                    f"- `{path}` `{name}`: passed along, not unused. Nothing in this file "
                    f"uses it, and {by} {verb} it from here"
                )
            else:
                lines.append(f"- `{path}` `{name}`: imported from here by {by}")
    return "\n".join(lines)


def changed_python_files(diff: str) -> list[str]:
    return list(dict.fromkeys(_CHANGED.findall(diff)))


def candidate_paths(module: str) -> list[str]:
    rel = module.replace(".", "/")
    return [f"src/{rel}.py", f"{rel}.py", f"src/{rel}/__init__.py", f"{rel}/__init__.py"]


def imported_code(read, diff: str, read_head=None) -> str:
    """The repository's own modules the changed files import, whole, as on the base branch.

    The imports are read from each changed Python file as it is at the pull
    request's head (`read_head(path)`), not only from the diff: tests appended
    to an existing file import at its top, outside the diff, which is exactly
    how PR #88's did. `read(path)` returns a file's text on the base branch, or
    None. A module with no file in the repository (the standard library, a
    dependency) is not the project's code, and is left out.
    """
    sources = [diff]
    if read_head is not None:
        sources += [text for path in changed_python_files(diff) if (text := read_head(path))]
    # Followed through the project's own modules: `from sprint_metrics import
    # main` reaches a package `__init__` that only re-exports, and the code the
    # tests exercise is one import further on.
    queue = list(dict.fromkeys(m for text in sources for m in imported_modules(text)))
    seen: set[str] = set()
    shown: list[str] = []
    omitted: list[str] = []
    budget = MAX_IMPORTED_CHARS
    while queue:
        module = queue.pop(0)
        # The standard library is never the project's code, and looking a
        # module up tries four paths: through GitHub, four 404s a module on
        # every review (#283's traces).
        if module in seen or module.split(".")[0] in sys.stdlib_module_names:
            continue
        seen.add(module)
        for path in candidate_paths(module):
            text = read(path)
            if text is None:
                continue
            if len(text) > budget:
                omitted.append(path)
            else:
                budget -= len(text)
                shown += [f"`{path}`", "", "```python", text.rstrip(), "```", ""]
                queue += [m for m in imported_modules(text) if m not in seen]
            break
    if not (shown or omitted):
        return ""
    lines = ["## The code this diff imports, as it is on the base branch", "", *shown]
    if omitted:
        lines.append(
            "Not shown, because they did not fit: "
            + ", ".join(f"`{p}`" for p in omitted)
            + ". Treat anything they define as code you cannot see."
        )
    return "\n".join(lines)


def unguarded_section(diff: str) -> str:
    """The changed files no regression guard read, said to the Code Reviewer (#404).

    The guard reads Python's syntax tree: a part in another language is edited
    as whole files or find-and-replace, with nothing refusing a definition that
    disappears. Said, so review knows to look.
    """
    from crew_org.profiles import profile_for  # noqa: PLC0415
    from crew_org.tools.deploy_evidence import changed_files  # noqa: PLC0415

    bare = [path for path in changed_files(diff) if not profile_for(path).guarded]
    if not bare:
        return ""
    return (
        "**No regression guard ran on:** "
        + ", ".join(f"`{p}`" for p in bare)
        + ". The crew's guard reads Python only. Check that this change removes nothing "
        "that was there before and wasn't meant to go."
    )
