"""The deliver phase around the Developer's session: what it claims, holds and moves.

The session itself is faked here (`session_delivery.deliver_by_session`): its own
tests are `test_session_delivery.py`. What's tested is the phase every Developer runs
inside: the WIP limit, the holds, the allow-list, the healing of a stranded card,
what a blocked card keeps, and a returned story claimed again (crew#778, ADR 0032).
"""

from __future__ import annotations

import pytest

from crew_org.events import EventKind, EventSink
from crew_org.flows import delivery, session_delivery
from crew_org.flows.delivery import DeliveryOutcome
from crew_org.git_ops import MergeConflict, branch_name
from crew_org.process import ProcessRules
from crew_org.tools.github_issues import QueueState
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, CommandResult

SPRINT = "S1"


def story(number: int = 6) -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"Show metric {number}",
        status="Sprint Backlog",
        state="OPEN",
        work_type="Story",
        sprint=SPRINT,
        points=3,
        repo="sprint-metrics",
    )


def green() -> CheckResult:
    return CheckResult(results=[CommandResult(command="pytest", code=0, output="")])


def red(output: str = "2 failed") -> CheckResult:
    return CheckResult(results=[CommandResult(command="pytest", code=1, output=output)])


class FakeBoard:
    def __init__(self, cards):
        self._cards, self.moves = cards, []
        self.owners = []

    def cards(self):
        return self._cards

    def counts(self, cards=None):
        out = {}
        for c in cards if cards is not None else self._cards:
            if c.status and c.work_type not in {"Goal", "Epic"}:
                out[c.status] = out.get(c.status, 0) + 1
        return out

    def repo_of(self, item_id):
        return None

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        self.owners.append((item_id, role))

    def clear_field(self, item_id, field):
        self.moves.append((item_id, f"cleared {field}"))


class FakeIssues:
    def __init__(self):
        self.owner, self.comments_, self.prs, self.labels = "o", [], [], []

    def get(self, repo, number):
        return {"body": "As a Sponsor…"}

    def open_pulls(self, repo):
        return []

    def closed_pulls(self, repo):
        return []

    def comment(self, repo, number, body):
        self.comments_.append((number, body))

    def add_labels(self, repo, number, labels):
        self.labels.extend((number, name) for name in labels)

    def create_pull(self, repo, *, title, head, base, body):
        self.prs.append(head)
        return {"number": 100 + len(self.prs)}

    # Set by a test that wants a card to have a pull request worth landing.
    landable: dict[str, dict] = {}

    def pull_for_branch(self, repo, branch, *, known=None):

        if known is not None:
            return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
        return self.landable.get(branch)

    def pull(self, repo, number):
        return {"mergeable_state": "clean", "mergeable": True}

    def failed_checks(self, repo, sha):
        return []

    def merge_group_failures(self, repo, pull):
        return []

    def queue_state(self, repo, number, *, branch):
        return QueueState()

    def pulls_for_branch(self, repo, branch, *, state="all"):
        return []

    def pull_reviews(self, repo, number):
        return [{"state": "APPROVED"}]

    def review_decision(self, repo, number):
        return "APPROVED"

    def sub_issues(self, repo, number):
        return [{"number": 6}]

    def branches(self, repo):
        return []

    def ensure_label(self, repo, name, *, color, description):
        self.labels.append((0, f"ensured {name}"))


