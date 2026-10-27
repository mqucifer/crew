"""An approved story that conflicts with main gets its conflicts resolved, not the story
rebuilt (crew#933).

On 2026-10-27 four approved sprint-metrics stories were rebuilt from scratch for small
conflicts: each moved the `api_version` pin in `.github/workflows/tests.yml`, and
mqucifer/sprint-metrics#809's rebuild took 135 calls. Keep-both (#436) resolves only
pure additions. Real git over a local origin, as delivery's own tests; the session and
the container are faked.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from crew_org.events import EventKind, EventSink
from crew_org.flows import gates, session_delivery
from crew_org.flows import review as review_flow
from crew_org.flows.merge import (
    REBUILD_MARKER,
    RESOLVED_MARKER,
    merge_approved,
    only_files,
    resolved_at,
)
from crew_org.flows.strain import read_rebuilds
from crew_org.git_ops import unresolved
from crew_org.session.conversation import Ending
from crew_org.session.tools import Tools
from tests.test_merge import FakeBoard
from tests.test_merge import FakeIssues as MergeIssues
from tests.test_merge import card as merge_card
from tests.test_review import APPROVAL, BOT, crew_pull, waiting_card
from tests.test_review import FakeBoard as ReviewBoard
from tests.test_review import FakeIssues as ReviewIssues
from tests.test_session import CHECKS, Fake
from tests.test_session_delivery import (  # noqa: F401
    BRANCH,
    CREW,
    DONE,
    Held,
    Issues,
    Routes,
    Sessions,
    deliver,
    on_origin,
    origin_sha,
    pushes,
    ws,
)

PIN = ".github/workflows/tests.yml"
TRUSTED = SimpleNamespace(crew=frozenset({CREW}), sponsor="mquarters")


class Returned(Issues):
    """A pull request the merge returned at `head` for conflicting with main, with the
    comments said on it since, each by the crew's App."""

    def __init__(self, head: str) -> None:
        super().__init__(live={"number": 700, "head": {"sha": head, "ref": BRANCH}})
        self.trust = TRUSTED
        self.said: list[tuple[int, str]] = [(700, REBUILD_MARKER.format(head=head))]
        self.comments = self._comments  # the fake keeps what's said on `said` instead

    def comment(self, repo, number, body):
        self.said.append((number, body))
        return {}

    def _comments(self, repo, number):
        return [{"body": b, "user": {"login": CREW}} for n, b in self.said if n == number]


def approved_and_conflicting(ws) -> str:  # noqa: F811
    """Main and an approved branch that each moved the pin from 1: the branch to 2, main
    to 3 after it. Its approved head."""
    on_origin(ws, "main", {PIN: "api_version: 1\n", "src/rate.py": "RATE = 0\n"})
    on_origin(ws, BRANCH, {PIN: "api_version: 2\n", "src/rate.py": "RATE = 1\n"})
    on_origin(ws, "main", {PIN: "api_version: 3\n", "docs.md": "landed\n"})
    return origin_sha(ws, BRANCH)


def recorded(sink: EventSink) -> list:
    seen: list = []
    sink.subscribe(seen.append)
    return seen


