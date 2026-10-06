"""A rebuild waits for the story still changing the files it conflicted in (#436).

sprint-metrics#384 was rebuilt, landed behind a sibling in another epic, conflicted in the
same test file again and was rebuilt a second time. Rebuilding while that other story's
pull request was still open could only repeat it.
"""

from __future__ import annotations

from crew_org.flows.delivery import rebuild_waits_for
from crew_org.flows.merge import REBUILD_FILES_MARKER, REBUILD_MARKER, rebuild_files
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"


def card(number=384, status="In Progress", title="Trend table"):
    return Card(
        item_id=f"C{number}",
        number=number,
        title=title,
        status=status,
        state="OPEN",
        work_type="Story",
        repo=REPO,
    )


def rebuilt(head, files):
    return {
        "body": f"{REBUILD_MARKER.format(head=head)}\n"
        + (REBUILD_FILES_MARKER.format(files=",".join(files)) + "\n" if files else "")
        + "**Conflicts with `main`, returned for a rebuild.**"
    }


class Github:
    def __init__(self, *, mine_files=("tests/test_crew_performance.py",), others=None, fail=False):
        self.fail = fail
        self.pulls = {405: {"number": 405, "head": {"sha": "h405", "ref": "feat/384-trend-table"}}}
        self.comments_on = {405: [rebuilt("h405", list(mine_files))]}
        self.files = {}
        for number, ref, files, waiting in others or []:
            self.pulls[number] = {"number": number, "head": {"sha": f"h{number}", "ref": ref}}
            self.files[number] = files
            self.comments_on[number] = [rebuilt(f"h{number}", ["x"])] if waiting else []

    def pull_for_branch(self, repo, branch, known=None):
        if self.fail:
            raise RuntimeError("GitHub is down")
        return self.pulls[405] if branch.startswith("feat/384-") else None

    def comments(self, repo, number):
        return self.comments_on.get(number, [])

    def open_pulls(self, repo):
        return list(self.pulls.values())

    def pull_files(self, repo, number):
        return self.files[number]


SHARED = ["tests/test_crew_performance.py", "src/report.py"]


def test_a_rebuild_waits_while_another_story_changes_the_same_files():
    gh = Github(others=[(410, "feat/382-sprint-range", SHARED, False)])
    assert rebuild_waits_for(gh, [card()], card(), repo=REPO) == (
        410,
        ["tests/test_crew_performance.py"],
    )


def test_a_rebuild_goes_ahead_when_no_open_story_touches_its_files():
    gh = Github(others=[(410, "feat/382-sprint-range", ["src/other.py"], False)])
    assert rebuild_waits_for(gh, [card()], card(), repo=REPO) is None


def test_a_pull_request_that_is_not_a_crew_story_holds_nothing():
    gh = Github(others=[(411, "dependabot/pip/ruff-0.7", SHARED, False)])
    assert rebuild_waits_for(gh, [card()], card(), repo=REPO) is None


def test_two_rebuilds_never_wait_on_each_other():
    gh = Github(others=[(410, "feat/382-sprint-range", SHARED, True)])
    assert rebuild_waits_for(gh, [card()], card(), repo=REPO) is None


def test_a_blocked_story_holds_nothing():
    gh = Github(others=[(410, "feat/382-sprint-range", SHARED, False)])
    cards = [card(), card(382, status="Blocked", title="Sprint range")]
    assert rebuild_waits_for(gh, cards, card(), repo=REPO) is None


def test_a_rebuild_that_names_no_files_and_a_failure_both_go_ahead():
    assert rebuild_waits_for(Github(mine_files=()), [card()], card(), repo=REPO) is None
    assert rebuild_waits_for(Github(fail=True), [card()], card(), repo=REPO) is None


def test_the_rebuild_comment_carries_its_files_for_delivery_to_read():
    from crew_org.events import EventSink
    from crew_org.flows.merge import merge_approved
    from crew_org.git_ops import MergeConflict
    from tests.test_merge import FakeBoard, FakeIssues
    from tests.test_merge import card as merging_card

    def keep_both(repo, branch):
        raise MergeConflict(SHARED)

    issues = FakeIssues(mergeable_state="dirty")
    merge_approved(
        FakeBoard(),
        issues,
        EventSink(None),
        cards=[merging_card(6)],
        default_repo=REPO,
        keep_both=keep_both,
    )
    (_, body) = issues.posted[0]
    assert rebuild_files(body) == SHARED
    assert rebuild_files("no marker here") == []
