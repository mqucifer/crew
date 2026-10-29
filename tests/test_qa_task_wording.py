"""A QA Task's session is told about its Task, not a story, and QA isn't the Developer
(crew#944).

mqucifer/qa-suite#8's session opened with "## The story", listed "The tests the story
declares", and was sent back with "CI failed before review: returned to the Developer"
and "this story was sent back to be re-worked".
"""

from __future__ import annotations

import re

from crew_org.events import EventSink
from crew_org.flows import ci, delivery, session_delivery
from crew_org.session.start import start
from crew_org.tools.github_project import Card
from crew_org.work_types import STORY, SUITE, TASK
from tests.test_merge import FakeBoard, FakeIssues


def _card(work_type: str = TASK) -> Card:
    return Card(
        item_id="I8",
        number=8,
        title="Disputed: crew-presentation/test_reads_crew.py::test_a_sprints_page",
        status="Reviewing",
        state="OPEN",
        work_type=work_type,
        labels=frozenset({"role:qa", "project:crew-presentation"})
        if work_type == TASK
        else frozenset(),
        repo=SUITE if work_type == TASK else "crew-presentation",
    )


def test_a_task_s_session_opens_with_the_task_and_its_tests(tmp_path):
    opening, _, _ = start(tmp_path, "the Task's body", None, work="Task")
    assert "## The Task\n\nthe Task's body" in opening
    assert "## The tests the Task declares" in opening
    assert "story" not in opening.split("## The map")[0].lower()
    # A story's is as it was.
    story, _, _ = start(tmp_path, "the story's body", None)
    assert "## The story\n\n" in story and "## The tests the story declares" in story


def _returned(card: Card) -> str:
    issues = FakeIssues()
    ci.return_for_ci(
        FakeBoard(),
        issues,
        EventSink(None),
        card,
        repo=card.repo,
        pull={"number": 10, "head": {"sha": "abc"}},
        failed=[{"name": "suite / qa", "conclusion": "failure"}],
        where="before review",
    )
    return issues.posted[-1][1]


def test_ci_returns_a_qa_task_to_qa_not_the_developer():
    said = _returned(_card())
    assert "returned to the QA Engineer" in said and "Developer" not in said
    assert "returned to the Developer" in _returned(_card(STORY))


class Pulls:
    def __init__(self, state: str = "open") -> None:
        self.state = state

    def pulls_for_branch(self, repo, branch, state="all"):
        return [{"number": 10, "state": self.state}]


def test_an_open_pull_request_s_fate_names_the_task():
    fate = delivery.previous_fate(Pulls(), SUITE, "task/8", work="Task")
    assert fate == "PR #10 is still open, and this Task was sent back to be re-worked."


def test_a_returned_task_s_session_says_the_task_came_back(tmp_path, monkeypatch):
    monkeypatch.setattr(session_delivery, "prior_verdicts", lambda *a, **k: "CI said no")
    monkeypatch.setattr(session_delivery, "last_done_on", lambda *a: "")

    class Issues(Pulls):
        def pull_for_branch(self, repo, branch, known=None):
            return None

    told, _, _ = session_delivery.returned_start(
        Issues(), SUITE, 8, "task/8", tmp_path, known=_card()
    )
    assert "Review or CI sent the Task back" in told and "## What review or CI said" in told
    assert "story" not in told and "QA" not in told


# --- CI's log, in whole lines (crew#944) -------------------------------------------------


def test_a_cut_log_keeps_whole_lines_and_says_how_many_were_left_out():
    log = "\n".join(f"line {n}: returned to the Developer." for n in range(1, 101))
    shown = ci.last_lines(log, 200)
    first, *rest = shown.splitlines()
    assert first.startswith("(") and first.endswith("earlier lines left out)")
    assert all(line.startswith("line ") and line.endswith("Developer.") for line in rest)
    assert rest[-1] == "line 100: returned to the Developer."
    assert first == f"({100 - len(rest)} earlier lines left out)"
    assert len("\n".join(rest)) <= 200


def test_a_short_log_is_shown_whole():
    assert ci.last_lines("one\ntwo", 200) == "one\ntwo"


