"""The Goal's look as a step of the tick (crew#858): when it runs, what it writes, and the turn.

The two roles' sessions and the Product Owner's are replaced here, and so is what
the look is shown (`gather`, tested in `test_goal_look.py`): this is the step around
them. The look runs once a Goal's epics are approved, writes the Goal's record and
target diagram, asks the Sponsor when a call is beyond the Goal, and reads only the
epics added since when it runs again. One Goal of a project refines at a time.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from crew_org.columns import INBOX
from crew_org.columns import NEEDS_REFINEMENT as REFINEMENT
from crew_org.crews import settle_crew
from crew_org.crews.goal_look_crew import APPROVED, EpicText, GoalContext, LookResult
from crew_org.crews.settle_crew import (
    GoalConclusion,
    GoalQuestion,
    GoalRow,
    GoalSettlement,
    Unsettled,
    check_goal_covers,
    check_goal_epics,
    look_items,
)
from crew_org.events import EventSink
from crew_org.flows import board_flow, goal_look
from crew_org.flows import record as record_flow
from crew_org.flows.board_flow import STORY_SPLIT_MARKER
from crew_org.rulebook import Refusal
from crew_org.tools.github_issues import Trust
from crew_org.tools.github_project import Card
from tests.test_goal_look import CONTAINERS, reader, system

TRUST = Trust(sponsor="mquarters", crew=frozenset({"mqucifer-crew[bot]"}), tools=frozenset())
REPO = "sprint-metrics"
OWNER = "mqucifer"


def ref(n: int, repo: str = REPO) -> str:
    return f"{OWNER}/{repo}#{n}"


class GitHub:
    """Issues and their comments, as the step reads and writes them."""

    owner = OWNER
    trust = TRUST

    def __init__(self, parents: dict[int, int] | None = None, bodies=None) -> None:
        self.parents = dict(parents or {})
        self.bodies = dict(bodies or {})
        self.threads: dict[int, list[dict]] = {}
        self.labels: dict[int, set[str]] = {}
        self.next = 0

    def get(self, repo, number):
        parent = self.parents.get(number)
        return {
            "number": number,
            "title": f"Issue {number}",
            "body": self.bodies.get(number, ""),
            "parent_issue_url": f"https://api.github.com/x/issues/{parent}" if parent else None,
        }

    def comments(self, repo, number):
        return [dict(c) for c in self.threads.get(number, [])]

    def comment(self, repo, number, body, *, by="mqucifer-crew[bot]"):
        self.next += 1
        found = {
            "id": self.next,
            "body": body,
            "user": {"login": by},
            "html_url": f"https://github.com/{OWNER}/{repo}/issues/{number}#c{self.next}",
            "created_at": f"2026-10-22T10:00:{self.next:02d}Z",
        }
        self.threads.setdefault(number, []).append(found)
        return found

    def edit_comment(self, repo, comment_id, body):
        for thread in self.threads.values():
            for c in thread:
                if c["id"] == comment_id:
                    c["body"] = body

    def edit_issue(self, repo, number, *, body):
        self.bodies[number] = body

    def has_comment_marked(self, repo, number, marker):
        return any(marker in c["body"] for c in self.threads.get(number, []))

    def add_labels(self, repo, number, labels):
        self.labels.setdefault(number, set()).update(labels)

    def remove_label(self, repo, number, name):
        self.labels.get(number, set()).discard(name)

    def ensure_label(self, repo, name, **kw):
        pass

    def marked(self, number, marker):
        return [c for c in self.threads.get(number, []) if marker in c["body"]]


def epic(n, goal, status=REFINEMENT, repo=REPO, labels=()):
    return Card(
        item_id=f"E{n}",
        number=n,
        title=f"Epic {n}",
        status=status,
        state="OPEN",
        work_type="Epic",
        parent=goal,
        repo=repo,
        labels=frozenset(labels),
    )


def goal(n, priority="P1", rank=None, repo=REPO):
    return Card(
        item_id=f"G{n}",
        number=n,
        title=f"Goal {n}",
        status=INBOX,
        state="OPEN",
        work_type="Goal",
        repo=repo,
        priority=priority,
        rank=rank,
    )


class Roles:
    """The look's and the settle's sessions, replaced: what each was asked, and answers."""

    def __init__(self, monkeypatch, *, settlements=()):
        self.looks: list[GoalContext] = []
        self.settles: list[tuple[GoalContext, str]] = []
        self.settlements = list(settlements)
        self.system = system()
        self.reader = None

        def gather(issues, repo, number, *, approved, search):
            record = record_flow.goal_record_comment(issues, repo, number)
            text = record_flow.comment_text(record["body"]) if record else ""
            covered = set(record_flow.parse(text).looked_at) if text else set()
            return GoalContext(
                goal_ref=ref(number),
                goal="Goal: a sprint at a glance\n\n## Who reads it\n\nthe Sponsor",
                decisions="",
                epics=[
                    EpicText(ref(n), "looked at" if ref(n) in covered else APPROVED, f"# {n}")
                    for n in approved
                ]
                + [EpicText(r, "looked at", "# done") for r in sorted(covered)],
                record=text,
                diagram=goal_look.diagram_of(issues, repo, number),
            )

        def run_look(context, reading=None):
            self.looks.append(context)
            return LookResult(self.system, self.reader or reader(*context.looking_at), {})

        def settle_goal(context, look, *, reply="", reading=None):
            self.settles.append((context, reply))
            return self.settlements.pop(0)

        monkeypatch.setattr(goal_look, "gather", gather)
        monkeypatch.setattr(goal_look, "run_look", run_look)
        monkeypatch.setattr(goal_look, "settle_goal", settle_goal)


def row(*epics: str, settles=(1,), **kw) -> GoalRow:
    fields = dict(
        context="Both charts",
        decision="One chart, shared",
        consequences="Stories reuse it",
        source="the Goal's reader",
        settles=list(settles),
        epics=list(epics) or ["all"],
    )
    return GoalRow(**{**fields, **kw})


def settled(*rows: GoalRow) -> GoalSettlement:
    return GoalSettlement(conclusion=GoalConclusion(rows=list(rows) or [row()]))


def step(gh, card, cards, turns=None):
    result = SimpleNamespace(skipped=[], failed=[])
    ok = goal_look.look_step(
        None,
        gh,
        EventSink(None),
        result,
        card,
        REPO,
        cards=cards,
        turns=turns or goal_look.Turns(gh, cards, []),
        search=[REPO],
    )
    return ok, result


def the_record(gh, number=666) -> record_flow.Record:
    [held] = gh.marked(number, record_flow.GOAL_RECORD_MARKER)
    return record_flow.parse(record_flow.comment_text(held["body"]))


# --- the first look ---------------------------------------------------------------------------


def test_the_first_look_writes_the_goals_record_and_its_diagram(monkeypatch):
    gh = GitHub({684: 666, 685: 666})
    roles = Roles(monkeypatch, settlements=[settled()])
    cards = [goal(666), epic(684, 666), epic(685, 666)]

    ok, result = step(gh, cards[1], cards)

    assert ok and result.failed == [] and result.skipped == []
    assert [c.looking_at for c in roles.looks] == [[ref(684), ref(685)]]
    found = the_record(gh)
    assert found.on_goal and found.looked_at == (ref(684), ref(685))
    by = {(d.set_by, d.binds) for d in found.decisions}
    # The Architect's decision binds every epic, the reader's criteria none here,
    # and the Product Owner's row as it named.
    assert (record_flow.ARCHITECT, (record_flow.ALL,)) in by
    assert (record_flow.PRODUCT_OWNER, (record_flow.ALL,)) in by
    [diagram] = gh.marked(666, goal_look.DIAGRAM_MARKER)
    assert "```mermaid\nC4Container" in diagram["body"]
    assert 'UpdateElementStyle(events, $borderColor="green")' in diagram["body"]
    assert goal_look.diagram_of(gh, REPO, 666).startswith("C4Container")
    assert gh.marked(666, goal_look.LOOK_MARKER)


def test_the_goals_other_epics_go_on_from_the_same_record(monkeypatch):
    gh = GitHub({684: 666, 685: 666})
    roles = Roles(monkeypatch, settlements=[settled()])
    cards = [goal(666), epic(684, 666), epic(685, 666)]
    step(gh, cards[1], cards)
    ok, _ = step(gh, cards[2], cards)
    assert ok and len(roles.looks) == 1 and len(roles.settles) == 1


def test_the_readers_criteria_bind_the_epic_they_were_written_for(monkeypatch):
    gh = GitHub({684: 666, 685: 666})
    roles = Roles(monkeypatch, settlements=[settled()])
    roles.reader = reader(ref(684), ref(685))
    roles.reader.epics[0].criteria = ["At 375 px the chart fits without scrolling sideways"]
    cards = [goal(666), epic(684, 666), epic(685, 666)]
    step(gh, cards[1], cards)
    [criterion] = [d for d in the_record(gh).decisions if d.set_by == record_flow.UX_DESIGNER]
    assert criterion.binds == (ref(684),)
    seen = the_record(gh).for_epic(ref(685))
    assert criterion.id not in seen.binding_ids()


def test_a_look_with_nothing_for_the_product_owner_writes_the_record_without_a_settle(
    monkeypatch,
):
    gh = GitHub({684: 666})
    roles = Roles(monkeypatch)
    roles.system = system(callouts=[])
    cards = [goal(666), epic(684, 666)]
    ok, _ = step(gh, cards[1], cards)
    assert ok and roles.settles == [] and the_record(gh).looked_at == (ref(684),)


def test_an_epic_split_before_the_goal_had_a_record_goes_on_from_its_own(monkeypatch):
    own = (
        "# 684\n\nThe approved text.\n\n## Refinement conclusion\n\n"
        "| R1 | binding | a | b | c | Product Owner | 2026-10-01 | — | the Goal |"
    )
    gh = GitHub({684: 666, 685: 666}, bodies={684: own})
    roles = Roles(monkeypatch)
    cards = [goal(666), epic(684, 666), epic(685, 666, status=INBOX)]
    ok, _ = step(gh, cards[1], cards)
    assert ok and roles.looks == []


def test_an_epic_with_no_goal_is_split_as_before(monkeypatch):
    roles = Roles(monkeypatch)
    technical = epic(700, None, labels=("technical",))
    ok, _ = step(GitHub(), technical, [technical])
    assert ok and roles.looks == []


# --- what holds it ------------------------------------------------------------------------------


def test_an_epic_still_waiting_for_the_sponsor_holds_the_look_and_is_named(monkeypatch):
    gh = GitHub({684: 666, 686: 666})
    roles = Roles(monkeypatch)
    cards = [goal(666), epic(684, 666), epic(686, 666, status=INBOX)]
    ok, result = step(gh, cards[1], cards)
    assert not ok and roles.looks == []
    assert result.skipped == [
        (684, f"waits for the Goal's look: {ref(686)} still waits for the Sponsor")
    ]


def test_an_epic_parked_back_in_inbox_holds_its_goal_too(monkeypatch):
    """The Sponsor, 2026-10-22: holding one epic holds the Goal, naming the epic."""
    gh = GitHub({684: 666, 685: 666})
    roles = Roles(monkeypatch, settlements=[settled()])
    cards = [goal(666), epic(684, 666), epic(685, 666, status=INBOX, labels=("needs:human",))]
    ok, result = step(gh, cards[1], cards)
    assert not ok and ref(685) in result.skipped[0][1] and roles.looks == []


# --- the Sponsor's question -----------------------------------------------------------------


def test_a_call_beyond_the_goal_is_asked_on_the_goal_and_it_waits(monkeypatch):
    gh = GitHub({684: 666})
    roles = Roles(monkeypatch, settlements=[GoalSettlement(sponsor_question="Weeks or sprints?")])
    cards = [goal(666), epic(684, 666)]
    ok, result = step(gh, cards[1], cards)
    assert not ok and result.skipped == [(684, "waits for the Sponsor's answer on the Goal")]
    [asked] = gh.marked(666, goal_look.GOAL_QUESTION_MARKER)
    assert "Weeks or sprints?" in asked["body"] and "needs:human" in gh.labels[666]
    assert not gh.marked(666, record_flow.GOAL_RECORD_MARKER)

    # Nothing is asked again, and no model is called, until the Sponsor replies.
    ok, _ = step(gh, cards[1], cards)
    assert not ok and len(roles.settles) == 1 and len(roles.looks) == 1

    gh.comment(REPO, 666, "Sprints.", by="a-stranger")
    assert not step(gh, cards[1], cards)[0]
    gh.comment(REPO, 666, "Sprints.", by="mquarters")
    roles.settlements.append(settled())
    ok, _ = step(gh, cards[1], cards)
    assert ok and roles.settles[-1][1] == "Sprints." and len(roles.looks) == 1
    assert the_record(gh).looked_at == (ref(684),)


# --- a later look ---------------------------------------------------------------------------


def test_a_later_look_reads_only_the_new_epic_and_changes_no_row(monkeypatch):
    gh = GitHub({684: 666, 687: 666})
    roles = Roles(monkeypatch, settlements=[settled()])
    first = [goal(666), epic(684, 666)]
    step(gh, first[1], first)
    before = the_record(gh)

    # The Sponsor asked for an epic the Goal lacked; it was proposed and approved.
    roles.settlements.append(settled(row(ref(687))))
    later = [*first, epic(687, 666)]
    ok, _ = step(gh, later[2], later)

    assert ok and roles.looks[-1].looking_at == [ref(687)]
    after = the_record(gh)
    assert after.decisions[: len(before.decisions)] == before.decisions
    added = after.decisions[len(before.decisions) :]
    assert added and all(d.binds for d in added)
    assert after.looked_at == (ref(684), ref(687))
    # The diagram is a new version; the one before stays, marked as replaced.
    assert len(gh.marked(666, goal_look.DIAGRAM_MARKER)) == 1
    assert len(gh.marked(666, "crew:goal-diagram-replaced")) == 1


# --- one Goal refines at a time -------------------------------------------------------------


def split(gh, *numbers):
    for n in numbers:
        gh.comment(REPO, n, f"{STORY_SPLIT_MARKER}\nsplit")


def test_a_second_goal_waits_until_the_first_goals_epics_are_all_split(monkeypatch):
    gh = GitHub({684: 666, 685: 666, 701: 700})
    roles = Roles(monkeypatch, settlements=[settled(), settled()])
    cards = [goal(666), goal(700, "P2"), epic(684, 666), epic(685, 666), epic(701, 700)]

    ok, result = step(gh, cards[4], cards)
    assert not ok and result.skipped == [(701, f"waits for {ref(666)}: one Goal refines at a time")]

    assert step(gh, cards[2], cards)[0]
    split(gh, 684)
    assert not step(gh, cards[4], cards)[0], "685 isn't split yet"
    split(gh, 685)
    ok, _ = step(gh, cards[4], cards)
    assert ok and [c.goal_ref for c in roles.looks] == [ref(666), ref(700)]


def test_the_next_goal_may_be_proposed_and_approved_meanwhile(monkeypatch):
    gh = GitHub({684: 666, 701: 700, 702: 700})
    Roles(monkeypatch, settlements=[settled()])
    cards = [goal(666), goal(700), epic(684, 666), epic(701, 700, status=INBOX)]
    turns = goal_look.Turns(gh, cards, [])
    # Unapproved, Goal 700's epics take no turn; Goal 666 refines.
    assert turns.refining(REPO) == 666


def test_a_goal_in_another_repository_is_not_held(monkeypatch):
    gh = GitHub({684: 666, 31: 30})
    roles = Roles(monkeypatch, settlements=[settled()])
    other = epic(31, 30, repo="crew-presentation")
    cards = [goal(666), epic(684, 666), goal(30, "P0", repo="crew-presentation"), other]
    gh.comment("crew-presentation", 30, goal_look.LOOK_MARKER)
    ok, _ = step(gh, cards[1], cards)
    assert ok and roles.looks[0].goal_ref == ref(666)


def test_three_goals_approved_at_once_refine_one_after_another_in_admissions_order(
    monkeypatch,
):
    gh = GitHub({11: 10, 21: 20, 31: 30})
    roles = Roles(monkeypatch, settlements=[settled(), settled(), settled()])
    cards = [
        goal(10, "P2"),
        goal(20, "P1", rank=2.0),
        goal(30, "P1", rank=1.0),
        epic(11, 10),
        epic(21, 20),
        epic(31, 30),
    ]
    epics = cards[3:]
    order = []
    for _ in range(6):  # more passes than Goals: nothing waits on itself
        turns = goal_look.Turns(gh, cards, [])
        for card in epics:
            if gh.has_comment_marked(REPO, card.number, STORY_SPLIT_MARKER):
                continue
            ok, _ = step(gh, card, cards, turns)
            if ok:
                split(gh, card.number)
                order.append(card.parent)
    assert order == [30, 20, 10]
    assert [c.goal_ref for c in roles.looks] == [ref(30), ref(20), ref(10)]


def test_a_goal_that_has_begun_keeps_its_turn_over_a_higher_priority_one(monkeypatch):
    gh = GitHub({684: 666, 685: 666, 901: 900})
    cards = [goal(666, "P2"), goal(900, "P0"), epic(684, 666), epic(685, 666), epic(901, 900)]
    gh.comment(REPO, 666, goal_look.LOOK_MARKER)
    assert goal_look.Turns(gh, cards, []).refining(REPO) == 666


def test_a_technical_epic_is_never_held_and_takes_no_turn(monkeypatch):
    gh = GitHub({684: 666})
    roles = Roles(monkeypatch)
    technical = epic(800, None, labels=("technical",))
    cards = [goal(666), epic(684, 666), technical]
    assert goal_look.Turns(gh, cards, []).refining(REPO) == 666
    ok, result = step(gh, technical, cards)
    assert ok and result.skipped == [] and roles.looks == []


def test_an_epic_split_this_pass_counts_as_split():
    gh = GitHub({684: 666})
    cards = [goal(666), epic(684, 666)]
    assert goal_look.Turns(gh, cards, [684]).refining(REPO) is None


# --- the Product Owner's settle -------------------------------------------------------------


def look(**system_overrides) -> LookResult:
    return LookResult(system(**system_overrides), reader(ref(684)), {})


def test_the_items_left_to_the_product_owner_are_numbered_in_a_fixed_order():
    from crew_org.crews.goal_look_crew import BeyondTheGoal

    found = look(beyond_the_goal=[BeyondTheGoal(question="Weeks?", why="The Goal says neither")])
    found.reader.epics[0].missing = "the units on the axis"
    kinds = [kind for _, kind, _ in look_items(found)]
    assert kinds == ["risk", "missing", "beyond"]


def test_every_item_is_answered_and_none_that_isnt_there():
    items = look_items(look())
    check_goal_covers(settled(row(settles=[1])), items)
    with pytest.raises(Unsettled, match="No row, question or dismissal for N1"):
        check_goal_covers(
            GoalSettlement(
                conclusion=GoalConclusion(
                    open=[
                        GoalQuestion(
                            question="Which?", impact="the chart", settles=[2], epics=["all"]
                        )
                    ]
                )
            ),
            items,
        )


def test_a_row_names_only_epics_the_look_read():
    check_goal_epics(settled(row(ref(684))), [ref(684)])
    with pytest.raises(Refusal, match="#999 aren't epics the look read"):
        check_goal_epics(settled(row(ref(999))), [ref(684)])


def test_a_settlement_is_a_record_or_one_question_never_both():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GoalSettlement()
    with pytest.raises(ValidationError):
        GoalSettlement(conclusion=GoalConclusion(), sponsor_question="Why?")


def test_a_call_beyond_the_goal_is_asked_before_it_is_settled(monkeypatch):
    from crew_org.crews.goal_look_crew import BeyondTheGoal
    from crew_org.session import steps

    seen = {}
    monkeypatch.setattr(
        steps, "run", lambda step, parts, reading, **kw: seen.update(kw) or settled(row())
    )
    found = look(callouts=[], beyond_the_goal=[BeyondTheGoal(question="Weeks?", why="unsaid")])
    context = GoalContext(goal_ref=ref(666), goal="G", decisions="", epics=[])
    settle_crew.settle_goal(context, found)
    with pytest.raises(Unsettled, match="N1 reach beyond the Goal"):
        seen["check"](settled(row(settles=[1])))
    seen["check"](GoalSettlement(sponsor_question="Weeks or sprints?"))


def test_an_own_call_stays_within_the_goals_intent():
    from crew_org.crews.settle_crew import check_grounded

    goal_text = "## Who reads it\n\nthe Sponsor\n\n## What's out\n\nthe CLI"
    check_grounded(settled(row(own_call=True, serves="the Sponsor")), goal_text)
    with pytest.raises(Unsettled):
        check_grounded(settled(row(own_call=True, serves="the CLI")), goal_text)


# --- the Sponsor's words on the Goal ---------------------------------------------------------


def test_the_sponsors_words_on_the_goal_bind_every_epic_and_on_an_epic_that_epic(monkeypatch):
    gh = GitHub({684: 666})
    Roles(monkeypatch, settlements=[settled()])
    cards = [goal(666), epic(684, 666)]
    step(gh, cards[1], cards)
    gh.comment(REPO, 666, "Show weeks, not sprints.", by="mquarters")
    gh.comment(REPO, 684, "Keep the legend.", by="mquarters")
    placed = SimpleNamespace(context="Units", consequences="Stories say weeks", replaces="")
    monkeypatch.setattr(board_flow, "place_sponsor_words", lambda **kw: placed)

    result = board_flow.TickResult()
    assert board_flow.record_sponsor_words(gh, EventSink(None), result, cards[1], REPO)

    rows = {d.decision: d for d in the_record(gh).decisions if d.set_by == record_flow.SPONSOR}
    assert rows['"Show weeks, not sprints."'].binds == (record_flow.ALL,)
    assert rows['"Keep the legend."'].binds == (ref(684),)
    # Each once: the next pass finds them in the record.
    assert board_flow.record_sponsor_words(gh, EventSink(None), result, cards[1], REPO)
    assert len([d for d in the_record(gh).decisions if d.set_by == record_flow.SPONSOR]) == 2


def test_the_old_panel_key_is_a_startup_error(tmp_path):
    from crew_org.config import load_org

    org = tmp_path / "org.yaml"
    from pathlib import Path

    text = Path("src/crew_org/config/org.yaml").read_text().replace("look: true", "panel: true")
    org.write_text(text)
    with pytest.raises(ValueError, match="Rename it to refinement.look"):
        load_org(org)


def test_the_diagram_is_read_back_as_it_was_drawn():
    gh = GitHub()
    goal_look.post_diagram(gh, EventSink(None), repo=REPO, goal=666, system=system(new=[]))
    assert goal_look.diagram_of(gh, REPO, 666) == CONTAINERS.strip()


def test_the_look_records_how_big_its_context_is(monkeypatch):
    """crew#468: the decisions and the project's log are counted before they crowd it."""
    gh = GitHub({684: 666})
    Roles(monkeypatch, settlements=[settled()])
    events = []
    sink = EventSink(None)
    sink.subscribe(events.append)
    cards = [goal(666), epic(684, 666)]
    result = SimpleNamespace(skipped=[], failed=[])
    goal_look.look_step(
        None,
        gh,
        sink,
        result,
        cards[1],
        REPO,
        cards=cards,
        turns=goal_look.Turns(gh, cards, []),
        search=[REPO],
    )
    [note] = [e for e in events if "look context" in e.summary]
    assert note.detail["decisions_chars"] == 0 and note.detail["epics"] == 1


