"""The deliver phase runs Developer sessions to their end (crew#778, ADR 0032, Stage D).

Real git, over a local origin; the workspace container, the session and the route
are faked, each by what it hands back (Stage B's `Held`, Stage C's `Ending`, Stage
D part 1's `Route`).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from crew_org import git_ops
from crew_org.events import EventSink
from crew_org.flows import session_delivery, session_end
from crew_org.flows.delivery import DeliveryResult, _work_one_card
from crew_org.flows.session_delivery import (
    BACKSTOP_S,
    SessionInterrupted,
    deliver_by_session,
    story_section,
)
from crew_org.rules import Rule
from crew_org.session.conversation import Ending
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
BRANCH = "feat/662-show-the-rate"
DONE = {
    "criteria": [{"criterion": "The rate is shown", "test": "tests/test_rate.py::test_shown"}],
    "changed_tests": [],
    "docs": "none needed",
    "summary": "Shows the rate.",
}


def card(**more: Any) -> Card:
    return Card(
        item_id="I662",
        number=662,
        title="Show the rate",
        status="In Progress",
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        **more,
    )


class Issues:
    def __init__(self, live: dict[str, Any] | None = None) -> None:
        self.live = live
        self.pulls: list[dict[str, Any]] = []
        self.comments: list[tuple[int, str]] = []

    def get(self, repo, number):
        return {"body": "**Acceptance criteria**\n- The rate is shown"}

    def pull_for_branch(self, repo, branch, known=None):
        return self.live

    def create_pull(self, repo, *, title, head, base, body):
        self.pulls.append({"title": title, "head": head, "base": base, "body": body})
        return {"number": 700}

    def comment(self, repo, number, body):
        self.comments.append((number, body))
        return {}


class Held:
    """A session's container: started, set up, stopped, and nothing run in it.

    `start_code` and `setup_codes` script how its start and each setup answer.
    """

    instances: list[Held] = []
    start_code = 0
    setup_codes: list[int] = []

    def __init__(self, worktree, name, *a, **k):
        self.worktree, self.name = worktree, name
        self.tools = SimpleNamespace(verify=(), autofix=(), setup=["uv", "sync"])
        self.stopped = False
        self.lost = False
        self.setups = 0
        Held.instances.append(self)

    def start(self):
        code = Held.start_code
        said = "no such image" if code == 126 else "names no `image`"
        return SimpleNamespace(ok=code == 0, code=code, output="" if code == 0 else said)

    def setup(self, timeout=0):
        self.setups += 1
        code = Held.setup_codes.pop(0) if Held.setup_codes else 0
        return SimpleNamespace(ok=code == 0, code=code, command="uv sync", output="no network")

    def stop(self):
        self.stopped = True


class Sessions:
    """Stage C's sessions, each scripted: what it does to the files, and how it ends."""

    def __init__(self, *scripts) -> None:
        self.scripts = list(scripts)
        self.started: list[dict[str, Any]] = []

    def __call__(self, worktree, story, workspace, toolchain, **kw):
        script = self.scripts.pop(0)
        self.started.append({"story": story, **kw})
        session = SimpleNamespace(model_s=script.get("model_s", 60.0), backstop_s=None)

        def run():
            self.started[-1]["backstop_s"] = session.backstop_s
            for path, text in script.get("writes", {}).items():
                (worktree / path).write_text(text)
            for record in script.get("runs", []):
                kw["after_run"](record)
            for path, text in script.get("later", {}).items():
                (worktree / path).write_text(text)
            return script["ending"]

        session.run = run
        return session


class Routes:
    """Stage D part 1's route, each answer scripted, and what it was asked."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.asked: list[Any] = []
        self.leavings: list[Any] = []

    def __call__(self, ending, **kw):
        self.asked.append(ending)
        self.leavings.append(kw["leaving"])
        kw["run_checks"]  # the container's checks are always offered
        return self.answers.pop(0)


class Leaves(Routes):
    """A route that sends the work back for a rewrite: it keeps it, then writes the note."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: list[str] = []

    def __call__(self, ending, **kw):
        self.asked.append(ending)
        kept = kw["leaving"].take()
        self.notes.append(
            session_end.returned_note(
                ending, card=kw["card"], story=kw["story"], leaving=kw["leaving"], kept=kept
            )
        )
        return session_end.Route(session_end.REFINEMENT, Rule.RETURN_SESSION_BLOCKED)


