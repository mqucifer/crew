"""delivery-history's mock: a whole package built from illustrative inputs (crew#521).

crew-presentation builds against the schema and this mock before the first
release (the Sponsor, 2026-10-11: the shape comes first, then the values). It's
built by the same `build()` as a real package, from events shaped as the event
log records them, so it can't drift from the schema: `crew package schema`
writes it with the schemas, and the tests check the committed one is current.

**The data is illustrative.** It retells crew-presentation's design reference
(Sprint 6 in sprint-metrics, the night of 25 September): a module split into
stories, #127 refused three times until two fixes to the crew landed, a design
revision the crew merged on its own, and UX epics waiting at the Sponsor's
gate. The numbers and titles are made up to read true; none is a measurement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from crew_org.packages import delivery_history as dh
from crew_org.packages import sprint_metrics_answers as sma
from crew_org.tools.github_project import Card

DIRECTORY = "mock"
SM = "sprint-metrics"
# 18:30 on Sprint 6's day in Chicago.
T = datetime(2026, 9, 25, 23, 30, tzinfo=UTC)
# An event as the log records it: (the log it came from, the event).
Raw = tuple[str, dict[str, Any]]
SPRINTS = [
    {"title": "Sprint 5", "startDate": "2026-09-24", "duration": 1},
    {"title": "Sprint 6", "startDate": "2026-09-25", "duration": 1},
]


def _at(minutes: float) -> str:
    return (T + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _moved(minutes: float, card: int, from_: str, to: str, role: str = "Developer") -> Raw:
    detail = {"from": from_, "to": to, "repo": SM}
    return (
        "tick",
        {"at": _at(minutes), "kind": "card.moved", "role": role, "card": card, "detail": detail},
    )


def _by_person(minutes: float, card: int, from_: str, to: str, by: str = "person") -> Raw:
    """A move the crew saw someone else make: a person's approval or unblock, or the
    board's own workflow."""
    detail = {"from": from_, "to": to, "by": by, "repo": SM}
    return ("tick", {"at": _at(minutes), "kind": "card.seen_moved", "card": card, "detail": detail})


def _work(
    minutes: float, card: int | None, role: str, *, done: bool = False, pr: int | None = None
) -> Raw:
    detail: dict[str, Any] = {"repo": SM}
    if pr is not None:
        detail["pr"] = pr
    kind = "agent.finished" if done else "agent.started"
    return (
        "tick",
        {"at": _at(minutes), "kind": kind, "role": role, "card": card, "detail": detail},
    )


def _call(
    minutes: float,
    card: int,
    role: str,
    step: str,
    prompt: int,
    completion: int,
    reasoning: int,
    seconds: float,
    call_id: str,
) -> Raw:
    detail = {
        "model": "crew-code-think" if role == "Developer" else "crew-general",
        "for": step,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "duration_s": seconds,
        "call_id": call_id,
        "repo": SM,
    }
    return (
        "tick",
        {"at": _at(minutes), "kind": "llm.finished", "role": role, "card": card, "detail": detail},
    )


def _refused(
    minutes: float, card: int, attempt: int, failure: str, decision: str, **detail: Any
) -> Raw:
    return (
        "tick",
        {
            "at": _at(minutes),
            "kind": "escalation.decided",
            "role": "Developer",
            "card": card,
            "summary": f"{failure} — {decision}",
            "detail": {"failure_class": failure, "attempt": attempt, "repo": SM, **detail},
        },
    )


def _plain(minutes: float, kind: str, card: int) -> Raw:
    return (
        "tick",
        {
            "at": _at(minutes),
            "kind": kind,
            "role": "Developer",
            "card": card,
            "detail": {"repo": SM},
        },
    )


def _session(minutes: float, kind: str, card: int, /, **detail: Any) -> Raw:
    """A Developer session's event (crew#778), as the session records it."""
    return (
        "tick",
        {
            "at": _at(minutes),
            "kind": kind,
            "role": "Developer",
            "card": card,
            "detail": {"repo": SM, "for": "session", **detail},
        },
    )