# --- what the split, the Developer, the Code Reviewer and QA are shown ------------------------


def goal_records(gh):
    """Goal 666's record and target diagram, as its look leaves them."""
    binds_first, binds_second = (ref(684),), (ref(685),)
    held = record_flow.Record(
        decisions=(
            record_flow.Decision(
                "R1",
                "Store",
                "One table",
                "-",
                "the Goal",
                "Architect",
                "2026-10-22",
                binds=(record_flow.ALL,),
            ),
            record_flow.Decision(
                "R2",
                "Units",
                "Weeks",
                "-",
                "the reader",
                "UX Designer",
                "2026-10-22",
                binds=binds_first,
            ),
            record_flow.Decision(
                "R3",
                "Legend",
                "On the right",
                "-",
                "the reader",
                "UX Designer",
                "2026-10-22",
                binds=binds_second,
            ),
        ),
        on_goal=True,
        looked_at=(ref(684), ref(685)),
    )
    gh.comment(REPO, 666, f"{record_flow.GOAL_RECORD_MARKER}\n{record_flow.render(held)}\n")
    goal_look.post_diagram(gh, EventSink(None), repo=REPO, goal=666, system=system(new=[]))


def test_the_split_is_shown_the_goals_rows_that_bind_its_epic_and_the_diagram(monkeypatch):
    from crew_org.config import load_org
    from tests.test_board_flow import FakeBoard
    from tests.test_split_conclusion import proposal, story

    class Issues(GitHub):
        def create(self, repo, title, body, labels=None):
            self.next += 1
            self.bodies[self.next] = body
            return {"number": self.next, "id": self.next, "node_id": f"N{self.next}"}

        def add_sub_issue(self, *a):
            pass

        def sub_issues(self, repo, number):
            return []

        def list_dir(self, *a):
            return []

        def branches(self, repo):
            return []

    gh = Issues({684: 666, 685: 666}, bodies={684: "The approved text."})
    goal_records(gh)
    asked = []

    def split(title, context="", **kw):
        asked.append((context, kw))
        return proposal(story("A", "R1", "R2"))

    monkeypatch.setattr(board_flow, "split_epic", split)
    board_flow.refine_epics(
        FakeBoard([epic(684, 666), epic(685, 666)]),
        gh,
        EventSink(None),
        board_flow.ProcessRules.from_config(load_org()),
        board_flow.TickResult(),
        cards=[epic(684, 666)],
        default_repo=REPO,
        context=board_flow.RepoContext(None, EventSink(None)),
    )
    context, kw = asked[0]
    assert context == "The approved text."
    assert kw["conclusion"].startswith(record_flow.GOAL_HEADER)
    assert list(kw["rows"]) == ["R1", "R2"], "R3 binds the other epic"
    assert kw["diagram"].startswith("C4Container")
    [made] = [b for n, b in gh.bodies.items() if n not in (684,) and "As a" in b]
    assert "Follows the Goal's R1, R2." in made
    [split_comment] = gh.marked(684, STORY_SPLIT_MARKER)
    assert "[target diagram](https://github.com/" in split_comment["body"]


