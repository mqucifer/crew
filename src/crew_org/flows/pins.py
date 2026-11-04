"""A release's pin pull request is the crew's upgrade work (crew#1028).

A release reaches each consumer as a pull request that changes the one line naming it
(`packages/release.py`, ADR 0021). When its checks failed, nothing in the crew picked
it up: mqucifer/crew-presentation#294 failed a contract test, and
mqucifer/crew-presentation#303 a QA test hard-coding nine board columns, and a person
filed each one's work by hand. The Sponsor's call (2026-11-04): the crew does the work.

So each open pin pull request has a card, a Task labelled `pin`, and each pass follows
its checks:

- **Failed:** CI's return (`ci.return_for_ci`) puts the log on it, as on any card, and
  finding routing (`role_tasks.finding_role`, crew#1018) says whose work each failure
  is. A QA test failing in qa-suite is QA's Task there, shaped as a dispute, so QA's fix
  runs against the pin's head and lands straight after it (crew#945). Anything else is
  the Developer's bug in the consumer: whatever the new package breaks is part of the
  upgrade. The card waits on them in Blocked, labelled `waits:upgrade`.
- **Its upgrade work in:** QA's Task approved or closed, the Developer's bug closed as
  completed. An empty commit runs the pin's checks again, and the card goes back to
  Merging. QA saying a test stands files the Developer's bug instead.
- **Only what the crew can't fix goes to a person:** work closed without a fix, or the
  same tests failing `delivery.ci_same_failure_stop` times running, blocks the card,
  saying why.
- **Green:** it waits in Merging for the Sponsor, who merges it, as before. The crew
  never lands it. Merged or superseded, its card closes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from crew_org.columns import BLOCKED, DONE, MERGING, READY
from crew_org.crews.outside import OutsideFinding
from crew_org.events import EventSink, held
from crew_org.flows import artifacts, ci
from crew_org.flows import qa_tasks as qa
from crew_org.flows import role_tasks as rt
from crew_org.flows.moves import move_card
from crew_org.llm import reraise_if_down
from crew_org.packages import release
from crew_org.tools.github_issues import FAILED_CONCLUSIONS, IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.work_types import PIN_LABEL, SUITE, TASK

LABEL = PIN_LABEL
# On a pin's card while it waits on its upgrade work: neither a person's nor sent back.
WAITS_LABEL = "waits:upgrade"
# In the card's body: the pull request it's the card of.
MARKER = "<!-- crew:pin pull={pull} -->"
_MARKER = re.compile(r"<!-- crew:pin pull=(\d+) -->")
# On the card: the head CI returned and the work it waits on, as `repo#n`, comma-separated.
WAITS = "<!-- crew:pin-waits head={head} on={on} -->"
_WAITS = re.compile(r"<!-- crew:pin-waits head=([0-9a-f]+) on=(\S*) -->")
# On the pull request, once per head: its upgrade work is in, and its checks run again.
RERUN = "<!-- crew:pin-rerun head={head} -->"
# A test QA's upgrade Task names, in its Done when.
_DONE_WHEN = re.compile(r"^- \[ \] `(\S+::\S+)` passes on the upgrade", re.MULTILINE)


@dataclass
class Followed:
    """What following the pins did, each as (consumer, number)."""

    opened: list[tuple[str, int]] = field(default_factory=list)
    returned: list[tuple[str, int]] = field(default_factory=list)
    rerun: list[tuple[str, int]] = field(default_factory=list)
    closed: list[tuple[str, int]] = field(default_factory=list)
    # Green, merged by the Sponsor.
    sponsor: list[tuple[str, int]] = field(default_factory=list)
    # (where, why), said as held lines.
    waiting: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        return bool(self.opened or self.returned or self.rerun or self.closed)


def pull_of(issue: dict[str, Any]) -> int | None:
    """The pin pull request a card's issue is for, or None."""
    found = _MARKER.search(issue.get("body") or "")
    return int(found.group(1)) if found else None


def follow(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    *,
    cards: list[Card],
    repos: set[str],
    stop: int | None = None,
) -> Followed:
    """Follow every open pin pull request in the consumers a tick works. Never fails the
    pass: what couldn't be done is said, and the next pass tries again."""
    result = Followed()
    for consumer in sorted(set(release.CONSUMERS) & repos):
        try:
            _follow(issues, board, sink, ws, consumer, cards=cards, result=result, stop=stop)
        except Exception as exc:  # noqa: BLE001 - tried again next pass
            reraise_if_down(exc)
            result.failed.append((consumer, f"pins not followed: {exc}"[:200]))
    return result