class FakeWorkspace:
    # Set by a test that wants `open(resume=True)` to find prior work.
    unfinished = False

    def for_repo(self, repo):
        self.repos_asked.append(repo)
        return self

    def __init__(self, tmp):
        self.tmp, self.committed, self.pushed, self.closed = tmp, [], 0, 0
        self.forced = None
        self.repos_asked: list[str] = []
        # Whether `open` found a previous attempt on the branch to build on.
        self.resumed = False
        # Recorded in order so a test can prove the evidence was read BEFORE
        # the worktree was removed, which is the whole point of keeping it.
        self.events: list[str] = []

    def open(self, branch, *, resume=False):
        self.resumed = bool(resume) and self.unfinished
        path = self.tmp / branch.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text("[project]\nname='x'\n")
        return path

    def commit(self, message, *, allow_empty=False):
        self.committed.append(message)
        self.empty_commits = getattr(self, "empty_commits", 0) + int(allow_empty)
        return True

    # A resumed branch is brought up to date with main before the work (#119).
    # Set `conflict` to the paths a test wants the catch-up to conflict on.
    conflict: list[str] | None = None

    def catch_up(self):
        self.events.append("catch_up")
        if self.conflict is not None:
            raise MergeConflict(self.conflict)
        return True

    def push(self, *, force=False):
        self.forced = force
        self.pushed += 1

    def diff(self):
        return "diff --git a/mod.py b/mod.py\n+what the model wrote\n"

    def diff_if_open(self):
        self.events.append("diff")
        return self.diff()

    def close(self, path=None):
        self.events.append("close")
        self.closed += 1

    def forget(self, branch):
        self.forgotten = [*getattr(self, "forgotten", []), branch]


def opened(card: Card, *, ws, issues, rework: bool = False) -> DeliveryOutcome:
    """A session that reached `done` and passed the gates: pushed, and opened for review."""
    branch = branch_name(card.number or 0, card.title)
    ws.push(force=not rework)
    live = issues.pull_for_branch(card.repo, branch)
    number = (
        live["number"]
        if live
        else issues.create_pull(card.repo, title=card.title, head=branch, base="main", body="")[
            "number"
        ]
    )
    return DeliveryOutcome(card=card.number or 0, branch=branch, pr=number, attempts=1, ended=True)


def blocked(why: str = "the session was stopped: no progress"):
    """A delivery its session couldn't finish, for a person."""

    def developer(card: Card, **_) -> DeliveryOutcome:
        branch = branch_name(card.number or 0, card.title)
        return DeliveryOutcome(
            card=card.number or 0, branch=branch, blocked_reason=why, attempts=1, ended=True
        )

    return developer


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Everything the deliver phase needs, with the Developer's session faked out."""
    calls: dict = {"delivered": []}

    def make(*, developer=None, cards=None, limit=None, repos=None):
        board = FakeBoard(cards or [story()])
        issues = FakeIssues()
        ws = FakeWorkspace(tmp_path)

        def fake(card, *, ws, rework=False, **_):
            calls["delivered"].append((card.number, rework))
            ws.open(branch_name(card.number or 0, card.title), resume=rework)
            return (developer or opened)(card, ws=ws, issues=issues, rework=rework)

        monkeypatch.setattr(session_delivery, "deliver_by_session", fake)
        sink = EventSink(None)
        seen = []
        sink.subscribe(seen.append)
        org = {
            "board": {
                "columns": ["Sprint Backlog", "In Progress", "Reviewing", "QAing", "Done"],
                "blocked_column": "Blocked",
                "human_gates": [],
            },
            "wip_limits": {"In Progress": 3},
            "sprint": {"blocked_aging_days": 3},
        }
        result = delivery.deliver(
            board,
            issues,
            sink,
            ProcessRules.from_config(org),
            ws,
            sprint=SPRINT,
            repo="sprint-metrics",
            limit=limit,
            repos=repos,
        )
        return result, board, issues, ws, calls, seen

    return make


# --- a delivered story -----------------------------------------------------


def test_a_delivered_story_goes_to_review_with_its_pull_request(harness):
    result, board, _, ws, calls, _ = harness()
    assert len(result.delivered) == 1
    assert result.delivered[0].pr == 101
    assert calls["delivered"] == [(6, False)]
    assert ws.pushed == 1
    assert ("S6", "Reviewing") in board.moves


