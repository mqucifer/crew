"""The Product Owner orders a repository's backlog when new work arrives (#358).

"Builds on" was written once, when an epic was split, and could only name work
that existed then. Work found later (a design revision's technical epics, the
post-merge watcher's, the release check's) never looked back at the queue: on
2026-09-29 sprint-metrics#293 (cut 1.0.0) and #297 obviously depended on the
image work #303-#306, nothing noticed, and the Sponsor added both holds by hand.
And nothing ordered the epics: planning took them by number.

So when an open epic in a repository has no Rank, the Product Owner is asked,
once, to order all of that repository's open epics and name any unstarted story
that must now wait for the new work. It's shown one line per item, never bodies,
code or criteria (the Sponsor: don't bloat the context). Code checks the answer
and does the writing: Rank on the board, `**Builds on** —` lines on stories.
Holds are only ever added here, never removed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crew_org.columns import DONE, READY, SPRINT_BACKLOG
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows.board_flow import BUILDS_ON, EPIC_TYPE, STORY_TYPE, builds_on
from crew_org.llm import reraise_if_down
from crew_org.tools.github_project import Card

ATTEMPTS = 2
# Past this many unstarted stories the list is cut, and the cut is said (#358).
MAX_STORIES = 40
UNSTARTED = (READY, SPRINT_BACKLOG)


@dataclass
class BacklogOrders:
    # (repo, epic numbers in order)
    ordered: list[tuple[str, list[int]]] = field(default_factory=list)
    # (repo, story, the numbers it now waits for, why)
    holds: list[tuple[str, int, list[int], str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def open_epics(cards: list[Card], repo: str) -> list[Card]:
    return [
        c
        for c in cards
        if c.work_type == EPIC_TYPE and c.repo == repo and c.state != "CLOSED" and c.status != DONE
    ]


def needs_ordering(cards: list[Card], repo: str) -> list[Card]:
    """The open epics with no Rank: the trigger, and the new work to describe."""
    return [c for c in open_epics(cards, repo) if c.rank is None]


def with_holds(body: str, numbers: set[int]) -> str:
    """The story's body with `numbers` added to its Builds on line, never removed."""
    have = builds_on(body)
    wanted = sorted(have | numbers)
    if set(wanted) == have:
        return body
    line = f"{BUILDS_ON} " + ", ".join(f"#{n}" for n in wanted)
    lines = (body or "").splitlines()
    for i, existing in enumerate(lines):
        if existing.startswith(BUILDS_ON):
            lines[i] = line
            return "\n".join(lines)
    at = next((i for i, ln in enumerate(lines) if ln.startswith("**Estimate**")), len(lines))
    return "\n".join([*lines[:at], line, "", *lines[at:]])


def _first_paragraph(text: str) -> str:
    return next((p.strip() for p in (text or "").split("\n\n") if p.strip()), "")[:400]


