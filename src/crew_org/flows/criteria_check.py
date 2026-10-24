"""A split whose criteria can't all be met goes to the Product Owner, not the board (#428).

The check runs on the Business Analyst's proposal before any story is
created. A conflict gets one repair: the flagged stories' criteria are rewritten
and the rest stay as they were (crew#440). One that survives goes on the epic as
a story problem with `needs:rework`, so the Product Owner settles it (#189) and
the epic is split again following that.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from crew_org import sendbacks
from crew_org.crews.criteria_crew import CriteriaCheck, CriteriaConflict
from crew_org.crews.refinement_crew import CriteriaRepair, Story, StoryProposal
from crew_org.events import EventKind, EventSink
from crew_org.flows import artifacts, removed_tests
from crew_org.flows import record as record_flow
from crew_org.rulebook import says
from crew_org.rules import Rule
from crew_org.session import steps

# What the check is shown of other epics' planned stories. A pass can plan
# dozens; past this, their titles are still in the split's own context.
PLANNED_LIMIT = 30
CRITERIA = "## Acceptance criteria"


def render_split(proposal: StoryProposal) -> str:
    """The proposed stories as the check reads them: titles, criteria, existing tests."""
    return render_stories(proposal.stories)


def render_stories(stories: list[Story]) -> str:
    parts = []
    for story in stories:
        lines = [f"### {story.title}"]
        lines += [
            f"{i}. Given {ac.given} / When {ac.when} / Then {ac.then}"
            for i, ac in enumerate(story.acceptance_criteria, 1)
        ]
        # Which lands first, and which rows bind it (crew#839): without them the check
        # couldn't tell a planned story's change from one already merged.
        if story.builds_on:
            lines.append("Builds on: " + ", ".join(f"#{n}" for n in story.builds_on))
        if story.follows:
            lines.append("Follows: " + ", ".join(story.follows))
        if story.pinned_behaviour:
            lines.append(f"Existing tests: {story.pinned_behaviour}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


@dataclass(frozen=True)
class Earlier:
    """A story as its first check saw it, and what that check found (crew#839)."""

    story: Story
    conflicts: list[CriteriaConflict]


def earlier(stories: list[Story], check: CriteriaCheck) -> dict[str, Earlier]:
    """Each of `stories` with its own conflicts, for its re-check after a repair."""
    return {
        _key(s.title): Earlier(s, [c for c in check.conflicts if _key(c.story) == _key(s.title)])
        for s in stories
    }


def check_each(
    check: Callable[..., CriteriaCheck],
    proposal: StoryProposal,
    *,
    only: list[Story] | None = None,
    before: dict[str, Earlier] | None = None,
    precheck: Callable[[Story], dict[str, list[str]]] | None = None,
    planned_in: dict[int, int] | None = None,
    conclusion: str = "",
    **shown: Any,
) -> CriteriaCheck:
    """The criteria checked one story per session, all at once, every conflict found kept
    (crew#583, D3; the Sponsor, crew#807).

    Checked all at once, the criteria check thought a median of 12.6k tokens to write
    641 characters, at the edge where answers come back empty (the context review,
    C5). Each session is shown one story, with the split's other stories and the planned
    ones to check it against, and the record, and reads the code it needs (crew#807). A
    conflict between two stories can be found from both sides; the repair rewrites both.
    The record is shown as that story reads it (`record_for_story`), and `precheck`'s
    merged tests that use what it removes, undeclared, as facts (crew#839). A conflict
    with another epic's planned stories (`planned_in`: story to epic) is kept once per
    epic: it's settled between the epics, not story by story.

    Each story's check is independent, so they run at once, and the repair waits for
    them all. A check that fails or stops is recorded, and the others' conflicts stand;
    if every one fails, the first failure is raised. One event records each check's
    model time and conflicts found, with the wall time, as the panel's does.

    `only` checks just those stories, each still against all the others: after a
    repair, a story nobody changed can't have a new conflict except with a repaired
    one, and the repaired one's own check sees it. sprint-metrics#551's re-check ran
    all ten stories again, about an hour, for the few the repair touched (crew#612).

    `before` makes it a re-check (crew#839): each story is shown the conflicts its
    first check found and what the repair changed, and asked whether they're resolved.
    sprint-metrics#752's re-check started blank and took 33 minutes over a repair of
    56 seconds. A story the repair left as it was keeps its conflicts with no session.
    The repair can't reach a story it wasn't given: `with_repairs` drops that part.
    """
    titles = {_key(s.title) for s in only} if only is not None else None
    checked = [s for s in proposal.stories if titles is None or _key(s.title) in titles]
    unchanged = [s for s in checked if _unchanged((before or {}).get(_key(s.title)), s)]
    kept = [c for s in unchanged for c in (before or {})[_key(s.title)].conflicts]
    checked = [s for s in checked if s not in unchanged]
    if not checked and before is None:
        return CriteriaCheck()
    measured: dict[str, dict[str, Any]] = {}
    tests = {s.title: precheck(s) if precheck else {} for s in checked}

    def measure(title: str) -> Callable[[Any, Any], None]:
        return lambda session, ending: measured.__setitem__(title, steps.measured(session, ending))

    def one(story: Story) -> CriteriaCheck:
        mine = render_stories([story])
        rest = render_stories([s for s in proposal.stories if s is not story])
        was = (before or {}).get(_key(story.title))
        return check(
            stories=mine,
            siblings=rest,
            conclusion=record_for_story(conclusion, story) if conclusion else "",
            recheck=repaired(was, story) if was else "",
            facts=removed_tests.described(tests[story.title]),
            noted={
                "story": story.title,
                "recheck": was is not None,
                "precheck_tests": len(tests[story.title]),
            },
            on_end=measure(story.title),
            **shown,
        )

    found: dict[int, CriteriaCheck] = {}
    failed: dict[str, BaseException] = {}
    began = time.monotonic()
    if checked:
        with ThreadPoolExecutor(max_workers=len(checked)) as pool:
            # Each thread runs in its own copy of this context, so its calls are attributed
            # to the tick's span and card.
            futures = [pool.submit(contextvars.copy_context().run, one, story) for story in checked]
            for i, (story, future) in enumerate(zip(checked, futures, strict=True)):
                try:
                    found[i] = future.result()
                except Exception as exc:  # noqa: BLE001 - a failed check is recorded, not a crash
                    failed[story.title] = exc
    # Each story's conflicts, counted: since the check became a session (crew#807) it
    # flagged 1 conflict in 33 checks, and nothing showed whether it still fires (crew#860).
    # Whether it was a re-check, and what the pre-check found, so a retro can compare
    # times (crew#839).
    steps.at_once(
        "criteria check",
        measured,
        {t: f"{type(e).__name__}: {e}"[:300] for t, e in failed.items()},
        time.monotonic() - began,
        conflicts={checked[i].title: len(found[i].conflicts) for i in sorted(found)},
        recheck=before is not None,
        unchanged={s.title: len(before[_key(s.title)].conflicts) for s in unchanged}
        if before
        else {},
        precheck_tests={t: len(v) for t, v in tests.items()},
    )
    if failed and not found and not kept:
        raise next(iter(failed.values()))
    conflicts = kept + [c for i in sorted(found) for c in found[i].conflicts]
    return CriteriaCheck(conflicts=_per_epic(_once(conflicts), planned_in or {}))


def _unchanged(was: Earlier | None, story: Story) -> bool:
    return (
        was is not None
        and was.story.acceptance_criteria == story.acceptance_criteria
        and was.story.pinned_behaviour == story.pinned_behaviour
    )


def repaired(was: Earlier, story: Story) -> str:
    """What a re-check is told: the conflicts its first check found, and what changed."""
    old, new = was.story.acceptance_criteria, story.acceptance_criteria
    changes = []
    for i in range(max(len(old), len(new))):
        a = _criterion(old[i]) if i < len(old) else ""
        b = _criterion(new[i]) if i < len(new) else ""
        if a == b:
            continue
        changes.append(
            f"- Criterion {i + 1}, "
            + (f"before: {a}\n  after: {b}" if a and b else f"added: {b}" if b else f"removed: {a}")
        )
    if was.story.pinned_behaviour != story.pinned_behaviour:
        changes.append(
            f"- Existing tests, before: {was.story.pinned_behaviour or '(none)'}\n"
            f"  after: {story.pinned_behaviour or '(none)'}"
        )
    found = described(CriteriaCheck(conflicts=was.conflicts)) or "- (no conflict of its own)"
    return (
        "## This is a re-check, after the Business Analyst's repair\n\n"
        f"Your first check of this story found:\n\n{found}\n\n"
        "The repair changed:\n\n" + "\n".join(changes) + "\n\n"
        "Your question now: is each conflict above resolved, and did the repair make a new "
        "conflict with the other stories, the planned ones, the code or the record? The "
        "rest of the story is as you checked it."
    )


def _criterion(ac: Any) -> str:
    return f"Given {ac.given} / When {ac.when} / Then {ac.then}"


# A test function, not its file: `tests/test_service.py :: test_x` names test_x.
_TEST_NAME = re.compile(r"\btest_\w+\b(?!\.py)")


def _once(conflicts: list[CriteriaConflict]) -> list[CriteriaConflict]:
    """Each story's conflict with one merged test once, however often it was found.

    Checked one story per call, the same test was named against the same story from
    several calls: sprint-metrics#551's refusal listed 14 conflicts for 4 tests.
    """
    seen: set[tuple[str, str]] = set()
    kept = []
    for c in conflicts:
        test = _TEST_NAME.search(c.against)
        key = (_key(c.story), test.group(0) if test else c.criterion.strip())
        if key not in seen:
            seen.add(key)
            kept.append(c)
    return kept


def planned_criteria(
    issues: Any,
    repo: str,
    planned: list[tuple[int, str, int]],
    *,
    cards: Sequence[Any] = (),
    goal: int | None = None,
    epic: int | None = None,
) -> str:
    """Other epics' planned stories in this repository, under their epic, criteria whole.

    Each story says its board status, and each epic its own and whether it's in this
    Goal (crew#839): sprint-metrics#752's check spent its longest turns weighing
    stories it couldn't tell were still live. Cut at 2,000 characters, a criterion
    reached it as "(truncated)". `epic`'s own are the stories its earlier split kept.
    """
    on_board = {c.number: c for c in cards if c.number}
    groups: dict[int, list[str]] = {}
    for number, title, parent in planned[:PLANNED_LIMIT]:
        try:
            body = issues.get(repo, number).get("body") or ""
        except Exception:  # noqa: BLE001
            continue
        start = body.find(CRITERIA)
        if start == -1:
            continue
        section = body[start + len(CRITERIA) :].split("\n**Estimate**", 1)[0].strip()
        card = on_board.get(number)
        status = card.status if card else "split this pass, not on the board yet"
        groups.setdefault(parent, []).append(f"#### #{number} {title}: {status}\n\n{section}")
    return "\n\n".join(
        f"### {_epic_heading(parent, on_board.get(parent), goal, epic)}\n\n" + "\n\n".join(found)
        for parent, found in groups.items()
    )


def _epic_heading(number: int, card: Any, goal: int | None, epic: int | None) -> str:
    if number == epic:
        return f"This epic, #{number}: the stories its earlier split kept"
    if card is None:
        return f"Epic #{number}"
    where = (
        "in this Goal"
        if goal is not None and card.parent == goal
        else f"Goal #{card.parent}"
        if card.parent
        else "no Goal"
    )
    return f"Epic #{number} {card.title}: {card.status}, {where}"


def record_for_story(conclusion: str, story: Story) -> str:
    """The record as one story's check reads it: the rows it follows whole, the rest a line.

    sprint-metrics#752's start carried the Goal's whole record, 15.6k characters, a
    third of it rows restating others (crew#839). A row the story follows is shown in
    full, with any row that replaced it; every other row is one line, its context and
    its decision's first sentence. The record's other tables stay as they are.
    """
    record = record_flow.parse(conclusion)
    if not record.decisions:
        return conclusion
    full = record_flow.rows_for(conclusion, story.follows)
    shown = {m.group(1) for m in _ROW.finditer(full)}
    rest = [
        f"- {d.id}"
        + ("" if d.binding else f" ({d.status})")
        + f", {_first_sentence(d.context, 80)}: {_first_sentence(d.decision, 240)}"
        for d in record.decisions
        if d.id not in shown
    ]
    # The rest of the record, its decisions table taken out.
    lines = conclusion.splitlines()
    rows = [i for i, ln in enumerate(lines) if _ROW.match(ln)]
    if rows:
        lines = lines[: max(rows[0] - 2, 0)] + lines[rows[-1] + 1 :]
    parts = ["\n".join(lines).strip()]
    if full:
        parts.append(f"The rows this story follows, in full:\n\n{full}")
    if rest:
        parts.append("The other rows, one line each:\n\n" + "\n".join(rest))
    return "\n\n".join(p for p in parts if p)


# A row of a record's decisions table: `| R4 | binding | ...`.
_ROW = re.compile(r"^\| (R\d+) \|", re.M)


def _first_sentence(text: str, limit: int) -> str:
    one = " ".join(text.split())
    end = re.search(r"(?<=[.!?])\s", one)
    one = one[: end.start()] if end else one
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _per_epic(
    conflicts: list[CriteriaConflict], planned_in: dict[int, int]
) -> list[CriteriaConflict]:
    """A conflict with another epic's planned stories once for that epic, naming the
    other stories it was found for (crew#839)."""
    first: dict[int, int] = {}
    also: dict[int, list[str]] = {}
    kept: list[CriteriaConflict] = []
    for c in conflicts:
        epics = {
            planned_in[int(n)] for n in re.findall(r"#(\d+)", c.against) if int(n) in planned_in
        }
        epic = min(epics) if epics else None
        if epic is None:
            kept.append(c)
        elif epic in first:
            if _key(c.story) != _key(kept[first[epic]].story):
                also[epic].append(c.story)
        else:
            first[epic], also[epic] = len(kept), []
            kept.append(c)
    for epic, at in first.items():
        if also[epic]:
            names = ", ".join(dict.fromkeys(also[epic]))
            c = kept[at]
            kept[at] = c.model_copy(
                update={"why": f"{c.why} The same clash with epic #{epic} was found for: {names}."}
            )
    return kept


def described(check: CriteriaCheck) -> str:
    """The conflicts, one per line, for the re-split and for the epic."""
    return "\n".join(
        f'- *{c.story}*: "{c.criterion}" can\'t pass alongside {c.against}. {c.why}'
        for c in check.conflicts
    )


def _key(title: str) -> str:
    return title.strip().strip("*\"'“”‘’ ").casefold()


def flagged(proposal: StoryProposal, check: CriteriaCheck) -> list[Story] | None:
    """The proposal's stories the conflicts name, once each, in the proposal's order.

    None when a conflict names a story the proposal doesn't have: then it can't be
    repaired in place, and the epic is split again whole.
    """
    names = {_key(c.story) for c in check.conflicts}
    found = [s for s in proposal.stories if _key(s.title) in names]
    if names - {_key(s.title) for s in found}:
        return None
    return found


def with_repairs(
    proposal: StoryProposal, named: list[Story], repair: CriteriaRepair
) -> StoryProposal:
    """The proposal with the named stories' criteria replaced by their repair.

    Only the criteria and the Existing tests line change, and only for stories
    named: titles, order, rows followed and dependencies were checked already and
    stay. A conflict with a merged test the epic means to change is fixed by
    declaring the test, not by rewriting the criterion (crew#612). A named story the
    repair left out keeps what it had, and the check refuses it again.
    """
    repaired = {_key(r.title): r for r in repair.stories}
    allowed = {_key(s.title) for s in named}

    def mended(story: Story) -> Story:
        fix = repaired[_key(story.title)]
        update: dict[str, Any] = {"acceptance_criteria": fix.acceptance_criteria}
        if fix.pinned_behaviour is not None:
            update["pinned_behaviour"] = fix.pinned_behaviour
        return story.model_copy(update=update)

    stories = [
        mended(s) if _key(s.title) in allowed and _key(s.title) in repaired else s
        for s in proposal.stories
    ]
    return proposal.model_copy(update={"stories": stories})


def feedback(check: CriteriaCheck) -> str:
    """What the Business Analyst is told when its split is sent back once."""
    return says("check.criteria_check.feedback", conflicts=described(check))


def to_product_owner(
    issues: Any,
    sink: EventSink,
    *,
    repo: str,
    number: int,
    check: CriteriaCheck,
    marker: str,
    rework: str,
    replacing: Sequence[int] = (),
) -> None:
    """The conflicts on the epic as a story problem, for the Product Owner to settle.

    A re-split names the stories it was replacing, so the next round replaces the same
    ones (crew#859).
    """
    with contextlib.suppress(Exception):
        issues.ensure_label(
            repo,
            rework,
            color="d4c5f9",
            description="Split this epic again: see the latest comment",
        )
    artifacts.label(issues, sink, repo=repo, number=number, by="QA Engineer", add=[rework])
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=number,
        body=f"{marker}\n"
        "**Not split: its stories' criteria can't all pass, even after a second split.**\n\n"
        f"{described(check)}\n\n"
        "Settle which outcome holds before this epic is split again. No story was created."
        + (
            " It was replacing "
            + ", ".join(f"#{n}" for n in replacing)
            + ", which stay open until it's split again."
            if replacing
            else ""
        ),
        by="QA Engineer",
    )
    sink.note(
        EventKind.NOTE,
        f"#{number} not split: {len(check.conflicts)} criteria can't all pass",
        card=number,
        rule=Rule.CRITERIA_CANNOT_ALL_PASS,
    )
    sendbacks.record(
        sink,
        sendbacks.CRITERIA_TO_PRODUCT_OWNER,
        card=number,
        repo=repo,
        role="QA Engineer",
        conflicts=len(check.conflicts),
    )