def test_the_card_moves_through_in_progress_first(harness):
    _, board, _, _, _, _ = harness()
    assert [m[1] for m in board.moves] == ["In Progress", "Reviewing"]


def test_a_story_that_cannot_be_finished_is_blocked_and_labelled(harness):
    result, board, issues, _, _, _ = harness(developer=blocked())
    assert len(result.blocked) == 1
    assert (6, "blocked") in issues.labels
    assert ("S6", "Blocked") in board.moves
    (said,) = [body for number, body in issues.comments_ if number == 6]
    assert "no progress" in said and "Sessions: 1." in said


def test_the_wip_limit_stops_the_loop(harness):
    """Three in progress is the limit; a fourth waits."""
    busy = [
        Card(
            item_id=f"P{i}",
            number=200 + i,
            title="wip",
            status="In Progress",
            state="OPEN",
            work_type="Story",
        )
        for i in range(3)
    ]
    result, board, _, _, calls, seen = harness(cards=[story(), *busy])
    assert result.delivered == []
    assert calls["delivered"] == []
    assert any("WIP limit" in e.summary for e in seen)


def test_the_worktree_is_always_closed(harness):
    """Even when delivery fails, the worktree must not be left behind."""
    _, _, _, ws, _, _ = harness(developer=blocked())
    assert ws.closed == 1


def test_an_ended_delivery_forgets_its_branch_s_checkpoints(harness):
    """Only an interrupted session leaves its checkpoint for the next pass (P3)."""
    _, _, _, ws, _, _ = harness(developer=blocked())
    assert ws.forgotten == ["feat/6-show-metric-6"]


# --- keeping the evidence of a failure -----------------------------------


def test_a_blocked_card_keeps_the_diff_that_failed(harness):
    """The worktree is the only copy of what the Developer wrote until it's pushed.
    Losing it means the card blocks with no record of why."""
    result, _, _, _, _, _ = harness(developer=blocked())
    assert result.blocked[0].rejected_diff is not None
    assert "what the model wrote" in result.blocked[0].rejected_diff


def test_the_evidence_is_read_before_the_worktree_is_removed(harness):
    """Ordering is the whole fix: a diff taken after close reads nothing."""
    _, _, _, ws, _, _ = harness(developer=blocked())
    assert ws.events == ["diff", "close"]


def test_a_card_that_succeeds_keeps_no_rejected_diff(harness):
    """`rejected_diff` means a failure. Setting it on success would make `ok`
    and the evidence disagree about what happened."""
    result, _, _, _, _, _ = harness()
    assert result.delivered[0].rejected_diff is None


def test_the_worktree_closed_is_the_one_that_was_opened(harness):
    """`for_repo` returns a new workspace when the repository differs. Closing
    the parent would leak the worktree and close one that never existed."""
    card = story()
    card.repo = "sprint-metrics"
    _, _, _, ws, _, _ = harness(cards=[card])
    assert ws.repos_asked == ["sprint-metrics"]
    assert ws.closed == 1


def test_the_limit_caps_how_many_stories_are_attempted(harness):
    result, _, _, _, calls, _ = harness(cards=[story(6), story(7)], limit=1)
    assert len(result.delivered) == 1
    assert len(calls["delivered"]) == 1


# --- healing an interrupted run -----------------------------------------


def in_progress(number: int, title: str = "Show metric") -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"{title} {number}",
        status="In Progress",
        state="OPEN",
        work_type="Story",
        sprint=SPRINT,
        points=3,
        repo="sprint-metrics",
    )


def test_a_card_stranded_in_progress_returns_to_the_backlog(harness):
    """A tick can be killed mid-story. The board is the state, so the next pass
    heals it rather than assuming it was left tidy."""
    result, board, _, _, _, _ = harness(cards=[in_progress(6)])
    assert result.recovered == [6]
    assert ("S6", "Sprint Backlog") in board.moves


