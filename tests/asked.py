"""A stand-in for the crew's call layer, for tests that read what a step is asked.

`capture` replaces `calls.ask` and a refinement step's session (`steps.run`, crew#807)
and keeps what a step would send: its role, its form, and its task as the model reads
it (`description`, the name CrewAI's Task gave it; for a session, its own parts of the
start). With no answer it stops the step there, so a test needs no model.
"""

from __future__ import annotations

from typing import Any

from crew_org import calls
from crew_org.session import steps


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

    def run(step, parts, reading, *, about, check=None, notes=None, **_):
        seen.update(
            role=step.role,
            form=step.form,
            step=step,
            description="\n\n".join(p for p in parts.values() if p),
            parts=parts,
            check=check,
            notes=notes or {},
            reading=reading,
        )
        if answer is None:
            raise Stop
        if check is not None:
            check(answer)
        return answer

    monkeypatch.setattr(calls, "ask", ask)
    monkeypatch.setattr(steps, "run", run)
    return seen