def _sent_back(
    minutes: float, card: int | None, sent_from: str, sent_to: str, rule: str, **more: Any
) -> Raw:
    """A send-back (crew#862), as `sendbacks.record` writes it."""
    return (
        "tick",
        {
            "at": _at(minutes),
            "kind": "work.sent_back",
            "role": more.pop("role", None),
            "card": card,
            "detail": {
                "repo": SM,
                "sent_from": sent_from,
                "sent_to": sent_to,
                "rule": rule,
                "round": more.pop("round", None),
                "counts": more,
            },
        },
    )


def _tool(
    minutes: float, card: int, turn: int, tool: str, result: str = "ok", **detail: Any
) -> Raw:
    return _session(
        minutes,
        "tool.finished",
        card,
        tool=tool,
        turn=turn,
        result=result,
        args_chars=60,
        reply_chars=1800,
        duration_s=0.3,
        **detail,
    )


def _sessioned(
    start: float, card: int, *, rework: bool = False, how: str = "done", **ending: Any
) -> list[Raw]:
    """One session: its start, two calls' signals and tools, and its end."""
    hints = "instead_of_hints" if rework else "hints"
    sizes = {"story": 3200, "project": 5500, "map": 4300, hints: 800, "rules": 1400}
    events = [
        _session(start, "session.started", card, start_chars={**sizes, "total": 15200}),
        _tool(start + 1, card, 1, "view"),
        _tool(start + 1, card, 1, "search", found_nothing=True),
        _session(
            start + 1,
            "session.turn",
            card,
            turn=1,
            tool_calls=2,
            since_action=1,
            most_views=1,
            edits_since_run=0,
            empty_lookups=1,
            reasoning_tokens=900,
            model_s=12.0,
        ),
    ]
    if how == "done":
        events += [
            _tool(start + 2, card, 2, "replace_text"),
            _tool(
                start + 3,
                card,
                2,
                "run",
                "passed",
                run="suite",
                commands=[{"code": 0}, {"code": 0}],
            ),
            _session(
                start + 3,
                "session.turn",
                card,
                turn=2,
                tool_calls=2,
                since_action=0,
                most_views=1,
                edits_since_run=0,
                empty_lookups=0,
                reasoning_tokens=1400,
                model_s=30.0,
            ),
        ]
    said = (
        {"criteria": [{"criterion": "", "test": ""}] * 2, "changed_tests": [], "docs": ""}
        if how == "done"
        else {"missing": "", "kind": "stop"}
    )
    signals = {
        "since_action": 0 if how == "done" else 60,
        "most_views": 2,
        "edits_since_run": 0,
        "empty_lookups_total": 1,
    }
    events.append(
        _session(
            start + 4,
            "session.ended",
            card,
            how=how,
            said=said,
            evidence={"signals": signals, **ending.pop("evidence", {})},
            model_calls=2,
            model_s=30.0,
            edits_applied=1 if how == "done" else 0,
            test_runs=1 if how == "done" else 0,
            prompt_tokens=38000,
            cached_tokens=34000,
            completion_tokens=2600,
            reasoning_tokens=2300,
            **ending,
        )
    )
    return events


def _cites(minutes: float, issue: int, cards: list[str]) -> Raw:
    return (
        "tick",
        {
            "at": _at(minutes),
            "kind": "issue.cites",
            "card": issue,
            "detail": {"repo": "crew", "cites": cards},
        },
    )


def _delivered(start: float, card: int, pr: int, *, calls: int = 2) -> list[Raw]:
    """A story that landed first time: claimed, built, reviewed, proven and merged."""
    events = [
        _moved(start, card, "Sprint Backlog", "In Progress"),
        _work(start, card, "Developer"),
    ]
    for i in range(calls):
        events.append(
            _call(
                start + 2 + 4 * i,
                card,
                "Developer",
                "deliver",
                41000 + 900 * card,
                5200,
                3800,
                190.0 + 10 * i,
                f"c{card}-{i}",
            )
        )
    events += [
        _work(start + 12, card, "Developer", done=True),
        _moved(start + 12, card, "In Progress", "Reviewing"),
        _work(start + 14, None, "Code Reviewer", pr=pr),
        _work(start + 17, None, "Code Reviewer", done=True, pr=pr),
        _moved(start + 17, card, "Reviewing", "QAing", role="Code Reviewer"),
        _moved(start + 21, card, "QAing", "Merging", role="QA Engineer"),
        _moved(start + 23, card, "Merging", "Done", role="DevOps Engineer"),
    ]
    return events


