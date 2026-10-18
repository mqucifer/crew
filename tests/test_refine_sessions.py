"""Refinement in a session: the read tools, the step runner and its records (crew#807).

The model is stood in for by `Proxy`, which answers each call in turn, and the
checkout at main is a real worktree of a clone, as `Workspace.at_main` makes it.
Each step's own start and checks are tested with its flow.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from crew_org.crews.panel_crew import PanelAnswer
from crew_org.crews.refinement_crew import StoryProposal
from crew_org.events import EventKind
from crew_org.session import steps
from crew_org.session.reading import ReadTools
from tests.test_git_ops import _real_workspace
from tests.test_session import Proxy, kinds, said, seen  # noqa: F401
from tests.test_session_git import clone, sh  # noqa: F401

NOTE = "@notes/delivered.md"
NOTES = {NOTE: "- #12 Show the escalation rate\n  - when run, then it prints 50%\n"}


@pytest.fixture
def main(clone: Path, tmp_path: Path) -> Path:  # noqa: F811 - the fixture it builds on
    """A detached worktree of the clone's default branch, as refinement reads it."""
    path = tmp_path / "main"
    sh(clone, "worktree", "add", "-q", "--detach", str(path), "main")
    return path.resolve()


def test_a_step_is_offered_the_read_tools_and_nothing_that_edits_or_runs(main: Path):
    t = ReadTools(main, notes=NOTES)
    for name, args in [
        ("replace_text", {"path": "src/app.py", "old": "x", "new": "y"}),
        ("write_file", {"path": "src/x.py", "content": "x = 1\n"}),
        ("run", {"what": "suite"}),
    ]:
        reply = t.call(name, args)
        assert (reply.kind, reply.rule) == ("refused", "session.arguments")
        assert "view, search, find, tests_for, git" in reply.text
    assert "src/app.py" not in t.edited and not t.runs


def test_its_git_reads_and_never_restores(main: Path):
    t = ReadTools(main)
    log = t.call("git", {"command": "git log --oneline -- src/app.py"})
    assert log.kind == "ok" and "merged: f returns 1" in log.text
    (main / "src" / "app.py").write_text("def f():\n    return 9\n")
    restore = t.call("git", {"command": "git restore --source=HEAD -- src/app.py"})
    assert (restore.kind, restore.rule) == ("refused", "session.git")
    assert "this step reads, and changes nothing" in restore.text
    assert "return 9" in (main / "src" / "app.py").read_text()


def test_its_search_reads_the_notes_with_the_repository(main: Path):
    t = ReadTools(main, notes=NOTES)
    both = t.call("search", {"pattern": "(?i)escalation|return 1"})
    assert "src/app.py:" in both.text and f"{NOTE}:" in both.text
    only = t.call("search", {"pattern": "escalation", "path": NOTE})
    assert f"in `{NOTE}`" in only.text and "src/app.py" not in only.text
    assert "50%" in t.call("view", {"path": NOTE}).text


def test_the_developers_search_leaves_the_notes_alone_unless_it_names_one(main: Path):
    from crew_org.session.tools import Tools
    from tests.test_session import CHECKS, Fake

    t = Tools(main, Fake(), CHECKS, notes=NOTES)
    assert f"{NOTE}:" not in t.call("search", {"pattern": "escalation|return"}).text
    assert f"{NOTE}:" in t.call("search", {"pattern": "escalation", "path": NOTE}).text


def test_the_shared_checkout_is_a_worktree_of_the_default_branch_put_back_each_time(
    monkeypatch, tmp_path
):
    ws = _real_workspace(monkeypatch, tmp_path)
    root = ws.at_main()
    assert (root / ".git").is_file() and (root / "README.md").read_text() == "x\n"
    (root / "README.md").write_text("changed\n")
    (root / "stray.txt").write_text("left by a tool\n")
    assert ws.at_main() == root
    assert (root / "README.md").read_text() == "x\n" and not (root / "stray.txt").exists()
    reading = steps.Reading.at(root, "r")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True)
    assert reading.head == head.stdout.decode().strip()


# --- `done` is the step's own form ---------------------------------------------------------


class Note(BaseModel):
    title: str = Field(description="What it's called")
    lines: list[str] = Field(description="Its lines")
    need_files: list[str] = Field(default_factory=list, description="Files to see first")


def test_done_is_the_form_with_its_rules_told_and_no_titles_but_a_field_named_title():
    spec = steps.done_spec(StoryProposal, "the stories.", head="aa78bf29b0fa")
    function = spec["function"]
    assert function["name"] == "done" and function["description"].startswith(
        "Your answer, which ends the session: the stories."
    )
    assert "`_flag` (line 117 at aa78bf2)" in function["description"]
    params = function["parameters"]
    assert "need_files" not in params["properties"] and "title" not in params
    story = params["$defs"]["Story"]
    assert "title" in story["properties"] and "title" not in story
    # A rule a field is checked by is told in that field's description (ADR 0031).
    points = story["properties"]["points"]["description"]
    assert "Refused" in points or "refused" in points


def step(form=PanelAnswer, name="panel qa_engineer", role="qa_engineer", sub="panel"):
    return steps.Step(name, role, form, "your notes, or nothing to add.", "the epic", step=sub)


