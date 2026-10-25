"""QA's Tasks: its tests, written in qa-suite, each in its project's folder (crew#896)."""

from __future__ import annotations

from crew_org.flows import role_tasks
from crew_org.flows.role_tasks import QA, SUITE, outside_scope, role_of
from crew_org.tools.github_project import Card
from crew_org.work_types import TASK


def _qa_task(number: int = 1, project: str = "sprint-metrics", **update) -> Card:
    labels = {"role:qa", *([role_tasks.project_label(project)] if project else [])}
    return Card(
        item_id=f"I{number}",
        number=number,
        title=f"QA {number}",
        state="OPEN",
        work_type=TASK,
        labels=frozenset(labels),
        repo=SUITE,
        **update,
    )


def test_qa_has_its_session():
    assert QA.has_session


def test_a_qa_task_may_change_only_its_projects_folder_in_qa_suite():
    role = role_of(_qa_task())
    assert role is not None and role.key == "qa"
    assert role.scope_for(SUITE) == ("sprint-metrics/*",)
    assert outside_scope(role, ["sprint-metrics/test_api.py"], SUITE) == []
    assert outside_scope(
        role, ["crew-presentation/test_page.py", "README.md", ".github/workflows/qa.yml"], SUITE
    ) == ["crew-presentation/test_page.py", "README.md", ".github/workflows/qa.yml"]


def test_a_qa_task_naming_no_project_may_change_nothing():
    role = role_of(_qa_task(project=""))
    assert role is not None
    assert outside_scope(role, ["sprint-metrics/test_api.py"], SUITE) == [
        "sprint-metrics/test_api.py"
    ]


def test_qa_changes_nothing_in_a_projects_own_repository():
    role = role_of(_qa_task().model_copy(update={"repo": "sprint-metrics"}))
    assert role is not None
    assert outside_scope(role, ["tests/test_x.py"], "sprint-metrics") == ["tests/test_x.py"]


def test_its_tests_are_in_its_scope_so_done_asks_for_them():
    role = role_of(_qa_task())
    assert role is not None and role_tasks.covers_tests(role, SUITE)
    assert not role_tasks.covers_tests(QA, "sprint-metrics")


# --- opening the test Tasks ---------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, READY, REVIEWING  # noqa: E402
from crew_org.flows import qa_tasks, session_end  # noqa: E402
from crew_org.flows.ci import CI_MARKER  # noqa: E402
from crew_org.flows.role_tasks import marked  # noqa: E402
from crew_org.work_types import BUG, STORY  # noqa: E402
from tests.role_task_fakes import Board, Issues, events, sink  # noqa: E402

MERGED = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class Suite(Issues):
    """Issues across a project and qa-suite: numbers are kept apart by the tests."""

    owner = "mqucifer"

    def __init__(self) -> None:
        super().__init__()
        self.files["sm/README.md"] = "# sm\n\n**The surface:** its HTTP API."
        self.pulls: dict[int, dict] = {}

    def pull(self, repo: str, number: int) -> dict:
        return self.pulls[number]

    def pull_for_branch(self, repo: str, branch: str, known=None) -> dict | None:
        return next(iter(self.pulls.values()), None)

    def has_comment_marked(self, repo: str, number: int, marker: str) -> bool:
        return any(marker in (c.get("body") or "") for c in self.comments(repo, number))


class SuiteBoard(Board):
    def __init__(self, cards=()) -> None:
        super().__init__()
        self._cards = list(cards)

    def cards(self):
        return self._cards


def _merged(number: int, work_type: str = STORY, **update) -> Card:
    fields = {
        "item_id": f"I{number}",
        "number": number,
        "title": f"Work {number}",
        "status": DONE,
        "state": "CLOSED",
        "work_type": work_type,
        "repo": "sm",
        "closed": MERGED,
    }
    return Card(**{**fields, **update})


def _open(issues, board, s, cards, repos=frozenset({"sm", SUITE})):
    return qa_tasks.open_tests(issues, board, s, cards=cards, repos=set(repos))


