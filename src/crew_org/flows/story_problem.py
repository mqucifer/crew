"""A story whose failures show a story problem goes back to refinement by itself (#189).

sprint-metrics#97 asked for threshold flags on the default table without saying
whether they were opt-in or a change to the table's contract. Every attempt
broke the same ~25 merged tests pinning the table's rows, the repairs aimed at
the wrong target, and it blocked. Unblocking it took the Sponsor three manual
steps. The failure was the story's, not the code's, and no number of attempts,
nor escalation, would have fixed it.

The signal is mechanical: the same merged tests, ones this story didn't write,
failing on consecutive attempts. The story is changing behaviour other work
pinned, and the story doesn't say whether it should. It goes back to be split
again, with the evidence on its epic.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.columns import READY
from crew_org.events import EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER, started
from crew_org.flows.moves import move_card
from crew_org.tools.github_project import Card

_FAILED = re.compile(r"^FAILED ([^\s:]+\.py)::(\w+)(?:\[[^\]]*\])?(?: - (.*))?$", re.MULTILINE)
# How many of the failing tests the evidence lists: the pattern shows well
# before 25, and the comment is read by a person as well as the crew.
SHOWN = 8


def failing_tests(report: str) -> dict[str, str]:
    """`path::test` -> its one-line failure, from pytest's short summary."""
    found: dict[str, str] = {}
    for path, test, why in _FAILED.findall(report or ""):
        found.setdefault(f"{path}::{test}", (why or "").strip()[:200])
    return found


def pinned_failures(report: str, merged: Any, implementation: Any) -> dict[str, str]:
    """The failing tests that are merged work, not this story's own.

    Merged: its file on the default branch defines it. Not this story's: the
    story didn't write or edit it. What's left is behaviour other stories
    pinned, which this one is changing.
    """
    if merged is None:
        return {}
    own = {(e.path, e.target) for e in getattr(implementation, "all_edits", [])}
    texts: dict[str, str | None] = {}
    pinned: dict[str, str] = {}
    for key, why in failing_tests(report).items():
        path, _, test = key.partition("::")
        if (path, test) in own:
            continue
        if path not in texts:
            texts[path] = merged.text(path)
        text = texts[path]
        if text and re.search(rf"^\s*(?:async\s+)?def {re.escape(test)}\b", text, re.MULTILINE):
            pinned[key] = why
    return pinned


def evidence(card: Card, pinned: dict[str, str], attempts: int) -> str:
    """The comment on the epic: what the story asked, and what it kept breaking."""
    shown = sorted(pinned.items())[:SHOWN]
    more = len(pinned) - len(shown)
    return (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{card.number} went back to refinement: its failures are the story's, "
        "not the code's.**\n\n"
        f"*{card.title}* failed {attempts} attempts in a row by breaking the same "
        f"{len(pinned)} merged tests, which it didn't write. It changes behaviour "
        "other stories pinned, and it doesn't say whether it should:\n\n"
        + "\n".join(f"- `{key}`: {why or 'failed'}" for key, why in shown)
        + (f"\n- … and {more} more" if more > 0 else "")
        + "\n\nWhen this epic is split again, each story that touches this behaviour "
        "must say whether the change is opt-in or an expected contract change that "
        "updates those tests."
    )


def return_to_refinement(
    board: Any,
    issues: Any,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    cards: list[Card],
    comment: str,
) -> bool:
    """Send the story's epic back to be split again. False if it has no epic.

    The story and its unbuilt siblings go back to Ready, out of their sprint,
    so the rework supersedes them and they stop counting against capacity. The
    evidence goes on the epic with `needs:rework`.
    """
    epic = card.parent
    if epic is None:
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
    ]
    busy = started(issues, repo, siblings)
    back = [card] + [c for c in siblings if c.number not in busy and c.status != READY]
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
    return True
