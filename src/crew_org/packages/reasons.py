"""Which refusal reasons the crew may publish about itself (ADR 0020, crew#521).

ADR 0020 allows "the fixed messages of the crew's own checks, with names filled
in", and nothing a model wrote. That a check wrote the message isn't enough on
its own, because a check can fill in what the model sent it. In the event log,
"these named tests don't exist in the repository: " is followed by a model's
sentence, and "no definition named 'from x import y'" by a line of its code.

So a reason is published only when both hold, mechanically:

- the whole message matches a string template in the crew's own source code;
- every value filled into that template is a name: an identifier, a path, or a
  list of them.

Anything else is left out, and the replay shows the failure class alone. A
message assembled at runtime from pieces matches no template, so it's left
out too: the safe direction.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]

# A name the crew's checks fill in: a test or definition, a file path, a column.
_NAME = r"[\w.][\w./\-]*(?:::[\w.]+)?"
NAME = re.compile(rf"^{_NAME}(?:, {_NAME})*$")
# Longer than any real name, shorter than a sentence of code or prose.
LONGEST = 160
# A template whose first clause has less fixed text than this is only
# placeholders, not a message: f"{a}: {b}" would let any two names through.
FIXED_TEXT = 6

_PLACEHOLDER = "(.+?)"
_QUOTES = ("'", '"', "`")
_CLAUSE_END = ". "


def first_clause(text: str) -> str:
    """The rule that was broken, without the sentences on how to put it right."""
    return text.strip().split(_CLAUSE_END, 1)[0].rstrip(".")


def _clause_pattern(node: ast.expr) -> str | None:
    """The first clause of a string or f-string in the source, as a pattern."""
    values = node.values if isinstance(node, ast.JoinedStr) else [node]
    pieces: list[str | None] = []  # literal text, or None for a filled-in value
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
            if _CLAUSE_END in text:
                pieces.append(text.split(_CLAUSE_END, 1)[0])
                break
            pieces.append(text)
        elif isinstance(value, ast.FormattedValue):
            pieces.append(None)
        else:
            return None
    if pieces and pieces[-1] is not None:
        pieces[-1] = pieces[-1].rstrip(".")
    if sum(len(p.strip()) for p in pieces if p is not None) < FIXED_TEXT:
        return None
    return "".join(_PLACEHOLDER if p is None else re.escape(p) for p in pieces)


@cache
def templates() -> tuple[re.Pattern[str], ...]:
    """The first clause of every string and f-string in the crew's source.

    The first clause, because that's what's published, and because it outlasts
    rewording: crew#511 rewrote what follows "has no source", not that.
    """
    found: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) or (
                isinstance(node, ast.Constant) and isinstance(node.value, str)
            ):
                pattern = _clause_pattern(node)
                if pattern:
                    found.add(pattern)
    return tuple(re.compile(p, re.DOTALL) for p in sorted(found))


def _is_name(value: str) -> bool:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        value = value[1:-1]
    return 0 < len(value) <= LONGEST and NAME.match(value) is not None


def published_reason(message: str) -> str | None:
    """The reason to publish for a refusal, or None if it may not be published.

    Its first clause, when that is one of the crew's own with only names
    filled in.
    """
    clause = first_clause(message)
    if not clause:
        return None
    for template in templates():
        match = template.fullmatch(clause)
        if match and all(_is_name(v) for v in match.groups()):
            return clause
    return None
