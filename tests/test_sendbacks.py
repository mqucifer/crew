"""Every send-back records one `work.sent_back` (crew#862).

The done-when: each send-back, run once through its own code path, leaves its event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crew_org import sendbacks, telemetry
from crew_org.crews.criteria_crew import CriteriaCheck
from crew_org.crews.refinement_crew import CriteriaRepair, RepairedStory
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import board_flow
from tests import test_criteria_check as criteria
from tests.test_board_flow import FakeBoard, FakeIssues, epic_card
from tests.test_revisit import clone, sprint6  # noqa: F401  (fixtures)
from tests.test_session_delivery import deliver, ws  # noqa: F401  (fixtures)
from tests.test_session_end import verdict  # noqa: F401  (a fixture)


def sent_back(sink: EventSink) -> list[CrewEvent]:
    return [e for e in sink.replay() if e.kind == EventKind.WORK_SENT_BACK]


def collecting() -> tuple[EventSink, list[CrewEvent]]:
    """A sink with no log, and the send-backs it saw."""
    sink, seen = EventSink(None), []
    sink.subscribe(lambda e: seen.append(e) if e.kind == EventKind.WORK_SENT_BACK else None)
    return sink, seen


def which(seen: list[CrewEvent]) -> list[tuple[Any, ...]]:
    return [(e.card, e.detail["sent_from"], e.detail["sent_to"], e.detail["rule"]) for e in seen]


def test_a_send_back_names_its_steps_rule_round_and_counts(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events" / "tick.jsonl")

    sendbacks.record(
        sink, sendbacks.QA_UNPROVEN, card=12, repo="o/r", role="QA Engineer", round_=2, unproven=3
    )

    (event,) = sent_back(sink)
    assert event.card == 12
    assert event.role == "QA Engineer"
    assert event.detail == {
        "repo": "o/r",
        "sent_from": "qa",
        "sent_to": "build",
        "rule": "gate.qa_unproven",
        "round": 2,
        "counts": {"unproven": 3},
    }


def test_a_send_back_leaves_for_telemetry_as_names_and_numbers(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events" / "tick.jsonl")

    sendbacks.record(sink, sendbacks.MERGE_CONFLICT, card=7, repo="o/r", round_=1)

    out = telemetry.view(sent_back(sink)[0])
    assert {k: out[k] for k in ("sent_from", "sent_to", "rule", "round")} == {
        "sent_from": "merge",
        "sent_to": "build",
        "rule": "gate.merge_conflict",
        "round": 1,
    }
    assert "summary" not in out


def test_each_send_back_is_declared_once_and_goes_to_its_own_step_or_an_earlier_one() -> None:
    order = list(sendbacks.Step)
    # The steps outside the line of the flow: the Sponsor, refinement's own checks
    # across an epic, and the after-merge checks.
    outside = {sendbacks.Step.SPONSOR, sendbacks.Step.MAIN, sendbacks.Step.RELEASE_CHECK}
    outside |= {sendbacks.Step.REVERT, sendbacks.Step.REVISIT, sendbacks.Step.REFINEMENT}
    seen = set()
    for back in sendbacks.CATALOG:
        assert (back.sent_from, back.rule) not in seen
        seen.add((back.sent_from, back.rule))
        if back.sent_from not in outside and back.sent_to != sendbacks.Step.DESIGN:
            assert order.index(back.sent_to) <= order.index(back.sent_from), back


# --- the criteria check (R5 to R7) ---


def split_checked(monkeypatch: pytest.MonkeyPatch, checker: Any) -> list[CrewEvent]:
    criteria.splitting(monkeypatch, checker)
    monkeypatch.setattr(board_flow, "propose_epics", lambda g, **kw: None)
    sink, seen = collecting()
    board_flow.tick(FakeBoard([epic_card(3)]), FakeIssues(), sink, default_repo="sprint-metrics")
    return seen


def test_the_criteria_checks_repair_is_a_send_back_to_the_split(monkeypatch):
    repaired = RepairedStory(title="Cycle time", acceptance_criteria=criteria.FIXED)
    monkeypatch.setattr(
        board_flow, "repair_criteria", criteria.Repairer(CriteriaRepair(stories=[repaired]))
    )

    seen = split_checked(monkeypatch, criteria.Checker(criteria.ON_CYCLE_TIME, CriteriaCheck()))

    assert which(seen) == [(3, "criteria_check", "split", "gate.criteria_repair")]
    assert seen[0].detail["counts"] == {"conflicts": 1, "stories": 1}


def test_the_criteria_checks_whole_re_split_is_a_send_back_to_the_split(monkeypatch):
    seen = split_checked(monkeypatch, criteria.Checker(criteria.CONFLICT, CriteriaCheck()))

    assert which(seen) == [(3, "criteria_check", "split", "gate.criteria_resplit")]


def test_criteria_that_still_conflict_are_sent_to_the_product_owner(monkeypatch):
    seen = split_checked(monkeypatch, criteria.Checker(criteria.CONFLICT, criteria.CONFLICT))

    assert which(seen) == [
        (3, "criteria_check", "split", "gate.criteria_resplit"),
        (3, "criteria_check", "story_problem", "gate.criteria_cannot_all_pass"),
    ]


# --- refinement's other send-backs (R1 to R4) ---


def test_a_goal_the_sponsor_sends_back_is_a_send_back_to_decompose(monkeypatch):
    from tests import test_board_flow as goals

    issues = goals.ReworkIssues(
        comments=[goals.marked(board_flow.EPIC_PROPOSAL_MARKER), goals.sponsor("try again")],
        children=[7],
    )
    board = FakeBoard([goals.reworked(1), goals.card(7, status="Inbox")])
    _, seen = goals.run(board, issues, monkeypatch)

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(1, "sponsor", "decompose", "decision.board_flow.rework_gate")]
    assert sent[0].detail["counts"] == {"superseded": 1}


def test_a_goal_the_sponsor_says_lacks_an_epic_is_a_send_back_to_decompose(monkeypatch):
    from tests import test_board_flow as goals
    from tests import test_goal_lacks_an_epic as lacks

    issues = lacks.Lacking(
        comments=[goals.marked(board_flow.EPIC_PROPOSAL_MARKER), goals.sponsor("not durable")],
        children=[7],
    )
    monkeypatch.setattr(board_flow, "propose_missing_epics", lambda goal, **kw: lacks.DURABLE)
    sink, seen = collecting()
    board_flow.tick(
        FakeBoard([lacks.asking(1), goals.card(7, status="Inbox")]),
        issues,
        sink,
        default_repo="sprint-metrics",
        sponsor="mquarters",
    )

    assert which(seen) == [(1, "sponsor", "decompose", "decision.board_flow.lacking_goals")]
    assert seen[0].detail["counts"] == {"epics": 1}


def test_an_epic_the_sponsor_sends_back_is_a_send_back_to_the_split(monkeypatch):
    from tests import test_resplit_flow as epics

    issues = epics.Issues([epics.SPLIT, "Split it smaller."])
    monkeypatch.setattr("crew_org.flows.board_flow.from_sponsor", lambda issues, c: True)
    _, events = epics.run(monkeypatch, issues, [epics.sent_back(), *epics.stories(*epics.FOUR)])

    sent = [e for e in events if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(epics.EPIC, "sponsor", "split", "sent_back.epic_rework")]
    assert sent[0].detail["counts"] == {"replaced": 4}


def test_a_story_problems_re_split_is_not_the_sponsors_send_back(monkeypatch):
    """The story's own send-back was recorded where it was sent back."""
    from tests import test_resplit_flow as epics

    issues = epics.Issues([epics.SPLIT, epics.PROBLEM, epics.ANSWER])
    _, events = epics.run(monkeypatch, issues, [epics.sent_back(), *epics.stories(*epics.FOUR)])

    assert not [e for e in events if e.kind == EventKind.WORK_SENT_BACK]
    [resplit] = [e for e in events if e.kind == EventKind.EPIC_RESPLIT]
    assert resplit.detail["round"] == 1


