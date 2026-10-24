"""A split whose criteria can't all pass goes to the Product Owner, not the board (#428).

sprint-metrics#184's split gave #393 two criteria for one request, a
`card_blocked` for a card never created: 404 in one, 400 in the other.
"""

from __future__ import annotations

import threading

import pytest

from crew_org.crews.criteria_crew import CriteriaCheck, CriteriaConflict
from crew_org.crews.refinement_crew import (
    AcceptanceCriterion,
    CriteriaRepair,
    RepairedStory,
    StoryProposal,
)
from crew_org.flows import board_flow, criteria_check
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from tests.test_board_flow import SPLIT, FakeBoard, FakeIssues, epic_card, make_story

CONFLICT = CriteriaCheck(
    conflicts=[
        CriteriaConflict(
            story="Accept blocked events",
            criterion="Then the response has HTTP status 400",
            against="criterion 4 of the same story: Then the response has HTTP status 404",
            why="both post card_blocked for a card that was never created",
        )
    ]
)

# A conflict on a story the split has: on sprint-metrics#406 a lead time of 6 days
# from data that gives 4.
ON_CYCLE_TIME = CriteriaCheck(
    conflicts=[
        CriteriaConflict(
            story="Cycle time",
            criterion="Then I see a table",
            against="its own data: started on the 3rd, finished on the 7th",
            why="the data gives 4 days, the criterion expects 6",
        )
    ]
)
FIXED = [
    AcceptanceCriterion(given="data exists", when="I run it", then="the cycle time is 4"),
    AcceptanceCriterion(given="no data", when="I run it", then="it says so"),
]


class Repairer:
    def __init__(self, *answers) -> None:
        self.answers, self.calls = list(answers), []

    def __call__(self, title, context="", **kw):
        self.calls.append(kw)
        return self.answers.pop(0)


class Checker:
    """One answer per round of checks, a round being one call per story (crew#583, D3).

    Each call is given the round's conflicts about its own story. `rounds` holds the
    first call of each round, which is what a test of the whole split reads.
    """

    def __init__(self, *answers) -> None:
        self.answers, self.calls, self.rounds = list(answers), [], []
        self._seen: set[str] = set()
        self._current = CriteriaCheck()
        # The stories are checked at once (crew#807): one call at a time in here.
        self._lock = threading.Lock()

    def __call__(self, **context):
        with self._lock:
            return self._answer(context)

    def _answer(self, context):
        self.calls.append(context)
        title = context["stories"].split("\n", 1)[0]
        if not self.rounds or title in self._seen:
            self._current = self.answers.pop(0) if self.answers else CriteriaCheck()
            self._seen = set()
            self.rounds.append(context)
        self._seen.add(title)
        known = {
            title,
            *(ln for ln in context.get("siblings", "").splitlines() if ln[:4] == "### "),
        }
        # A conflict on a story the split doesn't have comes back from the first call.
        first = len(self._seen) == 1
        return CriteriaCheck(
            conflicts=[
                c
                for c in self._current.conflicts
                if f"### {c.story}" == title or (first and f"### {c.story}" not in known)
            ]
        )


def splitting(monkeypatch, checker, proposals=None):
    asked: list[dict] = []
    answers = list(proposals or [SPLIT, SPLIT])

    def split(title, context="", **kw):
        asked.append(kw)
        return answers.pop(0) if answers else SPLIT

    monkeypatch.setattr(board_flow, "check_criteria", checker)
    monkeypatch.setattr(board_flow, "split_epic", split)
    return asked


def test_a_split_whose_criteria_pass_creates_its_stories(monkeypatch):
    checker = Checker()
    issues = FakeIssues()
    result = run_split_checked(monkeypatch, issues, checker)
    assert len(result.stories_created) == 2 and len(checker.rounds) == 1


def test_a_conflict_on_a_story_it_does_not_have_is_split_again_whole(monkeypatch):
    checker = Checker(CONFLICT, CriteriaCheck())
    issues = FakeIssues()
    asked = []
    result = run_split_checked(monkeypatch, issues, checker, asked=asked)
    assert len(asked) == 2 and "HTTP status 400" in asked[1]["feedback"]
    assert len(result.stories_created) == 2