def _follow(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    consumer: str,
    *,
    cards: list[Card],
    result: Followed,
    stop: int | None,
) -> None:
    pulls = {
        int(p["number"]): p
        for p in issues.open_pulls(consumer)
        if release.pinned_tag((p.get("head") or {}).get("ref") or "")
    }
    known = {n: i for i in issues.labelled(consumer, LABEL) if (n := pull_of(i)) is not None}
    for number, issue in sorted(known.items()):
        if issue.get("state") == "open" and number not in pulls:
            _close(issues, board, sink, consumer, issue, pull=number, cards=cards, result=result)
    for number, pull in sorted(pulls.items()):
        where = f"{consumer}#{number}"
        try:
            card = _card(issues, board, sink, consumer, pull, known.get(number), cards, result)
            if card is None:
                held("decision.pins.upgrade_work", repo=consumer, card=number)
                result.waiting.append((where, "its card was closed by a person"))
                continue
            _step(issues, board, sink, ws, consumer, card, pull, cards, result, stop)
        except Exception as exc:  # noqa: BLE001 - tried again next pass
            reraise_if_down(exc)
            result.failed.append((where, f"pin not followed: {exc}"[:200]))


def _card(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    pull: dict[str, Any],
    issue: dict[str, Any] | None,
    cards: list[Card],
    result: Followed,
) -> Card | None:
    """The pin's card, opened once and placed in Merging, or None when it's closed."""
    number = int(pull["number"])
    tag = release.pinned_tag(pull["head"]["ref"]) or ""
    new = issue is None
    if issue is None:
        issues.ensure_label(
            consumer,
            LABEL,
            color="c5def5",
            description="A release's pin: the crew's upgrade work, merged by the Sponsor",
        )
        issue = issues.create(
            consumer,
            f"Upgrade to {tag}",
            f"{MARKER.format(pull=number)}\nThe crew released `{tag}`, and "
            f"{issues.owner}/{consumer}#{number} moves {consumer} to it. The crew follows its "
            "checks: a failure goes back to the crew as upgrade work, and once they pass the "
            "Sponsor merges it (mqucifer/crew#1028).",
            labels=[LABEL],
        )
    if issue.get("state") == "closed":
        return None
    own = int(issue["number"])
    card = next((c for c in cards if c.repo == consumer and c.number == own), None)
    if card is not None:
        return card
    item = board.add_issue(issue["node_id"])
    board.set_select(item, "Work Type", TASK)
    move_card(
        board,
        sink,
        item_id=item,
        to=MERGING,
        by=None,
        card=own,
        summary=f"the pin {consumer}#{number} to {tag}: its checks are followed"[:100],
        rule="decision.pins.upgrade_work",
    )
    if new:
        result.opened.append((consumer, own))
    return Card(
        item_id=item,
        number=own,
        title=str(issue.get("title") or ""),
        repo=consumer,
        state="OPEN",
        status=MERGING,
        work_type=TASK,
        labels=frozenset({LABEL}),
    )


def _step(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    consumer: str,
    card: Card,
    pull: dict[str, Any],
    cards: list[Card],
    result: Followed,
    stop: int | None,
) -> None:
    number = int(pull["number"])
    where = f"{consumer}#{number}"
    if card.status == BLOCKED and WAITS_LABEL in card.labels:
        _settle(issues, board, sink, ws, consumer, card, pull, cards, result)
        return
    if card.status != MERGING:
        held("decision.pins.upgrade_work", repo=consumer, card=card.number)
        result.waiting.append((where, f"its card is in {card.status}, for a person"))
        return
    head = (pull.get("head") or {}).get("sha", "")
    runs = issues.check_runs(consumer, head)
    running = ci.running_checks(runs)
    if not runs or running:
        held("decision.pins.sponsor_merges", repo=consumer, card=card.number)
        result.waiting.append((where, f"its checks run: {', '.join(running) or 'not started'}"))
        return
    failed = [r for r in runs if r.get("conclusion") in FAILED_CONCLUSIONS]
    if not failed:
        held("decision.pins.sponsor_merges", repo=consumer, card=card.number)
        result.sponsor.append((consumer, number))
        return
    said = ci.return_for_ci(
        board,
        issues,
        sink,
        card,
        repo=consumer,
        pull=pull,
        failed=failed,
        where="on the upgrade",
        frm=MERGING,
        rule="decision.pins.upgrade_work",
        stop=stop,
        to=BLOCKED,
        who="the crew, as upgrade work",
    )
    result.returned.append((consumer, number))
    if said is None:
        # Blocked for a person: the same tests failed `stop` returns running.
        return
    artifacts.label(
        issues, sink, repo=consumer, number=card.number or 0, by=None, add=[WAITS_LABEL]
    )
    _route(issues, board, sink, consumer, card, pull, said, cards)


