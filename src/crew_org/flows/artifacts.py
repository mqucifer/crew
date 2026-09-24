"""Writing to GitHub, with the role that wrote it.

#22 put the acting role on the *card* — the board's `Owner Agent` field, and an
event carrying the columns. That covers movement. It does not cover what the
crew writes: a comment and a label change go straight to GitHub through
`IssueClient`, which has no notion of who is calling it, so everything the crew
said appeared as `crew[bot]` and a Sponsor reading the audit trail could not
tell refinement from review without reading the body text.

The two halves are not symmetrical, and that is worth knowing rather than
discovering. A comment has a body, so the role goes in it and can be read back.
A label change has nowhere on GitHub to record an actor — the timeline shows the
bot and nothing else — so that half can only ever land in the crew's own event
log. Both are recorded; only one is visible on GitHub.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.tools.github_issues import IssueClient

BY_MARKER = "<!-- crew:by "

# `repo#N`, a repository-qualified reference as `Card.name(qualify=True)` writes
# it. Not preceded by `/`, so an already-linked `owner/repo#N` is left alone.
_QUALIFIED = re.compile(r"(?<![\w/.-])([A-Za-z0-9][\w.-]*)#(\d+)\b")
# A bare `#N`, not part of a longer token or a heading's `##`.
_BARE = re.compile(r"(?<![\w/#])#(\d+)\b")


def link_references(
    text: str, *, owner: str, home: str, delivery: list[str], known: Iterable[str] = ()
) -> str:
    """Make every card reference in `text` link to that card from `home`.

    GitHub links a bare `#31` to issue 31 of the repository it is written in.
    The standup and retro are written on the crew repository and name
    delivery cards, so a bare `#31` there linked to crew#31, a different issue
    (#118). `repo#N` does not link at all; `owner/repo#N` does.

    A bare number is qualified only when there is exactly one delivery
    repository to qualify it with. With several it is ambiguous, and it is left
    as it was rather than guessed. `known` names other repositories whose
    `repo#N` should link, such as the crew's own; any other `word#N` is left
    alone, so `PR#5` is not mistaken for a repository.
    """
    repos = {home, *delivery, *known}

    def qualified(match: re.Match[str]) -> str:
        repo, number = match.group(1), match.group(2)
        if repo not in repos:
            return match.group(0)
        return f"#{number}" if repo == home else f"{owner}/{repo}#{number}"

    # Bare numbers first, while `crew#9` still reads as crew's: done the other
    # way round, `crew#9` became `#9` and was then taken for a delivery card.
    if len(delivery) == 1 and delivery[0] != home:
        text = _BARE.sub(lambda m: f"{owner}/{delivery[0]}#{m.group(1)}", text)
    return _QUALIFIED.sub(qualified, text)


def signed(body: str, by: str | None) -> str:
    """The comment, signed by the role that wrote it.

    Visible, because the point is that a Sponsor can tell who spoke without
    reading for clues; and marked, so the crew can read it back.
    """
    if not by:
        return body
    return f"{body}\n\n{BY_MARKER}{by} -->\n— *{by}*"


def comment(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    number: int,
    body: str,
    by: str | None,
) -> None:
    """Write a comment, signed.

    `by=None` where no single role wrote it — a merge, a card closing because
    its children are done. Nothing is claimed rather than a role being guessed,
    which is the ruling #22 made for card moves.
    """
    issues.comment(repo, number, signed(body, by))
    sink.emit(
        CrewEvent(
            kind=EventKind.NOTE,
            role=by,
            card=number,
            summary="commented",
            detail={"artifact": "comment", "repo": repo},
        )
    )


def label(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    number: int,
    by: str | None,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
) -> None:
    """Add and remove labels, recording who did it.

    GitHub's timeline attributes a label change to the bot and offers nowhere
    else to put an actor, so the role is recorded in the event log only. That is
    a real limitation of the platform rather than a shortcut.
    """
    add, remove = list(add), list(remove)
    if add:
        issues.add_labels(repo, number, add)
    for name in remove:
        issues.remove_label(repo, number, name)
    if add or remove:
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                role=by,
                card=number,
                summary="+" + ",".join(add) if add else "-" + ",".join(remove),
                detail={"artifact": "label", "added": add, "removed": remove, "repo": repo},
            )
        )