def test_an_epic_whose_wait_breaks_is_a_send_back_to_the_split_and_not_the_sponsors(
    monkeypatch,
):
    from tests import test_waits_for_planned as waits

    issues = waits.Issues(earlier=[waits.SPLIT_BEFORE])
    cards = [epic_card(waits.EPIC), waits.story(waits.PLANNED, 180, state="CLOSED")]
    monkeypatch.setattr(
        "crew_org.flows.board_flow.split_epic",
        lambda title, context="", **kw: waits.covered_by(waits.PLANNED),
    )
    sink, seen = collecting()
    board_flow.tick(FakeBoard(cards), issues, sink, default_repo="sprint-metrics")

    assert which(seen) == [(waits.EPIC, "refinement", "split", "skip.board_flow.waits_for_planned")]
    assert seen[0].detail["counts"] == {"closed": 1}


# --- delivery (D1 to D11) ---


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch) -> list[CrewEvent]:
    """The send-backs of every sink made while it's on, for harnesses that make their own."""
    seen: list[CrewEvent] = []
    made = EventSink.__init__

    def watching(self: EventSink, *args: Any, **kw: Any) -> None:
        made(self, *args, **kw)
        self.subscribe(lambda e: seen.append(e) if e.kind == EventKind.WORK_SENT_BACK else None)

    monkeypatch.setattr(EventSink, "__init__", watching)
    return seen