def test_a_conflict_that_survives_goes_to_the_product_owner_and_creates_nothing(monkeypatch):
    checker = Checker(CONFLICT, CONFLICT)
    issues = FakeIssues()
    result = run_split_checked(monkeypatch, issues, checker)
    assert result.stories_created == []
    assert (3, NEEDS_REWORK) in issues.added_labels
    body = [b for n, b in issues.posted if n == 3][-1]
    assert STORY_PROBLEM_MARKER in body and "HTTP status 400" in body and "404" in body


def test_a_conflict_repairs_only_the_flagged_story_and_the_rest_stay(monkeypatch):
    checker = Checker(ON_CYCLE_TIME, CriteriaCheck())
    repairer = Repairer(
        CriteriaRepair(stories=[RepairedStory(title="Cycle time", acceptance_criteria=FIXED)])
    )
    monkeypatch.setattr(board_flow, "repair_criteria", repairer)
    issues = FakeIssues()
    asked = []
    result = run_split_checked(monkeypatch, issues, checker, asked=asked)
    assert len(asked) == 1, "the epic isn't split again whole"
    [call] = repairer.calls
    assert "### Cycle time" in call["flagged"] and "Throughput" not in call["flagged"]
    assert "### Throughput" in call["others"] and "expects 6" in call["conflicts"]
    assert len(result.stories_created) == 2 and len(checker.rounds) == 2
    assert any("the cycle time is 4" in c["stories"] for c in checker.calls[2:])
    bodies = {i["title"]: i["body"] for i in issues.created}
    assert "the cycle time is 4" in bodies["Cycle time"]
    assert "the cycle time is 4" not in bodies["Throughput"]


def test_a_repair_that_doesnt_hold_goes_to_the_product_owner(monkeypatch):
    checker = Checker(ON_CYCLE_TIME, ON_CYCLE_TIME)
    repairer = Repairer(
        CriteriaRepair(stories=[RepairedStory(title="Cycle time", acceptance_criteria=FIXED)])
    )
    monkeypatch.setattr(board_flow, "repair_criteria", repairer)
    issues = FakeIssues()
    result = run_split_checked(monkeypatch, issues, checker)
    assert result.stories_created == [] and (3, NEEDS_REWORK) in issues.added_labels
    body = [b for n, b in issues.posted if n == 3][-1]
    assert STORY_PROBLEM_MARKER in body and "expects 6" in body


def test_a_repair_changes_only_the_named_stories_criteria():
    proposal = StoryProposal(
        epic_title="E", stories=[make_story("Cycle time", 3), make_story("Throughput", 2)]
    )
    named = criteria_check.flagged(proposal, ON_CYCLE_TIME)
    assert [s.title for s in named] == ["Cycle time"]
    repair = CriteriaRepair(
        stories=[
            RepairedStory(title=" *cycle TIME* ", acceptance_criteria=FIXED),
            RepairedStory(title="Throughput", acceptance_criteria=FIXED),
        ]
    )
    out = criteria_check.with_repairs(proposal, named, repair)
    assert [s.title for s in out.stories] == ["Cycle time", "Throughput"]
    assert out.stories[0].acceptance_criteria == FIXED and out.stories[0].points == 3
    assert out.stories[1] == proposal.stories[1], "a story not named isn't touched"
    left_out = criteria_check.with_repairs(proposal, named, CriteriaRepair())
    assert left_out.stories == proposal.stories


def test_a_conflict_naming_an_unknown_story_cant_be_repaired_in_place():
    proposal = StoryProposal(epic_title="E", stories=[make_story("Cycle time", 3)])
    assert criteria_check.flagged(proposal, CONFLICT) is None


def test_a_repaired_story_still_needs_two_criteria():
    with pytest.raises(ValueError):
        RepairedStory(title="Cycle time", acceptance_criteria=FIXED[:1])