def _route(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    card: Card,
    pull: dict[str, Any],
    said: str,
    cards: list[Card],
) -> None:
    """Open each failure's upgrade work, as finding routing says whose it is (crew#1018),
    and say on the card what it waits on."""
    head = (pull.get("head") or {}).get("sha", "")
    parts = ci.by_check(said)
    project = card.model_copy(update={"labels": card.labels | {rt.project_label(consumer)}})
    qa_said = parts.get(qa.QA_CHECK, "")
    qa_tests = sorted(
        t
        for t in ci.failing_tests(qa_said)
        if rt.finding_role(rt.QA.key, project, SUITE, t.partition("::")[0]) is not None
    )
    own = sorted(t for text in parts.values() for t in ci.failing_tests(text) if t not in qa_tests)
    untested = [name for name, text in parts.items() if not ci.failing_tests(text)]
    on: list[tuple[str, int]] = []
    if qa_tests:
        task = _qa_task(issues, board, sink, consumer, card, pull, cards, qa_tests, qa_said)
        on.append((SUITE, task))
    if own or untested or not qa_tests:
        shown = [f"`{t}`" for t in own] or [f"the check `{n}`" for n in untested]
        bug = _bug(
            issues,
            board,
            sink,
            consumer,
            card,
            pull,
            cards,
            what=", ".join(shown) or "its checks",
            where=", ".join(t.partition("::")[0] for t in own) or ", ".join(untested),
            evidence=f"\n\n{said}",
            round_key=head[:12],
        )
        on.append((consumer, bug))
    _wait(issues, sink, consumer, card, head, on)


def _qa_task(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    card: Card,
    pull: dict[str, Any],
    cards: list[Card],
    tests: list[str],
    said: str,
) -> int:
    """QA's Task in qa-suite for its tests the pin fails, shaped as a dispute: qa-suite
    runs QA's fix against the pin's head, and lands it straight after the pin (crew#945).
    """
    number = int(pull["number"])
    head = (pull.get("head") or {}).get("sha", "")
    tag = release.pinned_tag(pull["head"]["ref"]) or ""
    key = f"{consumer}-p{number}-{head[:12]}"
    shown = ", ".join(f"`{t}`" for t in tests)
    body = (
        rt.render(
            rt.QA,
            kind=qa.DISPUTE,
            key=key,
            why=f"The crew released `{tag}`, and `{qa.QA_CHECK}` fails {shown} on "
            f"{issues.owner}/{consumer}#{number}, the pull request that moves {consumer} to "
            "it. Whatever the new package breaks is part of the upgrade, and a test in "
            "qa-suite is QA's (mqucifer/crew#1028).",
            work=f"Decide whether each test is wrong for the new package. If it is, fix it "
            f"in `{consumer}/`: your pull request runs it against the upgrade's head, not "
            f"{consumer}'s main. Once yours is approved the upgrade's checks run again; the "
            "Sponsor merges the upgrade, and yours lands straight after. If a test is right, "
            f"change nothing and say why in `done`'s `justified`: {consumer}'s own code is "
            f"then the Developer's to change. {consumer} as the upgrade has it is at "
            f"`{qa.REFERENCE}/{consumer}/` in your worktree, to read and never to change."
            f"\n\n{qa.reach(issues, consumer)}"
            f"\n\n## What CI said\n\n{ci.last_lines(said, qa.CI_SHOWN)}",
            done_when=[f"`{t}` passes on the upgrade, or is justified with why" for t in tests],
        )
        + "\n\n"
        + qa.DISPUTED.format(repo=consumer, story=card.number or 0, pull=number, head=head)
    )
    return rt.open_task(
        issues,
        board,
        sink,
        repo=SUITE,
        role=rt.QA,
        kind=qa.DISPUTE,
        key=key,
        title=f"Upgrade to {tag}: {tests[0]}"[:120],
        body=body,
        points=qa.DISPUTE_POINTS,
        cards=cards,
        column=READY,
        by=None,
        event="pin.failed",
        rule="decision.pins.upgrade_work",
        labels=(rt.project_label(consumer),),
        detail={"project": consumer, "pull": number},
    ).number