def test_a_stranded_card_with_an_open_pull_request_goes_to_review(harness, monkeypatch):
    """It is not stranded — the work landed, only the bookkeeping did not."""
    monkeypatch.setattr(
        FakeIssues,
        "open_pulls",
        lambda self, repo: [{"number": 42, "head": {"ref": "feat/6-show-metric-6"}}],
        raising=False,
    )
    result, board, _, _, _, _ = harness(cards=[in_progress(6)])
    assert result.recovered == []
    assert ("S6", "Reviewing") in board.moves


def test_healing_does_not_touch_cards_that_are_where_they_belong(harness):
    result, _, _, _, _, _ = harness(cards=[story(6)])
    assert result.recovered == []


def test_healing_an_interrupted_run_claims_nothing(harness):
    """Orphan reconciliation moves a card nobody decided to move. Attributing
    that to a role is a guess, and a wrong owner is worse than none."""
    stranded = story(6).model_copy(update={"status": "In Progress"})
    _, board, _, _, _, seen = harness(cards=[stranded])

    assert ("S6", "Sprint Backlog") in board.moves
    assert board.owners == [], "no role stranded it, so no role claims it"
    moved = [e for e in seen if e.kind is EventKind.CARD_MOVED and e.role is None]
    assert moved, "still reported, just not attributed"
    assert moved[0].detail["from"] == "In Progress"


def test_a_story_that_could_not_be_landed_says_why(harness):
    """merge_approved has always worked out why a ready story did not land, and
    the result dropped it on the floor: a run that silently skipped every merge
    looked exactly like a run with nothing to merge."""
    approved = story(6).model_copy(update={"status": "Merging"})
    result, *_ = harness(cards=[approved, story(7)])

    assert result.landed == []
    assert result.unmergeable == [(6, "no open pull request")]


def test_a_push_github_s_side_failed_is_tried_again_not_blocked(harness, monkeypatch):
    """crew#444: sprint-metrics#383's push got GitHub's `remote: fatal error in
    commit_refs` and the card was blocked as the story's own failure, though
    GitHub was operational minutes later."""
    from crew_org.flows.delivery import INTERRUPTED_MARKER
    from crew_org.git_ops import GitError

    def stumble(self, *, force=False):
        raise GitError(
            "git push failed: remote: fatal error in commit_refs\n"
            " ! [remote rejected] feat/6-x -> feat/6-x (failure)"
        )

    monkeypatch.setattr(FakeWorkspace, "push", stumble)
    result, _, issues, _, _, _ = harness(cards=[story(6)])

    assert result.blocked == [], "GitHub interrupted it; the story didn't fail"
    assert result.interrupted == [6]
    assert any(INTERRUPTED_MARKER in body for _, body in issues.comments_)


def test_a_session_its_workspace_interrupted_is_tried_again_not_blocked(harness):
    """A lost container or a failed model call isn't the story's (crew#778, P3)."""

    def lost(card, **_):
        raise session_delivery.SessionInterrupted("the workspace container", "it went")

    result, board, issues, _, _, _ = harness(developer=lost)
    assert result.blocked == [] and result.interrupted == [6]
    assert ("S6", "Blocked") not in board.moves
    assert any("Interrupted by the workspace container" in body for _, body in issues.comments_)


def test_a_model_server_that_is_down_stops_the_tick_and_blames_no_card(harness):
    """A dead backend isn't the story's failure (#153): it passes through to stop the
    tick, and the next pass's reconciliation returns the card."""
    from crew_org import calls
    from crew_org.llm import ModelUnavailable

    def down(card, **_):
        raise calls.ServiceUnavailableError("Connection error: refused")

    with pytest.raises(ModelUnavailable):
        harness(developer=down)


def test_an_exception_no_step_owns_blocks_the_card_saying_what_it_was(harness):
    """The last resort: nothing is left In Progress with nobody to act (§9)."""

    def broke(card, **_):
        raise ValueError("a bug in the crew")

    result, board, *_ = harness(developer=broke)
    assert [o.card for o in result.blocked] == [6]
    assert result.blocked[0].blocked_reason == "ValueError: a bug in the crew"
    assert ("S6", "Blocked") in board.moves