def _events() -> list[Raw]:
    day_before = -24 * 60
    reason = "no definition named 'DEFAULT_THRESHOLDS'. This file defines: THRESHOLDS"
    events = [
        # Sprint 5: two stories land first time, one after a failing test.
        *_delivered(day_before, 110, 115),
        *_delivered(day_before + 30, 111, 116),
        _moved(day_before + 60, 112, "Sprint Backlog", "In Progress"),
        _work(day_before + 60, 112, "Developer"),
        _call(day_before + 62, 112, "Developer", "deliver", 39000, 4100, 3300, 170.0, "c112"),
        _refused(day_before + 66, 112, 1, "VERIFY", "retry_local"),
        *_delivered(day_before + 70, 112, 117)[2:],
        _cites(day_before + 80, 200, []),
        # Sprint 6. The Sponsor approves the API epic; the Architect revises the design,
        # the crew merges it and files two technical epics, no one asked.
        _by_person(-40, 59, "Inbox (Goals)", "Needs Refinement"),
        _work(-10, None, "Architect"),
        _work(7, None, "Architect", done=True),
        # The stories of the split.
        *_delivered(20, 125, 134),
        _moved(50, 126, "Sprint Backlog", "In Progress"),
        _work(50, 126, "Developer"),
        _call(52, 126, "Developer", "deliver", 52000, 6100, 4400, 230.0, "c126-0"),
        _refused(57, 126, 1, "VERIFY", "retry_local"),
        *_delivered(58, 126, 135)[2:],
        # #127: refused three times with the same message, blocked, the crew fixes
        # itself twice, and it lands on the next attempt.
        _moved(94, 127, "Sprint Backlog", "In Progress"),
        _work(94, 127, "Developer"),
        _call(95, 127, "Developer", "deliver", 47864, 10184, 8412, 335.8, "c127-0"),
        _refused(100, 127, 1, "EDIT", "retry_local", error=reason),
        _call(101, 127, "Developer", "deliver", 49210, 9876, 7990, 301.2, "c127-1"),
        _refused(102, 127, 2, "EDIT", "retry_local", error=reason),
        _call(103, 127, "Developer", "deliver", 50102, 9455, 7702, 288.4, "c127-2"),
        _refused(104, 127, 3, "EDIT", "block", error=reason),
        _plain(104, "card.blocked", 127),
        _moved(104, 127, "In Progress", "Blocked"),
        _cites(105, 206, ["sprint-metrics#127"]),
        _cites(112, 207, ["sprint-metrics#127"]),
        _by_person(120, 127, "Blocked", "In Progress"),
        _call(121, 127, "Developer", "deliver", 51007, 8820, 6610, 274.9, "c127-3"),
        *_delivered(122, 127, 137, calls=1)[2:],
        *_delivered(150, 128, 138),
        # #133 needs the escalation model.
        _moved(175, 133, "Sprint Backlog", "In Progress"),
        _work(175, 133, "Developer"),
        _call(176, 133, "Developer", "deliver", 44000, 5000, 3900, 210.0, "c133-0"),
        _refused(180, 133, 1, "VERIFY", "retry_local"),
        _call(181, 133, "Developer", "deliver", 45000, 5100, 4000, 205.0, "c133-1"),
        _refused(185, 133, 2, "VERIFY", "escalate"),
        _plain(186, "escalation.sent", 133),
        *_delivered(190, 133, 139, calls=1)[2:],
        # #130 was superseded when its epic was split again after a story problem:
        # closed as not planned, which the board shows as Done.
        (
            "tick",
            {
                "at": _at(199),
                "kind": "epic.resplit",
                "role": None,
                "card": 123,
                "summary": "epic #123 split again after a story problem",
                "detail": {"repo": SM, "superseded": [130], "because": "story problem"},
            },
        ),
        # The design revision the Code Reviewer refused once before the crew merged it.
        _sent_back(
            199.5,
            None,
            "review",
            "design",
            "sent_back.design_refused",
            role="Code Reviewer",
            round=1,
        ),
        _by_person(200, 130, "Ready", "Done", by="platform"),
        # Still going at the sprint's end, and one blocked.
        _moved(220, 129, "Sprint Backlog", "In Progress"),
        _work(220, 129, "Developer"),
        _call(222, 129, "Developer", "deliver", 43000, 4800, 3500, 200.0, "c129-0"),
        _moved(225, 131, "Sprint Backlog", "In Progress"),
        _call(226, 131, "Developer", "deliver", 42000, 4400, 3000, 180.0, "c131-0"),
        _refused(230, 131, 1, "SCOPE", "block"),
        _moved(230, 131, "In Progress", "Blocked"),
        # The first stories in Developer sessions (crew#778). #136: the gate returns its
        # first branch, review asks for a change, and it's back in review at the end.
        _moved(240, 136, "Sprint Backlog", "In Progress"),
        *_sessioned(241, 136),
        _session(
            246,
            "gate.checked",
            136,
            passed=False,
            rules=["contract.declared_tests"],
            already_done=False,
        ),
        _sent_back(246, 136, "gate", "build", "gate.findings_at_done", round=1, findings=1),
        *_sessioned(247, 136, rework=True),
        _session(249, "session.compacted", 136, turn=3, estimated_tokens=126000, turns_masked=14),
        _session(252, "gate.checked", 136, passed=True, rules=[], already_done=False),
        _moved(252, 136, "In Progress", "Reviewing"),
        _work(255, None, "Code Reviewer", pr=140),
        (
            "tick",
            {
                "at": _at(258),
                "kind": "agent.finished",
                "role": "Code Reviewer",
                "card": 136,
                "detail": {"repo": SM, "pr": 140, "approved": False},
            },
        ),
        _moved(258, 136, "Reviewing", "In Progress", role="Code Reviewer"),
        _sent_back(
            258,
            136,
            "review",
            "build",
            "gate.review_changes_requested",
            role="Code Reviewer",
            round=1,
            findings=2,
        ),
        *_sessioned(260, 136, rework=True),
        _session(263, "tool.failed", 136, tool="after_run", turn=2),
        _session(265, "gate.checked", 136, passed=True, rules=[], already_done=False),
        _moved(265, 136, "In Progress", "Reviewing"),
        # #132 reads for sixty calls with no edit and no run, and goes back to the
        # Business Analyst who wrote it.
        _moved(245, 132, "Sprint Backlog", "In Progress"),
        *_sessioned(
            246,
            132,
            how="blocked",
            stop="ceiling",
            evidence={"stop": {"ceiling": "reading", "calls": 60}},
        ),
        _session(251, "story.returned", 132, reason="session blocked", kind="stop", stop="ceiling"),
        _moved(251, 132, "In Progress", "Ready"),
        _sent_back(
            251, 132, "build", "split", "story.session_blocked", role="Developer", stories=1
        ),
    ]
    return events