def test_a_story_is_built_and_judged_with_the_goals_target_diagram():
    from crew_org.flows import conclusion

    gh = GitHub({684: 666})
    goal_records(gh)
    story = SimpleNamespace(parent=684, number=700, repo=REPO)
    assert conclusion.story_diagram(gh, story, REPO).startswith("C4Container")
    assert conclusion.story_diagram(gh, SimpleNamespace(parent=None), REPO) == ""
    no_goal = SimpleNamespace(parent=900, number=901, repo=REPO)
    assert conclusion.story_diagram(gh, no_goal, REPO) == ""


def test_the_developer_reviewer_and_qa_are_told_what_the_diagram_is_for(monkeypatch):
    from crew_org.crews import qa_crew, review_crew
    from crew_org.flows.session_delivery import story_section
    from tests import asked

    assert "### The Goal's target diagram\n\n```mermaid\nC4Context" in story_section(
        "Story", "", "", 'C4Context\nPerson(a, "A")'
    )
    for call in (
        lambda: review_crew.review_diff("t", "diff", diagram="C4Context"),
        lambda: qa_crew.verify_story("s", test_output="", test_code="", diagram="C4Context"),
    ):
        seen = asked.capture(monkeypatch)
        with pytest.raises(asked.Stop):
            call()
        assert "## The Goal's target diagram\n\n```mermaid\nC4Context" in seen["description"]


