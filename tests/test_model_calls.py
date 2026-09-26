"""A sprint's model calls, exported raw (#179)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from crew_org.events import attributed, working, working_on
from crew_org.flows.model_calls import read_model_calls


def test_contexts_nest_and_unwind():
    with working_on(sprint="Sprint 7", purpose="refine"):
        with working_on(card=59, repo="sprint-metrics"):
            assert working() == {
                "sprint": "Sprint 7",
                "for": "refine",
                "card": 59,
                "repo": "sprint-metrics",
            }
        assert working() == {"sprint": "Sprint 7", "for": "refine"}
    assert working() == {}


def test_an_attributed_call_is_attributed_only_while_it_runs():
    seen = attributed(working, card=5, repo="r")()
    assert seen == {"card": 5, "repo": "r"} and working() == {}


def call(card, at, repo="sprint-metrics", kind="llm.finished", **detail):
    return {
        "at": at,
        "kind": kind,
        "role": "Developer",
        "card": card,
        "detail": {"repo": repo, "model": "crew-code-think", "prompt_tokens": 10, **detail},
    }


def test_the_export_carries_each_call_for_the_sprints_cards_raw(tmp_path):
    """#179, criteria 3 and 4: the calls, as recorded, and nothing computed."""
    events = [
        call(147, "2026-09-26T17:40:00", attempt=1, duration_s=12.5, finish_reason="stop"),
        call(147, "2026-09-26T17:30:00", attempt=1, kind="llm.failed", error="timeout"),
        call(147, "2026-09-26T17:41:00", repo="crew"),  # another repo's #147
        call(999, "2026-09-26T17:42:00"),  # not this sprint's
        {"at": "2026-09-26T17:43:00", "kind": "card.moved", "card": 147, "detail": {}},
    ]
    (tmp_path / "tick.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    calls = read_model_calls(tmp_path, [SimpleNamespace(number=147)], "sprint-metrics")

    assert [c["at"] for c in calls] == ["2026-09-26T17:30:00", "2026-09-26T17:40:00"]
    failed, done = calls
    assert failed["failed"] and failed["error"] == "timeout"
    assert done == {
        "at": "2026-09-26T17:40:00",
        "card": 147,
        "role": "Developer",
        "failed": False,
        "attempt": 1,
        "model": "crew-code-think",
        "finish_reason": "stop",
        "prompt_tokens": 10,
        "duration_s": 12.5,
    }
