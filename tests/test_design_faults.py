"""A design fault met while building the skeleton goes back to the Architect (crew#690).

The Sponsor's option B (plan Part 2, ADR 0030): when the skeleton's story is
blocked because setup failed or a check's own tool is missing, the crew reruns
the design with that output, and the Sponsor merges the revision. Once it
merges, the story goes back to Ready, once.
"""

from __future__ import annotations

import json

from crew_org.events import EventSink
from crew_org.flows.design import DESIGN_PR, FAULTS_MARKER, answered_faults
from crew_org.flows.revisit import (
    BACK_TO_WORK,
    FAULT_SENT,
    back_to_work,
    design_fault,
    revisit_designs,
)
from crew_org.project import Design
from crew_org.tools.github_project import Card
from tests.test_revisit import REPO, Architect, Board, Issues, approves, clone, proposal

__all__ = ["clone"]

PLAYWRIGHT = Design(
    sandbox_image="mcr.microsoft.com/playwright/python@sha256:abc",
    setup="pip install -r requirements.txt",
    checks=["python -m pytest tests/test_contracts.py -v", "python build.py"],
)
CODE = "**Blocked.** escalation did not resolve it: $ python -m pytest -q\n(exit 1)\nassert 2 == 3"


def blocked(number: int, status: str = "Blocked") -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title=f"Story {number}",
        status=status,
        work_type="Story",
        repo=REPO,
        labels=frozenset({"blocked"}),
    )


class Labels(Issues):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.removed: list[tuple[int, str]] = []

    def add_labels(self, repo, number, labels):
        pass

    def remove_label(self, repo, number, label):
        self.removed.append((number, label))


# --- what counts as the design's fault ---------------------------------------------------


def test_a_design_that_cannot_run_at_all_is_the_architect_s():
    block = "**Blocked.** $ design\n(exit 2)\nthe design's command `a && b` uses `&&`"
    assert design_fault(block, PLAYWRIGHT)


def test_a_failed_setup_is_sent_to_the_architect():
    block = "**Blocked.** $ pip install -r requirements.txt\n(exit 1)\nERROR: Invalid requirement"
    assert design_fault(block, PLAYWRIGHT)


def test_a_check_s_own_tool_missing_is_the_design_s():
    """mqucifer/crew-presentation#35, with the setup the design named."""
    block = (
        "**Blocked.** $ python -m pytest tests/test_contracts.py -v\n(exit 1)\n"
        "/usr/bin/python: No module named pytest"
    )
    assert design_fault(block, PLAYWRIGHT)
    assert design_fault(
        "**Blocked.** $ npx playwright test\n(exit 127)\nnpx: not found", PLAYWRIGHT
    )


def test_a_failing_test_is_the_code_s():
    assert not design_fault(CODE, PLAYWRIGHT)
    passed_setup = "$ pip install -r requirements.txt\n(exit 0)\n\n" + CODE
    assert not design_fault(passed_setup, PLAYWRIGHT)


# --- the revisit sends it back ----------------------------------------------------------


def run(issues, clone, cards, architect, *, skeleton=True):
    return revisit_designs(
        issues,
        issues,
        Board(),
        EventSink(),
        clone,
        cards,
        repos={REPO},
        events_dir=None,
        propose_design=architect,
        review_design=approves,
        skeleton_only={REPO} if skeleton else set(),
    )


FAULT = "**Blocked.** escalation did not resolve it: $ design\n(exit 2)\nuses `&&`"


def test_a_skeleton_story_blocked_on_the_design_gets_a_revision_for_the_sponsor(clone):
    issues = Labels()
    issues.comments_on[35] = [FAULT]
    architect = Architect(proposal(structure="build.py reads the fetched package"))

    found = run(issues, clone, [blocked(35)], architect)

    assert (
        "#35:" in architect.calls[0]["reason"]
        and "never through a shell" in (architect.calls[0]["reason"])
    )
    (pull,) = issues.pulls
    assert DESIGN_PR in pull["body"], "the Sponsor's to merge, not a crew-merged revisit"
    assert answered_faults(pull["body"]) == [35]
    assert found.holds[REPO].startswith("the Architect's design lands first")
    assert FAULT_SENT in issues.comments_on[35][-1]


def test_it_is_answered_once(clone):
    issues = Labels()
    issues.comments_on[35] = [FAULT, f"answered\n\n{FAULT_SENT}"]
    architect = Architect()
    run(issues, clone, [blocked(35)], architect)
    assert architect.calls == [] and issues.pulls == []


def test_a_design_with_nothing_to_change_leaves_the_story_for_a_person(clone):
    """A setup that failed for want of a file the story writes changes nothing."""
    from tests.test_revisit import RECORD

    issues = Labels()
    issues.comments_on[35] = [FAULT]
    same = proposal(structure=RECORD.design.structure)
    run(issues, clone, [blocked(35)], Architect(same))
    assert issues.pulls == []
    assert "stays blocked for a person" in issues.comments_on[35][-1]


def test_a_code_failure_is_not_sent(clone):
    issues = Labels()
    issues.comments_on[35] = [CODE]
    architect = Architect()
    run(issues, clone, [blocked(35)], architect)
    assert architect.calls == []


def test_a_project_past_its_skeleton_is_not_asked(clone):
    issues = Labels()
    issues.comments_on[35] = [FAULT]
    architect = Architect()
    run(issues, clone, [blocked(35)], architect, skeleton=False)
    assert architect.calls == []


# --- once merged, back to work, once -----------------------------------------------------


def merged(number: int = 9, faults=(35,)) -> dict:
    body = f"{DESIGN_PR}\n{FAULTS_MARKER.format(json.dumps(list(faults)))}"
    return {"number": number, "body": body, "merged_at": "now"}


def test_a_merged_design_puts_its_stories_back_in_ready():
    issues, board = Labels(), Board()
    back_to_work(issues, board, EventSink(), [blocked(35)], repo=REPO, pull=merged())
    assert issues.removed == [(35, "blocked")]
    assert board.status == {"I35": "Ready"}
    assert BACK_TO_WORK.format(9) in issues.comments_on[35][-1]


def test_a_later_block_of_the_same_story_is_not_lifted_by_the_same_design():
    issues, board = Labels(), Board()
    issues.comments_on[35] = [f"Back in Ready.\n\n{BACK_TO_WORK.format(9)}", CODE]
    back_to_work(issues, board, EventSink(), [blocked(35)], repo=REPO, pull=merged())
    assert issues.removed == [] and board.status == {}


def test_a_story_no_longer_blocked_is_left_alone():
    issues, board = Labels(), Board()
    back_to_work(
        issues, board, EventSink(), [blocked(35, status="In Progress")], repo=REPO, pull=merged()
    )
    assert board.status == {}