def _card(
    number: int,
    title: str,
    work_type: str,
    status: str,
    *,
    created: datetime,
    status_set: datetime | None = None,
    closed: datetime | None = None,
    not_planned: bool = False,
    parent: int | None = None,
    points: float | None = None,
    sprint: str | None = None,
    labels: tuple[str, ...] | set[str] = (),
) -> Card:
    return Card(
        item_id=f"mock-{number}",
        number=number,
        repo=SM,
        title=title,
        url=f"https://github.com/mqucifer/{SM}/issues/{number}",
        work_type=work_type,
        status=status,
        status_set=status_set,
        state="CLOSED" if closed else "OPEN",
        state_reason=("NOT_PLANNED" if not_planned else "COMPLETED") if closed else None,
        created=created,
        closed=closed,
        parent=parent,
        points=points,
        sprint=sprint,
        labels=frozenset(labels),
    )


def _when(minutes: float) -> datetime:
    return T + timedelta(minutes=minutes)


def _cards() -> list[Card]:
    filed = _when(-3 * 24 * 60)
    s5, s6 = "Sprint 5", "Sprint 6"

    def story(
        number: int,
        title: str,
        parent: int,
        sprint: str,
        status: str,
        closed: datetime | None = None,
        **kw: Any,
    ) -> Card:
        return _card(
            number,
            title,
            "Story",
            status,
            created=filed,
            parent=parent,
            points=3,
            sprint=sprint,
            closed=closed,
            **kw,
        )

    return [
        _card(
            48,
            "Goal: the tool presents, not just reports",
            "Goal",
            "Needs Refinement",
            created=filed,
        ),
        _card(59, "Stable JSON API", "Epic", "Needs Refinement", created=filed, parent=48),
        _card(
            62,
            "A five-second health summary",
            "Epic",
            "Inbox (Goals)",
            created=filed,
            status_set=_when(-2 * 24 * 60),
            parent=48,
        ),
        _card(
            63,
            "A trend line a person can read",
            "Epic",
            "Inbox (Goals)",
            created=filed,
            status_set=_when(-2 * 24 * 60),
            parent=48,
        ),
        _card(
            105,
            "A JSON report of one sprint",
            "Epic",
            "Done",
            created=filed,
            closed=_when(-22 * 60),
        ),
        story(110, "Write the sprint report as JSON", 105, s5, "Done", _when(-24 * 60 + 23)),
        story(111, "Show the prior sprint beside it", 105, s5, "Done", _when(-24 * 60 + 53)),
        story(112, "Exit 2 when the sprint is missing", 105, s5, "Done", _when(-24 * 60 + 93)),
        _card(
            123,
            "Split crew_performance.py into seven modules",
            "Epic",
            "In Progress",
            created=_when(8),
            labels={"technical"},
        ),
        _card(
            124,
            "One entry point for the CLI and the service",
            "Epic",
            "Ready",
            created=_when(8),
            labels={"technical"},
        ),
        story(125, "Move the card model into card.py", 123, s6, "Done", _when(43)),
        story(126, "Move the metric functions into metrics.py", 123, s6, "Done", _when(81)),
        story(127, "Extract threshold logic into thresholds.py", 123, s6, "Done", _when(145)),
        story(128, "Move the report into report.py", 123, s6, "Done", _when(173)),
        story(129, "Move sprint ranges into sprint_range.py", 123, s6, "In Progress"),
        story(130, "Move serve into serve.py", 123, s6, "Done", _when(200), not_planned=True),
        story(131, "Move the CLI into cli.py", 123, s6, "Blocked"),
        story(133, "Add the package's entry point", 124, s6, "Done", _when(213)),
        story(132, "Serve the trend from the package", 124, s6, "Ready"),
        story(136, "Read the sprint from the service's query", 124, s6, "Reviewing"),
    ]


