"""Landing approved work.

Merging only at sprint close meant every story branched from a main missing its
predecessors — six stories editing one module from six different starting
points, producing their conflicts all at once. Approval and merge timing are
therefore decoupled: the Sponsor's gate is the approval, and this lands what has
passed it before new work is claimed.
"""

from __future__ import annotations

from crew_org.events import EventKind, EventSink
from crew_org.flows.merge import REBUILD_MARKER, merge_approved, ready_to_land
from crew_org.tools.github_project import Card

APPROVED = [{"state": "APPROVED"}]
COMMENTED = [{"state": "COMMENTED"}]


def card(number: int, status: str = "Merging", repo: str = "sprint-metrics") -> Card:
    return Card(
        item_id=f"C{number}",
        number=number,
        title=f"Show metric {number}",
        status=status,
        state="OPEN",
        work_type="Story",
        repo=repo,
    )


class FakeBoard:
    def __init__(self):
        self.moves = []
        self.owners = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        self.owners.append((item_id, role))


class FakeIssues:
    def __init__(
        self, *, reviews=APPROVED, mergeable_state="clean", pull=True, decision="APPROVED"
    ):
        self.owner = "mqucifer"
        self._reviews, self._state, self._has_pull = reviews, mergeable_state, pull
        self._decision = decision
        self.merged, self.labels, self.posted = [], [], []
        # Comments already on the pull request, e.g. earlier rebuilds.
        self.earlier: list[str] = []

    def comments(self, repo, number):
        return [{"body": b} for b in self.earlier] + [
            {"body": b} for n, b in self.posted if n == number
        ]

    def review_decision(self, repo, number):
        return self._decision

    def pull_for_branch(self, repo, branch, *, known=None):

        if known is not None:
            return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
        return {"number": 100} if self._has_pull else None

    def pull(self, repo, number):
        return {"mergeable_state": self._state, "mergeable": self._state != "dirty"}

    def pull_reviews(self, repo, number):
        return self._reviews

    def merge_pull(self, repo, number, **kw):
        self.merged.append(number)
        return {}

    def add_labels(self, repo, number, labels):
        self.labels.extend((number, n) for n in labels)

    def comment(self, repo, number, body):
        self.posted.append((number, body))


def run(cards, issues, repos=None):
    board = FakeBoard()
    sink = EventSink(None)
    seen = []
    sink.subscribe(seen.append)
    result = merge_approved(
        board, issues, sink, cards=cards, default_repo="sprint-metrics", repos=repos
    )
    return result, board, seen


# --- selection -----------------------------------------------------------


def test_only_stories_that_passed_qa_are_landed():
    cards = [card(6), card(7, status="QAing"), card(8, status="Done")]
    assert [c.number for c in ready_to_land(cards)] == [6]


def test_the_merge_queue_is_ordered_by_the_analyst():
    """This was a bare comprehension over whatever order the board returned.
    Three pull requests from one epic, all touching the same file, merged out
    of order conflicts the remainder — which blocks those cards and labels them
    for a person, turning an ordering accident into work for a human."""
    cards = [card(32), card(30), card(31)]
    assert [c.number for c in ready_to_land(cards)] == [30, 31, 32]


def test_repositories_outside_the_allow_list_are_left_alone():
    cards = [card(6), card(20, repo="crew")]
    assert [c.number for c in ready_to_land(cards, {"sprint-metrics"})] == [6]


# --- landing -------------------------------------------------------------


def test_an_approved_story_is_merged_and_done():
    issues = FakeIssues()
    result, board, _ = run([card(6)], issues)
    assert issues.merged == [100]
    assert result.merged == [(6, 100)]
    assert ("C6", "Done") in board.moves


def test_an_unapproved_story_waits():
    """The Sponsor's approval is the gate; QA passing is not enough."""
    issues = FakeIssues(reviews=COMMENTED)
    result, board, _ = run([card(6)], issues)
    assert issues.merged == []
    assert result.awaiting_approval == [(6, 100)]
    assert board.moves == []


def test_a_story_with_no_pull_request_is_reported():
    result, _, _ = run([card(6)], FakeIssues(pull=False))
    assert result.failed == [(6, "no open pull request")]


# --- conflicts -----------------------------------------------------------


def test_an_approved_conflict_is_returned_for_the_crew_to_rebuild():
    """Decided 2026-09-25: rebuilding on main costs model time, not a person, and the
    gates run again on what's rebuilt. sprint-metrics #95 and #100 both hit this."""
    issues = FakeIssues(mergeable_state="dirty")
    result, board, _ = run([card(6)], issues)
    assert issues.merged == [] and result.conflicted == []
    assert result.rebuilding == [(6, 100)]
    assert ("C6", "In Progress") in board.moves
    assert not any(label == "needs:human" for _, label in issues.labels)
    (_, body) = issues.posted[0]
    assert REBUILD_MARKER.split("{")[0] in body and "review and QA again" in body


def rebuilt_twice() -> FakeIssues:
    issues = FakeIssues(mergeable_state="dirty")
    issues.earlier = [REBUILD_MARKER.format(head=h) for h in ("a", "b")]
    return issues


def test_past_the_rebuild_cap_a_conflict_blocks_the_card():
    """Parallel work can conflict a rebuilt pull request again; it doesn't loop."""
    issues = rebuilt_twice()
    result, board, _ = run([card(6)], issues)
    assert result.conflicted == [(6, 100)] and result.rebuilding == []
    assert ("C6", "Blocked") in board.moves