def _bug(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    card: Card,
    pull: dict[str, Any],
    cards: list[Card],
    *,
    what: str,
    where: str,
    evidence: str,
    round_key: str,
) -> int:
    """The Developer's bug in the consumer, filed as a finding no role's Task may change
    is (crew#1018). Its fix lands on main, which still names the old release."""
    number = int(pull["number"])
    tag = release.pinned_tag(pull["head"]["ref"]) or ""
    try:
        old = issues.file_at(consumer, release.TAG_FILE, issues.default_branch(consumer)) or ""
    except Exception:  # noqa: BLE001 - said as unknown
        old = ""
    named = f"`{old.strip()}`" if old.strip() else "the release before"
    filed = rt.file_findings(
        issues,
        board,
        sink,
        repo=consumer,
        story=card,
        found=[
            OutsideFinding(
                owner="bug",
                what=f"`{tag}` fails {what} on {issues.owner}/{consumer}#{number}"[:400],
                where=where or release.TAG_FILE,
                evidence=evidence,
            )
        ],
        by="Developer",
        round_key=f"pin-{round_key}",
        cards=cards,
        found_while=f"The crew released `{tag}`, and {issues.owner}/{consumer}#{number} "
        f"moves {consumer} to it from {named}; its checks fail. Whatever the new package "
        "breaks is part of the upgrade (mqucifer/crew#1028). The fix lands on main first, "
        f"where {named} is still named, so it holds with both; then the upgrade's checks "
        "run again.",
    )
    if not filed:
        raise RuntimeError("the Developer's upgrade work wasn't filed")
    return filed[0]


def _wait(
    issues: IssueClient,
    sink: EventSink,
    consumer: str,
    card: Card,
    head: str,
    on: list[tuple[str, int]],
) -> None:
    links = ", ".join(f"{issues.owner}/{r}#{n}" for r, n in on)
    artifacts.comment(
        issues,
        sink,
        repo=consumer,
        number=card.number or 0,
        body=f"{WAITS.format(head=head, on=','.join(f'{r}#{n}' for r, n in on))}\n"
        f"**Waits for its upgrade work:** {links}. Once it's in, its checks run again.",
        by=None,
    )


def _waits(
    issues: IssueClient, consumer: str, number: int, head: str
) -> list[tuple[str, int]] | None:
    """What the card waits on for `head`, from its latest note; None when it has none."""
    found = [
        m
        for c in issues.comments(consumer, number)
        if (m := _WAITS.search(c.get("body") or "")) is not None and m.group(1) == head
    ]
    if not found:
        return None
    items = [i.rpartition("#") for i in found[-1].group(2).split(",") if i]
    return [(repo, int(n)) for repo, _, n in items]


