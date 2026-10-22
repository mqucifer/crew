"""A story whose failures show a story problem goes back to refinement by itself (#189).

sprint-metrics#97 asked for threshold flags on the default table without saying
whether they were opt-in or a change to the table's contract. Every attempt
broke the same ~25 merged tests pinning the table's rows, the repairs aimed at
the wrong target, and it blocked. Unblocking it took the Sponsor three manual
steps. The failure was the story's, not the code's, and no number of attempts,
nor escalation, would have fixed it.

A story goes back to be split again, with the evidence on its epic, when the
gates keep returning it, when its review asks for what a criterion forbids, when
the Business Analyst rules against the merged tests it changed, or when its
Developer session ends blocked on what the story doesn't give (crew#778).
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.columns import READY
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER, started
from crew_org.flows.moves import move_card
from crew_org.tools.github_project import Card
from crew_org.work_types import TASK


def return_to_refinement(
    board: Any,
    issues: Any,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    cards: list[Card],
    comment: str,
    reason: str,
    **detail: Any,
) -> bool:
    """Send the story's epic back to be split again. False if it has no epic.

    `reason` is why, for the event, from `rules.RETURN_RULES`: "gate round trips"
    (the gates kept returning it, #242), "review conflicts with a criterion",
    "contract ruling", or "session blocked" (crew#778). `detail` goes on the event
    with it: which kind of `blocked`, or which stop.

    The story and its unbuilt later siblings go back to Ready, out of their sprint,
    so they stop counting against capacity: they build on it, so the re-split replaces
    them (crew#859). Earlier siblings stay in their sprint. The evidence goes on the
    epic with `needs:rework`.
    """
    epic = card.parent
    # A role's Task isn't split from its epic, so splitting the epic again can't
    # mend it: it goes to a person, as a story with no epic does (crew#839).
    if epic is None or card.work_type == TASK:
        return False
    try:
        children = {c["number"] for c in issues.sub_issues(repo, epic)}
    except Exception:  # noqa: BLE001
        children = set()
    siblings = [
        c
        for c in cards
        if (c.repo or repo) == repo
        and c.number in children
        and c.number != card.number
        and c.state != "CLOSED"
        and c.work_type != TASK
    ]
    busy = started(issues, repo, siblings)
    back = [card] + [
        c
        for c in siblings
        if c.number not in busy and c.status != READY and (c.number or 0) > (card.number or 0)
    ]
    for story in back:
        move_card(
            board,
            sink,
            item_id=story.item_id,
            to=READY,
            by="Developer",
            card=story.number,
            frm=story.status,
            summary=f"back to refinement with epic #{epic}",
        )
        try:
            board.clear_field(story.item_id, "Sprint")
        except Exception as exc:  # noqa: BLE001
            sink.note(EventKind.NOTE, f"#{story.number} sprint not cleared: {exc}"[:120])
    issues.ensure_label(
        repo,
        NEEDS_REWORK,
        color="d4c5f9",
        description="Split this epic again: see the latest comment",
    )
    artifacts.label(issues, sink, repo=repo, number=epic, by="Developer", add=[NEEDS_REWORK])
    artifacts.comment(issues, sink, repo=repo, number=epic, body=comment, by="Developer")
    record_pinned(issues, sink, repo=repo, epic=epic, card=card, comment=comment)
    sink.emit(
        CrewEvent(
            kind=EventKind.STORY_RETURNED,
            role="Developer",
            card=card.number,
            summary=f"#{card.number} back to refinement ({reason}); epic #{epic} to split again"[
                :120
            ],
            detail={
                "repo": repo,
                "epic": epic,
                "reason": reason,
                "with": [s.number for s in back if s.number != card.number],
                **detail,
            },
        )
    )
    return True


# One pinned test as `evidence` lists it.
_LISTED = re.compile(r"^- `([^`\s]+\.py::\w+)`: (.*)$", re.MULTILINE)


def record_pinned(
    issues: Any, sink: EventSink, *, repo: str, epic: int, card: Card, comment: str
) -> None:
    """The merged tests a story kept breaking, as rows of its epic's record (step A4).

    The re-split is shown them and has to carry each into a story or drop it with
    why: sprint-metrics#529's re-splits each forgot the tests the last one named.
    Only an epic with a record; a failure costs the rows, not the return.
    """
    from crew_org.flows import record  # noqa: PLC0415
    from crew_org.llm import reraise_if_down  # noqa: PLC0415

    listed = _LISTED.findall(comment)
    if not listed:
        return
    try:
        if not record.text_for(issues, repo, epic):
            return

        def change(current: record.Record) -> record.Record:
            for test, why in listed:
                current = current.declare(
                    test,
                    f"broken by the story as written: {why}",
                    f"the story problem on {issues.owner}/{repo}#{card.number}",
                )
            return current

        record.edit(
            issues,
            sink,
            repo=repo,
            epic=epic,
            by="Developer",
            change=change,
            card=card.number,
            kind="tests",
            why="The merged tests this story kept breaking. The re-split carries or drops each.",
        )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        sink.note(EventKind.NOTE, f"#{epic}: the pinned tests weren't recorded: {exc}"[:160])


# A story delivered this many times and returned again goes back to refinement
# instead of being reworked once more (#242).
MAX_ROUND_TRIPS = 3
DELIVERED = "Implemented in #"


def round_trips(issues: Any, repo: str, number: int) -> int:
    """How many times the Developer has delivered this story: its own comments say."""
    try:
        comments = issues.comments(repo, number)
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for c in comments if (c.get("body") or "").startswith(DELIVERED))


def round_trip_evidence(issues: Any, repo: str, card: Card, pull: int | None, rounds: int) -> str:
    """Both gates' latest word, for the epic: where they disagree is the question."""
    qa = ""
    try:
        verdicts = [
            c.get("body") or ""
            for c in issues.comments(repo, card.number or 0)
            if "## QA — not accepted" in (c.get("body") or "")
        ]
        if verdicts:
            qa = "\n".join(line for line in verdicts[-1].splitlines() if "not proven" in line)
    except Exception:  # noqa: BLE001
        pass
    review = ""
    if pull is not None:
        try:
            asked = [
                r.get("body") or ""
                for r in issues.pull_reviews(repo, pull)
                if r.get("state") == "CHANGES_REQUESTED"
            ]
            if asked:
                body = asked[-1]
                review = body[body.find("## Findings") :] if "## Findings" in body else body
                review = review.split("<!-- crew:by")[0].strip()
        except Exception:  # noqa: BLE001
            pass
    return (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{card.number} went back to refinement: the gates kept returning it.**\n\n"
        f"*{card.title}* was delivered {rounds} times and returned again. When QA and "
        "the Code Reviewer keep sending it back, repairing it won't settle it: the "
        "story, its criteria and the epic's design note don't agree.\n\n"
        f"**QA's latest:**\n\n{qa or '(no QA return recorded)'}\n\n"
        f"**The Code Reviewer's latest:**\n\n{review or '(no changes requested)'}\n\n"
        "Settle which holds before this epic is split again."
    )