def test_a_merged_story_opens_qa_s_test_task_in_qa_suite_once(tmp_path):
    issues, board, s = Suite(), SuiteBoard(), sink(tmp_path)
    issues.add(5, "**As a** reader\n\n- [ ] GET /range counts it", title="Count a card")
    result = _open(issues, board, s, [_merged(5)])
    assert result.opened == [("sm#5", 901)]
    task = issues.issues[901]
    found = marked(task["body"])
    assert (found.role.key, found.kind, found.key) == ("qa", qa_tasks.STORY_TEST, "sm-s5")
    assert {lab["name"] for lab in task["labels"]} == {"role:qa", "project:sm"}
    body = task["body"]
    assert "mqucifer/sm#5" in body and "GET /range counts it" in body
    assert "`sm/README.md`" in body and "`.project/sm/`" in body and "`no_test`" in body
    assert ("status", "item-N901", READY) in board.done
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert opened[0]["detail"]["project"] == "sm"

    assert _open(issues, board, s, [_merged(5)]).opened == []
    assert len(issues.issues) == 2


def test_an_escaped_bugs_fix_opens_the_test_that_would_have_caught_it(tmp_path):
    issues, board, s = Suite(), SuiteBoard(), sink(tmp_path)
    issues.add(6, "Throughput counts a card twice")
    _open(issues, board, s, [_merged(6, BUG)])
    found = marked(issues.issues[901]["body"])
    assert (found.kind, found.key) == (qa_tasks.ESCAPED, "sm-b6")
    assert "would have caught" in issues.issues[901]["title"]


@pytest.mark.parametrize(
    "card",
    [
        _merged(7, closed=datetime(2026, 10, 20, tzinfo=UTC)),
        _merged(7, state_reason="NOT_PLANNED"),
        _merged(7, status=IN_PROGRESS, state="OPEN"),
        _merged(7, TASK),
        _merged(7, repo="other"),
    ],
)
def test_only_merged_stories_and_bugs_since_it_was_built_open_one(tmp_path, card):
    issues = Suite()
    issues.add(7)
    assert _open(issues, SuiteBoard(), sink(tmp_path), [card]).opened == []


def test_a_project_with_no_folder_in_qa_suite_opens_none(tmp_path):
    issues = Suite()
    issues.files.clear()
    issues.add(5)
    assert _open(issues, SuiteBoard(), sink(tmp_path), [_merged(5)]).opened == []


def test_a_task_github_refused_is_said_and_opened_next_pass(tmp_path):
    issues, board, s = Suite(), SuiteBoard(), sink(tmp_path)
    issues.add(5)
    issues.fail = "create"
    result = _open(issues, board, s, [_merged(5)])
    assert result.opened == [] and "not opened" in result.failed[0][1]
    issues.fail = None
    assert _open(issues, board, s, [_merged(5)]).opened == [("sm#5", 901)]


# --- QA's `done`, and the Task closed with no change -----------------------------------


def _done(kind: str) -> dict:
    specs = role_tasks.done_specs(role_of(_qa_task()), (), SUITE, kind)
    return next(s for s in specs if s["function"]["name"] == "done")["function"]


def test_qa_s_done_offers_no_test_for_a_story_and_justified_for_a_dispute():
    story = _done(qa_tasks.STORY_TEST)["parameters"]["properties"]
    dispute = _done(qa_tasks.DISPUTE)["parameters"]["properties"]
    assert "no_test" in story and "justified" not in story
    assert "justified" in dispute and "no_test" not in dispute
    assert "a test that proves it" in _done(qa_tasks.STORY_TEST)["description"]


def _ending(**said):
    from crew_org.session.conversation import Ending

    return Ending(how="done", said={"criteria": [], "docs": "", "summary": "s", **said})


@pytest.fixture
def unchanged(monkeypatch):
    from crew_org.tools import regression, reproduction

    changed: list[str] = []
    monkeypatch.setattr(regression, "merged_base", lambda worktree: type("M", (), {"sha": "m"}))
    monkeypatch.setattr(reproduction, "_changed", lambda worktree, sha: list(changed))
    return changed


