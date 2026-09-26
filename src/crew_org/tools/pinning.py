"""The tests that pin the behaviour an epic touches, in full, for the Business Analyst (#189).

Roles that decide rather than edit see test files by name only (#230). But a
story that changes behaviour merged tests pin has to say whether that change
is opt-in or an expected contract change, and it can only say so if the role
writing it has read those tests. sprint-metrics#97 changed the default
table's rows, which 25 merged tests pinned, and said neither.

Chosen mechanically: a test is shown when its body mentions something the
epic names (a `backticked` identifier, a `--flag`, a quoted output string) or
when a story-problem report named it. Imperfect by design; #231 is where a
role asks for what it needs instead.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# A generous guard, not a budget (§16): it should not fire in normal work, and
# when it does, what was left out is named.
PINNING_CHAR_CEILING = 40_000
MIN_TERM = 4
_BACKTICKED = re.compile(r"`([^`\n]{4,80})`")  # MIN_TERM characters or more
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})")
_TEST_ID = re.compile(r"([\w/.-]+\.py)::(\w+)")
_IGNORED = {".git", ".venv", "__pycache__", "node_modules"}


def terms(text: str) -> set[str]:
    """What an epic names that a test might assert on: identifiers, flags, literals."""
    found = {t.strip() for t in _BACKTICKED.findall(text or "")}
    found |= set(_FLAG.findall(text or ""))
    return {t for t in found if len(t) >= MIN_TERM}


def named_tests(text: str) -> set[tuple[str, str]]:
    """`path::test` ids a story-problem report listed."""
    return set(_TEST_ID.findall(text or ""))


def pinning_tests(clone: Path, epic_text: str, evidence: str = "") -> str:
    """The block the Business Analyst is shown: each pinning test in full, by file.

    Empty when nothing the epic names is pinned by a test.
    """
    wanted_terms = terms(epic_text) | terms(evidence)
    wanted_ids = named_tests(evidence)
    if not (wanted_terms or wanted_ids):
        return ""
    chosen: list[tuple[str, str, str]] = []
    for path in sorted(clone.glob("tests/**/*.py")):
        if _IGNORED & set(path.relative_to(clone).parts):
            continue
        rel = path.relative_to(clone).as_posix()
        source = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test"):
                continue
            body = ast.get_source_segment(source, node) or ""
            if (rel, node.name) in wanted_ids or any(t in body for t in wanted_terms):
                chosen.append((rel, node.name, body))
    if not chosen:
        return ""

    lines = [
        "## Tests that pin behaviour this epic touches",
        "",
        "Merged tests whose assertions mention what this epic names. A story that "
        "changes what one of them asserts must say whether the change is opt-in, "
        "leaving them passing, or an expected contract change that updates them.",
        "",
    ]
    budget, left_out, current = PINNING_CHAR_CEILING, [], None
    for rel, name, body in chosen:
        if len(body) > budget:
            left_out.append(f"{rel}::{name}")
            continue
        budget -= len(body)
        if rel != current:
            lines += [f"### `{rel}`", ""]
            current = rel
        lines += ["```python", body, "```", ""]
    if left_out:
        lines.append(
            "Also pinning it, and not shown because they did not fit: "
            + ", ".join(f"`{t}`" for t in left_out)
            + "."
        )
    return "\n".join(lines)