def test_the_product_owner_answers_a_story_problem_shown_the_goals_rows(monkeypatch):
    gh = GitHub({684: 666}, bodies={684: "The approved text."})
    goal_records(gh)
    text = board_flow._with_record(gh, REPO, 684)
    assert text.startswith("The approved text.") and "| R2 |" in text and "| R3 |" not in text


def test_what_a_delivered_epic_lacks_is_no_item_and_its_criteria_no_rows():
    """mqucifer/sprint-metrics#666's real look: the UX Designer also wrote views of two
    delivered epics, whose items the Product Owner couldn't bind to any epic it read."""
    found = LookResult(system(callouts=[]), reader(ref(684), ref(685)), {})
    for view in found.reader.epics:
        view.missing, view.criteria = "the units", ["The chart's axis says weeks"]
    assert [text for _, _, text in look_items(found, [ref(684)])] == [
        f"Missing for the reader of {ref(684)}: the units (the UX Designer)"
    ]
    rows = goal_look.rows_from(
        found,
        None,
        owner=OWNER,
        repo=REPO,
        today="2026-10-22",
        source="x",
        looked_at=[ref(684)],
    )[0]
    assert [d.binds for d in rows if d.set_by == record_flow.UX_DESIGNER] == [(ref(684),)]


# --- the operator's review of mqucifer/crew#864 ----------------------------------------------