# --- one story at a time within an epic ----------------------------------


def sibling(number: int, *, parent: int = 100, status: str = "Sprint Backlog"):
    return story(number).model_copy(update={"parent": parent, "status": status})


def test_a_story_waits_for_its_earlier_sibling(harness):
    """sprint-metrics #31 created scrape.py with an HTTP server on it; #32,
    whose story was "handle unavailable data at the scrape endpoint", was
    claimed 100 seconds later, could not see scrape.py, and built a second
    server in another module. Both passed their own tests."""
    result, board, _, _, _, _ = harness(cards=[sibling(6), sibling(7)])

    assert [n for n, _ in result.waiting_on_a_sibling] == [7]
    assert result.waiting_on_a_sibling[0][1] == 6, "and it says which story it waits for"
    assert ("S7", "In Progress") not in board.moves


def test_a_hold_names_the_cards_repository(harness, monkeypatch):
    """crew#756: every hold note named a card and no repository, so a count by card
    couldn't tell sprint-metrics#7 from crew-presentation#7 (crew#752's class)."""
    result, _, _, _, _, seen = harness(cards=[sibling(6), sibling(7)])
    (held,) = [e for e in seen if e.detail.get("rule") == "hold.sibling"]
    assert (held.detail["card"], held.detail["repo"]) == (7, "sprint-metrics")

    monkeypatch.setattr(delivery, "held_by_what_it_builds_on", lambda cards, card, body: story(5))
    result, _, _, _, _, seen = harness(cards=[story(8)])
    (held,) = [e for e in seen if e.detail.get("rule") == "hold.builds_on"]
    assert (held.detail["card"], held.detail["repo"]) == (8, "sprint-metrics")


def test_a_landed_sibling_does_not_hold_anything(harness):
    """Landed means Done or closed — an open pull request is not in anyone's
    base, but a merged one is."""
    done = sibling(6, status="Done")
    result, board, _, _, _, _ = harness(cards=[done, sibling(7)])

    assert result.waiting_on_a_sibling == []
    assert ("S7", "In Progress") in board.moves


def test_stories_from_different_epics_run_side_by_side(harness):
    """Epics are decomposed by outcome precisely so they do not touch the same
    code. That concurrency is the point and stays."""
    result, board, _, _, _, _ = harness(cards=[sibling(6, parent=100), sibling(7, parent=200)])

    assert result.waiting_on_a_sibling == []
    assert len([m for m in board.moves if m[1] == "In Progress"]) == 2


def test_a_story_with_no_epic_is_not_held(harness):
    """A card filed by hand has no parent and nothing to wait for."""
    orphan = story(7).model_copy(update={"parent": None})
    result, _, _, _, _, _ = harness(cards=[orphan])

    assert result.waiting_on_a_sibling == []
    assert len(result.delivered) == 1


def test_the_limit_counts_claims_not_candidates(harness):
    """A held story is not an attempt. Counting it against --limit would mean a
    tick that claimed nothing because the first candidate was waiting."""
    result, board, _, _, _, _ = harness(
        cards=[sibling(6, parent=100), sibling(7, parent=100)], limit=1
    )

    assert len(result.delivered) == 1
    assert result.delivered[0].card == 6


# --- attribution ---------------------------------------------------------


def test_the_developer_owns_the_card_it_claimed(harness):
    """The board has always had an Owner Agent field and nothing wrote it, so
    every card said a machine had acted and not which role."""
    _, board, _, _, _, _ = harness()

    assert ("S6", "Developer") in board.owners


# --- which repositories the crew may work in ----------------------------


