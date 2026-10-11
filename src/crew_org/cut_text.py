"""A field the model's own double quote cut short, found before it reaches the record (crew#618).

The server holds each answer to its JSON form as it is written, so a double quote
the model means inside a value ends the string, and the rest of the value is gone
without any error. On mqucifer/sprint-metrics#555 (2026-10-11) the Architect wrote
`because accepts exactly two values: "sponsor"…`, and the record got "because
accepts exactly two values:". In thirty days of LiteLLM's log about nine answers were
cut this way, four of them the Architect's design decisions, one on epic 468's field
names. A cut field ends where the quoted value would have begun: on a colon, or on
a space.

The check names each such field so the answer is asked for again with the reason.
Fields that hold a file's text exactly as written (code, a YAML key that ends in a
colon) are skipped: there a colon at the end is the text, not a cut.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

# (form, field) pairs whose value is a file's text, copied or written exactly.
VERBATIM = frozenset(
    {
        ("FileWrite", "content"),
        ("FileEdit", "target"),
        ("FileEdit", "source"),
        ("TextEdit", "find"),
        ("TextEdit", "replace"),
        ("CriterionTest", "source"),
    }
)

REASON = (
    "These fields stop mid-sentence: {fields}. A double quote inside a value ends the "
    "field and the rest is lost. Write values in single quotes ('sponsor'), never "
    "double quotes, and finish each field."
)


def _strings(value: Any, where: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, BaseModel):
        form = type(value).__name__
        for name in type(value).model_fields:
            if (form, name) not in VERBATIM:
                yield from _strings(getattr(value, name), f"{where}.{name}" if where else name)
    elif isinstance(value, list | tuple):
        for i, item in enumerate(value):
            yield from _strings(item, f"{where}[{i}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{where}.{key}")
    elif isinstance(value, str):
        yield where, value


def cut_fields(answer: BaseModel) -> list[str]:
    """Each prose field of the answer that ends where a quoted value would begin."""
    return [
        where
        for where, text in _strings(answer, "")
        if text and (text.endswith(" ") or text.rstrip().endswith(":"))
    ]


def check_not_cut_at_a_quote(answer: BaseModel) -> None:
    """Refuse an answer with a field cut short, saying which and why."""
    found = cut_fields(answer)
    if found:
        raise ValueError(REASON.format(fields=", ".join(found)))