# An epic in the Product Owner's proposal comment: `### 2. Shrink the service — #752`.
_PROPOSED = re.compile(r"^### \d+\. (.+?) — #(\d+)\s*$", re.M)
_ORDER_WORDS = re.compile(
    r"\b(?:lands?|landed|ships?|merges?) (?:after|before|first|last)\b|\blanding order\b"
    r"|\bin (?:this|that|the) order\b",
    re.I,
)


def goal_order(
    proposal: str, cards: Sequence[Any], *, goal: int, epic: int, conclusion: str = ""
) -> str:
    """The Goal's epics in order, each with its status, and the record's rows on order.

    A story's Given can rest on another epic's stories having landed; without the
    order the check couldn't tell which lands first (crew#839). The proposal gives the
    first order and the Sponsor can change it: a binding row that orders two or more
    of the Goal's epics is shown whole, and holds over the proposal.
    """
    on_board = {c.number: c for c in cards if c.number}
    proposed = [(int(n), title) for title, n in _PROPOSED.findall(proposal)]
    known = {n for n, _t in proposed}
    proposed += sorted(
        (c.number, c.title)
        for c in cards
        if c.parent == goal and c.work_type == "Epic" and c.number not in known
    )
    if not proposed:
        return ""
    lines = []
    for i, (number, title) in enumerate(proposed, 1):
        card = on_board.get(number)
        status = "this epic" if number == epic else card.status if card else "not on the board"
        lines.append(f"{i}. #{number} {title}: {status}")
    numbers = {str(n) for n, _t in proposed}
    rows = (
        [
            d
            for d in record_flow.parse(conclusion).binding()
            if len(numbers & set(re.findall(r"#(\d+)", f"{d.context} {d.decision}"))) >= 2
            and _ORDER_WORDS.search(d.decision)
        ]
        if conclusion
        else []
    )
    text = "## The Goal's epics, in the order proposed\n\n" + "\n".join(lines)
    if rows:
        text += (
            "\n\nThe record's rows on their order, which hold over the proposal's:\n\n"
            + "\n".join(f"- {d.id}: {' '.join(d.decision.split())}" for d in rows)
        )
    return text