def other_repo_story(number: int = 20, repo: str = "crew") -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"Orchestrator work {number}",
        status="Sprint Backlog",
        state="OPEN",
        work_type="Story",
        sprint=SPRINT,
        points=3,
        repo=repo,
    )


def test_a_card_outside_the_allow_list_is_never_claimed(harness):
    """It belongs on the board — refined, prioritised, visible — but it is not
    the crew's to implement."""
    result, board, _, _, calls, _ = harness(cards=[other_repo_story()], repos={"sprint-metrics"})
    assert calls["delivered"] == []
    assert board.moves == []
    assert result.not_ours == [20]


def test_cards_inside_the_allow_list_are_worked(harness):
    result, _, _, _, calls, _ = harness(cards=[story(6)], repos={"sprint-metrics"})
    assert len(calls["delivered"]) == 1
    assert result.not_ours == []


def test_a_mixed_sprint_works_only_what_it_may(harness):
    result, _, _, _, calls, _ = harness(
        cards=[other_repo_story(20), story(6)], repos={"sprint-metrics"}
    )
    assert len(calls["delivered"]) == 1
    assert result.delivered[0].card == 6
    assert result.not_ours == [20]


def test_work_happens_in_the_cards_own_repository(harness):
    """A global default would implement a card belonging to one repo inside
    another, silently."""
    _, _, _, ws, _, _ = harness(cards=[story(6)])
    assert ws.repos_asked == ["sprint-metrics"]


def test_without_an_allow_list_nothing_is_excluded(harness):
    """Absent configuration should not silently stop the crew working."""
    result, _, _, _, calls, _ = harness(cards=[story(6)])
    assert len(calls["delivered"]) == 1
    assert result.not_ours == []


# --- a story the reviewer sent back --------------------------------------

REFUSED_HEAD = "deadbeefcafe"


def _refused_pull(branch="feat/31-scrape-endpoint", number=67):
    return {"number": number, "head": {"ref": branch, "sha": REFUSED_HEAD}}


def _returned_card(status):
    return Card(
        item_id="S31",
        number=31,
        title="Scrape endpoint",
        status=status,
        state="OPEN",
        work_type="Story",
        sprint=SPRINT,
        points=3,
        repo="sprint-metrics",
    )


@pytest.fixture
def refused(monkeypatch):
    """A branch with an open pull request whose current head was refused."""
    branch = "feat/31-scrape-endpoint"
    pull = _refused_pull(branch)
    monkeypatch.setattr(FakeIssues, "landable", {branch: pull}, raising=False)
    monkeypatch.setattr(FakeIssues, "open_pulls", lambda self, repo: [pull], raising=False)
    monkeypatch.setattr(
        FakeIssues,
        "pull_reviews",
        lambda self, repo, n: [{"state": "CHANGES_REQUESTED", "commit_id": REFUSED_HEAD}],
        raising=False,
    )
    monkeypatch.setattr(FakeWorkspace, "unfinished", True, raising=False)
    return pull


def test_a_story_stuck_in_reviewing_is_re_worked_with_no_board_surgery(harness, refused):
    """sprint-metrics#31 exactly as it sits: Reviewing, open PR #67, changes
    requested. It ping-ponged In Progress <-> Reviewing and nothing ever
    addressed the findings."""
    result, board, _, _, calls, _ = harness(cards=[_returned_card("Reviewing")])
    assert result.reworked == [31]
    assert calls["delivered"] == [(31, True)], "a new session, told it's a rework (P2)"
    assert ("S31", "In Progress") in board.moves
    assert ("S31", "Reviewing") in board.moves[board.moves.index(("S31", "In Progress")) :]


def test_a_rework_updates_the_open_pull_request(harness, refused):
    """A second pull request from the same branch is not possible, and the review
    it answers is on the one that's open."""
    result, _, issues, ws, _, _ = harness(cards=[_returned_card("In Progress")])
    assert issues.prs == []
    assert result.delivered[0].pr == 67
    assert ws.forced is False