def _pull(
    number: int,
    title: str,
    merged: float,
    closes: list[tuple[str, int]],
    *,
    labels: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "created_at": _at(merged - 30),
        "merged_at": _at(merged),
        "closed_at": _at(merged),
        "closes": closes,
        "closes_labels": list(labels),
    }


def _pulls() -> dict[str, list[dict[str, Any]]]:
    day_before = -24 * 60
    return {
        SM: [
            _pull(115, "feat: the sprint report as JSON", day_before + 23, [(SM, 110)]),
            _pull(116, "feat: the prior sprint beside it", day_before + 53, [(SM, 111)]),
            _pull(117, "fix: exit 2 when the sprint is missing", day_before + 93, [(SM, 112)]),
            _pull(
                122,
                "chore: revisit this project's design",
                8,
                [(SM, 121)],
                labels=["design:revisit"],
            ),
            _pull(134, "refactor: the card model in card.py", 43, [(SM, 125)]),
            _pull(135, "refactor: the metric functions in metrics.py", 81, [(SM, 126)]),
            _pull(137, "refactor: threshold logic in thresholds.py", 145, [(SM, 127)]),
            _pull(138, "refactor: the report in report.py", 173, [(SM, 128)]),
            _pull(139, "feat: the package's entry point", 213, [(SM, 133)]),
            {
                "number": 140,
                "title": "feat: read the sprint from the service's query",
                "created_at": _at(252),
                "merged_at": None,
                "closed_at": None,
                "closes": [(SM, 136)],
                "closes_labels": [],
            },
        ],
        "crew": [
            _pull(
                201,
                "fix: a story's tests run from its own directory",
                day_before + 75,
                [("crew", 200)],
            ),
            _pull(208, "fix: a refusal names the step that's missing", 109, [("crew", 206)]),
            _pull(209, "fix: annotated constants are addressable by name", 118, [("crew", 207)]),
        ],
    }


