"""Design notes for epics that need one, before their stories are built (#155).

An epic labelled `needs:design` gets the Architect's note as a comment on the
epic. The Code Reviewer checks it against the crew-wide guidelines and the
project's own, as it checks a project's design (#144); the Architect doesn't
grade itself. A conflict gets one retry, then the epic is blocked for a person
with the guideline named.

Until the note exists, planning holds the epic's stories back. Once it does,
the Developer building one of them is shown it, and so is the Code Reviewer
judging the diff.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crew_org.events import EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.board_flow import NEEDS_DESIGN
from crew_org.llm import reraise_if_down
from crew_org.project import ProjectRecordError, brief, read_record
from crew_org.tools.github_project import Card

NOTE_MARKER = "<!-- crew:design-note -->"
ATTEMPTS = 2
BY = "Architect"

EPIC_TYPE = "Epic"


@dataclass
class DesignNotes:
    written: list[int] = field(default_factory=list)
    blocked: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)


def needs_note(card: Card) -> bool:
    """An open epic labelled `needs:design`. It wins over `no:design` (§13)."""
    return card.work_type == EPIC_TYPE and card.state != "CLOSED" and NEEDS_DESIGN in card.labels


def note_for(issues: Any, repo: str, epic: int) -> str:
    """The epic's design note, as posted, or '' if it has none."""
    try:
        comments = issues.comments(repo, epic)
    except Exception:  # noqa: BLE001
        return ""
    notes = [c.get("body") or "" for c in comments if NOTE_MARKER in (c.get("body") or "")]
    return notes[-1] if notes else ""


def awaiting_design(issues: Any, cards: list[Card], default_repo: str) -> set[tuple[str, int]]:
    """The epics whose stories wait: labelled `needs:design`, with no note yet."""
    return {
        epic.key
        for epic in cards
        if needs_note(epic) and not note_for(issues, epic.repo or default_repo, epic.number or 0)
    }


def story_note(issues: Any, card: Card, default_repo: str) -> str:
    """The design note of the epic this story belongs to, for delivery and review."""
    if card.parent is None:
        return ""
    return note_for(issues, card.repo or default_repo, card.parent)


def write_notes(
    issues: Any,
    sink: EventSink,
    ws: Any,
    cards: list[Card],
    *,
    default_repo: str,
    repos: set[str],
    write: Callable[..., Any],
    review: Callable[..., Any],
    render: Callable[[Any], str],
) -> DesignNotes:
    """Write the note for every epic that needs one and has its stories split."""
    from crew_org.tools.repo_context import repository_context  # noqa: PLC0415

    result = DesignNotes()
    for epic in cards:
        repo = epic.repo or default_repo
        number = epic.number or 0
        if repo not in repos or not needs_note(epic) or note_for(issues, repo, number):
            continue
        stories = issues.sub_issues(repo, number)
        if not stories:
            continue  # nothing split yet: the note is written against the stories
        try:
            clone = ws.for_repo(repo).current()
            try:
                record = read_record(clone)
            except ProjectRecordError:
                record = None
            project = brief(record) if record else ""
            epic_text = f"#{number} {epic.title}\n\n{issues.get(repo, number).get('body') or ''}"
            story_text = "\n\n".join(
                f"### #{s['number']} {s['title']}\n\n{s.get('body') or ''}"
                for s in sorted(stories, key=lambda s: s["number"])
            )
            outcome = _write_one(
                write=write,
                review=review,
                render=render,
                epic=epic_text,
                stories=story_text,
                project=project,
                repository=repository_context(clone, editing=False),
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((number, f"{type(exc).__name__}: {exc}"[:200]))
            sink.note(EventKind.NOTE, f"#{number} design note failed: {exc}"[:120])
            continue

        if isinstance(outcome, str):
            artifacts.comment(
                issues, sink, repo=repo, number=number, body=f"{NOTE_MARKER}\n{outcome}", by=BY
            )
            result.written.append(number)
            continue
        why = outcome[0]
        artifacts.label(
            issues, sink, repo=repo, number=number, by=BY, add=["blocked", "needs:human"]
        )
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=number,
            body=f"**No design note: this needs a person.** {why}\n\n"
            "The epic's stories wait until it has one.",
            by=BY,
        )
        result.blocked.append((number, why))
    return result


def _write_one(*, write, review, render, epic, stories, project, repository):
    """The rendered note, or (why it couldn't be written,) for a person."""
    feedback = ""
    reasons: list[str] = []
    for _attempt in range(ATTEMPTS):
        note = write(
            epic=epic, stories=stories, project=project, repository=repository, feedback=feedback
        )
        if note.beyond_reach:
            return (f"The Architect could not resolve: {note.beyond_reach}",)
        shown = render(note)
        checked = review(project=project, design=shown)
        reasons = [f"{c.choice} contradicts {c.guideline}: {c.why}" for c in checked.conflicts]
        if not reasons:
            return shown
        feedback = "\n".join(f"- {r}" for r in reasons)
    return ("It contradicts a guideline after a retry: " + "; ".join(reasons),)
