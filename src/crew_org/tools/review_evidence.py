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
        if module in seen:
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