def _git(ws, *args: str) -> str:  # noqa: F811
    return subprocess.run(
        ["git", "--git-dir", str(ws.origin), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


# --- the resolution ----------------------------------------------------------------------


def test_a_shared_line_is_resolved_by_a_session_not_rebuilt(ws, deliver, monkeypatch):  # noqa: F811
    approved = approved_and_conflicting(ws)
    forced = pushes(ws, monkeypatch)
    gated: list = []
    real = gates.at_done
    monkeypatch.setattr(
        session_delivery.gates,
        "at_done",
        lambda worktree, said, **kw: gated.append(worktree) or real(worktree, said, **kw),
    )
    sessions = Sessions({"writes": {PIN: "api_version: 3\n"}, "ending": Ending("done", DONE)})
    issues = Returned(approved)
    sink = EventSink(None)
    seen = recorded(sink)

    outcome = deliver(ws, sessions, Routes(), issues, rework=True, sink=sink)

    started = sessions.started[0]
    assert started["resolving"] == (PIN,), "edits only the conflicted file"
    told = started["instead_of_hints"]
    assert "api_version: 2" in told and "api_version: 3" in told and "api_version: 1" in told
    assert "sibling" not in told and "on main" in told, "main's side, by what landed it"
    assert "RATE = 1" in told, "the story's approved diff, to read"
    assert len(sessions.started) == 1 and gated, "one session, and the gates ran again"

    head = origin_sha(ws, BRANCH)
    assert _git(ws, "log", "-1", "--format=%P", BRANCH).split() == [
        approved,
        origin_sha(ws, "main"),
    ], "a merge onto the approved head, so review's history stays"
    assert _git(ws, "show", f"{BRANCH}:{PIN}") == "api_version: 3"
    assert _git(ws, "show", f"{BRANCH}:src/rate.py") == "RATE = 1", "the story as approved"
    assert forced == [False] and outcome.pr == 700

    marker = RESOLVED_MARKER.format(approved=approved, head=head, files=PIN)
    assert any(marker in body for _, body in issues.said)
    [resolved] = [e for e in seen if e.kind == EventKind.MERGE_RESOLVED]
    assert resolved.detail["files"] == [PIN] and "calls" in resolved.detail
    assert not any(e.detail.get("rebuilt") for e in seen), "no rebuild for the strain count"


def test_review_sees_only_what_the_resolution_changed(monkeypatch):
    head = "f00d"
    whole = (
        f"diff --git a/{PIN} b/{PIN}\n--- a/{PIN}\n+++ b/{PIN}\n-api_version: 2\n+api_version: 3\n"
        "diff --git a/docs.md b/docs.md\n+++ b/docs.md\n+landed\n"
    )

    class Resolving(ReviewIssues):
        trust = TRUSTED

        def comments(self, repo, number):
            marker = RESOLVED_MARKER.format(approved="ab12", head=head, files=PIN)
            return [{"body": marker, "user": {"login": CREW}}]

        def compare_diff(self, repo, base, at):
            assert (base, at) == ("ab12", head)
            return whole

        def pull_diff(self, repo, number):
            raise AssertionError("the whole pull request isn't read")

    asked: dict = {}
    pull = crew_pull()
    pull["head"]["sha"] = head

    def review_diff(title, diff, **kw):
        asked.update(diff=diff, **kw)
        return APPROVAL

    def review_deploy(title, diff, **kw):
        # The pin is in a workflow, so the deploy review reads it too: as narrowed.
        asked["deploy_diff"] = diff
        return APPROVAL

    monkeypatch.setattr(review_flow, "review_diff", review_diff)
    monkeypatch.setattr(review_flow, "review_deploy", review_deploy)
    review_flow.review_open_pulls(
        Resolving([pull]),
        EventSink(None),
        repo="sprint-metrics",
        bot_login=BOT,
        board=ReviewBoard(),
        cards=[waiting_card()],
    )

    assert asked["diff"] == whole.split("diff --git a/docs.md")[0]
    assert asked["deploy_diff"] == asked["diff"]
    assert "This head resolves conflicts with main" in asked["prior_verdicts"]


def test_a_marker_not_by_the_crew_or_for_another_head_narrows_nothing():
    class Said(ReviewIssues):
        trust = TRUSTED

        def __init__(self, by):
            super().__init__([])
            self.by = by

        def comments(self, repo, number):
            marker = RESOLVED_MARKER.format(approved="ab12", head="f00d", files=PIN)
            return [{"body": marker, "user": {"login": self.by}}]

    assert resolved_at(Said("someone"), "sprint-metrics", 14, "f00d") is None
    assert resolved_at(Said(CREW), "sprint-metrics", 14, "beef") is None
    assert resolved_at(Said(CREW), "sprint-metrics", 14, "f00d").files == (PIN,)


def test_only_the_named_files_are_kept_from_a_diff():
    diff = "diff --git a/a b/a\n+a\ndiff --git a/b b/b\n+b\n"
    assert only_files(diff, {"b"}) == "diff --git a/b b/b\n+b\n"


# --- the write limit ---------------------------------------------------------------------


def test_an_edit_outside_the_conflicted_files_is_refused(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("<<<<<<< ours\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    t = Tools(tmp_path, Fake(), CHECKS, writable=frozenset({"CHANGELOG.md"}))

    refused = t.call("replace_text", {"path": "app.py", "old": "x = 1", "new": "x = 2"})
    assert (refused.kind, refused.rule) == ("refused", "session.path")
    assert "`CHANGELOG.md`" in refused.text
    assert t.call("write_file", {"path": "new.py", "content": "y\n"}).rule == "session.path"
    assert (tmp_path / "app.py").read_text() == "x = 1\n" and not (tmp_path / "new.py").exists()
    allowed = t.call("write_file", {"path": "CHANGELOG.md", "content": "both\n", "overwrite": True})
    assert allowed.kind == "ok"


def test_done_is_refused_while_a_conflict_s_markers_are_left(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> main\n")
    (tmp_path / "docs.md").write_text("Title\n=======\n")
    assert unresolved(tmp_path, ["CHANGELOG.md", "docs.md"]) == ["CHANGELOG.md"]
    [found] = gates.conflicts_left(tmp_path, ["CHANGELOG.md"])
    assert found.rule == "check.gates.conflicts_left" and "`CHANGELOG.md`" in found.says


def test_a_resolution_that_changes_a_file_beyond_the_conflicts_is_rebuilt(
    ws,  # noqa: F811
    deliver,  # noqa: F811
):
    approved = approved_and_conflicting(ws)
    sessions = Sessions(
        {
            "writes": {PIN: "api_version: 3\n", "docs.md": "changed\n"},
            "ending": Ending("done", DONE),
        },
        {"ending": Ending("done", DONE)},
    )
    deliver(ws, sessions, Routes(session_end_review()), Returned(approved), rework=True)
    assert "`docs.md`" in sessions.started[1]["story"], "the rebuild is told why"


# --- falling back to the rebuild ---------------------------------------------------------


def session_end_review():
    from crew_org.flows import session_end

    return session_end.Route(session_end.REVIEW, "r")


def test_a_resolution_whose_gates_fail_falls_back_to_a_rebuild(ws, deliver, monkeypatch):  # noqa: F811
    approved = approved_and_conflicting(ws)
    forced = pushes(ws, monkeypatch)
    refused = gates.Finding("check.gates.the_checks_failed", "tests/test_pin.py failed")
    monkeypatch.setattr(
        session_delivery.gates, "at_done", lambda *a, **k: gates.Verdict(findings=[refused])
    )
    sessions = Sessions(
        {"writes": {PIN: "api_version: 3\n"}, "ending": Ending("done", DONE)},
        {"writes": {"src/rate.py": "RATE = 1\n"}, "ending": Ending("done", DONE)},
    )
    issues = Returned(approved)
    sink = EventSink(None)
    seen = recorded(sink)

    outcome = deliver(ws, sessions, Routes(session_end_review()), issues, rework=True, sink=sink)

    rebuild = sessions.started[1]
    assert "resolving" not in rebuild or not rebuild["resolving"]
    assert "couldn't be resolved" in rebuild["story"] and "tests/test_pin.py" in rebuild["story"]
    assert rebuild["rebuilt"] is True
    worktree = Held.instances[-1].worktree
    assert (worktree / PIN).read_text() == "api_version: 3\n", "rebuilt on main"
    assert forced == [True] and outcome.pr == 700, "as today's rebuild"
    assert not any(RESOLVED_MARKER.split("{")[0] in body for _, body in issues.said)
    assert any(e.detail.get("rebuilt") for e in seen), "a rebuild, counted as one"


def test_a_resolution_its_session_blocks_falls_back_to_a_rebuild(ws, deliver):  # noqa: F811
    approved = approved_and_conflicting(ws)
    sessions = Sessions(
        {"ending": Ending("blocked", {"missing": "the two pins disagree on purpose"})},
        {"ending": Ending("done", DONE)},
    )
    deliver(ws, sessions, Routes(session_end_review()), Returned(approved), rework=True)
    assert "disagree on purpose" in sessions.started[1]["story"]


def test_a_story_review_sent_back_is_still_rebuilt_not_resolved(ws, deliver):  # noqa: F811
    """Only a head the merge returned for a conflict was approved as it stands."""
    approved = approved_and_conflicting(ws)
    issues = Returned(approved)
    issues.said = []
    sessions = Sessions({"ending": Ending("done", DONE)})
    deliver(ws, sessions, Routes(session_end_review()), issues, rework=True)
    assert len(sessions.started) == 1 and not sessions.started[0].get("resolving")
    assert sessions.started[0]["rebuilt"] is True


# --- the merge's side --------------------------------------------------------------------


def test_a_return_its_resolution_answered_isn_t_counted_as_a_rebuild():
    issues = MergeIssues(mergeable_state="dirty")
    issues.trust = TRUSTED
    issues.earlier = [REBUILD_MARKER.format(head=h) for h in ("a", "b")]
    resolved = RESOLVED_MARKER.format(approved="a", head="c", files=PIN)

    def comments(repo, number):
        said = [{"body": b, "user": {"login": CREW}} for b in [*issues.earlier, resolved]]
        return said + [{"body": b} for n, b in issues.posted if n == number]

    issues.comments = comments
    board = FakeBoard()
    result = merge_approved(
        board, issues, EventSink(None), cards=[merge_card(6)], default_repo="sprint-metrics"
    )
    assert result.rebuilding == [(6, 100)] and result.conflicted == []
    assert "rebuild 2 of 2" in issues.posted[-1][1]


def test_keeping_both_still_comes_first(monkeypatch):
    issues = MergeIssues(mergeable_state="dirty")
    result = merge_approved(
        FakeBoard(),
        issues,
        EventSink(None),
        cards=[merge_card(6)],
        default_repo="sprint-metrics",
        keep_both=lambda repo, branch: ["tests/test_it.py"],
    )
    assert result.updating == [(6, 100)] and result.rebuilding == []
    assert not any(REBUILD_MARKER.split("{")[0] in body for _, body in issues.posted)


def test_a_resolved_conflict_isn_t_counted_as_rebuild_strain(tmp_path):
    (tmp_path / "tick.jsonl").write_text(
        json.dumps(
            {
                "kind": "merge.resolved",
                "at": "2026-10-27T10:00:00+00:00",
                "detail": {"card": 809, "repo": "sprint-metrics", "files": [PIN], "paths": [PIN]},
            }
        )
        + "\n"
    )
    assert read_rebuilds(tmp_path) == []


def test_qa_judges_only_the_resolution_s_files_from_the_head_it_accepted(monkeypatch, tmp_path):
    from crew_org.flows import acceptance as flow
    from tests.test_acceptance import _QABoard, _QAIssues, _QAWorkspace, in_a_session, story

    seen = in_a_session(monkeypatch)

    class Resolving(_QAIssues):
        trust = TRUSTED

        def pull_for_branch(self, repo, branch, known=None):
            return {"number": 700}

        def comments(self, repo, number):
            if number != 700:
                return []
            marker = RESOLVED_MARKER.format(approved="ab12", head="abc1234def56", files=PIN)
            return [{"body": marker, "user": {"login": CREW}}]

    flow.run_qa(
        _QABoard(),
        Resolving(comments=[]),
        EventSink(None),
        _QAWorkspace(tmp_path),
        None,
        cards=[story(31, "QAing")],
        repo="sprint-metrics",
    )
    assert seen["changed"] == [PIN] and seen["base"] == "ab12"
    assert "This head resolves conflicts with main" in seen["answered"]
