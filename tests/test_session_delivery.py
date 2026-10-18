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
    """A session's container: started, set up, stopped, and nothing run in it."""

    instances: list[Held] = []

    def __init__(self, worktree, name, *a, **k):
        self.worktree, self.name = worktree, name
        self.tools = SimpleNamespace(verify=(), autofix=(), setup=["uv", "sync"])
        self.stopped = False
        self.setup_code = 0
        Held.instances.append(self)

    def start(self):
        return SimpleNamespace(ok=True, output="")

    def setup(self, timeout=0):
        code = Held.setup_code
        return SimpleNamespace(ok=code == 0, code=code, command="uv sync", output="no network")

    def stop(self):
        self.stopped = True


Held.setup_code = 0


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
            return script["ending"]

        session.run = run
        return session


class Routes:
    """Stage D part 1's route, each answer scripted, and what it was asked."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.asked: list[Any] = []

    def __call__(self, ending, **kw):
        self.asked.append(ending)
        kw["run_checks"]  # the container's checks are always offered
        return self.answers.pop(0)


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
    Held.setup_code = 0
    monkeypatch.setattr(session_delivery, "Held", Held)
    monkeypatch.setattr(session_delivery, "coverage_for", lambda *a, **k: None)
    monkeypatch.setattr(session_delivery, "story_notes", lambda *a: "")
    monkeypatch.setattr(session_delivery, "story_rows", lambda *a: "")
    monkeypatch.setattr(session_end, "pr_sections", lambda verdict: "", raising=False)
    workspace = git_ops.Workspace("mqucifer", "sprint-metrics", "tok", git_ops.BotIdentity("b", 1))
    workspace.origin = origin
    return workspace


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
            sink=EventSink(None),
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
    routes = Routes(session_end.Route(session_end.REVIEW, "decision.delivery.outcome"))
    issues = Issues()
    outcome = deliver(ws, sessions, routes, issues)
    assert outcome.pr == 700 and outcome.ended and Held.instances[0].stopped
    body = issues.pulls[0]["body"]
    assert "Shows the rate." in body and "`tests/test_rate.py::test_shown`" in body
    assert "`rate.py` (new)" in body and "Closes #662" in body
    assert pushed(ws).splitlines()[0] == "feat(662): Show the rate"


def test_the_session_s_story_holds_the_card_and_what_its_epic_decided(ws, deliver, monkeypatch):
    monkeypatch.setattr(session_delivery, "story_rows", lambda *a: "| rate | per hour |")
    monkeypatch.setattr(session_delivery, "story_notes", lambda *a: "Hours, not days.")
    sessions = Sessions({"ending": Ending("done", DONE)})
    deliver(ws, sessions, Routes(session_end.Route(session_end.REVIEW, "r")))
    story = sessions.started[0]["story"]
    assert story.startswith("Show the rate\n\n**Acceptance criteria**")
    assert "| rate | per hour |" in story and "Hours, not days." in story
    assert sessions.started[0]["story_parts"] == {"card": 58, "decided": 19, "note": 16}


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
    sessions = Sessions({"ending": Ending("done", DONE), "model_s": BACKSTOP_S})
    routes = Routes(
        session_end.Route(session_end.SESSION, "check.gates.x", start="## Findings"),
        session_end.Route(session_end.REFINEMENT, Rule.RETURN_SESSION_BLOCKED),
    )
    outcome = deliver(ws, sessions, routes)
    stopped = routes.asked[1]
    assert stopped.how == "blocked" and stopped.stop == "backstop"
    assert stopped.said["kind"] == "stop" and "## Findings" in stopped.said["missing"]
    assert outcome.routed == session_end.REFINEMENT and outcome.ended


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


def test_a_failed_setup_blocks_as_a_design_s_fault_is_found(ws, deliver):
    Held.setup_code = 1
    outcome = deliver(ws, Sessions(), Routes())
    assert outcome.blocked_rule == Rule.WORKSPACE_UNREADY and outcome.ended
    assert "$ uv sync\n(exit 1)\nno network" in outcome.blocked_reason
    assert Held.instances[0].stopped


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
