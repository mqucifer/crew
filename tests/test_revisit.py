"""The Architect revisits a project's design when delivery shows it's needed (#192).

The Architect and the Code Reviewer are scripted. What is tested is when a
revisit starts, what the Architect is told, that the crew merges its own
revision, that declared changes become technical epics once, and that a
project's other epics wait while it happens.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_org.crews.design_crew import Change, Choice, Conflict, DesignProposal, DesignReview
from crew_org.events import EventSink
from crew_org.flows.board_flow import TECHNICAL
from crew_org.flows.design import (
    REVISIT_LABEL,
    REVISIT_MARKER,
    changes_block,
    declared_changes,
    open_design_pr,
)
from crew_org.flows.loop import LoopResult, PhaseOutcome
from crew_org.flows.revisit import EPICS_MARKER, Revisits, file_technical_epics, revisit_designs
from crew_org.flows.standup import decided_by_crew
from crew_org.flows.strain import Rebuild, evidence, read_rebuilds, strained
from crew_org.project import RECORD_PATH, parse, render
from crew_org.tools.github_project import Card
from tests.test_onboard import FakeWorkspace

REPO = "sprint-metrics"
MODULE = "src/sprint_metrics/crew_performance.py"

RECORD = parse(
    """
version: 2
intent:
  scope:
    purpose: Report delivery metrics for the crew's own board.
  release:
    deploys: false
  done:
    bar: Its acceptance criteria are met and CI is green.
design:
  language: Python 3.12
  checks: [uv run pytest -q]
"""
)


def story(number, sprint="Sprint 6", repo=REPO, title="") -> Card:
    return Card(
        item_id=f"i{number}",
        number=number,
        title=title,
        repo=repo,
        sprint=sprint,
        work_type="Story",
    )


def epic(number, *, labels=(), status="Needs Refinement", state="OPEN") -> Card:
    return Card(
        item_id=f"e{number}",
        number=number,
        title=f"Epic {number}",
        repo=REPO,
        status=status,
        state=state,
        work_type="Epic",
        labels=frozenset(labels),
    )


# Sprint 6, as it happened: four stories rebuilt over the one module.
SPRINT6 = [story(73, title="Report a range")] + [story(n) for n in (95, 100, 101)]


def rebuilt(card, *paths, at="2026-09-25T21:00:00.000001Z", repo=REPO) -> Rebuild:
    return Rebuild(card=card, paths=paths or (MODULE,), at=at, repo=repo)


# --- the evidence ------------------------------------------------------------------------------


def write_events(tmp_path: Path, *events: dict) -> Path:
    folder = tmp_path / "events"
    folder.mkdir(parents=True)
    (folder / "deliver.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return folder


def test_rebuilds_are_read_from_their_detail_and_from_the_older_summaries(tmp_path: Path):
    folder = write_events(
        tmp_path,
        {
            "at": "2026-09-25T21:39:48.584224Z",
            "kind": "note",
            "summary": f"#95 rebuilt from main: its branch conflicted in {MODULE}, "
            "tests/test_sprint_range.py",
            "detail": {},
        },
        {
            "at": "2026-09-26T10:00:00Z",
            "kind": "note",
            "summary": "#120 rebuilt from main: …",
            "detail": {"rebuilt": True, "repo": REPO, "card": 120, "paths": [MODULE]},
        },
        {
            "at": "2026-09-26T10:00:01Z",
            "kind": "note",
            "summary": "#7 rebuilt from main: its branch conflicted in unknown files",
            "detail": {},
        },
        {"at": "2026-09-26T10:00:02Z", "kind": "card.moved", "summary": "#1 moved"},
    )
    assert read_rebuilds(folder) == [
        Rebuild(95, (MODULE, "tests/test_sprint_range.py"), "2026-09-25T21:39:48.584224Z"),
        Rebuild(120, (MODULE,), "2026-09-26T10:00:00Z", REPO),
    ]


def test_three_stories_of_one_sprint_in_the_same_file_is_strain():
    (strain,) = strained([rebuilt(n) for n in (73, 95, 100)], SPRINT6, REPO)
    assert (strain.sprint, strain.path, strain.cards) == ("Sprint 6", MODULE, [73, 95, 100])
    assert "3 stories had to be rebuilt" in evidence([strain]) and "  - #95" in evidence([strain])


def test_the_evidence_says_what_each_colliding_story_was():
    """The numbers alone don't say what kind of change keeps landing in one place."""
    (strain,) = strained([rebuilt(n) for n in (73, 95, 100)], SPRINT6, REPO)
    text = evidence([strain], {95: "Add prior-period values to sprint-range JSON output"})
    assert "  - #95 Add prior-period values to sprint-range JSON output" in text
    assert "  - #73\n" in text, "a story with no title known is still named"