def _settle(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    consumer: str,
    card: Card,
    pull: dict[str, Any],
    cards: list[Card],
    result: Followed,
) -> None:
    """A pin waiting on its upgrade work goes on once all of it is in."""
    number = int(pull["number"])
    where = f"{consumer}#{number}"
    head = (pull.get("head") or {}).get("sha", "")
    on = _waits(issues, consumer, card.number or 0, head)
    if on is None:
        # Returned on this head with no work opened yet, as when a pass stopped part-way:
        # it's opened now. A head CI hasn't returned goes back to have its checks read.
        said = ci.latest_ci_verdict(issues, consumer, number)
        if ci.CI_MARKER.format(head=head) in said:
            held("decision.pins.upgrade_work", repo=consumer, card=card.number)
            _route(issues, board, sink, consumer, card, pull, said, cards)
            return
        _go_on(issues, board, sink, consumer, card, f"its head moved to {head[:7]}")
        return
    waiting: list[tuple[str, int]] = []
    changed = False
    for repo, n in on:
        issue = issues.get(repo, n)
        closed = issue.get("state") == "closed"
        if repo == SUITE:
            stands = qa._justification(issues, n) if closed else None
            if stands is not None:
                bug = _stands(issues, board, sink, consumer, card, pull, cards, n, stands)
                waiting.append((consumer, bug))
                changed = True
                continue
            approved = any(c.repo == SUITE and c.number == n and c.status == MERGING for c in cards)
            if not (closed or approved):
                waiting.append((repo, n))
            continue
        if closed and issue.get("state_reason") == "not_planned":
            _for_a_person(issues, sink, consumer, card, f"{issues.owner}/{repo}#{n}")
            held("decision.pins.settled", repo=consumer, card=card.number)
            result.waiting.append((where, f"blocked for a person: {repo}#{n} closed unfixed"))
            return
        if not closed:
            waiting.append((repo, n))
    if changed:
        _wait(issues, sink, consumer, card, head, waiting)
    if waiting:
        held("decision.pins.settled", repo=consumer, card=card.number)
        shown = ", ".join(f"{r}#{n}" for r, n in waiting)
        result.waiting.append((where, f"waits for its upgrade work: {shown}"))
        return
    links = ", ".join(f"{issues.owner}/{r}#{n}" for r, n in on)
    if not issues.has_comment_marked(consumer, number, RERUN.format(head=head)):
        qa._rerun(ws, consumer, pull, message=f"ci: run the upgrade's checks again ({links})")
        artifacts.comment(
            issues,
            sink,
            repo=consumer,
            number=number,
            body=f"{RERUN.format(head=head)}\n**Its upgrade work is in** ({links}): its checks "
            "run again.",
            by=None,
        )
    _go_on(issues, board, sink, consumer, card, f"its upgrade work is in ({links})")
    result.rerun.append((consumer, number))


def _stands(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    card: Card,
    pull: dict[str, Any],
    cards: list[Card],
    task: int,
    why: str,
) -> int:
    """QA says its tests stand on the upgrade: the consumer's code is the Developer's."""
    found = _DONE_WHEN.findall(issues.get(SUITE, task).get("body") or "")
    tests = ", ".join(f"`{t}`" for t in found) or f"QA's tests ({issues.owner}/{SUITE}#{task})"
    return _bug(
        issues,
        board,
        sink,
        consumer,
        card,
        pull,
        cards,
        what=f"{tests}, which QA says stand",
        where=", ".join(t.partition("::")[0] for t in found),
        evidence=f"QA's answer ({issues.owner}/{SUITE}#{task}): {why}",
        round_key=f"q{task}",
    )


def _go_on(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    card: Card,
    why: str,
) -> None:
    """Back to Merging, where its checks are read."""
    artifacts.label(
        issues, sink, repo=consumer, number=card.number or 0, by=None, remove=[WAITS_LABEL]
    )
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=MERGING,
        by=None,
        card=card.number,
        frm=BLOCKED,
        summary=f"{why}: its checks are read again"[:100],
        rule="decision.pins.settled",
    )


def _for_a_person(
    issues: IssueClient, sink: EventSink, consumer: str, card: Card, link: str
) -> None:
    """What the crew can't fix: blocked for a person, saying why."""
    number = card.number or 0
    artifacts.label(
        issues,
        sink,
        repo=consumer,
        number=number,
        by=None,
        add=["blocked", "needs:human"],
        remove=[WAITS_LABEL],
        rule="decision.pins.settled",
    )
    artifacts.comment(
        issues,
        sink,
        repo=consumer,
        number=number,
        body=f"**Blocked: its upgrade work {link} was closed without a fix.** The crew "
        "can't take the upgrade further, so a person decides: the fix, or the release.",
        by=None,
    )


def _close(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    consumer: str,
    issue: dict[str, Any],
    *,
    pull: int,
    cards: list[Card],
    result: Followed,
) -> None:
    """The pin merged, or was closed (a later release supersedes it): so is its card."""
    live = issues.pull(consumer, pull)
    merged = bool(live.get("merged") or live.get("merged_at"))
    number = int(issue["number"])
    card = next((c for c in cards if c.repo == consumer and c.number == number), None)
    if card is not None and card.status != DONE:
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=DONE,
            by=None,
            card=number,
            frm=card.status,
            summary=f"the pin {consumer}#{pull} "
            + ("merged" if merged else "closed without merging"),
            rule="decision.pins.sponsor_merges",
        )
    issues.close(consumer, number, reason="completed" if merged else "not_planned")
    result.closed.append((consumer, number))
