"""An epic sent back is split again only where the problem reaches (crew#859).

sprint-metrics#684's problem named #700, the third of its four stories. Its re-split
closed all four, #698 and #699 included, and wrote four more.
"""

from __future__ import annotations

from crew_org.crews.refinement_crew import AcceptanceCriterion, Accounted, Story, StoryProposal
from crew_org.events import EventKind, EventSink
from crew_org.flows.board_flow import (
    NEEDS_REWORK,
    STORY_PROBLEM_MARKER,
    STORY_SPLIT_MARKER,
    tick,
)
from crew_org.flows.delivered import Delivered
from crew_org.tools.github_project import Card
from tests.test_board_flow import FakeBoard, FakeIssues, epic_card
from tests.test_planned_elsewhere import story

EPIC = 684
FOUR = (698, 699, 700, 701)
SPLIT = f"{STORY_SPLIT_MARKER}\n## Stories\n\nsplit into 4 stories"
PROBLEM = (
    f"{STORY_PROBLEM_MARKER}\n**No design note: [mqucifer/sprint-metrics#700]"
    "(https://github.com/mqucifer/sprint-metrics/issues/700) can't be designed as written.**"
)
ANSWER = "<!-- crew:record-change R8 answer -->\n**The epic's record changed.**"


class Issues(FakeIssues):
    """An epic with stories under it, comments to read back, and issues to close."""

    def __init__(self, comments: list[str], children=FOUR, branches=()) -> None:
        super().__init__()
        self.earlier = comments
        self.children = children
        self._branches = branches
        self.closed: list[tuple[int, str]] = []
        self.bodies: dict[int, str] = {}
        # Issues whose next close fails, once.
        self.stuck: set[int] = set()

    def comments(self, repo, number):
        if number != EPIC:
            return []
        return [{"body": b} for b in self.earlier] + [
            {"body": b} for n, b in self.posted if n == number
        ]

    def has_comment_marked(self, repo, number, marker):
        return any(marker in c["body"] for c in self.comments(repo, number))

    def sub_issues(self, repo, number):
        return [{"number": n} for n in self.children] if number == EPIC else []

    def get(self, repo, number):
        made = next((i for i in self.created if i["number"] == number), None)
        if made is not None:
            return {"body": made["body"]}
        return {"body": self.bodies.get(number, f"story {number}\n\nFollows the epic's R1.")}

    def branches(self, repo):
        return [{"name": b} for b in self._branches]

    def close(self, repo, number, *, reason="completed"):
        if number in self.stuck:
            self.stuck.discard(number)
            raise RuntimeError("502")
        self.closed.append((number, reason))
        return {}


def proposal(*, fails=False) -> StoryProposal:
    if fails:
        raise RuntimeError("the model's server went away")
    criteria = [
        AcceptanceCriterion(given="a card", when="it's read", then="it counts returns"),
        AcceptanceCriterion(given="no card", when="it's read", then="it says so"),
    ]
    new = Story(
        title="Count returns with the docs' definition",
        as_a="maintainer",
        i_want="returns counted",
        so_that="the rate means acceptance",
        acceptance_criteria=criteria,
        points=3,
    )
    return StoryProposal(
        epic_title=f"Epic {EPIC}",
        stories=[new],
        accounted=[
            Accounted(number=700, how="rewritten", note=new.title),
            Accounted(number=701, how="dropped", note="R8 rules it out"),
        ],
    )


def run(monkeypatch, issues, cards, *, fails=False, org=None):
    asked: list[dict] = []

    def split(title, context="", **kw):
        asked.append(kw)
        return proposal(fails=fails)

    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", split)
    monkeypatch.setattr(
        "crew_org.flows.board_flow.criteria_check.check_each",
        lambda *a, **kw: type("Checked", (), {"conflicts": []})(),
    )
    monkeypatch.setattr("crew_org.flows.board_flow.delivered", lambda *a: Delivered())
    sink = EventSink(None)
    seen = []
    sink.subscribe(seen.append)
    tick(FakeBoard(cards), issues, sink, default_repo="sprint-metrics", org=org)
    return asked, seen


def sent_back() -> Card:
    return epic_card(EPIC).model_copy(update={"labels": frozenset({NEEDS_REWORK})})


def stories(*numbers: int, **kw) -> list[Card]:
    return [story(n, EPIC, **kw) for n in numbers]