def test_two_is_not_and_one_story_rebuilt_twice_counts_once():
    assert strained([rebuilt(73), rebuilt(95), rebuilt(95)], SPRINT6, REPO) == []


def test_collisions_spread_across_sprints_do_not_add_up():
    cards = [story(1, "Sprint 3"), story(2, "Sprint 5"), story(3, "Sprint 6")]
    assert strained([rebuilt(n) for n in (1, 2, 3)], cards, REPO) == []


def test_evidence_the_architect_already_weighed_is_not_counted_again():
    rebuilds = [rebuilt(n) for n in (73, 95, 100)]
    assert strained(rebuilds, SPRINT6, REPO, since="2026-09-25T22:00:00Z") == []
    assert strained(rebuilds, SPRINT6, REPO, since="2026-09-25T20:00:00Z")


def test_the_threshold_is_the_organizations():
    rebuilds = [rebuilt(n) for n in (73, 95, 100)]
    assert strained(rebuilds, SPRINT6, REPO, threshold=4) == []


def test_another_projects_rebuilds_are_not_this_ones_evidence():
    other = [story(n, repo="other") for n in (73, 95, 100)]
    # Recorded with its repository: it simply isn't this one's.
    assert strained([rebuilt(n, repo="other") for n in (73, 95, 100)], SPRINT6, REPO) == []
    # Logged without one: a number that is also another project's card is not guessed at.
    unattributed = [rebuilt(n, repo=None) for n in (73, 95, 100)]
    assert strained(unattributed, SPRINT6 + other, REPO, repos={REPO, "other"}) == []
    assert strained(unattributed, SPRINT6, REPO, repos={REPO})


# --- the revisit -------------------------------------------------------------------------------


def proposal(*, changes=(), structure="One module per concern") -> DesignProposal:
    return DesignProposal(
        language=Choice(value="Python 3.12", basis=".python-version"),
        checks=[Choice(value="uv run pytest -q", basis="tests.yml")],
        structure=Choice(value=structure, basis="the rebuilds") if structure else None,
        changes=list(changes),
        summary="Split crew_performance.py by concern.",
    )


SPLIT = Change(
    what="Split crew_performance.py into loading, metrics and one module per output format",
    was="one module every story edits",
    why="four stories of Sprint 6 were rebuilt over it",
    needs_work=True,
)
NOTED = Change(what="Record the module layout", was="unwritten", why="so stories name files")


class Architect:
    def __init__(self, *proposals):
        self.proposals, self.calls = list(proposals), []

    def __call__(self, **context):
        self.calls.append(context)
        return self.proposals.pop(0)


def approves(**_context):
    return DesignReview()


class Workspace(FakeWorkspace):
    def current(self):
        return self.root