def _route(ending, card, issues, board, s, tmp_path):
    return session_end.route(
        ending,
        card=card,
        repo=SUITE,
        worktree=tmp_path,
        story="",
        record=None,
        board=board,
        issues=issues,
        sink=s,
    )


def test_no_test_closes_qa_s_task_with_no_pull_request_and_is_recorded(tmp_path, unchanged):
    issues, board, s = Suite(), SuiteBoard(), sink(tmp_path)
    body = role_tasks.render(
        QA, kind=qa_tasks.STORY_TEST, key="sm-s5", why="w", work="x", done_when=[]
    )
    issues.add(901, body)
    card = _qa_task(901, project="sm", status=IN_PROGRESS)
    routed = _route(_ending(no_test="It changes only a log line"), card, issues, board, s, tmp_path)
    assert (routed.to, routed.rule) == (session_end.SETTLED, "decision.qa_tasks.no_test")
    assert issues.issues[901]["state"] == "closed"
    said = issues.comments_on[901][0]["body"]
    assert said.startswith(qa_tasks.NO_TEST_MARKER) and "a log line" in said
    assert ("status", "I901", DONE) in board.done
    settled = [e for e in events(tmp_path) if e["kind"] == "role_task.settled"]
    assert settled[0]["detail"] == {
        "repo": SUITE,
        "task_role": "QA Engineer",
        "task_kind": qa_tasks.STORY_TEST,
        "project": "sm",
        "rule": "decision.qa_tasks.no_test",
    }


def test_no_test_on_a_branch_that_changes_files_goes_back_to_its_session(tmp_path, unchanged):
    issues, board, s = Suite(), SuiteBoard(), sink(tmp_path)
    issues.add(901, "")
    unchanged.append("sm/test_api.py")
    routed = _route(_ending(no_test="none"), _qa_task(901), issues, board, s, tmp_path)
    assert (routed.to, routed.rule) == (session_end.SESSION, "check.qa_tasks.no_change_changed")
    assert "`sm/test_api.py`" in routed.start
    assert issues.issues[901]["state"] == "open"


# --- the dispute ------------------------------------------------------------------------

TEST = "sm/test_api.py::test_a_finished_card_counts_in_its_sprint"
TEST_FILE = "def test_a_finished_card_counts_in_its_sprint():\n    assert True\n"
HEAD = "abc1234def"


def _story(number: int = 5, status: str = IN_PROGRESS, **update) -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title="Count a card",
        status=status,
        state="OPEN",
        work_type=STORY,
        repo="sm",
        **update,
    )


def _disputing(tmp_path):
    issues, board, s = Suite(), SuiteBoard(), sink(tmp_path)
    issues.files["sm/test_api.py"] = TEST_FILE
    issues.add(5, "the story")
    issues.pulls[40] = {"number": 40, "head": {"sha": HEAD, "ref": "feat/5-count-a-card"}}
    issues.comments_on[40] = [
        {
            "body": f"{CI_MARKER.format(head=HEAD)}\n**CI failed** "
            f"`{qa_tasks.QA_CHECK}` did not pass."
        }
    ]
    return issues, board, s


def _blocked(**said):
    from crew_org.session.conversation import Ending

    return Ending(how="blocked", said={"kind": "test", **said})


def test_qa_s_failing_check_on_the_head_makes_the_story_disputable(tmp_path):
    issues, _, _ = _disputing(tmp_path)
    assert qa_tasks.disputable(issues, "sm", issues.pulls[40])
    issues.comments_on[40] = [{"body": f"{CI_MARKER.format(head=HEAD)}\n`tests` did not pass."}]
    assert not qa_tasks.disputable(issues, "sm", issues.pulls[40])
    assert not qa_tasks.disputable(issues, "sm", None)


