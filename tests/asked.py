"""A stand-in for the crew's call layer, for tests that read what a step is asked.

`capture` replaces `calls.ask` and keeps what a step would send: its role, its
form, and its task as the model reads it (`description`, the name CrewAI's Task
gave it). With no answer it stops the step there, so a test needs no model.
"""

from __future__ import annotations

from typing import Any

from crew_org import calls


class Stop(Exception):
    """The step was asked, and the test has what it came for."""


def capture(monkeypatch: Any, answer: Any = None) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def ask(role, parts, form, *, expected="", **kw):
        seen.update(
            role=role,
            form=form,
            description=calls.task_text(parts, expected),
            expected_output=expected,
            **kw,
        )
        if answer is None:
            raise Stop
        return answer

    monkeypatch.setattr(calls, "ask", ask)
    return seen
