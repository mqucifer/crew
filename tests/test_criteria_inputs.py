"""QA's criteria check is given what it works out today (crew#839).

On the retire Goal, sprint-metrics#749, the check ran long: a re-check of one repaired
story took 33 minutes, deliberating over what its start left open. It started blank
after a repair, couldn't tell which story lands first or whether a planned one was
still live, read criteria cut at 2,000 characters and a record a third repeated, and
found merged tests pinning a removal by reading, where code can list them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_org.crews.criteria_crew import CriteriaCheck, CriteriaConflict, opening
from crew_org.crews.refinement_crew import (
    AcceptanceCriterion,
    CriteriaRepair,
    RepairedStory,
    Story,
    StoryProposal,
)
from crew_org.flows import board_flow, criteria_check, removed_tests
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from crew_org.rulebook import Refusal
from tests.test_board_flow import SPLIT, FakeIssues, make_story
from tests.test_criteria_check import (
    FIXED,
    ON_CYCLE_TIME,
    Checker,
    Repairer,
    run_split_checked,
    seen_events,
)

# --- 1. A re-check is a re-check --------------------------------------------------


def test_a_recheck_is_told_what_it_found_and_what_the_repair_changed():
    was = make_story("Cycle time", 3)
    now = was.model_copy(
        update={"acceptance_criteria": FIXED, "pinned_behaviour": "opt-in: new flag"}
    )
    before = criteria_check.earlier([was], ON_CYCLE_TIME)
    seen = []

    def check(**shown):
        seen.append(shown)
        return CriteriaCheck()

    proposal = StoryProposal(epic_title="E", stories=[now, make_story("Throughput", 2)])
    criteria_check.check_each(check, proposal, only=[now], before=before)
    [shown] = seen
    told = shown["recheck"]
    assert "This is a re-check" in told and "expects 6" in told, "the conflict it found"
    assert "Criterion 1, before: Given data exists / When I run it / Then I see a table" in told
    assert "after: Given data exists / When I run it / Then the cycle time is 4" in told
    assert "Existing tests, before: (none)" in told and "after: opt-in: new flag" in told
    assert "is each conflict above resolved" in told
    assert "### Throughput" in shown["siblings"], "still checked against the others"
    assert shown["noted"] == {"story": "Cycle time", "recheck": True, "precheck_tests": 0}
    # The re-check part comes straight after its step.
    parts = list(opening(stories="s", recheck=told, goal="g"))
    assert parts[:2] == ["step", "recheck"]


def test_a_first_check_is_told_nothing_of_a_recheck():
    seen = []

    def check(**shown):
        seen.append(shown)
        return CriteriaCheck()

    criteria_check.check_each(check, SPLIT)
    assert all(s["recheck"] == "" and s["noted"]["recheck"] is False for s in seen)


def test_a_story_the_repair_left_unchanged_keeps_its_conflicts_with_no_session():
    unchanged = make_story("Cycle time", 3)
    before = criteria_check.earlier([unchanged], ON_CYCLE_TIME)
    calls = []

    def check(**shown):
        calls.append(shown)
        return CriteriaCheck()

    proposal = StoryProposal(epic_title="E", stories=[unchanged, make_story("Throughput", 2)])
    found = criteria_check.check_each(check, proposal, only=[unchanged], before=before)
    assert calls == [] and found.conflicts == ON_CYCLE_TIME.conflicts


def test_an_unchanged_storys_conflicts_reach_the_product_owner(monkeypatch):
    """A repair that leaves the flagged story as it was: no re-check session, and its
    conflict is the story problem a re-checked one would be."""
    checker = Checker(ON_CYCLE_TIME)
    unchanged = make_story("Cycle time", 3).acceptance_criteria
    repairer = Repairer(
        CriteriaRepair(stories=[RepairedStory(title="Cycle time", acceptance_criteria=unchanged)])
    )
    monkeypatch.setattr(board_flow, "repair_criteria", repairer)
    issues = FakeIssues()
    result = run_split_checked(monkeypatch, issues, checker)
    assert len(checker.calls) == len(SPLIT.stories), "no session for the re-check"
    assert result.stories_created == [] and (3, NEEDS_REWORK) in issues.added_labels
    body = [b for n, b in issues.posted if n == 3][-1]
    assert STORY_PROBLEM_MARKER in body and "expects 6" in body


def test_the_tick_re_checks_with_what_it_found(monkeypatch):
    checker = Checker(ON_CYCLE_TIME, CriteriaCheck())
    repairer = Repairer(
        CriteriaRepair(stories=[RepairedStory(title="Cycle time", acceptance_criteria=FIXED)])
    )
    monkeypatch.setattr(board_flow, "repair_criteria", repairer)
    run_split_checked(monkeypatch, FakeIssues(), checker)
    first, again = checker.calls[: len(SPLIT.stories)], checker.calls[len(SPLIT.stories) :]
    assert all(c["recheck"] == "" for c in first)
    assert len(again) == 1 and "expects 6" in again[0]["recheck"]


def test_a_recheck_is_recorded_as_one_with_what_it_kept():
    from crew_org import events
    from crew_org.events import EventKind

    found = seen_events()
    unchanged, changed = make_story("Cycle time", 3), make_story("Throughput", 2)
    conflicts = CriteriaCheck(
        conflicts=[
            *ON_CYCLE_TIME.conflicts,
            CriteriaConflict(story="Throughput", criterion="c", against="a", why="w"),
        ]
    )
    before = criteria_check.earlier([unchanged, changed], conflicts)
    repaired = changed.model_copy(update={"acceptance_criteria": FIXED})

    def check(*, on_end, **shown):
        on_end(
            SimpleNamespace(calls=2, model_s=9.0, usage={"completion_tokens": 30}),
            SimpleNamespace(how="done"),
        )
        return CriteriaCheck()

    proposal = StoryProposal(epic_title="E", stories=[unchanged, repaired])
    try:
        criteria_check.check_each(check, proposal, only=[unchanged, repaired], before=before)
    finally:
        events.reset_bridge()
    [note] = [
        e for e in found if e.kind == EventKind.NOTE and e.detail.get("step") == "criteria check"
    ]
    assert note.detail["recheck"] is True
    assert note.detail["unchanged"] == {"Cycle time": 1}
    assert set(note.detail["sessions"]) == {"Throughput"}
    assert note.detail["precheck_tests"] == {"Throughput": 0}


# --- 2. Order and status ----------------------------------------------------------


def test_each_story_shows_what_it_builds_on_and_the_rows_it_follows():
    story = make_story("Cycle time").model_copy(update={"builds_on": [759, 760], "follows": ["R4"]})
    text = criteria_check.render_stories([story])
    assert "Builds on: #759, #760" in text and "Follows: R4" in text


def card(number, title, status, parent=None, work_type="Story"):
    return SimpleNamespace(
        number=number, title=title, status=status, parent=parent, work_type=work_type
    )


class Bodies:
    def __init__(self, criteria: str) -> None:
        self.criteria = criteria

    def get(self, repo, number):
        criteria = f"## Acceptance criteria\n\n{self.criteria}"
        return {"body": f"As a user.\n\n{criteria}\n\n**Estimate** — 2"}


def test_planned_stories_come_under_their_epic_with_each_status_and_whole():
    long = "1. **Then** " + "x" * 3000 + " the end"
    cards = [
        card(684, "Report returned stories", "Blocked", parent=600, work_type="Epic"),
        card(717, "Accept returned", "Ready", parent=684),
        card(751, "Retire the CLI", "In progress", parent=749, work_type="Epic"),
        card(759, "Remove the report", "Backlog", parent=751),
    ]
    planned = [(717, "Accept returned", 684), (759, "Remove the report", 751), (800, "New", 751)]
    text = criteria_check.planned_criteria(
        Bodies(long), "r", planned, cards=cards, goal=749, epic=752
    )
    assert "### Epic #684 Report returned stories: Blocked, Goal #600" in text
    assert "### Epic #751 Retire the CLI: In progress, in this Goal" in text
    assert "#### #717 Accept returned: Ready" in text
    assert "#### #800 New: split this pass, not on the board yet" in text
    assert text.count("### Epic #751") == 1, "one heading per epic"
    assert "the end" in text and "truncated" not in text, "criteria are whole"


def test_the_stories_an_earlier_split_kept_are_named_as_this_epics():
    text = criteria_check.planned_criteria(Bodies("1. x"), "r", [(9, "Kept", 752)], epic=752)
    assert "### This epic, #752: the stories its earlier split kept" in text


PROPOSAL = (
    "<!-- crew:epic-proposal -->\n## Proposed epics\n\n"
    "### 1. Retire the command-line tool — #751\n\n**For** — x\n\n"
    "### 2. Shrink the service — #752\n\n**For** — x\n\n"
    "### 3. Retire the escalation measures — #753\n\n### Ordering\n\nvalue first\n"
)


def test_the_goals_epics_are_shown_in_order_with_their_status():
    cards = [
        card(751, "Retire the command-line tool", "Done", parent=749, work_type="Epic"),
        card(753, "Retire the escalation measures", "Backlog", parent=749, work_type="Epic"),
        card(754, "Added later", "Backlog", parent=749, work_type="Epic"),
    ]
    text = criteria_check.goal_order(PROPOSAL, cards, goal=749, epic=752)
    assert text.splitlines()[2:] == [
        "1. #751 Retire the command-line tool: Done",
        "2. #752 Shrink the service: this epic",
        "3. #753 Retire the escalation measures: Backlog",
        "4. #754 Added later: Backlog",
    ]


ORDER_ROW = (
    "| R7 | binding | Order of the removals | "
    "mqucifer/sprint-metrics#753 lands after #751 and #752. | none | Sponsor | 2026-10-24 "
    "| — | the Sponsor on #753 |"
)
OTHER_ROW = (
    "| R8 | binding | Docs | The README drops the CLI. It is regenerated. | none | Architect "
    "| 2026-10-24 | — | the look |"
)


def record(*rows: str) -> str:
    return "\n".join(
        [
            "## The epic's record",
            "",
            "Ready to split: 2 decided.",
            "",
            "| ID | Status | Context | Decision | Consequences | Set by | Date | Replaces "
            "| Source |",
            "|---|---|---|---|---|---|---|---|---|",
            *rows,
            "",
            "Merged tests this epic changes:",
        ]
    )


def test_a_record_row_on_order_is_shown_over_the_proposals():
    text = criteria_check.goal_order(
        PROPOSAL, [], goal=749, epic=752, conclusion=record(ORDER_ROW, OTHER_ROW)
    )
    assert "hold over the proposal's" in text
    assert "- R7: mqucifer/sprint-metrics#753 lands after #751 and #752." in text
    assert "R8" not in text


def test_a_clash_with_another_epic_is_kept_once_for_that_epic():
    def clash(story: str, against: str) -> CriteriaConflict:
        return CriteriaConflict(story=story, criterion="8 types", against=against, why="7 now.")

    conflicts = [
        clash("Answer 410", "#717's criterion 6"),
        clash("Delete the schema", "#717's criterion 6"),
        clash("Delete the schema", "#718's criterion 1"),
        clash("Delete the schema", "tests/test_x.py::test_y"),
    ]
    kept = criteria_check._per_epic(conflicts, {717: 684, 718: 684})
    assert [c.against for c in kept] == ["#717's criterion 6", "tests/test_x.py::test_y"]
    assert "The same clash with epic #684 was found for: Delete the schema." in kept[0].why


def test_the_planned_part_says_a_clash_is_kept_once_per_epic():
    parts = opening(stories="s", planned="### Epic #684")
    assert "kept once, for that epic" in parts["planned"]


# --- 3. Whole criteria, only the rows a story follows -----------------------------


def test_the_record_shows_the_rows_a_story_follows_whole_and_the_rest_a_line():
    story = make_story("Cycle time").model_copy(update={"follows": ["R7"]})
    text = criteria_check.record_for_story(record(ORDER_ROW, OTHER_ROW), story)
    assert "The rows this story follows, in full:" in text and ORDER_ROW in text
    assert "- R8, Docs: The README drops the CLI." in text
    assert "It is regenerated" not in text, "the rest is one line"
    assert text.startswith("## The epic's record") and "Merged tests this epic changes:" in text
    assert text.count("| ID | Status |") == 1


def test_a_story_that_follows_no_row_gets_every_row_as_a_line():
    text = criteria_check.record_for_story(record(ORDER_ROW, OTHER_ROW), make_story("x"))
    assert "in full" not in text and "| ID |" not in text
    assert "- R7, Order of the removals:" in text and "- R8, Docs:" in text


def test_a_record_with_no_rows_is_shown_as_it_is():
    assert criteria_check.record_for_story("R1", make_story("x")) == "R1"


def test_the_check_shows_each_story_its_own_rows():
    seen = []

    def check(**shown):
        seen.append(shown)
        return CriteriaCheck()

    follows = SPLIT.model_copy(
        update={
            "stories": [
                SPLIT.stories[0].model_copy(update={"follows": ["R7"]}),
                SPLIT.stories[1].model_copy(update={"follows": ["R8"]}),
            ]
        }
    )
    criteria_check.check_each(check, follows, conclusion=record(ORDER_ROW, OTHER_ROW))
    assert ORDER_ROW in seen[0]["conclusion"] and OTHER_ROW not in seen[0]["conclusion"]
    assert OTHER_ROW in seen[1]["conclusion"] and ORDER_ROW not in seen[1]["conclusion"]


# --- 4. The pre-check in code -----------------------------------------------------


def removing(then: str, pinned: str = "") -> Story:
    return Story(
        title="Delete the formatter",
        as_a="a maintainer",
        i_want="less code",
        so_that="the service is small",
        acceptance_criteria=[
            AcceptanceCriterion(given="the code", when="it's imported", then=then),
            AcceptanceCriterion(given="no data", when="it runs", then="it says so"),
        ],
        points=2,
        pinned_behaviour=pinned,
    )


def test_the_names_a_criterion_removes_are_found_and_where_they_go_is_not():
    story = removing(
        "the import fails — format_json_report and its helpers are gone from report.py "
        "and no longer listed — `import sprint_metrics` succeeds; schemas/single-sprint.json "
        "is removed; GET /sprint is retired; the tests (tests/test_schema.py) are removed, "
        "and GET /range answers as before"
    )
    assert removed_tests.removed_names(story) == [
        "format_json_report",
        "schemas/single-sprint.json",
        "/sprint",
    ]


def test_a_rename_gives_the_old_name_not_the_new_one():
    story = removing("`escalation_rate` is renamed to returned_rate in the response")
    assert removed_tests.removed_names(story) == ["escalation_rate"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_report.py").write_text(
        "from sprint_metrics.report import format_json_report\n\n"
        "def test_formats():\n    assert format_json_report({})\n"
    )
    (tmp_path / "tests" / "test_service.py").write_text(
        "def test_sprint(client):\n    assert client.get('/sprint?s=1').status_code == 200\n\n"
        "def test_sprints(client):\n    assert client.post('/sprints').status_code == 200\n\n"
        "class TestSchema:\n"
        "    def test_file(self):\n        assert open('schemas/single-sprint.json')\n"
    )
    (tmp_path / "src.py").write_text("format_json_report = 1\n")
    return tmp_path


REMOVES = (
    "format_json_report is gone; schemas/single-sprint.json is deleted; GET /sprint is retired"
)


def test_the_merged_tests_using_what_a_story_removes_are_listed(repo: Path):
    found = removed_tests.undeclared(removed_tests.Index.of(repo), removing(REMOVES))
    assert found == {
        "tests/test_report.py": ["format_json_report"],
        "tests/test_report.py::test_formats": ["format_json_report"],
        "tests/test_service.py::test_sprint": ["/sprint"],
        "tests/test_service.py::TestSchema.test_file": ["schemas/single-sprint.json"],
    }


def test_tests_the_existing_tests_line_names_are_left_out(repo: Path):
    declared = removing(
        REMOVES,
        "contract change: tests/test_report.py, tests/test_service.py::test_sprint",
    )
    found = removed_tests.undeclared(removed_tests.Index.of(repo), declared)
    assert list(found) == ["tests/test_service.py::TestSchema.test_file"]
    opt_in = removing(REMOVES, "opt-in: tests/test_report.py")
    assert "tests/test_report.py" in removed_tests.undeclared(removed_tests.Index.of(repo), opt_in)


def test_the_check_is_given_them_as_facts(repo: Path):
    index = removed_tests.Index.of(repo)
    seen = []

    def check(**shown):
        seen.append(shown)
        return CriteriaCheck()

    proposal = StoryProposal(epic_title="E", stories=[removing(REMOVES), make_story("Other")])
    criteria_check.check_each(
        check, proposal, precheck=lambda s: removed_tests.undeclared(index, s)
    )
    facts = seen[0]["facts"]
    assert facts.startswith("## Merged tests that use what this story removes")
    assert "- `tests/test_service.py::test_sprint` uses `/sprint`" in facts
    assert seen[0]["noted"]["precheck_tests"] == 4 and seen[1]["facts"] == ""
    parts = list(opening(stories="s", facts=facts))
    assert parts.index("facts") == parts.index("stories") + 1


def test_the_split_is_refused_once_for_them_and_the_outcome_counted(repo: Path):
    once = removed_tests.Once(removed_tests.Index.of(repo))
    proposal = StoryProposal(epic_title="E", stories=[removing(REMOVES)])
    with pytest.raises(Refusal) as refused:
        removed_tests.refuse_once(once, proposal)
    assert refused.value.rule == "check.removed_tests.undeclared"
    text = str(refused.value)
    assert "`tests/test_service.py::test_sprint` uses `/sprint`" in text
    assert "or call done again" in text
    # The same tests again: passed, and left for the criteria check.
    removed_tests.refuse_once(once, proposal)
    assert once.outcome(proposal) == {"refused": 4, "declared": 0, "passed": 4}
    declared = removing(REMOVES, "contract change: tests/test_report.py")
    fixed = StoryProposal(epic_title="E", stories=[declared])
    removed_tests.refuse_once(once, fixed)
    assert once.outcome(fixed) == {"refused": 4, "declared": 2, "passed": 2}


def test_a_split_session_refuses_and_records_how_it_went(repo: Path, monkeypatch):
    from crew_org import events
    from crew_org.crews import refinement_crew
    from crew_org.events import EventKind

    found = seen_events()
    answers = [removing(REMOVES), removing(REMOVES, "contract change: tests/test_report.py")]

    def run(step, parts, reading, *, check, **kw):
        for story in answers:
            proposal = StoryProposal(epic_title="E", stories=[story])
            try:
                check(proposal)
            except Refusal:
                continue
            return proposal
        raise AssertionError("never passed")

    monkeypatch.setattr(refinement_crew.steps, "run", run)
    try:
        refinement_crew.split_epic("E", reading=SimpleNamespace(root=repo))
    finally:
        events.reset_bridge()
    [note] = [
        e
        for e in found
        if e.kind == EventKind.NOTE and e.detail.get("rule") == "check.removed_tests.undeclared"
    ]
    assert note.detail["counts"] == {"refused": 4, "declared": 2, "passed": 2}


def test_a_name_the_criterion_says_isnt_there_is_removed():
    """sprint-metrics#753's /range story: "has no escalation_rate_percent key"."""
    story = removing(
        "each entry has no escalation_rate_percent key and no escalation_count key; the "
        "schema declares no `trend_rate` or `ESC_LIMIT` property; it answers with no error "
        "and sprint_id set"
    )
    assert removed_tests.removed_names(story) == [
        "escalation_rate_percent",
        "escalation_count",
        "trend_rate",
        "ESC_LIMIT",
    ]