@pytest.fixture
def ws(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    subprocess.run([*GIT, "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README.md").write_text("x\n")
    subprocess.run([*GIT, "-C", str(seed), "add", "."], check=True)
    subprocess.run([*GIT, "-C", str(seed), "commit", "-qm", "first"], check=True)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    monkeypatch.setattr(git_ops, "CLONES", tmp_path / "repos")
    monkeypatch.setattr(git_ops, "WORKTREES", tmp_path / "wt")
    clone = tmp_path / "repos" / "sprint-metrics"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    Held.instances = []
    Held.start_code = 0
    Held.setup_codes = []
    monkeypatch.setattr(session_delivery, "Held", Held)
    monkeypatch.setattr(session_delivery, "coverage_for", lambda *a, **k: None)
    monkeypatch.setattr(session_delivery, "story_notes", lambda *a: "")
    monkeypatch.setattr(session_delivery, "story_rows", lambda *a: "")
    monkeypatch.setattr(session_delivery, "check", lambda at, **kw: CheckResult(results=[]))
    workspace = git_ops.Workspace("mqucifer", "sprint-metrics", "tok", git_ops.BotIdentity("b", 1))
    workspace.origin = origin
    workspace.seed = seed
    return workspace


def on_origin(ws, ref: str, files: dict[str, str], *, base: str = "main") -> None:
    """A commit of `files` on `ref` at the origin, made from `base` there."""
    seed = ws.seed
    subprocess.run(
        ["git", "-C", str(seed), "fetch", "-q", str(ws.origin), "+refs/heads/*:refs/remotes/o/*"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-q", "-B", "work", f"o/{base}"], check=True
    )
    for path, text in files.items():
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_text(text)
    subprocess.run([*GIT, "-C", str(seed), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(seed), "commit", "-qm", f"on {ref}"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "push", "-q", "-f", str(ws.origin), f"work:{ref}"], check=True
    )


def pushes(ws, monkeypatch) -> list[bool]:
    """Each push's `force`, the push itself made."""
    forced: list[bool] = []
    real = ws.push

    def push(*, force=False):
        forced.append(force)
        real(force=force)

    monkeypatch.setattr(ws, "push", push)
    return forced


@pytest.fixture
def deliver(monkeypatch):
    """`deliver_by_session` with Stage C's session and Stage D part 1's route scripted."""
    from crew_org.session import conversation

    def run(ws, sessions, routes, issues=None, **kw):
        monkeypatch.setattr(conversation, "session", sessions)
        monkeypatch.setattr(session_end, "route", routes)
        return deliver_by_session(
            kw.pop("story", card()),
            board=None,
            issues=issues or Issues(),
            sink=kw.pop("sink", None) or EventSink(None),
            ws=ws,
            sprint="Sprint 30",
            repo="sprint-metrics",
            default_branch="main",
            **kw,
        )

    return run


def pushed(ws, ref=BRANCH) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(ws.origin), "log", "--format=%s", ref],
        capture_output=True,
        text=True,
    ).stdout


def test_done_and_the_gates_pass_opens_the_pull_request(ws, deliver):
    sessions = Sessions({"writes": {"rate.py": "RATE = 1\n"}, "ending": Ending("done", DONE)})
    sections = ["", "## For the Code Reviewer", "", "- `tests/test_rate.py::test_old`: why?"]
    routes = Routes(
        session_end.Route(session_end.REVIEW, "decision.delivery.outcome", sections=sections)
    )
    issues = Issues()
    outcome = deliver(ws, sessions, routes, issues)
    assert outcome.pr == 700 and outcome.ended and Held.instances[0].stopped
    body = issues.pulls[0]["body"]
    assert "Shows the rate." in body and "`tests/test_rate.py::test_shown`" in body
    assert "`rate.py` (new)" in body and "Closes #662" in body
    assert "## For the Code Reviewer\n\n- `tests/test_rate.py::test_old`: why?" in body
    assert pushed(ws).splitlines()[0] == "feat(662): Show the rate"


def test_what_the_fixers_changed_is_recorded_committed_and_named_for_review(ws, deliver):
    """crew#758: named for review and QA, as the one-call path did."""
    from crew_org.flows.gates import Verdict

    class Fixing(Routes):
        def __call__(self, ending, **kw):
            (kw["worktree"] / "rate.py").write_text("RATE = 1  # formatted\n")
            fixed = CheckResult(results=[], fixed=["rate.py"], unsafe=["B905 rate.py:1"])
            return session_end.Route(
                session_end.REVIEW, "decision.delivery.outcome", verdict=Verdict(check=fixed)
            )

    seen: list[Any] = []
    issues = Issues()
    sink = EventSink(None)
    sink.subscribe(seen.append)
    outcome = deliver(
        ws,
        Sessions({"writes": {"rate.py": "RATE=1\n"}, "ending": Ending("done", DONE)}),
        Fixing(),
        issues,
        sink=sink,
    )
    assert outcome.autofixed == ["rate.py"] and outcome.unsafe_fixes == ["B905 rate.py:1"]
    applied = [e for e in seen if e.kind == "autofix.applied"]
    assert applied[0].detail == {
        "fixed": ["rate.py"],
        "reverted": [],
        "unsafe": ["B905 rate.py:1"],
        "rules": ["B905"],
    }
    body = issues.pulls[0]["body"]
    assert "## Fixed by the project's own tools before the checks\n\n- `rate.py`" in body
    assert "unsafe fix `B905` at `rate.py:1`, allowed by `workspace.UNSAFE_FIXES`" in body
    assert pushed(ws).splitlines()[0].startswith("style(662):"), "the fix lands with the work"


def test_the_session_s_story_holds_the_card_and_what_its_epic_decided(ws, deliver, monkeypatch):
    monkeypatch.setattr(session_delivery, "story_rows", lambda *a: "| rate | per hour |")
    monkeypatch.setattr(session_delivery, "story_notes", lambda *a: "Hours, not days.")
    sessions = Sessions({"ending": Ending("done", DONE)})
    deliver(ws, sessions, Routes(session_end.Route(session_end.REVIEW, "r")))
    story = sessions.started[0]["story"]
    assert story.startswith("Show the rate\n\n**Acceptance criteria**")
    assert "| rate | per hour |" in story and "Hours, not days." in story
    assert sessions.started[0]["story_parts"] == {
        "card": 58,
        "decided": 19,
        "note": 16,
        "diagram": 0,
    }


def test_a_green_run_is_a_checkpoint_and_a_red_one_isn_t(ws, deliver):
    green = {"what": "tests", "ok": True, "commands": [{"command": "pytest", "code": 0}]}
    red = {"what": "suite", "ok": False, "commands": [{"command": "pytest", "code": 1}]}

    class Writing(Sessions):
        def __call__(self, worktree, story, workspace, toolchain, **kw):
            session = super().__call__(worktree, story, workspace, toolchain, **kw)
            ran = session.run

            def run():
                (worktree / "a.py").write_text("a = 1\n")
                kw["after_run"](green)
                (worktree / "b.py").write_text("b = 1\n")
                kw["after_run"](red)
                return ran()

            session.run = run
            return session

    deliver(ws, Writing({"ending": Ending("done", DONE)}), Routes(session_end.Route("review", "r")))
    subjects = pushed(ws).splitlines()
    assert subjects[:2] == ["feat(662): Show the rate", "checkpoint(662): run tests passed"]


def test_findings_start_a_new_session_with_them_and_what_is_left_of_the_budget(ws, deliver):
    sessions = Sessions(
        {"ending": Ending("done", DONE), "model_s": 1500.0},
        {"ending": Ending("done", DONE), "model_s": 300.0},
    )
    routes = Routes(
        session_end.Route(session_end.SESSION, "check.gates.x", start="## Findings\n- one"),
        session_end.Route(session_end.REVIEW, "decision.delivery.outcome"),
    )
    outcome = deliver(ws, sessions, routes)
    assert outcome.pr == 700 and outcome.attempts == 2
    assert sessions.started[0]["instead_of_hints"] is None
    assert sessions.started[1]["instead_of_hints"] == "## Findings\n- one"
    assert sessions.started[1]["backstop_s"] == BACKSTOP_S - 1500.0
    assert len(Held.instances) == 1, "one container for the delivery"


def test_findings_with_the_budget_spent_are_a_stop_for_the_business_analyst(ws, deliver):
    from crew_org.flows.gates import Finding, Verdict

    finding = Finding("check.gates.x", "One finding.")
    sessions = Sessions({"ending": Ending("done", DONE), "model_s": BACKSTOP_S})
    routes = Routes(
        session_end.Route(
            session_end.SESSION,
            "check.gates.x",
            start="## Findings",
            verdict=Verdict(findings=[finding]),
        ),
        session_end.Route(session_end.REFINEMENT, Rule.RETURN_SESSION_BLOCKED),
    )
    outcome = deliver(ws, sessions, routes)
    stopped, leaving = routes.asked[1], routes.leavings[1]
    assert stopped.how == "blocked" and stopped.stop == "backstop"
    assert stopped.said["kind"] == "stop"
    assert (leaving.findings, leaving.model_s, leaving.sessions) == ([[finding]], BACKSTOP_S, 1)
    assert routes.leavings[0].findings == [], "the first `done` has no earlier findings"
    assert outcome.routed == session_end.REFINEMENT and outcome.ended


# sprint-metrics#671's finding, five times: a version bump made uv sync again, and the
# workspace container has no network after its setup.
OFFLINE = (
    "$ uv run ruff check .\n(exit 2)\nerror: Request failed after 3 retries\n"
    "  Caused by: Failed to fetch: `https://pypi.org/simple/pytest/`\n"
    "  Caused by: dns error: failed to lookup address information"
)
FAILING = "\n".join(
    [
        "$ uv run pytest -q",
        "(exit 1)",
        "..F.............                                                         [100%]",
        "=================================== FAILURES ===================================",
        "_________________________________ test_shown __________________________________",
        "    def test_shown():",
        ">       assert rate() == 2",
        "E       assert 1 == 2",
        "tests/test_rate.py:4: AssertionError",
        "FAILED tests/test_rate.py::test_shown - assert 1 == 2",
    ]
)


def refused(report: str) -> session_end.Route:
    """The gates sending the branch back with lint or the tests failing, in their words."""
    from crew_org.flows.gates import Finding, Verdict
    from crew_org.rulebook import says

    rule = "check.delivery.lint_or_tests_failed"
    finding = Finding(rule, says(rule, amended="", repair="", report=report))
    return session_end.Route(
        session_end.SESSION, rule, start="## The gates", verdict=Verdict(findings=[finding])
    )


class Gates(Leaves):
    """The gates' answer at each `done`, scripted; a stop leaves as `Leaves` does."""

    def __init__(self, *answers) -> None:
        super().__init__()
        self.answers = list(answers)

    def __call__(self, ending, **kw):
        if ending.how != "done":
            return super().__call__(ending, **kw)
        self.asked.append(ending)
        return self.answers.pop(0)


def test_the_gates_same_finding_three_times_stops_the_delivery_and_keeps_its_work(ws, deliver):
    """sprint-metrics#671: each refusal started a new session, until a person stopped it."""
    said = "The `run` tool could not execute checks due to a container network limitation."
    sessions = Sessions(
        {"writes": {"rate.py": "RATE = 2\n"}, "ending": Ending("done", DONE)},
        {"ending": Ending("done", DONE)},
        {"ending": Ending("done", DONE, evidence={"last_words": said})},
    )
    routes = Gates(refused(OFFLINE), refused(OFFLINE), refused(OFFLINE))
    outcome = deliver(ws, sessions, routes)
    assert outcome.attempts == 3, "no fourth session"
    assert outcome.routed == session_end.REFINEMENT and outcome.ended
    stopped = routes.asked[-1]
    assert (stopped.how, stopped.stop, stopped.said["kind"]) == ("blocked", "gate_repeat", "stop")
    assert stopped.evidence["stop"] == {"repeats": 3}
    [kept] = kept_on_origin(ws)
    shown = subprocess.run(
        ["git", "--git-dir", str(ws.origin), "show", f"{kept}:rate.py"],
        capture_output=True,
        text=True,
    ).stdout
    assert shown == "RATE = 2\n" and pushed(ws) == "", "kept, and the story's branch not pushed"
    [note] = routes.notes
    assert (
        "stopped by the gates at `done`, which sent back the same finding 3 times in a row.**"
        in note
    )
    assert (
        "**What the gates sent back each time:**\n\n"
        "> Lint or tests failed:\n"
        "> $ uv run ruff check .\n"
        "> (exit 2)\n"
        "> error: Request failed after 3 retries\n"
        ">   Caused by: Failed to fetch: `https://pypi.org/simple/pytest/`\n"
    ) in note
    assert f"[`{kept}`]" in note and f"> {said}" in note
    assert "For the rewrite" not in note, (
        "sprint-metrics#671's was the workspace's, not the story's"
    )


def test_two_same_findings_then_another_go_on_to_the_next_session(ws, deliver):
    sessions = Sessions(*({"ending": Ending("done", DONE)} for _ in range(4)))
    routes = Gates(
        refused(OFFLINE),
        refused(OFFLINE),
        refused(FAILING),
        session_end.Route(session_end.REVIEW, "decision.delivery.outcome"),
    )
    outcome = deliver(ws, sessions, routes)
    assert outcome.pr == 700 and outcome.attempts == 4
    assert routes.notes == [] and kept_on_origin(ws) == []


def test_rounds_are_the_same_by_rule_and_first_lines_whatever_a_run_s_timing():
    from crew_org.flows.gates import Finding

    def gates_round(timing: str) -> list[Finding]:
        return list(refused(f"{FAILING}\n1 failed, 15 passed in {timing}").verdict.findings)

    found = [gates_round("0.12s"), gates_round("0.19s"), gates_round("0.15s")]
    assert session_delivery._repeats(found) == 3
    other = Finding("check.gates.x", found[0][0].says)
    assert session_delivery._repeats([*found, [other]]) == 1, "another rule is another finding"
    assert session_delivery._repeats([[], [], []]) == 0, "no findings is never a repeat"


def test_the_backstop_still_stops_rounds_short_of_three_same_findings(ws, deliver):
    sessions = Sessions(
        {"ending": Ending("done", DONE), "model_s": 1800.0},
        {"ending": Ending("done", DONE), "model_s": BACKSTOP_S},
    )
    routes = Gates(refused(OFFLINE), refused(OFFLINE))
    outcome = deliver(ws, sessions, routes)
    assert routes.asked[-1].stop == "backstop" and outcome.attempts == 2
    assert outcome.routed == session_end.REFINEMENT
    assert "stopped by the backstop: 210 minutes of model time over 2 sessions" in routes.notes[0]


GREEN = {"what": "suite", "ok": True, "commands": [{"command": "pytest", "code": 0}]}
STOP = Ending(
    "blocked",
    {"missing": "180 minutes of model time, the backstop", "kind": "stop"},
    "backstop",
    evidence={"last_run": {"what": "suite", "ok": True}, "stop": {"model_minutes": 180}},
)


def kept_on_origin(ws) -> list[str]:
    return subprocess.run(
        ["git", "--git-dir", str(ws.origin), "for-each-ref", "--format=%(refname:short)"]
        + ["refs/heads/crew/kept/"],
        capture_output=True,
        text=True,
    ).stdout.split()


def test_a_stop_after_a_green_run_keeps_the_work_on_a_branch_of_its_own_and_links_it(ws, deliver):
    """sprint-metrics#662: two hours of green work were dropped with the clone's branch."""
    sessions = Sessions(
        {
            "writes": {"rate.py": "RATE = 2\n"},
            "runs": [GREEN],
            "later": {"rate.py": "RATE = 3\n"},
            "ending": STOP,
            "model_s": BACKSTOP_S,
        }
    )
    routes = Leaves()
    outcome = deliver(ws, sessions, routes)
    assert outcome.routed == session_end.REFINEMENT and outcome.ended
    [kept] = kept_on_origin(ws)
    assert kept.startswith("crew/kept/662-")
    assert pushed(ws, kept).splitlines()[:2] == [
        "stopped(662): edits since the last green run, not run green",
        "checkpoint(662): run suite passed",
    ], "every edit kept, and the unproven ones said to be"
    shown = subprocess.run(
        ["git", "--git-dir", str(ws.origin), "show", f"{kept}:rate.py"],
        capture_output=True,
        text=True,
    ).stdout
    assert shown == "RATE = 3\n"
    assert pushed(ws) == "", "the story's own branch isn't pushed"
    [note] = routes.notes
    assert f"[`{kept}`](https://github.com/mqucifer/sprint-metrics/compare/" in note
    assert f"...{kept}), with no pull request." in note
    assert "**Its last run:** `run suite`, green." in note
    assert "**What it changed:** 1 file: `rate.py` (new)." in note
    assert "which no run proved" in note
    left = subprocess.run(
        ["git", "-C", str(ws.clone), "branch", "--list", "crew/kept/*"],
        capture_output=True,
        text=True,
    ).stdout
    assert left == "", "the clone's copy goes once the push is made"


def test_a_kept_push_the_remote_refuses_blocks_as_a_failed_push_and_keeps_the_work(ws, deliver):
    hook = ws.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho no new branches >&2\nexit 1\n")
    hook.chmod(0o755)
    sessions = Sessions({"writes": {"rate.py": "RATE = 2\n"}, "runs": [GREEN], "ending": STOP})
    routes = Leaves()
    outcome = deliver(ws, sessions, routes)
    assert outcome.blocked_rule == Rule.PUSH_FAILED and not outcome.ended, "the work is kept"
    assert routes.notes == [], "no note links a branch that isn't there"
    assert "to keep its work" in (outcome.blocked_reason or "")
    assert "**Its last run:** `run suite`, green." in (outcome.blocked_reason or "")
    assert kept_on_origin(ws) == []
    left = subprocess.run(
        ["git", "-C", str(ws.clone), "branch", "--list", "--format=%(refname:short)"]
        + ["crew/kept/*"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert len(left) == 1 and left[0].startswith("crew/kept/662-"), "in the clone, unpushed"
    ws.close()
    assert ws.checkpoint_on(BRANCH), "and the story's branch keeps its checkpoint"


def test_a_kept_push_github_s_side_failed_is_an_interruption(ws, deliver, monkeypatch):
    def stumbled(*a, **kw):
        raise git_ops.GitError("git push failed: remote: fatal error in commit_refs")

    monkeypatch.setattr(ws, "keep", stumbled)
    sessions = Sessions({"writes": {"rate.py": "RATE = 2\n"}, "runs": [GREEN], "ending": STOP})
    with pytest.raises(git_ops.GitError):
        deliver(ws, sessions, Leaves())
    ws.close()
    assert ws.checkpoint_on(BRANCH), "retried next pass, as any failed push"


def test_work_no_different_from_main_keeps_nothing(ws, deliver):
    blocked = Ending("blocked", {"missing": "the rate's unit", "kind": "fact"})
    routes = Leaves()
    outcome = deliver(ws, Sessions({"ending": blocked}), routes)
    assert outcome.routed == session_end.REFINEMENT and kept_on_origin(ws) == []
    assert "**Nothing was kept:** its branch was no different from main." in routes.notes[0]


def test_a_story_the_route_moved_is_only_cleaned_up(ws, deliver):
    blocked = Ending("blocked", {"missing": "the rate's unit", "kind": "fact"})
    sessions = Sessions({"writes": {"half.py": "x = 1\n"}, "ending": blocked})
    issues = Issues()
    outcome = deliver(
        ws, sessions, Routes(session_end.Route(session_end.REFINEMENT, "story.x")), issues
    )
    assert outcome.routed == session_end.REFINEMENT and outcome.blocked_rule == "story.x"
    assert "half.py" in (outcome.rejected_diff or "") and issues.pulls == []
    assert pushed(ws) == "", "nothing is pushed"


def test_an_interrupted_session_keeps_its_checkpoint_and_the_next_resumes_it(ws, deliver):
    green = {"what": "tests", "ok": True, "commands": [{"command": "pytest", "code": 0}]}
    lost = Ending("interrupted", {"error": "No such container"}, why="workspace_gone")
    first = Sessions({"writes": {"a.py": "a = 1\n"}, "runs": [green], "ending": lost})
    with pytest.raises(SessionInterrupted) as raised:
        deliver(ws, first, Routes())
    assert raised.value.service == "the workspace container"
    ws.close()
    assert ws.checkpoint_on(BRANCH).startswith("checkpoint(662): run tests passed")

    second = Sessions({"ending": Ending("done", DONE)})
    deliver(ws, second, Routes(session_end.Route(session_end.REVIEW, "r")))
    told = second.started[0]["instead_of_hints"]
    assert told.startswith("## Where the last session stopped") and "run tests passed" in told
    assert (Held.instances[-1].worktree / "a.py").read_text() == "a = 1\n"


# --- every step that can fail has a handled ending (the Sponsor, on crew#778) ----


def test_a_setup_that_fails_twice_blocks_as_a_design_s_fault_is_found(ws, deliver):
    from crew_org.flows.revisit import design_fault

    Held.setup_codes = [1, 1]
    outcome = deliver(ws, Sessions(), Routes())
    assert outcome.blocked_rule == Rule.WORKSPACE_UNREADY and outcome.ended
    assert "$ uv sync\n(exit 1)\nno network" in outcome.blocked_reason
    assert design_fault(outcome.blocked_reason, SimpleNamespace(setup="uv sync", checks=[]))
    assert Held.instances[0].setups == 2 and Held.instances[0].stopped


def test_a_setup_that_fails_once_is_tried_again(ws, deliver):
    Held.setup_codes = [1, 0]
    outcome = deliver(
        ws, Sessions({"ending": Ending("done", DONE)}), Routes(session_end.Route("review", "r"))
    )
    assert outcome.pr == 700 and Held.instances[0].setups == 2


def test_an_engine_that_can_t_start_the_container_is_an_interruption(ws, deliver):
    Held.start_code = 126
    with pytest.raises(SessionInterrupted) as raised:
        deliver(ws, Sessions(), Routes())
    assert raised.value.service == "the container engine" and Held.instances[0].stopped


def test_a_definition_that_can_t_run_blocks_as_the_design_s_fault(ws, deliver):
    from crew_org.flows.revisit import design_fault

    Held.start_code = 2
    outcome = deliver(ws, Sessions(), Routes())
    assert outcome.blocked_rule == Rule.WORKSPACE_UNREADY
    assert design_fault(outcome.blocked_reason, SimpleNamespace(setup="", checks=[]))


def test_the_commit_at_done_failing_is_an_interruption(ws, deliver, monkeypatch):
    real = ws.commit

    def commit(message, **kw):
        if message.startswith("feat("):
            raise git_ops.GitError("git commit failed: index.lock exists")
        return real(message, **kw)

    monkeypatch.setattr(ws, "commit", commit)
    with pytest.raises(SessionInterrupted) as raised:
        deliver(ws, Sessions({"ending": Ending("done", DONE)}), Routes())
    assert raised.value.service == "git" and Held.instances[0].stopped


def test_a_container_lost_while_the_gates_run_is_resumed_not_blamed(ws, deliver):
    class Losing(Routes):
        def __call__(self, ending, **kw):
            Held.instances[-1].lost = True
            kw["run_checks"](kw["worktree"])

    with pytest.raises(SessionInterrupted) as raised:
        deliver(
            ws, Sessions({"writes": {"a.py": "a = 1\n"}, "ending": Ending("done", DONE)}), Losing()
        )
    assert raised.value.service == "the workspace container"
    ws.close()
    kept = ws.checkpoint_on(BRANCH)
    assert kept is not None and kept.startswith("feat(662): Show the rate"), "resumed from `done`"


def test_the_gates_raising_blocks_the_story_for_a_person_with_its_done(ws, deliver):
    class Raising(Routes):
        def __call__(self, ending, **kw):
            raise KeyError("criteria")

    outcome = deliver(ws, Sessions({"ending": Ending("done", DONE)}), Raising())
    assert outcome.blocked_rule == Rule.ROUTE_FAILED and outcome.ended
    assert "KeyError" in outcome.blocked_reason and "## The last `done`" in outcome.blocked_reason
    assert Held.instances[0].stopped


def test_a_passing_github_failure_in_the_route_is_an_interruption(ws, deliver):
    import httpx

    class Stumbling(Routes):
        def __call__(self, ending, **kw):
            raise httpx.ConnectError(
                "reset", request=httpx.Request("GET", "https://api.github.com/x")
            )

    with pytest.raises(httpx.ConnectError):
        deliver(ws, Sessions({"ending": Ending("done", DONE)}), Stumbling())
    assert Held.instances[0].stopped


def test_the_route_s_moves_failing_partway_still_block_it_with_the_evidence(monkeypatch):
    """Wherever the route's moves stopped, delivery blocks it, labels it and says why."""
    from crew_org.flows import delivery

    unrouted = delivery.DeliveryOutcome(
        card=662,
        branch=BRANCH,
        blocked_rule=Rule.ROUTE_FAILED,
        blocked_reason="its session ended `blocked`, and ... **The last failure:** none",
        ended=True,
    )
    monkeypatch.setattr(session_delivery, "deliver_by_session", lambda *a, **k: unrouted)
    moves, posted = [], []
    board = SimpleNamespace(
        set_status=lambda item, to: moves.append(to),
        set_owner_agent=lambda item, by: None,
        repo_of=lambda item: "sprint-metrics",
    )
    issues = SimpleNamespace(
        add_labels=lambda repo, n, labels: posted.append(("label", labels)),
        comment=lambda repo, n, body: posted.append(("comment", body)),
    )
    ws = SimpleNamespace(
        for_repo=lambda repo: ws, diff_if_open=lambda: None, close=lambda: None, forget=print
    )
    result = DeliveryResult()
    _work_one_card(
        card(),
        board=board,
        issues=issues,
        sink=EventSink(None),
        ws=ws,
        result=result,
        counts={"In Progress": 1},
        sprint="Sprint 30",
        repo="sprint-metrics",
        default_branch="main",
    )
    assert moves == ["Blocked"] and ("label", ["blocked"]) in posted
    assert any(kind == "comment" and "**The last failure:**" in body for kind, body in posted)
    assert result.blocked == [unrouted]


def test_a_refused_push_blocks_and_keeps_the_work(ws, deliver, monkeypatch):
    def refused(**kw):
        raise git_ops.GitError("git push failed: ! [rejected] (stale info)")

    monkeypatch.setattr(ws, "push", refused)
    outcome = deliver(
        ws, Sessions({"ending": Ending("done", DONE)}), Routes(session_end.Route("review", "r"))
    )
    assert outcome.blocked_rule == Rule.PUSH_FAILED and not outcome.ended, "the work is kept"


def test_a_push_github_s_side_failed_is_retried_from_the_done(ws, deliver, monkeypatch):
    def stumbled(**kw):
        raise git_ops.GitError("git push failed: remote: fatal error in commit_refs")

    monkeypatch.setattr(ws, "push", stumbled)
    with pytest.raises(git_ops.GitError):
        deliver(
            ws, Sessions({"ending": Ending("done", DONE)}), Routes(session_end.Route("review", "r"))
        )
    ws.close()
    assert git_ops.DONE_TRAILER in (ws.checkpoint_on(BRANCH) or "")


def test_a_pull_request_that_can_t_be_opened_is_resumed_from_the_pushed_done(ws, deliver):
    from crew_org.tools.github_issues import IssueError

    class Refusing(Issues):
        def create_pull(self, repo, **kw):
            raise IssueError("POST pulls -> 422: Validation Failed")

    outcome = deliver(
        ws,
        Sessions({"ending": Ending("done", DONE)}),
        Routes(session_end.Route("review", "r")),
        Refusing(),
    )
    assert outcome.blocked_rule == Rule.PULL_REQUEST_FAILED and not outcome.ended
    assert pushed(ws).startswith("feat(662)")
    ws.close()
    again = Sessions({"ending": Ending("done", DONE)})
    outcome = deliver(ws, again, Routes(session_end.Route("review", "r")))
    assert again.started[0]["instead_of_hints"].startswith("## Where the last session stopped")
    assert outcome.pr == 700


def test_a_rework_comment_that_fails_still_hands_the_pull_request_to_review(ws, deliver):
    class Mute(Issues):
        def comment(self, repo, number, body):
            raise RuntimeError("comment failed")

    issues = Mute(live={"number": 700})
    outcome = deliver(
        ws,
        Sessions({"ending": Ending("done", DONE)}),
        Routes(session_end.Route("review", "r", sections=["", "## Declared", "- `t`"])),
        issues,
        rework=True,
    )
    assert outcome.pr == 700 and outcome.blocked_rule is None


def test_a_host_made_venv_is_refused_before_the_container_starts(ws, deliver, monkeypatch):
    from crew_org.session import conversation

    monkeypatch.setattr(conversation, "unfit", lambda worktree: "a .venv the host made")
    outcome = deliver(ws, Sessions(), Routes())
    assert outcome.blocked_rule == Rule.WORKSPACE_UNREADY and Held.instances == []


def test_a_returned_story_s_session_is_told_the_findings_and_the_last_done(
    ws, deliver, monkeypatch
):
    sessions = Sessions({"ending": Ending("done", DONE)})
    deliver(ws, sessions, Routes(session_end.Route(session_end.REVIEW, "r")))
    ws.close()
    ws.forget(BRANCH)  # as the phase does once a delivery has ended
    monkeypatch.setattr(session_delivery, "prior_verdicts", lambda *a, **k: "Rename `rate`.")
    monkeypatch.setattr(session_delivery, "previous_fate", lambda *a, **k: "")
    again = Sessions({"ending": Ending("done", DONE)})
    issues = Issues(live={"number": 700})
    outcome = deliver(
        ws, again, Routes(session_end.Route(session_end.REVIEW, "r")), issues, rework=True
    )
    told = again.started[0]["instead_of_hints"]
    assert "Rename `rate`." in told and "## The last `done`" in told and "Shows the rate." in told
    assert outcome.pr == 700 and issues.comments and issues.pulls == []


def test_the_phase_counts_a_routed_story_without_moving_it_again(monkeypatch):
    from crew_org.flows import delivery

    routed = delivery.DeliveryOutcome(card=662, branch=BRANCH, routed="refinement", ended=True)
    monkeypatch.setattr(session_delivery, "deliver_by_session", lambda *a, **k: routed)
    forgotten = []
    ws = SimpleNamespace(
        for_repo=lambda repo: ws,
        diff_if_open=lambda: None,
        close=lambda: None,
        forget=forgotten.append,
    )
    board, result, counts = SimpleNamespace(), DeliveryResult(), {"In Progress": 1}
    _work_one_card(
        card(parent=650),
        board=board,
        issues=None,
        sink=EventSink(None),
        ws=ws,
        result=result,
        counts=counts,
        sprint="Sprint 30",
        repo="sprint-metrics",
        default_branch="main",
    )
    assert result.returned == [(662, 650)] and counts["In Progress"] == 0
    assert forgotten == [BRANCH], "an ended delivery drops the clone's checkpoints"


def test_the_story_section_heads_what_the_epic_decided_under_the_story():
    text = story_section("Title\n\nBody", "A note.", "| a | b |")
    assert text.index("Body") < text.index("### What the epic decided") < text.index("A note.")
    assert story_section("Title\n\nBody", "", "") == "Title\n\nBody"


# --- what the one-call Developer did, which a session's delivery keeps (Stage E) ----


def test_a_fresh_delivery_onto_an_open_pull_request_blocks_before_any_session(ws, deliver):
    """Re-delivering would overwrite the diff a review is reading: nothing is built or pushed."""
    sessions = Sessions()
    outcome = deliver(ws, sessions, Routes(), Issues(live={"number": 690}))
    assert outcome.blocked_rule == Rule.OPEN_PULL_REQUEST and "PR #690" in outcome.blocked_reason
    assert sessions.started == [] and Held.instances == [] and pushed(ws) == ""


def test_a_pull_request_opened_while_it_worked_isn_t_pushed_over(ws, deliver, monkeypatch):
    class Opened(Issues):
        asked = 0

        def pull_for_branch(self, repo, branch, known=None):
            self.asked += 1
            return None if self.asked == 1 else {"number": 691}

    forced = pushes(ws, monkeypatch)
    outcome = deliver(
        ws,
        Sessions({"ending": Ending("done", DONE)}),
        Routes(session_end.Route("review", "r")),
        Opened(),
    )
    assert outcome.blocked_rule == Rule.OPEN_PULL_REQUEST and forced == [] and not outcome.ended


def test_a_returned_branch_that_conflicts_with_main_is_rebuilt_on_main_and_forced(
    ws, deliver, monkeypatch
):
    """#158: the code is being rewritten anyway, so the conflict isn't a person's."""
    on_origin(ws, BRANCH, {"README.md": "the branch's\n"})
    on_origin(ws, "main", {"README.md": "main's\n"})
    forced = pushes(ws, monkeypatch)
    sessions = Sessions({"writes": {"rate.py": "RATE = 1\n"}, "ending": Ending("done", DONE)})
    outcome = deliver(
        ws,
        sessions,
        Routes(session_end.Route("review", "r")),
        Issues(live={"number": 700}),
        rework=True,
    )
    worktree = Held.instances[0].worktree
    assert (worktree / "README.md").read_text() == "main's\n", "rebuilt on main"
    assert sessions.started[0]["instead_of_hints"] is None, "not told of work that isn't there"
    assert forced == [True] and outcome.pr == 700


def test_a_resumed_branch_is_caught_up_with_main_first(ws, deliver, monkeypatch):
    """#119: what landed since the branch was cut is there before the session starts."""
    on_origin(ws, BRANCH, {"rate.py": "RATE = 1\n"})
    on_origin(ws, "main", {"sibling.py": "landed = True\n"})
    seen = {}

    class Looking(Sessions):
        def __call__(self, worktree, story, workspace, toolchain, **kw):
            seen["files"] = sorted(p.name for p in worktree.iterdir())
            return super().__call__(worktree, story, workspace, toolchain, **kw)

    deliver(
        ws,
        Looking({"ending": Ending("done", DONE)}),
        Routes(session_end.Route("review", "r")),
        Issues(live={"number": 700}),
        rework=True,
    )
    assert "sibling.py" in seen["files"] and "rate.py" in seen["files"]


def test_rework_says_so_on_its_pull_request_and_pushes_without_force(ws, deliver, monkeypatch):
    on_origin(ws, BRANCH, {"rate.py": "RATE = 1\n"})
    forced = pushes(ws, monkeypatch)
    issues = Issues(live={"number": 700})
    deliver(
        ws,
        Sessions({"writes": {"rate.py": "RATE = 2\n"}, "ending": Ending("done", DONE)}),
        Routes(session_end.Route("review", "r")),
        issues,
        rework=True,
    )
    assert forced == [False], "on top of what the review read"
    assert issues.comments and "**Re-worked.** Shows the rate." in issues.comments[0][1]


def test_a_fresh_delivery_force_pushes_over_the_crew_s_own_dead_branch(ws, deliver, monkeypatch):
    on_origin(ws, BRANCH, {"dead.py": "abandoned = True\n"})
    forced = pushes(ws, monkeypatch)
    sessions = Sessions({"writes": {"rate.py": "RATE = 1\n"}, "ending": Ending("done", DONE)})
    deliver(ws, sessions, Routes(session_end.Route("review", "r")))
    assert not (Held.instances[0].worktree / "dead.py").exists(), "from main, not the dead branch"
    assert forced == [True] and "on feat" not in pushed(ws)


def test_a_record_that_can_t_be_read_blocks_before_any_container(ws, deliver):
    on_origin(ws, "main", {".crew/project.yaml": "version: [unclosed\n"})
    outcome = deliver(ws, Sessions(), Routes())
    assert outcome.blocked_rule == Rule.RECORD_UNREADABLE and Held.instances == []


def test_a_roles_task_is_signed_and_recorded_as_its_role_not_the_developer(
    ws, deliver, monkeypatch
):
    """mqucifer/sprint-metrics#740, the Architect's Task, was signed Developer (crew#839)."""
    monkeypatch.setattr(session_delivery, "_today", lambda: "2026-10-23")
    task = Card(
        item_id="I662",
        number=662,
        title="Show the rate",
        status="In Progress",
        state="OPEN",
        work_type="Task",
        repo="sprint-metrics",
        labels=frozenset({"role:architect"}),
    )
    seen: list[Any] = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    issues = Issues()
    deliver(
        ws,
        Sessions({"writes": {"rate.py": "RATE = 1\n"}, "ending": Ending("done", DONE)}),
        Routes(session_end.Route(session_end.REVIEW, "decision.delivery.outcome")),
        issues,
        story=task,
        sink=sink,
    )
    body = issues.pulls[0]["body"]
    assert "<!-- crew:by Architect -->" in body and "- Architect sessions: 1" in body
    assert "Developer" not in body
    work = [e for e in seen if e.kind in ("agent.started", "agent.finished")]
    assert {e.role for e in work} == {"Architect"}
    assert all(e.detail.get("task_role") == "Architect" for e in work)


def test_a_storys_pull_request_is_still_the_developers(ws, deliver):
    seen: list[Any] = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    issues = Issues()
    deliver(
        ws,
        Sessions({"writes": {"rate.py": "RATE = 1\n"}, "ending": Ending("done", DONE)}),
        Routes(session_end.Route(session_end.REVIEW, "decision.delivery.outcome")),
        issues,
        sink=sink,
    )
    assert "<!-- crew:by Developer -->" in issues.pulls[0]["body"]
    work = [e for e in seen if e.kind in ("agent.started", "agent.finished")]
    assert {e.role for e in work} == {"Developer"}
    assert not any("task_role" in e.detail for e in work)