def test_the_check_reads_each_proposed_criterion_and_what_other_epics_plan():
    proposal = StoryProposal(epic_title="E", stories=[make_story("Accept blocked events", 3)])
    text = criteria_check.render_split(proposal)
    assert "### Accept blocked events" in text and "Given" in text and "Then" in text

    class Planned:
        def get(self, repo, number):
            body = "As a user.\n\n## Acceptance criteria\n\n1. **Given** x\n\n**Estimate** — 2"
            return {"body": body}

    planned = criteria_check.planned_criteria(Planned(), "r", [(397, "Schema", 185)])
    assert "### Epic #185" in planned and "#### #397 Schema" in planned
    assert "**Given** x" in planned
    assert "Estimate" not in planned


def run_split_checked(monkeypatch, issues, checker, asked=None):
    seen = splitting(monkeypatch, checker)
    result = run_split_tick(monkeypatch, issues)
    if asked is not None:
        asked.extend(seen)
    return result


def run_split_tick(monkeypatch, issues):
    from crew_org.events import EventSink

    monkeypatch.setattr(board_flow, "propose_epics", lambda g, **kw: None)
    board = FakeBoard([epic_card(3)])
    return board_flow.tick(board, issues, EventSink(None), default_repo="sprint-metrics")


def test_each_story_is_checked_in_its_own_call_against_the_others():
    """At once, the check thought 12.6k tokens for 641 characters, at the empty-answer
    edge (the context review, C5); one story per call (crew#583, step D3)."""
    from crew_org.flows.criteria_check import check_each

    seen = []

    def check(**shown):
        seen.append(shown)
        if shown["stories"].startswith("### Cycle time"):
            return ON_CYCLE_TIME
        return CriteriaCheck()

    found = check_each(check, SPLIT, conclusion="R1", goal="G", reading="THE CHECKOUT")
    titles = [s.title for s in SPLIT.stories]
    assert [s["stories"].split("\n", 1)[0] for s in seen] == [f"### {t}" for t in titles]
    assert all(s["conclusion"] == "R1" and s["goal"] == "G" for s in seen)
    for shown, title in zip(seen, titles, strict=True):
        others = [t for t in titles if t != title]
        assert all(f"### {t}" in shown["siblings"] for t in others)
        assert f"### {title}" not in shown["siblings"]
        # The code is read in each story's session, over the tick's checkout (crew#807).
        assert shown["reading"] == "THE CHECKOUT" and "repository" not in shown
    assert found.conflicts == ON_CYCLE_TIME.conflicts


def test_after_a_repair_only_the_repaired_story_is_checked_again(monkeypatch):
    """sprint-metrics#551's re-check ran all ten stories again for the few repaired (crew#612)."""
    checker = Checker(ON_CYCLE_TIME, CriteriaCheck())
    repairer = Repairer(
        CriteriaRepair(stories=[RepairedStory(title="Cycle time", acceptance_criteria=FIXED)])
    )
    monkeypatch.setattr(board_flow, "repair_criteria", repairer)
    run_split_checked(monkeypatch, FakeIssues(), checker)
    first = len(SPLIT.stories)
    again = checker.calls[first:]
    assert [c["stories"].split("\n", 1)[0] for c in again] == ["### Cycle time"]
    assert "### Throughput" in again[0]["siblings"], "still checked against the others"


def test_a_repair_may_declare_the_merged_tests_a_story_changes():
    """The fix for a test the epic means to change is declaring it (crew#612)."""
    proposal = StoryProposal(
        epic_title="E", stories=[make_story("Cycle time", 3), make_story("Throughput", 2)]
    )
    named = criteria_check.flagged(proposal, ON_CYCLE_TIME)
    declared = "contract change: tests/test_service.py::test_sprint_keys"
    repair = CriteriaRepair(
        stories=[
            RepairedStory(title="Cycle time", acceptance_criteria=FIXED, pinned_behaviour=declared)
        ]
    )
    out = criteria_check.with_repairs(proposal, named, repair)
    assert out.stories[0].pinned_behaviour == declared
    kept = criteria_check.with_repairs(
        proposal,
        named,
        CriteriaRepair(stories=[RepairedStory(title="Cycle time", acceptance_criteria=FIXED)]),
    )
    assert kept.stories[0].pinned_behaviour == proposal.stories[0].pinned_behaviour
    with pytest.raises(ValueError):
        RepairedStory(title="Cycle time", acceptance_criteria=FIXED, pinned_behaviour="maybe")