def test_a_conflict_past_the_cap_is_labelled_for_a_person():
    issues = rebuilt_twice()
    run([card(6)], issues)
    assert (6, "blocked") in issues.labels
    assert (6, "needs:human") in issues.labels


def test_a_conflict_past_the_cap_says_what_to_do_about_it():
    issues = rebuilt_twice()
    run([card(6)], issues)
    _number, body = issues.posted[0]
    assert "merge conflict" in body.lower()
    assert "re-delivered" in body or "Resolve the conflict" in body


def test_a_conflict_past_the_cap_is_visible_on_the_event_stream():
    _, _, seen = run([card(6)], rebuilt_twice())
    assert any(e.kind == EventKind.CARD_BLOCKED for e in seen)


def test_a_failed_merge_does_not_mark_the_card_done():
    class Refusing(FakeIssues):
        def merge_pull(self, repo, number, **kw):
            raise RuntimeError("base branch was modified")

    result, board, _ = run([card(6)], Refusing())
    assert result.merged == []
    assert ("C6", "Done") not in board.moves
    assert "base branch was modified" in result.failed[0][1]


# --- an approval that cannot arrive --------------------------------------


def test_an_approval_github_does_not_count_is_not_waiting():
    """The crew's reviewing identity approved it and branch protection still
    reports REVIEW_REQUIRED, because it only counts an approval from an actor
    with repository write access. That card is not slow — no tick will ever
    move it, and reporting it beside cards that are merely waiting is how
    sprint-metrics #31 and #32 sat in Merging for a whole tick unnoticed."""
    issues = FakeIssues(decision="REVIEW_REQUIRED")
    result, board, _ = run([card(6)], issues)
    assert issues.merged == []
    assert result.awaiting_approval == []
    assert result.unapprovable == [(6, 100)]
    assert ("C6", "Blocked") in board.moves


def test_an_approval_that_cannot_arrive_is_labelled_for_a_person():
    issues = FakeIssues(decision="REVIEW_REQUIRED")
    run([card(6)], issues)
    assert (6, "blocked") in issues.labels
    assert (6, "needs:human") in issues.labels


def test_an_approval_that_cannot_arrive_says_why_and_what_to_do():
    issues = FakeIssues(decision="REVIEW_REQUIRED")
    run([card(6)], issues)
    _number, body = issues.posted[0]
    assert "REVIEW_REQUIRED" in body
    assert "write access" in body
    assert "approve the pull request yourself" in body


def test_an_approval_that_cannot_arrive_is_visible_on_the_event_stream():
    issues = FakeIssues(decision="REVIEW_REQUIRED")
    _, _, seen = run([card(6)], issues)
    assert any(e.kind == EventKind.CARD_BLOCKED for e in seen)


def test_no_approving_review_is_still_ordinary_waiting():
    """The distinction only applies to a card GitHub refuses *despite* an
    approval. A card nobody has approved is waiting, as it always was."""
    issues = FakeIssues(reviews=COMMENTED, decision="REVIEW_REQUIRED")
    result, board, _ = run([card(6)], issues)
    assert result.awaiting_approval == [(6, 100)]
    assert result.unapprovable == []
    assert board.moves == []


def test_a_branch_with_no_review_requirement_still_merges():
    """`reviewDecision` is null when protection asks for no review at all."""
    issues = FakeIssues(decision=None)
    result, _, _ = run([card(6)], issues)
    assert result.merged == [(6, 100)]


# --- behind main, under protection that requires up to date (#116) ------------


class BehindIssues(FakeIssues):
    """PR #67 on 2026-09-23: approved, green, and behind `main`."""

    def __init__(self, *, update_error=None, **kw):
        super().__init__(mergeable_state="behind", **kw)
        self.updated: list[tuple[int, str | None]] = []
        self._update_error = update_error

    def pull(self, repo, number):
        return {"mergeable_state": "behind", "mergeable": True, "head": {"sha": "c0ffee"}}

    def update_branch(self, repo, number, *, head=None):
        if self._update_error is not None:
            raise self._update_error
        self.updated.append((number, head))


def test_a_pull_request_behind_main_is_brought_up_to_date_not_merged():
    """Criterion 1: merging it would be refused with a 405 about a status check."""
    issues = BehindIssues()
    result, board, _ = run([card(31)], issues)
    assert issues.merged == []
    assert issues.updated == [(100, "c0ffee")]
    assert result.updating == [(31, 100)]
    assert board.moves == []


def test_it_merges_on_the_next_pass_once_up_to_date():
    """Criterion 2: nothing new is needed; the next attempt finds it current."""
    issues = FakeIssues(mergeable_state="clean")
    result, _, _ = run([card(31)], issues)
    assert issues.merged == [100] and result.merged == [(31, 100)]


def test_an_update_that_conflicts_is_rebuilt_like_a_merge_conflict():
    """Criterion 3: never forced. Now rebuilt by the crew, as a merge conflict is."""
    from crew_org.tools.github_issues import BranchUpdateConflict

    issues = BehindIssues(update_error=BranchUpdateConflict("merge conflict between base and head"))
    result, board, _ = run([card(31)], issues)
    assert issues.merged == []
    assert result.rebuilding == [(31, 100)]
    assert ("C31", "In Progress") in board.moves


def test_an_update_that_fails_otherwise_is_reported_not_blocked():
    issues = BehindIssues(update_error=RuntimeError("502 Bad Gateway"))
    result, board, _ = run([card(31)], issues)
    assert board.moves == []
    assert result.failed and "could not update from main" in result.failed[0][1]