def post_look(gh, goal_number, *epic_refs):
    """A look on the Goal for these epics, as the step posts it."""
    context = GoalContext(
        goal_ref=ref(goal_number),
        goal="G",
        decisions="",
        epics=[EpicText(r, APPROVED, "# e") for r in epic_refs],
    )
    found = LookResult(system(), reader(*epic_refs), {})
    gh.comment(REPO, goal_number, goal_look.render(found, context))


def post_record(gh, goal_number, *covered):
    held = record_flow.Record(on_goal=True, looked_at=tuple(covered))
    gh.comment(REPO, goal_number, f"{record_flow.GOAL_RECORD_MARKER}\n{record_flow.render(held)}\n")


def test_an_old_record_with_a_question_for_the_architect_is_split_not_held(monkeypatch):
    """The design settle that answered such questions is gone; nothing would ever lift a
    hold on them, so the split goes on and writes no criterion on the question."""
    from tests.test_split_conclusion import BODY, Concluded, proposal, run_tick, story

    open_for_architect = BODY.replace(
        "| Q1 | Which key? | The store | Implementer |",
        "| Q1 | Which key? | The store | Architect |",
    )
    assert record_flow.parse(record_flow.split(open_for_architect)[1]).for_architect()

    class Old(Concluded):
        def get(self, repo, number):
            return {"body": open_for_architect, "state": "open"}

    result, asked, _ = run_tick(
        monkeypatch, proposal(story("A", "R1", "R2"), story("B", "R3")), issues=Old()
    )
    assert len(asked) == 1 and len(result.stories_created) == 2
    assert not any("still open for the Architect" in why for _, why in result.skipped)