def test_the_gates_findings_at_done_are_a_send_back_to_the_build_in_their_round(
    ws,  # noqa: F811
    deliver,  # noqa: F811
    watched,
):
    from crew_org.flows import gates, session_end
    from tests import test_session_delivery as sessions

    findings = gates.Verdict(findings=[gates.Finding("a.rule", "One."), gates.Finding("b", "Two")])
    deliver(
        ws,
        sessions.Sessions(
            {"ending": sessions.Ending("done", sessions.DONE)},
            {"ending": sessions.Ending("done", sessions.DONE)},
        ),
        sessions.Routes(
            session_end.Route(session_end.SESSION, "a.rule", start="x", verdict=findings),
            session_end.Route(session_end.REVIEW, "decision.delivery.outcome"),
        ),
    )

    assert which(watched) == [(662, "gate", "build", "gate.findings_at_done")]
    assert watched[0].detail["round"] == 1 and watched[0].detail["counts"] == {"findings": 2}


def test_the_business_analysts_repair_ruling_is_a_send_back_to_the_build(
    ws,  # noqa: F811
    deliver,  # noqa: F811
    watched,
):
    from crew_org.flows import session_end
    from tests import test_session_delivery as sessions

    deliver(
        ws,
        sessions.Sessions(
            {"ending": sessions.Ending("done", sessions.DONE)},
            {"ending": sessions.Ending("done", sessions.DONE)},
        ),
        sessions.Routes(
            session_end.Route(
                session_end.SESSION, "check.gates.the_business_analyst_ruled_that", start="x"
            ),
            session_end.Route(session_end.REVIEW, "decision.delivery.outcome"),
        ),
    )

    assert which(watched) == [(662, "gate", "build", "check.gates.the_business_analyst_ruled_that")]


def test_a_blocked_session_is_a_send_back_to_the_split():
    from crew_org.session.conversation import Ending
    from tests import test_session_end as ends

    ending = Ending("blocked", {"missing": "a fact", "kind": "fact"}, evidence=ends.EVIDENCE)
    _, _, _, seen = ends.route(ending)

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(124, "build", "split", "story.session_blocked")]
    assert sent[0].role == "Developer" and sent[0].detail["round"] is None


def test_a_contract_ruling_to_refinement_is_a_send_back_to_the_split(verdict, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from crew_org.flows import delivery, gates, session_end
    from crew_org.session.conversation import Ending
    from tests import test_session_end as ends

    monkeypatch.setattr(
        delivery,
        "rule_on_contract",
        lambda issues, sink, **kw: SimpleNamespace(decision="return", why="No.", tests=[]),
    )
    monkeypatch.setattr(session_end, "story_rows", lambda issues, card, repo: "")
    verdict.verdict = gates.Verdict(disagree={ends.CAPPED: "uncapped"})
    _, _, _, seen = ends.route(
        Ending("done", ends.SAID, evidence={"last_run": ends.GREEN}),
        leaving=session_end.Leaving(lambda: ends.kept({ends.CAPPED: "changed"})),
    )

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(124, "gate", "split", "story.contract_ruling")]


def test_the_round_trip_limit_is_a_send_back_to_the_split_in_its_round(watched):
    from crew_org.flows.story_problem import MAX_ROUND_TRIPS
    from tests import test_gate_round_trips as trips

    trips.gates_disagree(trips.Issues(deliveries=MAX_ROUND_TRIPS))

    assert which(watched) == [(145, "build", "split", "story.gate_round_trips")]
    assert watched[0].detail["round"] == MAX_ROUND_TRIPS
    assert watched[0].detail["counts"] == {"stories": 2}