def order_backlogs(
    board: Any,
    issues: Any,
    sink: EventSink,
    cards: list[Card],
    *,
    repos: set[str],
    order: Callable[..., Any],
) -> BacklogOrders:
    """Order each repository whose open epics include one with no Rank."""
    result = BacklogOrders()
    for repo in sorted(repos):
        unranked = needs_ordering(cards, repo)
        if not unranked:
            continue
        epics = open_epics(cards, repo)
        stories = [
            c
            for c in cards
            if c.work_type == STORY_TYPE
            and c.repo == repo
            and c.state != "CLOSED"
            and c.status in UNSTARTED
        ]
        bodies = {}
        for s in stories[:MAX_STORIES]:
            try:
                bodies[s.number] = issues.get(repo, s.number or 0).get("body") or ""
            except Exception:  # noqa: BLE001
                bodies[s.number] = ""
        lines = [
            f"- #{e.number} epic, {e.status}"
            + (f", rank {int(e.rank)}" if e.rank is not None else ", not yet ranked")
            + f": {e.title}"
            + (f" (Goal #{e.parent})" if e.parent else "")
            + (" [technical]" if "technical" in e.labels else "")
            for e in epics
        ]
        lines += [
            f"- #{s.number} story of #{s.parent}, {s.status}: {s.title}"
            + (
                f" (builds on {', '.join(f'#{n}' for n in sorted(builds_on(bodies[s.number])))})"
                if builds_on(bodies.get(s.number, ""))
                else ""
            )
            for s in stories[:MAX_STORIES]
        ]
        if len(stories) > MAX_STORIES:
            lines.append(f"- … and {len(stories) - MAX_STORIES} more unstarted stories, not shown")
        backlog = "\n".join(lines)
        new = "\n\n".join(
            f"#{e.number} {e.title}\n{_first_paragraph(_body(issues, repo, e.number or 0))}"
            for e in unranked
        )
        sink.note(EventKind.NOTE, f"{repo} backlog order: {len(backlog):,} chars of backlog")
        try:
            answer = attributed(_order_one, repo=repo)(
                order=order,
                new=new,
                backlog=backlog,
                epics={e.number for e in epics},
                stories={s.number for s in stories},
                known={e.number for e in epics} | {s.number for s in stories},
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((repo, f"{type(exc).__name__}: {exc}"[:200]))
            continue
        if isinstance(answer, str):
            result.failed.append((repo, answer))
            sink.note(EventKind.NOTE, f"{repo} backlog not ordered: {answer}"[:160])
            continue

        by_number = {e.number: e for e in epics}
        for rank, number in enumerate(answer.order, 1):
            epic = by_number[number]
            if epic.rank != rank:
                board.set_number(epic.item_id, "Rank", float(rank))
        result.ordered.append((repo, list(answer.order)))
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                role="Product Owner",
                summary=f"{repo}: ordered the backlog: " + ", ".join(f"#{n}" for n in answer.order),
                detail={"repo": repo, "order": list(answer.order)},
            )
        )
        for hold in answer.holds:
            body = _body(issues, repo, hold.story)
            updated = with_holds(body, set(hold.builds_on))
            if updated != body:
                issues.edit_issue(repo, hold.story, body=updated)
                result.holds.append((repo, hold.story, sorted(hold.builds_on), hold.why))
                sink.emit(
                    CrewEvent(
                        kind=EventKind.NOTE,
                        role="Product Owner",
                        card=hold.story,
                        summary=(
                            f"#{hold.story} now builds on "
                            + ", ".join(f"#{n}" for n in sorted(hold.builds_on))
                            + f": {hold.why}"
                        )[:120],
                        detail={"repo": repo, "builds_on": sorted(hold.builds_on)},
                    )
                )
    return result


def _body(issues: Any, repo: str, number: int) -> str:
    try:
        return issues.get(repo, number).get("body") or ""
    except Exception:  # noqa: BLE001
        return ""


def _order_one(*, order, new, backlog, epics, stories, known):
    """A checked answer, or why there isn't one after a retry."""
    feedback = ""
    problems: list[str] = []
    for _attempt in range(ATTEMPTS):
        answer = order(new=new, backlog=backlog, feedback=feedback)
        problems = check(answer, epics=epics, stories=stories, known=known)
        if not problems:
            return answer
        feedback = "\n".join(f"- {p}" for p in problems)
    return "the Product Owner's order didn't hold after a retry: " + "; ".join(problems)


def check(answer: Any, *, epics: set, stories: set, known: set) -> list[str]:
    """Every open epic exactly once; holds only on listed stories, naming listed work."""
    given = list(answer.order)
    problems = [f"#{n} isn't ranked" for n in sorted(epics - set(given))]
    problems += [f"#{n} isn't one of the open epics" for n in sorted(set(given) - epics)]
    problems += [f"#{n} is ranked twice" for n in sorted({n for n in given if given.count(n) > 1})]
    for hold in answer.holds:
        if hold.story not in stories:
            problems.append(f"#{hold.story} isn't an unstarted story, so it can't be held")
        for n in hold.builds_on:
            if n not in known or n == hold.story:
                problems.append(f"#{hold.story} can't wait for #{n}: it isn't open work listed")
    return problems