def test_a_goal_mid_look_keeps_its_turn_from_one_that_refined_before():
    """A refined earlier; a new epic of A, which is higher priority, is approved while B
    waits on the Sponsor mid-look. B keeps the turn."""
    gh = GitHub({11: 10, 12: 10, 21: 20})
    post_look(gh, 10, ref(11))
    post_record(gh, 10, ref(11))
    split(gh, 11)
    post_look(gh, 20, ref(21))
    gh.comment(REPO, 20, f"{goal_look.GOAL_QUESTION_MARKER}\nWeeks or sprints?")
    cards = [goal(10, "P0"), goal(20, "P2"), epic(11, 10), epic(12, 10), epic(21, 20)]
    assert goal_look.Turns(gh, cards, []).refining(REPO) == 20
    # Once B's settle is written, A's new epic takes the turn.
    post_record(gh, 20, ref(21))
    split(gh, 21)
    assert goal_look.Turns(gh, cards, []).refining(REPO) == 10


def test_the_sponsors_answer_to_the_goals_question_is_not_also_a_row_of_their_words(
    monkeypatch,
):
    gh = GitHub({684: 666, 687: 666})
    roles = Roles(monkeypatch, settlements=[settled()])
    first = [goal(666), epic(684, 666)]
    step(gh, first[1], first)

    # A later look asks the Sponsor, and the Sponsor answers on the Goal.
    roles.settlements.append(GoalSettlement(sponsor_question="Weeks or sprints?"))
    later = [*first, epic(687, 666)]
    assert not step(gh, later[2], later)[0]
    answer = gh.comment(REPO, 666, "Sprints.", by="mquarters")
    placed = SimpleNamespace(context="Units", consequences="-", replaces="")
    monkeypatch.setattr(board_flow, "place_sponsor_words", lambda **kw: placed)

    def sponsors_rows():
        return [d for d in the_record(gh).decisions if d.set_by == record_flow.SPONSOR]

    # A covered epic's pass comes first: the answer waits for the settle that reads it.
    board_flow.record_sponsor_words(gh, EventSink(None), board_flow.TickResult(), first[1], REPO)
    assert sponsors_rows() == []

    roles.settlements.append(settled(row(ref(687))))
    assert step(gh, later[2], later)[0]
    assert str(answer["id"]) in the_record(gh).replies
    board_flow.record_sponsor_words(gh, EventSink(None), board_flow.TickResult(), first[1], REPO)
    assert sponsors_rows() == []


def test_a_resplit_of_a_refined_goals_epic_waits_for_no_other_goal(monkeypatch):
    gh = GitHub({684: 666, 701: 700})
    roles = Roles(monkeypatch)
    post_look(gh, 666, ref(684))
    post_record(gh, 666, ref(684))
    split(gh, 684)
    post_look(gh, 700, ref(701))  # Goal 700 is mid-look, so it holds the turn
    cards = [
        goal(666, "P0"),
        goal(700, "P2"),
        epic(684, 666, labels=("needs:rework",)),
        epic(701, 700),
    ]
    assert goal_look.Turns(gh, cards, []).refining(REPO) == 700
    ok, result = step(gh, cards[2], cards)
    assert ok and result.skipped == [] and roles.looks == []