class Issues:
    """Enough of GitHub for a revisit: issues, pull requests, reviews, labels."""

    def __init__(self, *, labelled=(), open_pulls=(), closed_pulls=(), bodies=None):
        self._labelled = list(labelled)
        self._open_pulls = list(open_pulls)
        self._closed_pulls = list(closed_pulls)
        self.bodies = bodies or {}
        self.created: list[dict] = []
        self.closed: list[int] = []
        self.pulls: list[dict] = []
        self.comments_on: dict[int, list[str]] = {}
        self.reviews: dict[int, list[dict]] = {}
        self.merged: list[int] = []
        self.merge_fails = False
        self.labels: set[str] = set()

    # issues
    def create(self, repo, title, body, labels=None):
        number = 200 + len(self.created)
        self.created.append({"number": number, "title": title, "body": body, "labels": labels})
        return {"number": number, "node_id": f"n{number}", "id": number}

    def close(self, repo, number, reason="completed"):
        self.closed.append(number)

    def get(self, repo, number):
        return {"body": self.bodies.get(number, "")}

    def labelled(self, repo, label):
        return self._labelled if label == REVISIT_LABEL else []

    def ensure_label(self, repo, name, *, color, description):
        self.labels.add(name)

    def open_issues(self, repo):
        return []

    def comment(self, repo, number, body):
        self.comments_on.setdefault(number, []).append(body)

    def comments(self, repo, number):
        return [{"body": b} for b in self.comments_on.get(number, [])]

    def has_comment_marked(self, repo, number, marker):
        return any(marker in b for b in self.comments_on.get(number, []))

    def repository(self, repo):
        return {"default_branch": "main"}

    # pull requests
    def open_pulls(self, repo):
        return self._open_pulls

    def closed_pulls(self, repo):
        return self._closed_pulls

    def create_pull(self, repo, **kwargs):
        self.pulls.append(kwargs)
        return {"html_url": f"https://github.com/o/{repo}/pull/9"}

    def pull(self, repo, number):
        found = next(p for p in self._open_pulls + self._closed_pulls if p["number"] == number)
        return {**found, "mergeable_state": "clean", "merged_at": "now"}

    def pull_reviews(self, repo, number):
        return self.reviews.get(number, [])

    def create_review(self, repo, number, *, event, body):
        self.reviews.setdefault(number, []).append({"state": "APPROVED", "body": body})

    def merge_pull(self, repo, number, method="squash"):
        if self.merge_fails:
            raise RuntimeError("405 Required status check is expected")
        self.merged.append(number)


class Board:
    def __init__(self):
        self.status: dict[str, str] = {}
        self.fields: dict[str, dict] = {}

    def add_issue(self, node_id):
        return f"item-{node_id}"

    def set_status(self, item, to):
        self.status[item] = to

    def set_owner_agent(self, item, by):
        pass

    def set_select(self, item, name, value):
        self.fields.setdefault(item, {})[name] = value


@pytest.fixture
def clone(tmp_path: Path) -> Workspace:
    (tmp_path / RECORD_PATH).parent.mkdir(parents=True)
    (tmp_path / RECORD_PATH).write_text(render(RECORD))
    return Workspace(tmp_path)


def revisit(issues, clone, cards, architect, *, events_dir=None, reviewer=None, board=None):
    return revisit_designs(
        issues,
        reviewer or issues,
        board or Board(),
        EventSink(),
        clone,
        cards,
        repos={REPO},
        events_dir=events_dir,
        propose_design=architect,
        review_design=approves,
    )


@pytest.fixture
def sprint6(tmp_path: Path) -> Path:
    return write_events(
        tmp_path / "sprint6",
        *[
            {
                "at": "2026-09-25T21:00:00Z",
                "kind": "note",
                "summary": f"#{n} rebuilt from main: its branch conflicted in {MODULE}",
                "detail": {},
            }
            for n in (73, 95, 100, 101)
        ],
    )


def test_strain_sends_the_architect_back_with_the_evidence_and_the_waiting_epics(clone, sprint6):
    issues = Issues(bodies={59: "Expose sprint metrics as a stable JSON API."})
    architect = Architect(proposal(changes=[SPLIT]))

    result = revisit(issues, clone, SPRINT6 + [epic(59)], architect, events_dir=sprint6)

    reason = architect.calls[0]["reason"]
    assert f"`{MODULE}`:\n  - #73 Report a range\n  - #95" in reason
    assert "#59 Epic 59" in reason and "stable JSON API" in reason
    assert architect.calls[0]["current"], "a revision starts from the design in the record"
    (pull,) = issues.pulls
    assert (
        REVISIT_MARKER in pull["body"] and "**Structure:** One module per concern" in pull["body"]
    )
    assert declared_changes(pull["body"])[0]["needs_work"] is True
    assert issues.created[0]["labels"] == [REVISIT_LABEL], "when it looked, for next time"
    assert result.proposed == [(REPO, "https://github.com/o/sprint-metrics/pull/9")]
    assert REPO in result.holds, "its other epics wait for the revision"