# Each sprint's numbers as sprint-metrics would compute them from the mock's cards.
_SPRINT_5 = {
    "throughput": 3,
    "points_delivered": 9,
    "first_attempt_numerator": 2,
    "first_attempt_denominator": 3,
    "first_attempt_rate_percent": 67,
    "escalation_count": 0,
    "escalation_rate_percent": 0,
    "wip_violations": 0,
    "blocked_aging_days": 0,
    "cycle_time_days": 0,
    "lead_time_days": 1,
    "cycle_time_minutes": 410,
    "lead_time_minutes": 1900,
    "superseded_count": 0,
    "superseded_points": 0,
    "superseded_breakdown": {"sponsor": 0, "story_problem": 0},
    "top_failure_causes": {"VERIFY": 1},
}
_SPRINT_6 = {
    "throughput": 6,
    "points_delivered": 18,
    "first_attempt_numerator": 3,
    "first_attempt_denominator": 6,
    "first_attempt_rate_percent": 50,
    "escalation_count": 1,
    "escalation_rate_percent": 17,
    "wip_violations": 0,
    "blocked_aging_days": 1,
    "cycle_time_days": 0,
    "lead_time_days": 1,
    "cycle_time_minutes": 655,
    "lead_time_minutes": 1500,
    "superseded_count": 1,
    "superseded_points": 3,
    "superseded_breakdown": {"sponsor": 0, "story_problem": 1},
    "top_failure_causes": {"VERIFY": 2, "EDIT": 1},
}


def _answer(now: dict[str, Any], prior: dict[str, Any] | None, **rest: Any) -> dict[str, Any]:
    """One sprint's entry of sprint-metrics' GET /range, with the sprint before as `prior`."""
    delta = None
    if prior is not None:
        delta = {
            k: v - prior[k]
            for k, v in now.items()
            if isinstance(v, int) and isinstance(prior.get(k), int)
        }
        delta["superseded_breakdown"] = sum(now["superseded_breakdown"].values()) - sum(
            prior["superseded_breakdown"].values()
        )
    flags = {k: False for k in now if k.endswith(("_days", "_minutes", "_percent"))}
    flags |= {k: False for k in ("throughput", "wip_violations")}
    return {**now, **rest, "flags": flags, "prior": prior, "delta": delta}


def _sprint_metrics() -> dh.SprintMetrics:
    """sprint-metrics' part, checked against its schema as the service's answer is."""
    body: dict[str, Any] = {
        "api_version": "1",
        "sprints": {
            "Sprint 5": _answer(
                _SPRINT_5,
                None,
                failure_breakdown=[{"class": "VERIFY", "role": "Developer", "count": 1}],
            ),
            "Sprint 6": _answer(
                _SPRINT_6,
                dict(_SPRINT_5),
                failure_breakdown=[
                    {"class": "VERIFY", "role": "Developer", "count": 2},
                    {"class": "EDIT", "role": "Developer", "count": 1},
                ],
            ),
        },
    }
    body["sprints"]["Sprint 6"]["flags"]["first_attempt_rate_percent"] = True
    return dh.SprintMetrics(
        release=sma.RELEASE, answers=sma.checked(body, ["Sprint 5", "Sprint 6"])
    )


def inputs() -> dh.Inputs:
    return dh.Inputs(
        events=_events(),
        cards=_cards(),
        sprints=dh.sprints_from(SPRINTS),
        pulls=_pulls(),
        crew_repository="crew",
        repositories=[SM],
        timezone="America/Chicago",
        date=date(2026, 9, 25),
        generated=T + timedelta(hours=6),
        sprint_metrics=_sprint_metrics(),
    )


def files() -> dict[str, str]:
    """Every file of the mock package, by its path in the package, as written."""
    history, periods, manifest = dh.build(inputs())
    out = {
        "manifest.json": manifest.model_dump_json(indent=2) + "\n",
        f"{dh.NAME}.json": history.model_dump_json(indent=1) + "\n",
    }
    out.update({path: p.model_dump_json(indent=1) + "\n" for path, p in periods.items()})
    return out


def write(directory: Path) -> list[Path]:
    """Write the mock into `directory`, replacing whatever was there."""
    target = directory / DIRECTORY
    for old in sorted(target.rglob("*.json"), reverse=True) if target.exists() else []:
        old.unlink()
    written = []
    for path, text in files().items():
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        (target / path).write_text(text, encoding="utf-8")
        written.append(target / path)
    return written


def read(directory: Path) -> dict[str, str]:
    """The committed mock, by its path in the package."""
    target = directory / DIRECTORY
    return {
        str(p.relative_to(target)): p.read_text(encoding="utf-8")
        for p in sorted(target.rglob("*.json"))
    }