def test_ci_failing_before_review_is_a_send_back_to_the_build_in_its_round(monkeypatch):
    from crew_org.flows.ci import CI_MARKER
    from crew_org.flows.review import review_open_pulls
    from tests import test_review as reviews

    class Red(reviews.FakeIssues):
        def comments(self, repo, number):
            return [{"body": f"{CI_MARKER.format(head='0ld')}\nCI failed"}]

        def comment(self, repo, number, body):
            return {}

        def job_log(self, repo, job):
            return ""

    issues = Red([reviews.crew_pull()])
    issues._pulls[0]["head"]["sha"] = "c0ffee"
    issues.runs = [{"name": "image", "status": "completed", "conclusion": "failure", "id": 9}]
    sink, seen = collecting()
    review_open_pulls(
        issues,
        sink,
        repo="sprint-metrics",
        bot_login=reviews.BOT,
        board=reviews.FakeBoard(),
        cards=[reviews.waiting_card()],
    )

    assert which(seen) == [(6, "ci", "build", "gate.ci_before_review")]
    assert seen[0].detail["round"] == 2 and seen[0].detail["counts"] == {"jobs": 1}


def review_returned(monkeypatch: pytest.MonkeyPatch, code: Any, deploy: Any = None):
    from crew_org.flows import review as review_flow
    from crew_org.flows.review import REVIEW_MARKER, review_open_pulls
    from tests import test_review as reviews

    monkeypatch.setattr(review_flow, "review_diff", lambda *a, **k: code)
    if deploy is not None:
        monkeypatch.setattr(review_flow, "deploy_trigger", lambda diff, named: "compose")
        monkeypatch.setattr(review_flow, "_deploy_review", lambda *a, **k: deploy)
    earlier = {
        "user": {"login": reviews.BOT},
        "body": f"{REVIEW_MARKER}\nNot yet.\n\n## Findings",
        "state": "CHANGES_REQUESTED",
        "commit_id": "0ld",
    }
    pull = reviews.crew_pull()
    pull["head"]["sha"] = "c0ffee"
    issues = reviews.FakeIssues([pull], reviews={14: [earlier]})
    sink, seen = collecting()
    review_open_pulls(
        issues,
        sink,
        repo="sprint-metrics",
        bot_login=reviews.BOT,
        board=reviews.FakeBoard(),
        cards=[reviews.waiting_card()],
    )
    return seen


def test_changes_requested_are_a_send_back_to_the_build_in_their_round(monkeypatch):
    from tests import test_review as reviews

    seen = review_returned(monkeypatch, reviews.REJECTION)

    assert which(seen) == [(6, "review", "build", "gate.review_changes_requested")]
    assert seen[0].role == "Code Reviewer"
    assert seen[0].detail["round"] == 2 and seen[0].detail["counts"] == {"findings": 1}


def test_the_deploy_reviews_changes_alone_are_its_own_send_back(monkeypatch):
    from tests import test_review as reviews

    seen = review_returned(monkeypatch, reviews.APPROVAL, deploy=reviews.REJECTION)

    assert which(seen) == [(6, "deploy_review", "build", "gate.review_changes_requested")]
    assert seen[0].role == "DevOps Engineer" and seen[0].detail["round"] == 2


def test_a_review_finding_against_a_criterion_is_a_send_back_to_the_split(monkeypatch, watched):
    from tests import test_review_conflicts as conflicts

    conflicts.review(conflicts.CONFLICT, monkeypatch)

    assert which(watched) == [(6, "review", "split", "story.review_conflicts_with_criterion")]
    assert watched[0].role == "Code Reviewer"


def test_qa_unproven_is_a_send_back_to_the_build_in_its_round(monkeypatch, tmp_path, watched):
    from crew_org.flows.acceptance import QA_MARKER, qa_marker
    from tests import test_already_done as done

    earlier = [{"body": f"{QA_MARKER}\n{qa_marker('0ld')}\n## QA — not accepted"}]
    done.qa(monkeypatch, tmp_path, earlier, accepted=False)

    assert which(watched) == [(143, "qa", "build", "gate.qa_unproven")]
    assert watched[0].detail["round"] == 2 and watched[0].detail["counts"] == {"unproven": 1}