def test_the_developer_may_dispute_through_blocked():
    blocked = next(s for s in qa_tasks.dispute_specs() if s["function"]["name"] == "blocked")
    properties = blocked["function"]["parameters"]["properties"]
    assert "test" in properties["kind"]["enum"] and "test" in properties


def test_a_dispute_opens_qa_s_task_and_the_story_waits_on_it(tmp_path):
    issues, board, s = _disputing(tmp_path)
    story = _story()
    routed = session_end.route(
        _blocked(test=TEST, missing="The sprint has no cards, so throughput is 0"),
        card=story,
        repo="sm",
        worktree=tmp_path,
        story="",
        record=None,
        board=board,
        issues=issues,
        sink=s,
    )
    assert (routed.to, routed.rule) == (session_end.DISPUTED, "decision.qa_tasks.disputed")
    task = issues.issues[901]
    found = marked(task["body"])
    assert found.kind == qa_tasks.DISPUTE and found.key.startswith("sm-d5-")
    assert {lab["name"] for lab in task["labels"]} == {"role:qa", "project:sm"}
    assert "throughput is 0" in task["body"] and qa_tasks.QA_CHECK in task["body"]
    assert qa_tasks.reference_ref(task["body"]) == HEAD
    assert ("status", "I5", BLOCKED) in board.done
    assert ("label", 5, qa_tasks.WAITS_LABEL) in issues.done
    assert qa_tasks.WAITS.format(task=901) in issues.comments_on[5][0]["body"]


def test_a_dispute_naming_no_test_of_qa_s_goes_back_to_the_session(tmp_path):
    issues, board, s = _disputing(tmp_path)
    for test in ("sm/test_api.py::test_nothing", "other/test_x.py::test_a", "", "sm/test_api.py"):
        found = qa_tasks.dispute(
            issues, board, s, card=_story(), repo="sm", pull=issues.pulls[40], said={"test": test}
        )
        assert found.rule == "check.qa_tasks.dispute_names_no_test" and found.start
    assert len(issues.issues) == 1


def _settled_justified(issues, task: int) -> None:
    issues.issues[task]["state"] = "closed"
    issues.comments_on[task] = [{"body": f"{qa_tasks.JUSTIFIED_MARKER}\nThe API says so."}]


def test_a_test_already_justified_ends_already_justified_with_its_link(tmp_path):
    issues, board, s = _disputing(tmp_path)
    said = {"test": TEST, "missing": "wrong"}
    qa_tasks.dispute(issues, board, s, card=_story(), repo="sm", pull=issues.pulls[40], said=said)
    _settled_justified(issues, 901)
    again = qa_tasks.dispute(
        issues, board, s, card=_story(), repo="sm", pull=issues.pulls[40], said=said
    )
    assert again.rule == "decision.qa_tasks.already_justified" and again.task == 901
    assert "mqucifer/qa-suite#901" in again.start and "The API says so." in again.start
    assert len(issues.issues) == 2


def test_a_test_qa_changed_since_its_justification_is_a_new_dispute(tmp_path):
    issues, board, s = _disputing(tmp_path)
    said = {"test": TEST, "missing": "wrong"}
    qa_tasks.dispute(issues, board, s, card=_story(), repo="sm", pull=issues.pulls[40], said=said)
    _settled_justified(issues, 901)
    issues.files["sm/test_api.py"] = TEST_FILE + "\n# changed\n"
    again = qa_tasks.dispute(
        issues, board, s, card=_story(), repo="sm", pull=issues.pulls[40], said=said
    )
    assert again.rule == "decision.qa_tasks.disputed" and again.task == 902


def _waiting(tmp_path):
    issues, board, s = _disputing(tmp_path)
    story = _story()
    qa_tasks.dispute(
        issues, board, s, card=story, repo="sm", pull=issues.pulls[40], said={"test": TEST}
    )
    waiting = _story(status=BLOCKED, labels=frozenset({qa_tasks.WAITS_LABEL}))
    return issues, SuiteBoard([waiting]), s