def test_a_problem_on_one_story_replaces_it_and_its_dependants_only(monkeypatch):
    issues = Issues([SPLIT, PROBLEM, ANSWER])
    asked, seen = run(monkeypatch, issues, [sent_back(), *stories(*FOUR)])
    [kw] = asked
    assert kw["superseded"] == [(700, "Story 700"), (701, "Story 701")]
    assert "### #698 Story 698" in kw["kept"] and "### #699 Story 699" in kw["kept"]
    assert "#700" in kw["replaced"] and "#698" not in kw["replaced"]
    assert issues.closed == [(700, "not_planned"), (701, "not_planned")]
    [split] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert "Replaced, closed as not planned: #700, #701." in split
    assert "Kept as they are: #698, #699." in split
    assert (EPIC, NEEDS_REWORK) in issues.removed_labels
    [event] = [e for e in seen if e.kind == EventKind.EPIC_RESPLIT]
    assert event.detail["superseded"] == [700, 701] and event.detail["kept"] == [698, 699]
    assert event.detail["named"] == [700] and event.detail["positions"] == [3]
    assert event.detail["because"] == "story problem" and event.detail["reads_decision"]


def test_the_rows_kept_stories_follow_are_covered_for_the_split(monkeypatch):
    issues = Issues([SPLIT, PROBLEM, ANSWER])
    asked, _ = run(monkeypatch, issues, [sent_back(), *stories(*FOUR)])
    assert "R1" not in asked[0]["rows"]


def test_a_started_story_it_would_replace_holds_the_re_split(monkeypatch):
    issues = Issues([SPLIT, PROBLEM, ANSWER], branches=["feat/701-started"])
    cards = [sent_back(), *stories(698, 699, 700), story(701, EPIC, status="In Progress")]
    asked, seen = run(monkeypatch, issues, cards)
    assert asked == [] and issues.closed == []
    assert (EPIC, NEEDS_REWORK) not in issues.removed_labels
    assert any("rework refused — work already started on #701" in (e.summary or "") for e in seen)


def test_a_started_story_it_keeps_doesnt_hold_it(monkeypatch):
    issues = Issues([SPLIT, PROBLEM, ANSWER], branches=["feat/698-started"])
    cards = [sent_back(), story(698, EPIC, status="In Progress"), *stories(699, 700, 701)]
    asked, _ = run(monkeypatch, issues, cards)
    assert len(asked) == 1 and [n for n, _ in issues.closed] == [700, 701]


def test_a_split_that_fails_closes_nothing_and_leaves_the_epic_sent_back(monkeypatch):
    issues = Issues([SPLIT, PROBLEM, ANSWER])
    run(monkeypatch, issues, [sent_back(), *stories(*FOUR)], fails=True)
    assert issues.closed == []
    assert (EPIC, NEEDS_REWORK) not in issues.removed_labels
    assert not any(STORY_SPLIT_MARKER in b for _, b in issues.posted)


def test_the_sponsors_rework_naming_no_story_replaces_every_unstarted_one(monkeypatch):
    issues = Issues([SPLIT, "Split it smaller."])
    monkeypatch.setattr("crew_org.flows.board_flow.from_sponsor", lambda issues, c: True)
    asked, seen = run(monkeypatch, issues, [sent_back(), *stories(*FOUR)])
    assert [n for n, _ in asked[0]["superseded"]] == list(FOUR) and asked[0]["kept"] == ""
    [event] = [e for e in seen if e.kind == EventKind.EPIC_RESPLIT]
    assert event.detail["because"] == "sponsor"


def test_devops_release_task_stays_under_the_epic(monkeypatch):
    """crew#839's Task waits for the new stories; it isn't closed and reopened."""
    task = Card(
        item_id="T702",
        number=702,
        title="CI and release",
        status="Ready",
        state="OPEN",
        work_type="Task",
        repo="sprint-metrics",
        parent=EPIC,
    )
    issues = Issues([SPLIT, PROBLEM, ANSWER], children=(*FOUR, 702))
    asked, _ = run(monkeypatch, issues, [sent_back(), *stories(*FOUR), task])
    assert 702 not in [n for n, _ in asked[0]["superseded"]]
    assert 702 not in [n for n, _ in issues.closed]


def test_each_round_is_marked_on_the_split_and_counted_from_github(monkeypatch):
    earlier = f"{STORY_SPLIT_MARKER}\n<!-- crew:resplit round=1 -->\nRound 1."
    issues = Issues([SPLIT, earlier, PROBLEM, ANSWER])
    run(monkeypatch, issues, [sent_back(), *stories(*FOUR)])
    [split] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert "<!-- crew:resplit round=2 -->" in split


def test_the_sponsors_own_rework_isnt_a_round(monkeypatch):
    issues = Issues([SPLIT, "Split it smaller."])
    monkeypatch.setattr("crew_org.flows.board_flow.from_sponsor", lambda issues, c: True)
    run(monkeypatch, issues, [sent_back(), *stories(*FOUR)])
    [split] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert "crew:resplit round" not in split


# --- a round an earlier pass didn't finish (crew#859, the review) -------------------------