def test_ci_failing_at_merge_is_a_send_back_to_the_build():
    from tests import test_ci_failure as ci
    from tests.test_merge import card, run

    _, _, seen = run([card(6)], ci.red_on_head())

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(6, "merge", "build", "gate.merge_ci_failed")]
    assert sent[0].detail["round"] == 1


def test_the_merge_queue_removing_it_for_ci_is_a_send_back_to_the_build():
    from crew_org.tools.github_issues import QueueState
    from tests.test_merge import QueueIssues, card, run

    issues = QueueIssues(QueueState(has_queue=True, removed="CI failed"))
    issues.failed_in_queue = [{"name": "tests", "conclusion": "failure", "id": 77}]
    _, _, seen = run([card(6)], issues)

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(6, "merge", "build", "gate.merge_ci_failed")]


def test_a_conflict_at_merge_is_a_send_back_to_the_build_in_its_round():
    from crew_org.flows.merge import REBUILD_MARKER
    from tests.test_merge import FakeIssues, card, run

    issues = FakeIssues(mergeable_state="dirty")
    issues.earlier = [REBUILD_MARKER.format(head="a")]
    _, _, seen = run([card(6)], issues)

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(6, "merge", "build", "gate.merge_conflict")]
    assert sent[0].detail["round"] == 2


# --- after merge (A1 to A5) ---


def test_a_red_default_branch_is_a_send_back_to_refinement(watched):
    from tests import test_main_watch as main

    main.watch(main.Issues({main.RELEASE: main.run("failure")}))

    assert which(watched) == [(300, "main", "refinement", "sent_back.main_red")]


def test_a_release_with_gaps_is_a_send_back_to_refinement(watched):
    from crew_org.tools.registry import Image
    from tests import test_release_check as release

    image = Image(digest=release.DIGEST, platforms=["linux/amd64", "linux/arm64"], labels={})
    release.check(release.Issues(attested=False), release.FakeRegistry(image))

    assert which(watched) == [(400, "release_check", "refinement", "sent_back.release_gaps")]
    assert watched[0].detail["counts"]["gaps"] >= 2


def test_a_revert_that_lands_is_a_send_back_to_refinement():
    from tests import test_revert as revert

    pull = revert.revert_pull()
    _, _, seen = revert.land(
        revert.FakeIssues(pulls=[pull], open_=[pull], reviews=revert.APPROVED), [revert.card()]
    )

    sent = [e for e in seen if e.kind == EventKind.WORK_SENT_BACK]
    assert which(sent) == [(31, "revert", "refinement", "sent_back.reverted")]


def test_design_faults_are_a_send_back_to_the_design(clone, watched):  # noqa: F811
    from tests import test_design_faults as faults
    from tests.test_revisit import Architect, proposal

    issues = faults.Labels()
    issues.comments_on[35] = [faults.FAULT]
    faults.run(issues, clone, [faults.blocked(35)], Architect(proposal(structure="x")))

    assert which(watched) == [(35, "revisit", "design", "sent_back.design_faults")]


def test_rebuild_strain_and_each_refused_revision_are_send_backs_to_the_design(
    clone,  # noqa: F811
    sprint6,  # noqa: F811
    watched,
):
    from crew_org.crews.design_crew import Conflict, DesignReview
    from crew_org.flows.revisit import revisit_designs
    from tests.test_revisit import REPO, SPRINT6, Architect, Board, Issues, proposal

    issues = Issues()
    conflict = DesignReview(conflicts=[Conflict(guideline="§19 rule 1", choice="x", why="y")])
    revisit_designs(
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

    assert which(watched) == [
        (None, "revisit", "design", "sent_back.rebuild_strain"),
        (None, "review", "design", "sent_back.design_refused"),
        (None, "review", "design", "sent_back.design_refused"),
    ]
    assert [e.detail["round"] for e in watched[1:]] == [1, 2]


def test_every_declared_send_back_is_run_by_a_test_here():
    """The done-when (crew#862): each send-back, run once, leaves its event."""
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    asserted = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    unrun = [b for b in sendbacks.CATALOG if str(b.rule) not in asserted]
    assert not unrun, unrun
