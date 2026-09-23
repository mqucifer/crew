"""QA judges behaviour criterion by criterion, and parents close themselves.

The rule that matters: a story cannot be accepted while any criterion is
unproven. "The suite passes" is a different claim from "every criterion is
proven", and conflating them is how Definition of Done quietly erodes.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict
from crew_org.crews.retro_crew import ProcessDefect
from crew_org.events import EventSink
from crew_org.flows.acceptance import (
    DONE,
    awaiting_qa,
    close_finished_parents,
    render_qa,
    run_qa,
)
from crew_org.tools.github_project import Card


def criterion(
    proven: bool = True, name: str = "empty sprint shows unavailable"
) -> CriterionVerdict:
    return CriterionVerdict(
        criterion=name,
        proven=proven,
        evidence="test_empty_sprint_reports_unavailable exercises it directly",
    )


# --- the acceptance rule -------------------------------------------------


def test_a_story_cannot_be_accepted_with_an_unproven_criterion():
    with pytest.raises(ValidationError, match="unproven criteria"):
        QAVerdict(summary="s", accepted=True, criteria=[criterion(True), criterion(False)])


def test_rejection_with_unproven_criteria_is_fine():
    verdict = QAVerdict(summary="s", accepted=False, criteria=[criterion(False)])
    assert len(verdict.unproven) == 1


def test_acceptance_with_every_criterion_proven_is_fine():
    assert QAVerdict(summary="s", accepted=True, criteria=[criterion(), criterion()]).accepted


def test_evidence_must_name_the_test():
    with pytest.raises(ValidationError, match="not evidence"):
        CriterionVerdict(criterion="x", proven=True, evidence="tested")


def test_a_verdict_needs_criteria_to_judge():
    with pytest.raises(ValidationError, match="list them"):
        QAVerdict(summary="s", accepted=False, criteria=[])


def test_the_qa_comment_shows_what_was_not_proven():
    body = render_qa(QAVerdict(summary="s", accepted=False, criteria=[criterion(False)]))
    assert "not proven" in body
    assert "test_empty_sprint_reports_unavailable" in body


# --- what QA is shown ----------------------------------------------------


def test_every_test_function_survives_collection(tmp_path):
    """Story #13 was returned as unproven against two tests that were in the
    file. A 12,000-character slice cut them off the end — new tests are
    appended, so a head-slice lands on exactly the evidence QA needs."""
    from crew_org.flows.acceptance import collect_tests

    (tmp_path / "tests").mkdir()
    body = "\n\n".join(f"def test_case_{i}():\n    assert {i} == {i}" for i in range(400))
    (tmp_path / "tests/test_big.py").write_text(body)

    collected = collect_tests(tmp_path)
    assert len(body) > 12_000, "the fixture has to be big enough to have been cut"
    for i in range(400):
        assert f"def test_case_{i}()" in collected


def test_evidence_that_does_not_fit_refuses_a_verdict(tmp_path):
    """QAVerdict has no way to say "I could not see enough to tell", so a
    partial view has to resolve to proven or unproven and both are false. The
    run fails loudly instead, and says which file it got to."""
    import pytest as _pytest

    from crew_org.flows import acceptance

    # Sized off the ceiling, so raising the guard does not quietly stop this
    # from testing the guard. Two files, each just over half of it.
    filler_lines = acceptance.QA_CONTEXT_CHAR_CEILING // 8
    (tmp_path / "tests").mkdir()
    for name in ("a", "b"):
        (tmp_path / f"tests/test_{name}.py").write_text(
            f"MARKER_{name} = 1\n" + f"# {name}\n" * filler_lines
        )

    with _pytest.raises(acceptance.EvidenceTooLarge, match="tests/test_b.py"):
        acceptance.collect_tests(tmp_path)


def test_the_end_of_a_run_is_what_survives(tmp_path):
    """The summary and the failures are at the end. Delivery already learned
    that keeping the front of a failure report keeps the wrong half."""
    from dataclasses import dataclass

    from crew_org.flows.acceptance import QA_OUTPUT_CHAR_CEILING, collect_output

    @dataclass
    class Result:
        command: str
        output: str

    results = [Result("pytest -q", "x" * (QA_OUTPUT_CHAR_CEILING + 5_000) + "FAILED test_last")]
    collected = collect_output(results)

    assert "FAILED test_last" in collected
    assert "earlier output trimmed" in collected


# --- a verdict belongs to a revision -------------------------------------


def test_a_verdict_is_scoped_to_the_commit_it_judged():
    """`has_comment_marked` matches any comment carrying the bare marker, so the
    rejection QA itself wrote permanently disqualified the card: the Developer
    repaired, the card came back, and QA skipped it in silence — for good, while
    the command printed "Nothing awaiting QA"."""
    from crew_org.flows.acceptance import qa_marker

    assert qa_marker("a" * 40) != qa_marker("b" * 40)
    assert qa_marker("abcdef1234567890") == qa_marker("abcdef123456")


def test_a_rendered_verdict_carries_both_markers():
    """The bare marker keeps every verdict findable — a repair reads it back as
    prior context — and the revision one says which commit it judged."""
    from crew_org.flows.acceptance import QA_MARKER, qa_marker, render_qa

    verdict = QAVerdict(summary="s", accepted=False, criteria=[criterion(False)])
    body = render_qa(verdict, revision="deadbeefcafe")

    assert QA_MARKER in body
    assert qa_marker("deadbeefcafe") in body


def test_a_verdict_rendered_without_a_revision_still_works():
    """Nothing should depend on a revision being available to say what it found."""
    from crew_org.flows.acceptance import QA_MARKER, render_qa

    verdict = QAVerdict(summary="s", accepted=False, criteria=[criterion(False)])
    body = render_qa(verdict)

    assert QA_MARKER in body
    assert not re.search(r"<!-- crew:qa [0-9a-f]+ -->", body), "no revision claimed"


# --- selection -----------------------------------------------------------


def story(number: int, status: str, work_type: str = "Story") -> Card:
    return Card(
        item_id=f"C{number}",
        number=number,
        title=f"Card {number}",
        status=status,
        state="OPEN",
        work_type=work_type,
        repo="sprint-metrics",
    )


def test_qa_leaves_a_card_outside_the_crews_repositories_alone():
    """Nothing is opened for it: no worktree, no sandbox, which is why both can
    be None here — reaching either would raise."""
    own = story(9, "QAing").model_copy(update={"repo": "crew"})
    result = run_qa(
        None, None, EventSink(None), None, None, cards=[own], repo="r", repos={"sprint-metrics"}
    )
    assert not (result.verified or result.returned or result.failed)


def test_only_stories_awaiting_qa_are_verified():
    cards = [story(6, "QAing"), story(7, "Sprint Backlog"), story(3, "QAing", "Epic")]
    assert [c.number for c in awaiting_qa(cards)] == [6]


# --- parents close themselves -------------------------------------------


class FakeBoard:
    def __init__(self):
        self.moves = []
        self.owners = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        self.owners.append((item_id, role))


class FakeIssues:
    def __init__(self, children):
        self._children = children
        self.closed: list[tuple[str, int]] = []

    def sub_issues(self, repo, number):
        return self._children.get(number, [])

    def close(self, repo, number, *, reason="completed"):
        self.closed.append((repo, number))


def test_an_epic_closes_when_all_its_stories_are_done():
    cards = [story(3, "Needs Refinement", "Epic"), story(6, DONE), story(7, DONE)]
    issues = FakeIssues({3: [{"number": 6}, {"number": 7}]})
    board = FakeBoard()
    closed = close_finished_parents(board, issues, EventSink(None), cards, repo="r")
    assert closed == [3]
    assert ("C3", DONE) in board.moves
    assert issues.closed == [("sprint-metrics", 3)]


def test_a_parent_already_in_done_still_has_its_issue_closed():
    """sprint-metrics#5: moved to Done on 2026-09-19 and left open, because
    closing a parent used to mean moving its card and nothing else."""
    cards = [story(5, DONE, "Epic"), story(33, DONE), story(34, DONE)]
    issues = FakeIssues({5: [{"number": 33}, {"number": 34}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == [5]
    assert board.moves == []
    assert issues.closed == [("sprint-metrics", 5)]


def test_a_child_is_matched_by_repository_as_well_as_number():
    """The board spans repositories. crew#33 being Done says nothing about
    sprint-metrics#33, and now that this closes issues the mix-up would close
    an epic that is not finished."""
    other_repo = story(33, DONE).model_copy(update={"repo": "crew", "item_id": "X33"})
    cards = [story(5, "Needs Refinement", "Epic"), story(33, "In Progress"), other_repo]
    url = "https://api.github.com/repos/mqucifer/sprint-metrics"
    issues = FakeIssues({5: [{"number": 33, "repository_url": url}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == []
    assert issues.closed == []


def test_an_open_child_off_the_board_keeps_the_epic_open():
    cards = [story(3, "Needs Refinement", "Epic"), story(6, DONE)]
    issues = FakeIssues({3: [{"number": 6}, {"number": 7, "state": "open"}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == []
    assert issues.closed == []


def test_a_closed_child_off_the_board_counts_as_finished():
    cards = [story(3, "Needs Refinement", "Epic"), story(6, DONE)]
    issues = FakeIssues({3: [{"number": 6}, {"number": 7, "state": "closed"}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == [3]


def test_a_closed_parent_is_left_alone():
    epic = story(3, DONE, "Epic").model_copy(update={"state": "CLOSED"})
    cards = [epic, story(6, DONE)]
    issues = FakeIssues({3: [{"number": 6}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == []
    assert issues.closed == []


def test_a_parent_closing_claims_the_card_for_nobody():
    """Bookkeeping, not judgement. An epic whose children are all done closes
    itself, so the card keeps whichever role last actually worked on it rather
    than being attributed to one that did not act."""
    cards = [story(3, "Needs Refinement", "Epic"), story(6, DONE), story(7, DONE)]
    issues = FakeIssues({3: [{"number": 6}, {"number": 7}]})
    board = FakeBoard()
    close_finished_parents(board, issues, EventSink(None), cards, repo="r")

    assert ("C3", DONE) in board.moves
    assert board.owners == []


def test_one_open_story_keeps_the_epic_open():
    """Close enough is not done."""
    cards = [story(3, "Needs Refinement", "Epic"), story(6, DONE), story(7, "Merging")]
    issues = FakeIssues({3: [{"number": 6}, {"number": 7}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == []
    assert board.moves == []
    assert issues.closed == []


def test_a_parent_with_no_children_is_left_alone():
    cards = [story(3, "Needs Refinement", "Epic")]
    board = FakeBoard()
    assert close_finished_parents(board, FakeIssues({}), EventSink(None), cards, repo="r") == []


def test_a_parent_outside_the_crews_repositories_is_not_closed():
    epic = story(3, "Needs Refinement", "Epic").model_copy(update={"repo": "crew"})
    issues = FakeIssues({3: [{"number": 6}]})
    board = FakeBoard()
    cards = [epic, story(6, DONE)]
    assert (
        close_finished_parents(
            board, issues, EventSink(None), cards, repo="r", repos={"sprint-metrics"}
        )
        == []
    )
    assert issues.closed == [] and board.moves == []


def test_a_goal_closes_when_its_epics_are_done():
    cards = [story(1, "Inbox (Goals)", "Goal"), story(3, DONE, "Epic")]
    issues = FakeIssues({1: [{"number": 3}]})
    board = FakeBoard()
    assert close_finished_parents(board, issues, EventSink(None), cards, repo="r") == [1]


# --- the retro will not ask for a bigger budget -------------------------


@pytest.mark.parametrize(
    "change",
    [
        "increase the escalation budget to 6",
        "raise the budget for escalations",
        "allow more escalation budget next sprint",
    ],
)
def test_the_retro_cannot_propose_a_larger_escalation_budget(change):
    """Escalation rate is a symptom of task design. Treating it as a budget
    problem is how the discipline erodes."""
    with pytest.raises(ValidationError, match="not a process improvement"):
        ProcessDefect(subject="S1", problem="three escalations", change=change)


def test_a_real_design_change_is_accepted():
    defect = ProcessDefect(
        subject="#6",
        problem="acceptance criteria were ambiguous about empty sprints",
        change="require a worked example with concrete numbers in every criterion",
    )
    assert "worked example" in defect.change


# --- a card is verified where it lives -----------------------------------


class RepoSpy:
    """Records which repository each call was made against."""

    def __init__(self):
        self.asked: list[tuple[str, str, int]] = []

    def has_comment_marked(self, repo, number, marker):
        self.asked.append(("marker", repo, number))
        return True  # skip, so the test needs no model or sandbox

    def get(self, repo, number):
        self.asked.append(("get", repo, number))
        return {"body": "As a Sponsor…"}


class WorkspaceSpy:
    def __init__(self):
        self.repos: list[str] = []
        self.repo = "sprint-metrics"

    def for_repo(self, repo):
        self.repos.append(repo)
        return self

    def open_existing(self, branch):
        return None

    def head(self):
        return "a" * 40

    def close(self):
        pass


def test_a_card_is_verified_in_its_own_repository():
    """QA read the story, checked its marker and posted its verdict against the
    default repo whatever card it was judging. Delivery has always used
    `card.repo or repo`; QA was the one flow that did not."""
    from crew_org.flows.acceptance import run_qa

    issues, ws = RepoSpy(), WorkspaceSpy()
    cards = [story(6, "QAing").model_copy(update={"repo": "crew"})]
    run_qa(FakeBoard(), issues, EventSink(None), ws, None, cards=cards, repo="sprint-metrics")

    assert ws.repos == ["crew"], "the worktree comes from the card's repository"
    assert all(r == "crew" for _, r, _ in issues.asked), issues.asked


def test_a_card_naming_no_repository_falls_back():
    """`repo` is a fallback for a card that names none, never the answer."""
    from crew_org.flows.acceptance import run_qa

    issues, ws = RepoSpy(), WorkspaceSpy()
    cards = [story(6, "QAing").model_copy(update={"repo": None})]
    run_qa(FakeBoard(), issues, EventSink(None), ws, None, cards=cards, repo="sprint-metrics")

    assert ws.repos == ["sprint-metrics"]


def test_qa_is_shown_what_it_said_about_this_card_before(monkeypatch, tmp_path):
    """QA judged every attempt cold, so a story returned for one unproven
    criterion could come back for a different one nobody had mentioned — a
    target the Developer cannot hit."""
    from crew_org.flows import acceptance as flow

    seen = {}

    def fake_verify(story, *, test_output, test_code, prior_verdicts=""):
        seen["prior"] = prior_verdicts
        return QAVerdict(summary="ok", accepted=True, criteria=[criterion()])

    monkeypatch.setattr(flow, "verify_story", fake_verify)
    monkeypatch.setattr(flow.workspace, "check", lambda w, sandbox=None: _green())
    monkeypatch.setattr(flow, "collect_tests", lambda w: "def test_x(): pass")

    card = Card(
        item_id="C31",
        number=31,
        title="Scrape endpoint",
        status="QAing",
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
    )
    issues = _QAIssues(
        comments=[{"body": f"{flow.QA_MARKER}\ncriterion 2 is unproven"}],
    )
    flow.run_qa(
        _QABoard(),
        issues,
        EventSink(None),
        _QAWorkspace(tmp_path),
        None,
        cards=[card],
        repo="sprint-metrics",
    )
    assert "criterion 2 is unproven" in seen["prior"]


def _green():
    from crew_org.tools.workspace import CheckResult, CommandResult

    return CheckResult(results=[CommandResult(command="pytest", code=0, output="")])


class _QAIssues:
    def __init__(self, *, comments):
        self.owner = "mqucifer"
        self._comments = comments
        self.posted: list[tuple[int, str]] = []

    def comments(self, repo, number):
        return self._comments

    def get(self, repo, number):
        return {"body": "As a Sponsor…"}

    def has_comment_marked(self, repo, number, marker):
        return any(marker in (c.get("body") or "") for c in self._comments)

    def comment(self, repo, number, body):
        self.posted.append((number, body))

    def sub_issues(self, repo, number):
        return []


class _QABoard:
    def __init__(self):
        self.moves = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


class _QAWorkspace:
    def __init__(self, tmp):
        self.tmp = tmp

    def for_repo(self, repo):
        return self

    def open_existing(self, branch):
        path = self.tmp / branch.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def head(self):
        return "abc1234def56"

    def close(self, path=None):
        pass