def test_a_dispute_task_shows_ci_s_log_from_a_line_s_start(tmp_path):
    from crew_org.flows import qa_tasks
    from crew_org.flows.ci import CI_MARKER
    from tests.test_qa_tasks import HEAD, TEST, _disputing, _story

    issues, board, s = _disputing(tmp_path)
    long = "\n".join(f"step {n} returned to the Developer." for n in range(400))
    issues.comments_on[40] = [
        {"body": f"{CI_MARKER.format(head=HEAD)}\n`{qa_tasks.QA_CHECK}` did not pass.\n{long}"}
    ]
    qa_tasks.dispute(
        issues, board, s, card=_story(), repo="sm", pull=issues.pulls[40], said={"test": TEST}
    )
    section = issues.issues[901]["body"].split("## What CI said\n\n", 1)[1]
    assert section.splitlines()[0].endswith("earlier lines left out)")
    assert section.splitlines()[1].startswith("step ")


# --- the session start, rendered for the review (crew#944) ------------------------------


def test_a_test_task_s_session_start_renders_without_a_task_or_a_model(tmp_path):
    from tests.test_qa_tasks import Suite

    issues = Suite()
    issues.add(5, "**As a** reader\n\n- [ ] GET /range counts it", title="Count a card")
    (tmp_path / "sm").mkdir()
    (tmp_path / "sm" / "README.md").write_text("# sm\n\n**The surface:** its HTTP API.\n")
    (tmp_path / ".project" / "sm").mkdir(parents=True)
    shown = session_delivery.test_task_start(
        issues, project="sm", number=5, worktree=tmp_path, today="2026-10-29"
    )
    system = shown.split("## System prompt\n\n", 1)[1].split("\n\n## First message", 1)[0]
    first = shown.split("## First message\n\n", 1)[1].split("\n\n## Tools", 1)[0]
    assert "Write and keep QA's functional tests" in system
    assert "You do not fix the code" not in system
    assert first.startswith(
        "## The Task\n\nToday is 2026-10-29. This is the QA Engineer's own Task"
    )
    assert "The test of sm#5: Count a card" in first and "GET /range counts it" in first
    # The Task's own heading; the story QA tests is the Task's section about it.
    assert "## The story\n" not in first and "## The story as it merged" in first
    assert "`no_test`" in shown
    assert "- `done`" in shown
    # Nothing was opened.
    assert set(issues.issues) == {5}


def test_a_bug_s_fix_renders_the_test_that_would_have_caught_it(tmp_path):
    from tests.test_qa_tasks import Suite

    issues = Suite()
    issues.add(6, "Throughput counts a card twice", title="Counted twice")
    shown = session_delivery.test_task_start(
        issues, project="sm", number=6, worktree=tmp_path, bug=True
    )
    assert "kind=escaped-bug" in shown and "would have caught sm#6" in shown


def test_a_pull_request_is_refused_for_the_story_it_closes(tmp_path):
    import pytest

    from tests.test_qa_tasks import Suite

    issues = Suite()
    issues.add(200, "Closes #189")
    issues.issues[200]["pull_request"] = {"url": "u"}
    with pytest.raises(ValueError, match="is a pull request"):
        session_delivery.test_task_start(issues, project="sm", number=200, worktree=tmp_path)


def test_how_a_task_s_session_works_speaks_to_the_task_and_where_blocked_goes(tmp_path):
    opening, _, _ = start(tmp_path, "the Task's body", None, work="Task")
    rules = opening.split("## How this session works", 1)[1]
    assert "You work on the Task by calling tools" in rules
    assert "Change only what the Task needs" in rules
    assert "a fact the Task doesn't give" in rules
    assert "it goes to a person: the Task is blocked, with your words on it." in rules
    assert not re.search(r"\bstory\b", rules)
    story, _, _ = start(tmp_path, "the story's body", None)
    assert "it goes back to whoever wrote the story." in story


def test_a_blocked_qa_task_goes_to_a_person_not_to_a_story_s_author(tmp_path):
    from crew_org.flows import session_end
    from crew_org.session.conversation import Ending
    from tests.role_task_fakes import sink
    from tests.test_qa_tasks import Suite, SuiteBoard, _qa_task

    issues, board = Suite(), SuiteBoard()
    issues.add(1, "a test Task")
    task = _qa_task(1, parent=None)
    routed = session_end.route(
        Ending(how="blocked", said={"kind": "missing", "missing": "no build to read"}),
        card=task,
        repo=SUITE,
        worktree=tmp_path,
        story="",
        record=None,
        board=board,
        issues=issues,
        sink=sink(tmp_path),
    )
    assert routed.to == session_end.BLOCKED_FOR_A_PERSON