def test_no_strain_means_no_revisit(clone, tmp_path):
    architect = Architect()
    result = revisit(Issues(), clone, SPRINT6, architect, events_dir=tmp_path / "none")
    assert architect.calls == [] and not result.moved and result.holds == {}


def test_a_revisit_that_changes_nothing_is_recorded_and_holds_nothing(clone, sprint6):
    issues = Issues()
    unchanged = proposal(structure=None)
    unchanged.language = Choice(value="Python 3.12", basis="x")

    result = revisit(issues, clone, SPRINT6, Architect(unchanged), events_dir=sprint6)

    assert issues.pulls == []
    (look,) = issues.created
    assert look["labels"] == [REVISIT_LABEL] and look["number"] in issues.closed
    assert "no change" in look["title"]
    assert result.unchanged == [(REPO, look["number"])] and result.holds == {}


def test_a_refused_revision_is_recorded_so_it_is_not_retried_every_pass(clone, sprint6):
    issues = Issues()
    conflict = DesignReview(conflicts=[Conflict(guideline="§19 rule 1", choice="x", why="y")])

    result = revisit_designs(
        issues,
        issues,
        Board(),
        EventSink(),
        clone,
        SPRINT6,
        repos={REPO},
        events_dir=sprint6,
        propose_design=Architect(proposal(), proposal()),
        review_design=lambda **_: conflict,
    )

    assert issues.pulls == [] and result.refused
    assert (
        issues.created[0]["labels"] == [REVISIT_LABEL] and "refused" in issues.created[0]["title"]
    )


def test_evidence_before_the_last_look_starts_nothing(clone, sprint6):
    issues = Issues(labelled=[{"created_at": "2026-09-25T23:00:00Z"}])
    architect = Architect()
    revisit(issues, clone, SPRINT6, architect, events_dir=sprint6)
    assert architect.calls == []


# --- the crew merges its own revision ------------------------------------------------------------


def revision(number=9, changes=(SPLIT,)) -> dict:
    return {
        "number": number,
        "body": f"Closes #7\n\n{changes_block(proposal(changes=list(changes)))}\n{REVISIT_MARKER}",
    }


def test_an_open_revision_is_approved_and_merged_and_its_work_filed(clone):
    issues = Issues(open_pulls=[revision()])
    board = Board()

    result = revisit(issues, clone, SPRINT6, Architect(), board=board)

    assert issues.reviews[9][0]["state"] == "APPROVED" and issues.merged == [9]
    (filed,) = issues.created
    assert filed["labels"] == [TECHNICAL] and filed["title"].startswith("Split crew_performance")
    assert "four stories of Sprint 6" in filed["body"] and "#9" in filed["body"]
    item = f"item-n{filed['number']}"
    assert board.status[item] == "Needs Refinement", "not the Sponsor's gate"
    assert board.fields[item]["Work Type"] == "Epic"
    assert result.merged == [(REPO, 9)] and result.epics == [(REPO, filed["number"])]
    assert result.holds[REPO].endswith(f"#{filed['number']}"), "held on work filed this pass"


def test_a_revision_not_yet_mergeable_holds_its_projects_epics(clone):
    issues = Issues(open_pulls=[revision()])
    issues.merge_fails = True
    result = revisit(issues, clone, SPRINT6, Architect())
    assert issues.merged == [] and issues.created == []
    assert "PR #9" in result.holds[REPO]


def test_it_is_approved_once(clone):
    issues = Issues(open_pulls=[revision()])
    issues.merge_fails = True
    revisit(issues, clone, SPRINT6, Architect())
    revisit(issues, clone, SPRINT6, Architect())
    assert len(issues.reviews[9]) == 1


def test_an_open_technical_epic_holds_the_projects_other_epics(clone):
    cards = SPRINT6 + [epic(300, labels={TECHNICAL}), epic(301, labels={TECHNICAL}, state="CLOSED")]
    result = revisit(Issues(), clone, cards, Architect())
    assert result.holds == {REPO: "the Architect's technical work lands first: #300"}