class Ws:
    def __init__(self) -> None:
        self.reran: list[str] = []

    def for_repo(self, repo):
        return self

    def open(self, branch, resume=False):
        self.reran.append(branch)
        return "path"

    def commit(self, message, allow_empty=False):
        assert allow_empty
        return True

    def push(self):
        pass

    def close(self, path):
        pass


def _settle(issues, board, s, ws=None):
    return qa_tasks.settle_disputes(
        issues, board, s, ws or Ws(), cards=board.cards(), repos={"sm", SUITE}
    )


def test_a_story_waits_while_qa_s_dispute_task_is_open(tmp_path):
    issues, board, s = _waiting(tmp_path)
    result = _settle(issues, board, s)
    assert result.waiting == [(5, 901)] and not board.done


def test_justified_returns_the_story_to_the_developer_with_the_test_unchanged(tmp_path):
    issues, board, s = _waiting(tmp_path)
    _settled_justified(issues, 901)
    ws = Ws()
    result = _settle(issues, board, s, ws)
    assert result.returned == [5] and ws.reran == []
    assert ("status", "I5", IN_PROGRESS) in board.done
    answer = issues.comments_on[40][-1]["body"]
    # As CI's verdict on the same head, so the rework reads QA's why with the log.
    assert answer.startswith(CI_MARKER.format(head=HEAD))
    assert "unchanged" in answer and "The API says so." in answer
    assert ("unlabel", 5, qa_tasks.WAITS_LABEL) in issues.done
    from crew_org.flows.ci import latest_ci_verdict

    assert latest_ci_verdict(issues, "sm", 40) == answer


def test_a_fixed_test_runs_the_story_s_checks_again_and_sends_it_to_review(tmp_path):
    issues, board, s = _waiting(tmp_path)
    issues.issues[901]["state"] = "closed"
    ws = Ws()
    result = _settle(issues, board, s, ws)
    assert result.rerun == [5] and ws.reran == ["feat/5-count-a-card"]
    assert ("status", "I5", REVIEWING) in board.done
    assert "changed the disputed test" in issues.comments_on[40][-1]["body"]


def test_a_settled_dispute_is_answered_once_however_often_it_s_retried(tmp_path):
    issues, board, s = _waiting(tmp_path)
    _settled_justified(issues, 901)
    _settle(issues, board, s)
    _settle(issues, board, s)
    answers = [c for c in issues.comments_on[40] if qa_tasks.ANSWERED.format(task=901) in c["body"]]
    assert len(answers) == 1


def test_a_waiting_story_isn_t_a_person_s_to_unblock():
    from crew_org.flows.standup import waiting_on_a_person

    waiting = _story(status=BLOCKED, labels=frozenset({qa_tasks.WAITS_LABEL}))
    assert waiting_on_a_person([waiting, _story(6, status=BLOCKED)]) == ["#6"]


# --- the project, beside QA's folder ---------------------------------------------------


def _git(*args, cwd):
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_qa_s_session_reads_its_project_beside_its_folder_and_never_commits_it(tmp_path):
    project, suite = tmp_path / "sm", tmp_path / "suite"
    for repo in (project, suite):
        repo.mkdir()
        _git("init", "-q", cwd=repo)
        _git(
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
            cwd=repo,
        )
    (project / "docs").mkdir()
    (project / "docs" / "service.md").write_text("GET /range\n")
    _git("add", "-A", cwd=project)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "docs", cwd=project)

    class Clone:
        def for_repo(self, repo):
            return self

        def current(self):
            return project

    qa_tasks.place_reference(Clone(), suite, "sm")
    assert (suite / ".project" / "sm" / "docs" / "service.md").read_text() == "GET /range\n"
    import subprocess

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=suite, capture_output=True, text=True, check=True
    ).stdout
    assert status == ""
    qa_tasks.place_reference(Clone(), suite, "sm")  # again: replaced, still excluded once
    exclude = (suite / ".git" / "info" / "exclude").read_text()
    assert exclude.count("/.project/") == 1
