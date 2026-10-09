"""Issues joined to the cards they're about, and fixes to when they went live (crew#449, PR D).

The only link from a run's trouble to the issue it led to was a `#N` in prose,
and numbers collide across repositories. A fix was dated by its merge, not by
the first tick that ran it (discussion 552).
"""

from __future__ import annotations

import json
import subprocess

from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import citations, deployed, retro, runs
from crew_org.flows.retro import qualified


def test_a_cards_number_is_qualified_only_where_the_board_says_whose_it_is():
    board = [
        ("sprint-metrics", 475),
        ("sprint-metrics", 15),
        ("crew-presentation", 15),
        ("infra", 9),
    ]
    assert qualified([475, 15, 9], board, ["sprint-metrics", "crew-presentation"]) == [
        "sprint-metrics#475",
        "#15",
        "#9",
    ]


def test_an_issue_cites_qualified_cards_and_links_never_a_bare_number():
    body = (
        "Seen on mqucifer/sprint-metrics#475 and sprint-metrics#501, see "
        "https://github.com/mqucifer/crew-presentation/issues/15. Not #12, not "
        "someone/sprint-metrics#3, and not mqucifer/unknown#4."
    )
    assert citations.cited(
        body, owner="mqucifer", repos={"sprint-metrics", "crew-presentation", "crew"}
    ) == ["crew-presentation#15", "sprint-metrics#475", "sprint-metrics#501"]


class Issues:
    owner = "mqucifer"

    def __init__(self, issues):
        self.issues = issues
        self.since = []
        self.unresolved = {("crew", 12)}
        self.ignored = {("crew", 449, "stranger")}

    def updated_since(self, repo, since):
        self.since.append(since)
        return self.issues


def test_what_an_issue_cites_is_recorded_once_until_it_changes(tmp_path):
    issue = {
        "number": 554,
        "body": "Recurring on mqucifer/sprint-metrics#475 and mqucifer/sprint-metrics#501.",
        "labels": [{"name": "retro-finding"}],
        "state": "open",
    }
    client, seen, sink = Issues([issue]), [], EventSink(None)
    sink.subscribe(seen.append)
    for _ in range(2):
        citations.record_citations(
            client, sink, crew_repo="crew", repos={"sprint-metrics", "crew"}, events_dir=tmp_path
        )
    [event] = seen
    assert event.kind is EventKind.ISSUE_CITES and event.card == 554
    assert event.detail["cites"] == ["sprint-metrics#475", "sprint-metrics#501"]
    assert event.detail["labels"] == ["retro-finding"]
    assert client.since[0] == citations.HISTORY_FROM and client.since[1] != client.since[0]


def test_what_the_issue_client_dropped_is_recorded_once():
    client, seen, sink, reported = Issues([]), [], EventSink(None), set()
    sink.subscribe(seen.append)
    citations.record_dropped(client, sink, reported=reported)
    citations.record_dropped(client, sink, reported=reported)
    assert [e.kind for e in seen] == [EventKind.REFERENCE_UNRESOLVED, EventKind.COMMENT_IGNORED]


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_a_fix_went_live_with_the_first_tick_whose_code_contains_it(tmp_path):
    repo = tmp_path / "crew"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "a",
    )
    before = _git(repo, "rev-parse", "HEAD")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "fix",
    )
    fix = _git(repo, "rev-parse", "HEAD")
    events = tmp_path / "events"
    events.mkdir()
    lines = [
        {"at": "2026-10-01T10:00:00Z", "kind": "note", "ctx": {"commit": before}},
        {"at": "2026-10-01T12:00:00Z", "kind": "note", "ctx": {"commit": before}},
        {"at": "2026-10-02T09:00:00Z", "kind": "note", "ctx": {"commit": fix}},
        {"at": "2026-09-01T09:00:00Z", "kind": "note"},
    ]
    (events / "tick.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    seen = deployed.commits_seen(events)
    assert seen == [("2026-10-01T10:00:00Z", before), ("2026-10-02T09:00:00Z", fix)]
    assert deployed.live_at(fix, seen, repo) == "2026-10-02T09:00:00Z"
    assert deployed.live_at(before, seen, repo) == "2026-10-01T10:00:00Z"


def test_the_retro_judges_a_fix_from_when_it_went_live(monkeypatch):
    class Pulls:
        def closed_pulls(self, repo):
            return [
                {
                    "number": 548,
                    "merged_at": "2026-10-08T16:55:41Z",
                    "merge_commit_sha": "abc",
                    "body": "<!-- crew:cause:5d83abc21af7 -->",
                }
            ]

    monkeypatch.setattr(retro, "live_at", lambda sha, commits: "2026-10-08T16:57:22Z")
    fixes = retro.fixes_by_cause(Pulls(), "crew", [], commits=[("x", "y")])
    assert fixes["5d83abc21af7"] == ("crew PR #548", "2026-10-08T16:57:22Z")
    assert retro.fixes_by_cause(Pulls(), "crew", [])["5d83abc21af7"][1] == "2026-10-08T16:55:41Z"


def test_a_cards_runs_join_from_the_events_alone(tmp_path):
    def ev(at, kind, run, **detail):
        return {
            "at": at,
            "kind": kind,
            "role": "Developer",
            "card": 475,
            "summary": detail.pop("summary", ""),
            "detail": detail,
            "ctx": {"run": run, "card": 475, "repo": "sprint-metrics", "role": "Developer"},
        }

    sink = EventSink(tmp_path / "tick.jsonl")
    for raw in [
        ev("2026-10-08T15:44:58Z", "agent.started", "r1"),
        ev(
            "2026-10-08T16:02:37Z",
            "llm.finished",
            "r1",
            prompt_tokens=40000,
            completion_tokens=32768,
            prompt_hash="p1",
        ),
        ev("2026-10-08T16:02:38Z", "llm.failed", "r1", error="the length limit was reached"),
        ev(
            "2026-10-08T18:33:09Z",
            "escalation.decided",
            "r1",
            summary="EDIT — block",
            rule="edit.not_applied",
        ),
        ev("2026-10-08T18:33:11Z", "agent.finished", "r1", summary="blocked"),
        ev("2026-10-08T18:00:00Z", "agent.started", "r2"),
    ]:
        sink.emit(CrewEvent.model_validate(raw))
    sink.emit(
        CrewEvent(
            kind=EventKind.ISSUE_CITES,
            card=554,
            detail={"repo": "crew", "cites": ["sprint-metrics#475"], "state": "open"},
        )
    )
    found, citing = runs.card_runs(tmp_path, "sprint-metrics", 475)
    first = next(r for r in found if r.run == "r1")
    assert first.calls == 1 and first.tokens == 72768 and first.prompts == {"p1"}
    assert first.kinds == {"cut_off": 1, "edit_not_applied": 1}
    assert first.rules == ["edit.not_applied"] and first.outcome == "blocked"
    assert [c["issue"] for c in citing] == [554]
    text = "\n".join(runs.render("sprint-metrics", 475, found, citing))
    assert "decided by: edit.not_applied" in text and "crew#554 (open)" in text