# --- declared changes become work, once, whoever merged the design ----------------------------


def test_a_merged_design_the_sponsor_ran_files_its_work_too():
    issues = Issues()
    pull = {"number": 82, "body": changes_block(proposal(changes=[SPLIT, NOTED]))}
    result = Revisits()

    filed = file_technical_epics(issues, Board(), EventSink(), repo=REPO, pull=pull, result=result)

    assert len(filed) == 1, "a change to the record alone is not work"
    assert EPICS_MARKER in issues.comments_on[82][0]
    assert (
        file_technical_epics(issues, Board(), EventSink(), repo=REPO, pull=pull, result=result)
        == []
    )


def test_a_design_that_only_records_what_exists_files_nothing():
    issues = Issues()
    pull = {"number": 82, "body": changes_block(proposal())}
    assert (
        file_technical_epics(issues, Board(), EventSink(), repo=REPO, pull=pull, result=Revisits())
        == []
    )
    assert issues.comments_on == {}


def test_merged_design_pull_requests_are_found_among_the_closed(clone):
    issues = Issues(
        closed_pulls=[
            {**revision(82), "merged_at": "2026-09-25T00:00:00Z"},
            {**revision(83), "merged_at": None},
        ]
    )
    result = revisit(issues, clone, SPRINT6, Architect())
    assert [e for _r, e in result.epics] == [issues.created[0]["number"]]
    assert 83 not in issues.comments_on, "closed without merging is not a design"


def test_a_change_cannot_close_the_comment_it_is_carried_in():
    tricky = Change(what="Rename --> to ->", was="a -->", why="x", needs_work=True)
    assert declared_changes(changes_block(proposal(changes=[tricky])))[0]["what"] == tricky.what


def test_the_sponsors_design_pull_request_is_not_the_crews_to_merge(tmp_path: Path):
    from crew_org.flows.design import Designed

    issues = Issues()
    designed = Designed(record=RECORD, proposal=proposal(changes=[SPLIT]))
    open_design_pr(Workspace(tmp_path), issues, REPO, designed, base="main")
    assert REVISIT_MARKER not in issues.pulls[0]["body"]
    assert declared_changes(issues.pulls[0]["body"]), "its changes still become work"


# --- refinement waits; the Sponsor is told, not asked ---------------------------------------------


def test_refinement_holds_a_projects_epics_but_not_its_technical_ones(monkeypatch):
    from crew_org.flows import board_flow

    split: list[int] = []

    def split_epic(title, body, **_kw):
        split.append(title)
        raise RuntimeError("stop here")

    monkeypatch.setattr(board_flow, "split_epic", split_epic)
    result = board_flow.TickResult()
    cards = [epic(59), epic(300, labels={TECHNICAL})]

    class Quiet:
        def counts(self, cards):
            return {}

        def comments(self, repo, number):
            return []

        def get(self, repo, number):
            return {"body": ""}

        def __getattr__(self, name):
            return lambda *a, **k: None

    board_flow.refine_epics(
        Quiet(),
        Quiet(),
        EventSink(),
        None,
        None,
        result,
        cards=cards,
        default_repo=REPO,
        context=board_flow.RepoContext(None, EventSink()),
        holds={REPO: "the Architect's design revision lands first"},
    )
    assert result.held_for_design == [(59, "the Architect's design revision lands first")]
    assert split == ["Epic 300"]


def test_the_standup_says_what_the_architect_decided():
    found = Revisits(
        proposed=[(REPO, "https://github.com/o/sprint-metrics/pull/9")],
        merged=[(REPO, 9)],
        epics=[(REPO, 300)],
        unchanged=[("other", 12)],
    )
    result = LoopResult(outcomes=[PhaseOutcome("revisit", moved=True, result=found)])
    lines = "\n".join(decided_by_crew(result))
    assert "revised the design" in lines and "pull/9" in lines
    assert f"{REPO}#300: a technical epic" in lines
    assert "changed nothing (#12)" in lines
    assert "?" not in lines, "information, never a question"