def run_step(main, proxy, *, check=None, form=PanelAnswer):
    reading = steps.Reading.at(main, "r")
    parts = {"step": "## Your step: read the epic", "epic": "## The epic\n\nf returns 2."}
    return steps.run(
        step(form), parts, reading, about="f returns 2", check=check, notes=NOTES, send=proxy
    )


def test_a_form_refused_for_its_shape_is_a_reply_and_the_session_answers_it(main, seen):  # noqa: F811
    wrong = {"nothing_to_add": True, "notes": [{"problem": "p"}]}
    right = {"nothing_to_add": True, "notes": []}
    proxy = Proxy(
        said(("view", {"path": "src/app.py"})),
        said(("done", wrong)),
        said(("done", right)),
    )
    answer = run_step(main, proxy)
    assert isinstance(answer, PanelAnswer) and answer.nothing_to_add
    refused = proxy.sent[2]["messages"][-1]
    assert refused["role"] == "tool" and refused["content"].startswith("`done` was refused")
    assert "notes.0.source" in refused["content"]
    # The step's own role text, its read tools and its form; nothing that edits or runs.
    sent = proxy.sent[0]
    assert sent["messages"][0]["content"].startswith("You are QA Engineer. You read an epic")
    assert [t["function"]["name"] for t in sent["tools"]] == [
        *("view", "search", "find", "tests_for", "git", "done")
    ]


def test_a_steps_own_check_refuses_done_in_the_session(main, seen):  # noqa: F811
    def check(answer: PanelAnswer) -> None:
        if answer.nothing_to_add:
            raise ValueError("Refused: a member with nothing to add read no file.")

    proxy = Proxy(
        said(("done", {"nothing_to_add": True, "notes": []})),
        said(("done", {"nothing_to_add": True, "notes": []})),
        said(content="I'll stop."),
        said(content="Still stopping."),
        said(content="Stopped."),
    )
    with pytest.raises(steps.StepStopped, match="no_tool_call"):
        run_step(main, proxy, check=check)
    assert "a member with nothing to add read no file" in proxy.sent[1]["messages"][-1]["content"]


def test_every_call_is_recorded_with_the_step_named_and_the_answer_by_its_shape(main, seen):  # noqa: F811
    note = {"problem": "p", "source": "src/app.py:2", "settle": "s", "settled_by": "architect"}
    proxy = Proxy(
        said(("search", {"pattern": "return", "path": NOTE})),
        said(("done", {"nothing_to_add": False, "notes": [note]})),
    )
    run_step(main, proxy)
    started = kinds(seen, EventKind.SESSION_STARTED)[0]
    assert started.detail["step"] == "panel qa_engineer" and started.role == "QA Engineer"
    assert started.detail["start_chars"]["total"] > 0
    assert started.detail["limits"]["backstop_s"] == steps.BACKSTOP_S == 7200
    assert all(
        e.detail.get("step") == "panel qa_engineer" for e in kinds(seen, EventKind.SESSION_TURN)
    )
    tool = kinds(seen, EventKind.TOOL_FINISHED)[0]
    assert (tool.detail["tool"], tool.detail["step"]) == ("search", "panel qa_engineer")
    ended = kinds(seen, EventKind.SESSION_ENDED)[0]
    assert ended.detail["said"] == {"nothing_to_add": False, "notes": 1}
    assert "src/app.py:2" not in json.dumps(ended.detail)


def test_a_failed_model_call_is_raised_as_it_was(main, seen):  # noqa: F811
    from crew_org.calls import ServiceUnavailableError

    with pytest.raises(ServiceUnavailableError):
        run_step(main, Proxy(ServiceUnavailableError("down")))


def test_with_no_checkout_a_step_cant_be_worked():
    with pytest.raises(steps.NoCheckout):
        steps.run(step(), {"step": "x"}, None, about="x")


def test_a_form_with_every_field_defaulted_is_taken_with_a_nested_form_as_text(main, seen):  # noqa: F811
    """sprint-metrics#667's settle passed its whole `conclusion` as JSON text, to a form
    with no required field, and the session crashed on it (crew#807)."""
    from crew_org.crews.settle_crew import Settlement

    conclusion = {"dismissed": [{"note": 1, "why": "Already settled by the Goal's D1"}]}
    proxy = Proxy(said(("done", {"conclusion": json.dumps(conclusion)})))
    answer = run_step(main, proxy, form=Settlement)
    assert isinstance(answer, Settlement) and answer.conclusion.dismissed[0].note == 1


def test_a_failure_handling_done_is_a_reply_and_the_session_goes_on(main, seen, monkeypatch):  # noqa: F811
    from crew_org.session import conversation

    calls = []

    def broken(name, args, accepted=None):
        calls.append(name)
        if len(calls) == 1:
            raise KeyError("required")

    monkeypatch.setattr(conversation, "check_arguments", broken)
    proxy = Proxy(
        said(("done", {"nothing_to_add": True, "notes": []})),
        said(("done", {"nothing_to_add": True, "notes": []})),
    )
    assert run_step(main, proxy).nothing_to_add
    failed = proxy.sent[1]["messages"][-1]
    assert failed["role"] == "tool" and "The tool failed: KeyError" in failed["content"]
