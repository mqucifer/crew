"""`crew onboard` (#130): the interview, the take-away file, and the pull request.

The Product Owner is replaced by a scripted turn, so what is tested is what the
flow does with its answers: when it asks again, when it stops, what it writes.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from crew_org.crews.onboarding_crew import (
    Answers,
    DoneAnswers,
    Question,
    ReleaseAnswers,
    ScopeAnswers,
    Turn,
    turn_description,
)
from crew_org.flows.onboard import (
    ANSWER,
    CONFIRM,
    ISSUE_TITLE,
    QUESTIONS,
    describe,
    describe_intent,
    interview,
    merge,
    open_record_pr,
    read_project,
    release_line,
    takeaway,
    terminal_ask,
)
from crew_org.git_ops import ProtectedBranchError
from crew_org.project import RECORD_PATH, gaps, load_raw, parse, render, validate

PURPOSE = ("intent", "scope", "purpose")
DEPLOYS = ("intent", "release", "deploys")
CHECKS = ("intent", "done", "checks")

COMPLETE = {
    "intent": {
        "scope": {"purpose": "Report how the crew performs"},
        "release": {"deploys": False},
        "done": {"checks": ["pytest"]},
    }
}


def answers(**sections) -> Answers:
    return Answers(**sections)


class Script:
    """A Product Owner that plays back turns, and records what it was shown."""

    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.shown: list[dict] = []

    def __call__(self, **context) -> Turn:
        self.shown.append(context)
        return self.turns.pop(0)


class Sponsor:
    def __init__(self, *replies: str | None) -> None:
        self.replies = list(replies)
        self.asked: list[str] = []

    def __call__(self, prompt: str) -> str | None:
        self.asked.append(prompt)
        return self.replies.pop(0)


def told() -> tuple[list[str], callable]:
    out: list[str] = []
    return out, out.append


# --- 1 and 2: propose from what exists, or ask -----------------------------------


def test_a_project_with_content_is_described_with_its_ci_and_protection(tmp_path: Path):
    (tmp_path / "README.md").write_text("# sprint-metrics\nReports how the crew performs.")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("jobs:\n  tests:\n    run: uv run pytest\n")

    seen = describe(
        tmp_path,
        branch="main",
        protection={"required_status_checks": {"contexts": ["tests"]}},
    )

    assert "Reports how the crew performs." in seen
    assert "uv run pytest" in seen
    assert "Required checks: tests" in seen


def test_the_product_owner_proposes_when_there_is_content():
    prompt = turn_description(
        repository="### README.md\n\nA tool.",
        intent="",
        draft="",
        missing={},
        problems=[],
        conversation="",
    )
    assert "**propose**" in prompt
    assert "A tool." in prompt


def test_an_empty_repository_describes_as_nothing(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    assert describe(tmp_path, branch="main", protection=None) == ""


def test_a_repository_with_no_commits_is_read_as_empty():
    class NoBranches:
        def branches(self, repo):
            return []

        def open_issues(self, repo):
            return [{"number": 1, "title": "Goal: a tool", "body": "Make it.", "labels": []}]

    class NeverCloned:
        def current(self):
            raise AssertionError("an empty repository has nothing to clone")

    project = read_project(NeverCloned(), NoBranches(), "new-thing")
    assert project.repository == "" and project.default_branch is None
    # A project can be asked for before it has code, and the asking is still shown.
    assert "Goal: a tool" in project.intent


def test_the_product_owner_asks_when_there_is_nothing_to_read():
    prompt = turn_description(
        repository="",
        intent="",
        draft="(nothing yet)",
        missing={"intent.scope.purpose": "what the project is for (purpose)"},
        problems=[],
        conversation="",
    )
    assert "**ask**" in prompt and "**propose**" not in prompt
    assert "`intent.scope.purpose`: what the project is for (purpose)" in prompt


def test_the_interview_runs_until_every_required_answer_is_settled():
    po = Script(
        Turn(answers=answers(), say="Tell me about it."),
        Turn(
            answers=answers(
                scope=ScopeAnswers(purpose="Report how the crew performs"),
                release=ReleaseAnswers(deploys=False),
                done=DoneAnswers(checks=["pytest"]),
            ),
            say="Got it.",
        ),
    )
    sponsor = Sponsor("A metrics tool for the crew; no deploy; pytest must pass.", "yes")
    _, tell = told()

    ended = interview({}, repository="", turn=po, ask=sponsor, tell=tell)

    assert ended.settled
    assert validate(ended.raw).release_is == "the merge"
    assert len(po.shown) == 2


# --- 3: a pull request, never a push ---------------------------------------------


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.opened: str | None = None
        self.pushed = False
        self.committed = ""

    def for_repo(self, repo):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def open(self, branch):
        from crew_org.git_ops import assert_writable, validate_branch_name

        assert_writable(branch)
        validate_branch_name(branch)
        self.opened = branch
        return self.root

    def commit(self, message):
        self.committed = message
        return True

    def push(self, *, force=False):
        self.pushed = True


class FakeIssues:
    def __init__(self, *, open_issues=(), open_pulls=()) -> None:
        self._issues = list(open_issues)
        self._pulls = list(open_pulls)
        self.created: list[str] = []
        self.pulls: list[dict] = []
        self.comments: list[tuple[int, str]] = []

    def comment(self, repo, number, body):
        self.comments.append((number, body))

    def open_issues(self, repo):
        return self._issues

    def create(self, repo, title, body, labels=None):
        self.created.append(title)
        return {"number": 7}

    def open_pulls(self, repo):
        return self._pulls

    def create_pull(self, repo, **kwargs):
        self.pulls.append(kwargs)
        return {"html_url": "https://github.com/o/r/pull/8"}


def test_the_record_arrives_as_a_pull_request(tmp_path: Path):
    ws, issues = FakeWorkspace(tmp_path), FakeIssues()

    url = open_record_pr(ws, issues, "sprint-metrics", validate(COMPLETE), base="main")

    assert url.endswith("/pull/8")
    assert ws.opened == "chore/7-project-record" and ws.pushed
    assert parse((tmp_path / RECORD_PATH).read_text()) == validate(COMPLETE)
    (pull,) = issues.pulls
    assert pull["base"] == "main" and pull["head"] == "chore/7-project-record"
    assert "Closes #7" in pull["body"]
    assert issues.created == [ISSUE_TITLE]


def test_running_again_updates_the_same_pull_request(tmp_path: Path):
    ws = FakeWorkspace(tmp_path)
    issues = FakeIssues(
        open_issues=[{"number": 7, "title": ISSUE_TITLE}],
        open_pulls=[{"head": {"ref": "chore/7-project-record"}, "html_url": "u/pull/8"}],
    )

    assert open_record_pr(ws, issues, "r", validate(COMPLETE), base="main") == "u/pull/8"
    assert issues.created == [] and issues.pulls == []
    assert ws.pushed


def test_the_workspace_refuses_to_write_the_default_branch(tmp_path: Path):
    with pytest.raises(ProtectedBranchError):
        FakeWorkspace(tmp_path).open("main")


# --- 4: an answer that does not settle it is asked about again ------------------


def test_an_unsettled_answer_is_asked_about_again_specifically():
    po = Script(
        Turn(
            answers=answers(scope=ScopeAnswers(purpose="Report how the crew performs")),
            say="Noted the purpose.",
            questions=[
                Question(about="intent.release.deploys", question="Does it ship anywhere?"),
            ],
        ),
        Turn(answers=answers(), say="I still need the rest."),
    )
    sponsor = Sponsor("It reports metrics.", "Not sure yet.", "later")
    said, tell = told()

    ended = interview({}, repository="", turn=po, ask=sponsor, tell=tell)

    assert not ended.settled
    # The second turn is shown exactly what is still missing, and nothing settled.
    assert set(po.shown[1]["missing"]) == {"intent.release.deploys", "intent.done.checks"}
    # Its own question is used where it asked one, the standing one where it did not.
    assert "Does it ship anywhere?" in said[0]
    assert QUESTIONS[CHECKS] in said[1]


def test_an_answer_settled_earlier_is_kept_when_a_turn_leaves_it_out():
    kept = merge(COMPLETE, {"intent": {"scope": {"in_scope": ["cycle time"]}}})
    assert kept["intent"]["scope"] == {
        "purpose": "Report how the crew performs",
        "in_scope": ["cycle time"],
    }
    assert merge(COMPLETE, {"intent": {"done": None}})["intent"]["done"] == {"checks": ["pytest"]}


def test_a_correction_at_the_confirmation_goes_back_to_the_product_owner():
    po = Script(
        Turn(answers=answers(done=DoneAnswers(checks=["uv run pytest"])), say="Changed it."),
    )
    sponsor = Sponsor("the check is uv run pytest", "yes")
    _, tell = told()

    ended = interview(COMPLETE, repository="", turn=po, ask=sponsor, tell=tell)

    assert ended.settled
    assert ended.raw["intent"]["done"]["checks"] == ["uv run pytest"]


# --- 5: stopping early leaves a file to finish offline ----------------------------


def test_stopping_early_leaves_a_takeaway_with_the_questions_still_open(tmp_path: Path):
    po = Script(
        Turn(
            answers=answers(scope=ScopeAnswers(purpose="Report how the crew performs")),
            say="Noted.",
            questions=[Question(about="intent.done.checks", question="What must pass?")],
        )
    )
    sponsor = Sponsor("later")
    _, tell = told()

    ended = interview({}, repository="", turn=po, ask=sponsor, tell=tell)
    text = takeaway(ended.raw, ended.questions, repo="r", path=tmp_path / "r.yaml")

    assert not ended.settled
    assert "purpose: Report how the crew performs" in text
    assert "# Required. What must pass?" in text
    assert f"# Required. {QUESTIONS[DEPLOYS]}" in text
    assert "crew onboard r --from" in text
    # What was answered reads back; what was not is still missing, by name.
    raw = load_raw(text)
    assert raw["intent"]["scope"]["purpose"] == "Report how the crew performs"
    assert set(gaps(raw)) == {DEPLOYS, CHECKS}


def test_ending_the_session_is_the_same_as_later():
    po = Script(Turn(answers=answers(), say="What is it for?"))
    ended = interview({}, repository="", turn=po, ask=Sponsor(None), tell=told()[1])
    assert not ended.settled and set(ended.questions) == {PURPOSE, DEPLOYS, CHECKS}


def test_a_failed_turn_keeps_the_answers_so_far():
    def broken(**context):
        raise RuntimeError("proxy went away")

    raw = {"intent": {"scope": {"purpose": "p"}}}
    ended = interview(raw, repository="", turn=broken, ask=Sponsor(), tell=told()[1])

    assert ended.interrupted == "proxy went away"
    assert ended.raw == raw and set(ended.questions) == {DEPLOYS, CHECKS}


def test_a_takeaway_finished_by_hand_is_a_complete_record(tmp_path: Path):
    text = takeaway({}, {}, repo="r", path=tmp_path / "r.yaml")
    finished = (
        text.replace("    # purpose:", "    purpose: A tool")
        .replace("    # deploys:", "    deploys: false")
        .replace("    # checks: []", "    checks: [pytest]")
    )
    assert parse(finished).intent.scope.purpose == "A tool"


# --- 6: carrying on from a take-away file -----------------------------------------


def test_carrying_on_asks_only_about_what_is_still_missing():
    partial = {"intent": {"scope": {"purpose": "p"}, "release": {"deploys": False}}}
    po = Script(
        Turn(answers=answers(), say="What must pass?"),
        Turn(answers=answers(done=DoneAnswers(checks=["pytest"])), say="Thanks."),
    )
    sponsor = Sponsor("pytest", "yes")

    ended = interview(partial, repository="", turn=po, ask=sponsor, tell=told()[1])

    assert po.shown[0]["missing"] == {
        "intent.done.checks": "the checks a change must pass to be done"
    }
    assert ended.settled


def test_a_finished_takeaway_goes_straight_to_the_sponsors_yes():
    po = Script()  # would fail if it were called
    sponsor = Sponsor("yes")

    ended = interview(COMPLETE, repository="", turn=po, ask=sponsor, tell=told()[1])

    assert ended.settled and po.shown == []
    assert sponsor.asked == ["Open the pull request with this record? yes, or say what to change"]


def test_a_takeaway_with_a_wrong_type_is_put_to_the_product_owner():
    wrong = merge(COMPLETE, {"intent": {"priority": "high"}})
    po = Script(
        Turn(answers=answers(), say="Priority is a number against other projects."),
        Turn(answers=answers(priority=1), say="Priority 1, then."),
    )
    sponsor = Sponsor("first", "yes")

    ended = interview(wrong, repository="", turn=po, ask=sponsor, tell=told()[1])

    assert any("priority" in p for p in po.shown[0]["problems"])
    assert ended.settled and ended.raw["intent"]["priority"] == 1


# --- #136: an interview, not a transcription --------------------------------------


def test_the_product_owner_is_shown_the_projects_goals_first():
    intent = describe_intent(
        [
            {"number": 49, "title": "Report a previous sprint", "body": "Story.", "labels": []},
            {
                "number": 43,
                "title": "Goal: the tool remembers",
                "body": "Past sprints, not only this one.",
                "labels": [{"name": "goal"}],
            },
        ]
    )
    assert intent.index("#43 Goal: the tool remembers [goal]") < intent.index("#49")
    assert "Past sprints, not only this one." in intent

    prompt = turn_description(
        repository="code", intent=intent, draft="", missing={}, problems=[], conversation=""
    )
    assert "## What the project has been asked for" in prompt and "Past sprints" in prompt


def test_questions_about_a_filled_answer_keep_the_interview_going():
    partial = {"intent": {"release": {"deploys": False}, "done": {"checks": ["pytest"]}}}
    po = Script(
        Turn(
            answers=answers(scope=ScopeAnswers(purpose="Current-sprint metrics")),
            say="Noted, but goal #43 asks for past sprints.",
            questions=[Question(about="intent.scope.purpose", question="Past sprints too?")],
        ),
        Turn(answers=answers(scope=ScopeAnswers(purpose="Sprints, past and present")), say="Ok."),
    )
    sponsor = Sponsor("yes, past ones too", "yes")
    said, tell = told()

    ended = interview(partial, repository="", turn=po, ask=sponsor, tell=tell)

    # Every required field held something after the first turn, and the
    # Sponsor was still asked the question rather than offered the record.
    assert sponsor.asked == [ANSWER, CONFIRM]
    assert "Past sprints too?" in said[0]
    assert ended.settled and ended.raw["intent"]["scope"]["purpose"] == "Sprints, past and present"


def test_saying_it_is_fine_goes_back_to_the_product_owner():
    partial = {"intent": {"release": {"deploys": False}, "done": {"checks": ["pytest"]}}}
    po = Script(
        Turn(
            answers=answers(scope=ScopeAnswers(purpose="Metrics")),
            say="Is pytest enough to call a change done?",
            questions=[Question(about="intent.done.checks", question="Is pytest enough?")],
        ),
        Turn(answers=answers(), say="Understood, leaving it as pytest."),
    )
    sponsor = Sponsor("that's fine as it is", "yes")

    ended = interview(partial, repository="", turn=po, ask=sponsor, tell=told()[1])

    assert "**Sponsor:** that's fine as it is" in po.shown[1]["conversation"]
    assert sponsor.asked == [ANSWER, CONFIRM]
    assert ended.settled


def test_done_goes_straight_to_the_record_past_the_product_owners_questions():
    partial = {"intent": {"release": {"deploys": False}, "done": {"checks": ["pytest"]}}}
    po = Script(
        Turn(
            answers=answers(scope=ScopeAnswers(purpose="Metrics")),
            say="Anything out of scope?",
            questions=[Question(about="intent.scope.out_of_scope", question="Out of scope?")],
        )
    )
    sponsor = Sponsor("done", "yes")

    ended = interview(partial, repository="", turn=po, ask=sponsor, tell=told()[1])

    assert sponsor.asked == [ANSWER, CONFIRM]
    assert len(po.shown) == 1 and ended.settled


def test_done_cannot_skip_a_required_answer():
    po = Script(
        Turn(answers=answers(), say="What is it for?"),
        Turn(
            answers=answers(
                scope=ScopeAnswers(purpose="Metrics"),
                release=ReleaseAnswers(deploys=False),
                done=DoneAnswers(checks=["pytest"]),
            ),
            say="Thanks.",
        ),
    )
    sponsor = Sponsor("done", "metrics; no deploy; pytest", "yes")
    said, tell = told()

    ended = interview({}, repository="", turn=po, ask=sponsor, tell=tell)

    assert sponsor.asked == [ANSWER, ANSWER, CONFIRM]
    assert "can't be written without" in said[1] and "(purpose)" in said[1]
    assert len(po.shown) == 2 and ended.settled


def test_an_unanswered_optional_answer_is_left_out_not_written_empty():
    text = render(validate(COMPLETE))
    written = load_raw(text)
    assert "learned" not in written
    assert set(written["intent"]["scope"]) == {"purpose"}
    assert set(written["intent"]["done"]) == {"checks"}
    assert "[]" not in text
    assert parse(text) == validate(COMPLETE)


def test_the_transcript_is_kept_however_the_interview_ends():
    po = Script(Turn(answers=answers(), say="What is it for?"))
    stopped = interview({}, repository="", turn=po, ask=Sponsor("later"), tell=told()[1])
    assert stopped.transcript[0].startswith("**Product Owner:** What is it for?")
    assert stopped.transcript[1] == "**Sponsor:** later"

    confirmed = interview(
        COMPLETE, repository="", turn=Script(), ask=Sponsor("yes"), tell=told()[1]
    )
    assert confirmed.transcript == ["**Sponsor:** yes"]


def test_the_pull_request_carries_the_interview(tmp_path: Path):
    issues = FakeIssues()
    open_record_pr(
        FakeWorkspace(tmp_path),
        issues,
        "r",
        validate(COMPLETE),
        base="main",
        transcript=["**Product Owner:** What is it for?", "**Sponsor:** Metrics."],
    )
    body = issues.pulls[0]["body"]
    assert "<details><summary>The interview</summary>" in body
    assert "**Sponsor:** Metrics." in body


def test_an_updated_pull_request_gets_the_new_interview_as_a_comment(tmp_path: Path):
    issues = FakeIssues(
        open_issues=[{"number": 7, "title": ISSUE_TITLE}],
        open_pulls=[
            {"number": 8, "head": {"ref": "chore/7-project-record"}, "html_url": "u/pull/8"}
        ],
    )
    open_record_pr(
        FakeWorkspace(tmp_path), issues, "r", validate(COMPLETE), base="main", transcript=["x"]
    )
    ((number, body),) = issues.comments
    assert number == 8 and "The interview" in body


@pytest.mark.parametrize(
    "release, line",
    [
        ({"deploys": False}, "the merge. Nothing is deployed."),
        (
            {"deploys": True, "where": "A version tag", "how": "Tag vX.Y.Z."},
            "a deployment. Where: A version tag How: Tag vX.Y.Z.",
        ),
    ],
)
def test_the_release_reads_as_its_own_sentence(release, line):
    record = validate(merge(COMPLETE, {"intent": {"release": release}}))
    assert release_line(record) == line


# --- #135: the answer prompt edits like a terminal ---------------------------------


def test_the_answer_prompt_reads_piped_input(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("metrics for the crew\n"))
    assert terminal_ask("Your answer") == "metrics for the crew"


def test_the_end_of_input_ends_the_interview(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert terminal_ask("Your answer") is None


def test_the_prompt_is_given_to_input_with_its_styling_marked_invisible(monkeypatch):
    seen = []
    monkeypatch.setattr("builtins.input", lambda prompt: seen.append(prompt) or "ok")
    terminal_ask("Your answer")
    (prompt,) = seen
    # readline counts only what is outside \001…\002, so the wrap point is right.
    visible = re.sub("\001.*?\002", "", prompt)
    assert visible == "\nYour answer › "