WRITTEN = "<!-- crew:resplit-of 700,701 round=1 -->"


def test_a_round_whose_split_comment_never_landed_is_finished_not_split_again(monkeypatch):
    """700 and 701 replaced by 30 and 31, closed, then the split comment failed."""
    issues = Issues([SPLIT, PROBLEM, ANSWER], children=(698, 699, 700, 701, 30, 31))
    issues.bodies = {n: f"story {n}\n\n{WRITTEN}" for n in (30, 31)}
    cards = [
        sent_back(),
        *stories(698, 699, 30, 31),
        *stories(700, 701, state="CLOSED"),
    ]
    asked, seen = run(monkeypatch, issues, cards)
    assert asked == [], "nothing is split again"
    assert {n for n, _ in issues.closed} == {700, 701}
    [split] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert "#30, #31" in split and "<!-- crew:resplit round=1 -->" in split
    assert "Kept as they are: #698, #699." in split
    assert (EPIC, NEEDS_REWORK) in issues.removed_labels
    [event] = [e for e in seen if e.kind == EventKind.EPIC_RESPLIT]
    assert event.detail["superseded"] == [700, 701] and event.detail["because"] == "story problem"


def test_needs_rework_left_on_after_a_finished_round_is_spent_not_a_new_rework(monkeypatch):
    done = (
        f"{STORY_SPLIT_MARKER}\n- one — #30\n- two — #31\n\n<!-- crew:resplit-done -->\n"
        "<!-- crew:resplit round=1 -->"
    )
    issues = Issues([SPLIT, PROBLEM, ANSWER, done], children=(698, 699, 30, 31))
    issues.bodies = {n: f"story {n}\n\n{WRITTEN}" for n in (30, 31)}
    asked, _ = run(monkeypatch, issues, [sent_back(), *stories(698, 699, 30, 31)])
    assert asked == [] and issues.closed == []
    assert (EPIC, NEEDS_REWORK) in issues.removed_labels


def test_a_replaced_story_that_wont_close_holds_the_round_for_the_next_pass(monkeypatch):
    issues = Issues([SPLIT, PROBLEM, ANSWER])
    issues.stuck = {701}
    asked, _ = run(monkeypatch, issues, [sent_back(), *stories(*FOUR)])
    assert len(asked) == 1 and issues.closed == [(700, "not_planned")]
    assert not any(STORY_SPLIT_MARKER in b for _, b in issues.posted), "the round isn't over"
    assert (EPIC, NEEDS_REWORK) not in issues.removed_labels
    [new] = [i["number"] for i in issues.created]
    assert WRITTEN in issues.get("sm", new)["body"]

    # The next pass finishes it: 701 closes, and the round is said on the epic.
    issues.children = (698, 699, 701, new)
    cards = [sent_back(), *stories(698, 699, 701, new), story(700, EPIC, state="CLOSED")]
    asked, _ = run(monkeypatch, issues, cards)
    assert asked == [] and (701, "not_planned") in issues.closed
    [split] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert f"#{new}" in split and "<!-- crew:resplit round=1 -->" in split


def test_a_problem_an_epics_first_split_raised_isnt_a_round(monkeypatch):
    """The criteria check sends back an epic never split: its split is its first."""
    issues = Issues([PROBLEM, ANSWER], children=())
    asked, seen = run(monkeypatch, issues, [sent_back()])
    assert len(asked) == 1
    [split] = [b for n, b in issues.posted if n == EPIC and STORY_SPLIT_MARKER in b]
    assert "crew:resplit" not in split
    assert not [e for e in seen if e.kind == EventKind.EPIC_RESPLIT]
    assert not any("crew:resplit-of" in i["body"] for i in issues.created)
    assert (EPIC, NEEDS_REWORK) in issues.removed_labels


def test_an_org_yaml_without_a_stop_still_stops_at_three(monkeypatch):
    import copy

    from crew_org.config import load_org
    from crew_org.flows.board_flow import PRODUCT_QUESTION_MARKER

    org = copy.deepcopy(load_org())
    del org["refinement"]["resplit_stop"]
    done = [f"{STORY_SPLIT_MARKER}\n<!-- crew:resplit round={k} -->" for k in (1, 2, 3)]
    issues = Issues([SPLIT, *done, PROBLEM])
    answered = []
    monkeypatch.setattr(
        "crew_org.flows.board_flow.answer_story_problem", lambda **kw: answered.append(kw)
    )
    asked, _ = run(monkeypatch, issues, [sent_back(), *stories(*FOUR)], org=org)
    assert answered == [] and asked == [] and issues.closed == []
    assert any(b.startswith(PRODUCT_QUESTION_MARKER) for n, b in issues.posted if n == EPIC)
