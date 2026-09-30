"""Presentation notes for epics that change what a reader sees (#377).

An epic labelled `needs:ux` gets the UX Designer's note as a comment on the
epic, once its stories are split: who reads the output, a sample of it, and
for each story criteria about what the reader sees. Those criteria are added
to the story's own "Acceptance criteria", in the Business Analyst's form, so
the Developer builds to them, the Code Reviewer reads them, and QA proves them
with tests, all through the paths that already read a story's criteria.

Until the note exists, planning holds the epic's stories back, as it does for
a design note (#155). A note that can't cover every story after a retry blocks
the epic for a person, with the reason.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import artifacts
from crew_org.flows.board_flow import BUILDS_ON, EXISTING_TESTS, NEEDS_UX, STORY_SPLIT_MARKER
from crew_org.llm import reraise_if_down
from crew_org.project import ProjectRecordError, brief, read_record
from crew_org.tools.github_project import Card

NOTE_MARKER = "<!-- crew:presentation-note -->"
# In a story's body, where the UX Designer's criteria begin. Read back so a
# story is never given them twice.
CRITERIA_MARKER = "<!-- crew:ux-criteria -->"
ATTEMPTS = 2
BY = "UX Designer"
EPIC_TYPE = "Epic"

_CRITERION = re.compile(r"^\d+\. \*\*Given\*\*", re.M)


@dataclass
class PresentationNotes:
    written: list[int] = field(default_factory=list)
    blocked: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)


def needs_note(card: Card) -> bool:
    """An open epic labelled `needs:ux`."""
    return card.work_type == EPIC_TYPE and card.state != "CLOSED" and NEEDS_UX in card.labels


def note_for(issues: Any, repo: str, epic: int) -> str:
    """The epic's presentation note for its current stories, or '' if it has none.

    Only a note since the epic was last split: it's written against the stories,
    and a re-split makes it stale, as for a design note (#244).
    """
    try:
        comments = issues.comments(repo, epic)
    except Exception:  # noqa: BLE001
        return ""
    bodies = [c.get("body") or "" for c in comments]
    splits = [i for i, b in enumerate(bodies) if STORY_SPLIT_MARKER in b]
    since = bodies[splits[-1] + 1 :] if splits else bodies
    notes = [b for b in since if NOTE_MARKER in b]
    return notes[-1] if notes else ""


def awaiting_presentation(
    issues: Any, cards: list[Card], default_repo: str
) -> set[tuple[str, int]]:
    """The epics whose stories wait: labelled `needs:ux`, with no note yet."""
    return {
        epic.key
        for epic in cards
        if needs_note(epic) and not note_for(issues, epic.repo or default_repo, epic.number or 0)
    }


def story_presentation(issues: Any, card: Card, default_repo: str) -> str:
    """The presentation note of the epic this story belongs to, for delivery and review."""
    if card.parent is None:
        return ""
    return note_for(issues, card.repo or default_repo, card.parent)


def story_notes(issues: Any, card: Card, default_repo: str) -> str:
    """The notes a story is built and reviewed against: its epic's design and presentation."""
    from crew_org.flows.design_notes import story_note  # noqa: PLC0415

    notes = [story_note(issues, card, default_repo), story_presentation(issues, card, default_repo)]
    return "\n\n".join(n for n in notes if n)


def with_reader_criteria(body: str, criteria: list[Any], line: Callable[..., list[str]]) -> str:
    """The story's body with the reader criteria added to its "Acceptance criteria".

    They continue the numbering and sit before the lines that end the section
    (existing tests, builds on, the estimate), so every reader of a story's
    criteria sees them. A body that already has them is returned unchanged.
    """
    if CRITERIA_MARKER in body:
        return body
    ends = [
        i for m in (EXISTING_TESTS, BUILDS_ON, "**Estimate**", "\n---") if (i := body.find(m)) != -1
    ]
    at = min(ends) if ends else len(body)
    start = len(_CRITERION.findall(body[:at])) + 1
    block = [CRITERIA_MARKER, "*What the reader sees, added by the UX Designer:*", ""]
    for n, c in enumerate(criteria, start):
        block += line(n, c)
    head = body[:at].rstrip("\n")
    return f"{head}\n\n" + "\n".join(block) + body[at:]


def coverage(note: Any, stories: list[dict]) -> list[str]:
    """What's wrong with the note's reach: stories it misses or doesn't know."""
    wanted = {s["number"] for s in stories}
    given = [s.story for s in note.stories]
    problems = [f"#{n} has no criteria" for n in sorted(wanted - set(given))]
    problems += [f"#{n} isn't one of this epic's stories" for n in sorted(set(given) - wanted)]
    problems += [
        f"#{n} appears more than once" for n in sorted({n for n in given if given.count(n) > 1})
    ]
    return problems


def write_notes(
    issues: Any,
    sink: EventSink,
    ws: Any,
    cards: list[Card],
    *,
    default_repo: str,
    repos: set[str],
    write: Callable[..., Any],
    render: Callable[[Any], str],
    line: Callable[..., list[str]],
) -> PresentationNotes:
    """Write the note for every `needs:ux` epic whose stories are split, and add its criteria."""
    from crew_org.tools.repo_context import repository_context  # noqa: PLC0415

    result = PresentationNotes()
    for epic in cards:
        repo = epic.repo or default_repo
        number = epic.number or 0
        if repo not in repos or not needs_note(epic) or note_for(issues, repo, number):
            continue
        children = issues.sub_issues(repo, number)
        stories = [s for s in children if s.get("state") != "closed"]
        if not stories:
            continue  # nothing split yet, or everything built: no reader left to write for
        try:
            clone = ws.for_repo(repo).current()
            try:
                record = read_record(clone)
            except ProjectRecordError:
                record = None
            goal = ""
            if epic.parent:
                g = issues.get(repo, epic.parent)
                goal = f"#{epic.parent} {g.get('title') or ''}\n\n{g.get('body') or ''}"
            outcome = attributed(_write_one, card=number, repo=repo)(
                write=write,
                stories=sorted(stories, key=lambda s: s["number"]),
                goal=goal,
                epic=f"#{number} {epic.title}\n\n{issues.get(repo, number).get('body') or ''}",
                project=brief(record) if record else "",
                repository=repository_context(clone, editing=False),
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((number, f"{type(exc).__name__}: {exc}"[:200]))
            sink.note(EventKind.NOTE, f"#{number} presentation note failed: {exc}"[:120])
            continue

        if isinstance(outcome, tuple):
            why = outcome[0]
            artifacts.label(
                issues, sink, repo=repo, number=number, by=BY, add=["blocked", "needs:human"]
            )
            artifacts.comment(
                issues,
                sink,
                repo=repo,
                number=number,
                body=f"**No presentation note: this needs a person.** {why}\n\n"
                "The epic's stories wait until it has one.",
                by=BY,
            )
            result.blocked.append((number, why))
            continue

        # The criteria first, then the note: the note is what releases the
        # stories, so they're never released without their criteria.
        for entry in outcome.stories:
            body = issues.get(repo, entry.story).get("body") or ""
            updated = with_reader_criteria(body, entry.criteria, line)
            if updated != body:
                issues.edit_issue(repo, entry.story, body=updated)
                sink.emit(
                    CrewEvent(
                        kind=EventKind.NOTE,
                        role=BY,
                        card=entry.story,
                        summary=f"{len(entry.criteria)} reader criteria added",
                        detail={"artifact": "story", "repo": repo, "epic": number},
                    )
                )
        artifacts.comment(
            issues, sink, repo=repo, number=number, body=f"{NOTE_MARKER}\n{render(outcome)}", by=BY
        )
        result.written.append(number)
    return result


def _write_one(*, write, stories, goal, epic, project, repository):
    """The note, or (why it couldn't be written,) for a person."""
    story_text = "\n\n".join(
        f"### #{s['number']} {s['title']}\n\n{s.get('body') or ''}" for s in stories
    )
    feedback = ""
    problems: list[str] = []
    for _attempt in range(ATTEMPTS):
        note = write(
            goal=goal,
            epic=epic,
            stories=story_text,
            project=project,
            repository=repository,
            feedback=feedback,
        )
        if note.beyond_reach:
            return (f"The UX Designer could not resolve: {note.beyond_reach}",)
        problems = coverage(note, stories)
        if not problems:
            return note
        feedback = "\n".join(f"- {p}" for p in problems)
    return ("It didn't cover the epic's stories after a retry: " + "; ".join(problems),)