def test_a_story_conflicting_with_one_test_is_reported_once():
    """sprint-metrics#551's refusal listed 14 conflicts for 4 tests (crew#612)."""

    def against(text):
        return CriteriaConflict(story="Sprint keys", criterion="c", against=text, why="w")

    def check(**_shown):
        return CriteriaCheck(
            conflicts=[
                against("test_sprint_keys (tests/test_service.py)"),
                against("tests/test_service.py :: test_sprint_keys"),
                against("test_enum_exact in tests/test_service.py"),
            ]
        )

    found = criteria_check.check_each(check, SPLIT)
    assert [c.against for c in found.conflicts] == [
        "test_sprint_keys (tests/test_service.py)",
        "test_enum_exact in tests/test_service.py",
    ]


# --- the checks run at once (the Sponsor, crew#807) ------------------------------------


def seen_events():
    from crew_org import events
    from crew_org.events import EventSink

    found: list = []
    sink = EventSink(None)
    sink.subscribe(found.append)
    events.reset_bridge()
    events._TARGETS.append((sink, None))
    return found


def test_each_storys_check_runs_at_the_same_time():
    """Each waits for the other: this only passes if both are in flight together. The real
    run's five checks took 66 model minutes in turn, against about 29 for the longest."""
    barrier = threading.Barrier(len(SPLIT.stories), timeout=10)

    def check(**shown):
        barrier.wait()
        return ON_CYCLE_TIME if shown["stories"].startswith("### Cycle time") else CriteriaCheck()

    found = criteria_check.check_each(check, SPLIT)
    assert found.conflicts == ON_CYCLE_TIME.conflicts


def test_a_check_that_fails_leaves_the_others_conflicts_standing_and_is_recorded():
    from crew_org import events
    from crew_org.events import EventKind

    found_events = seen_events()

    def check(**shown):
        if shown["stories"].startswith("### Throughput"):
            raise RuntimeError("the session stopped")
        return ON_CYCLE_TIME

    try:
        found = criteria_check.check_each(check, SPLIT)
    finally:
        events.reset_bridge()
    assert found.conflicts == ON_CYCLE_TIME.conflicts
    [note] = [
        e
        for e in found_events
        if e.kind == EventKind.NOTE and e.detail.get("step") == "criteria check"
    ]
    assert note.detail["failed"] == {"Throughput": "RuntimeError: the session stopped"}


def test_when_every_check_fails_the_split_fails_with_the_first_failure():
    def check(**shown):
        raise RuntimeError(f"stopped on {shown['stories'].splitlines()[0]}")

    with pytest.raises(RuntimeError, match="stopped on ### Cycle time"):
        criteria_check.check_each(check, SPLIT)


def test_each_checks_model_time_is_recorded_with_the_wall_time():
    from types import SimpleNamespace

    from crew_org import events
    from crew_org.events import EventKind

    found_events = seen_events()

    def check(*, on_end, **shown):
        on_end(
            SimpleNamespace(calls=4, model_s=90.0, usage={"completion_tokens": 300}),
            SimpleNamespace(how="done"),
        )
        return CriteriaCheck()

    try:
        criteria_check.check_each(check, SPLIT)
    finally:
        events.reset_bridge()
    [note] = [
        e
        for e in found_events
        if e.kind == EventKind.NOTE and e.detail.get("step") == "criteria check"
    ]
    assert note.detail["sessions"]["Cycle time"] == {
        "how": "done",
        "model_calls": 4,
        "model_s": 90.0,
        "completion_tokens": 300,
    }
    assert set(note.detail["sessions"]) == {"Cycle time", "Throughput"}
    assert note.detail["completion_tokens"] == 600 and note.detail["wall_s"] >= 0
