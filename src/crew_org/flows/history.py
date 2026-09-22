"""A card's verdict trail, bounded, for the gate about to judge it again.

QA and the Code Reviewer judged every attempt cold. Neither was given the
card's own history, so a story returned for reason A, fixed, and resubmitted
could come back for reason B that was never mentioned the first time — a moving
target the Developer cannot hit, and one that costs a whole delivery cycle each
time it moves.

The trail is bounded rather than complete. §16 forbids a silent half-measure
and a partial unit both, so this drops whole verdicts, oldest first, and says
how many it dropped.
"""

from __future__ import annotations

from typing import Any

# Three is the trail, not the archive. A gate needs to know what it has already
# said, not every word it has ever said; past three, a card is in trouble for
# reasons no amount of history will settle.
MAX_VERDICTS = 3

# Bounded against the prompt, not against the model: the ceiling exists so a
# long history cannot crowd out the diff or the tests, which are the evidence.
MAX_VERDICT_CHARS = 12_000


def recent_verdicts(
    bodies: list[str], *, limit: int = MAX_VERDICTS, budget: int = MAX_VERDICT_CHARS
) -> str:
    """The most recent verdicts that fit, newest last, whole ones only.

    `bodies` is in the order they were written. Nothing is truncated mid-verdict
    — half a finding reads as a finding about half a file — so a verdict that
    does not fit is dropped entirely and the omission is stated.
    """
    kept: list[str] = []
    spent = 0
    for body in reversed([b for b in bodies if b and b.strip()][-limit:]):
        if spent + len(body) > budget and kept:
            break
        kept.append(body)
        spent += len(body)
    if not kept:
        return ""
    dropped = len([b for b in bodies if b and b.strip()]) - len(kept)
    trail = "\n\n---\n\n".join(reversed(kept))
    if dropped > 0:
        trail = f"_{dropped} earlier verdict(s) omitted._\n\n{trail}"
    return trail


def past_qa(issues: Any, repo: str, number: int, *, marker: str) -> str:
    """Every QA verdict already on this card, most recent last.

    Best effort: a card being verified for the first time has none, and a read
    failure is not worth losing the verdict over.
    """
    try:
        bodies = [
            c.get("body") or ""
            for c in issues.comments(repo, number)
            if marker in (c.get("body") or "")
        ]
    except Exception:  # noqa: BLE001
        return ""
    return recent_verdicts(bodies)


def past_reviews(issues: Any, repo: str, pull: int, *, marker: str, head: str = "") -> str:
    """The crew's earlier reviews of this pull request, most recent last.

    Reviews of the current head are left out: those are not history, they are
    the verdict this pass is deciding whether to give, and a gate shown its own
    answer is not judging.
    """
    try:
        bodies = [
            r.get("body") or ""
            for r in issues.pull_reviews(repo, pull)
            if marker in (r.get("body") or "") and (not head or r.get("commit_id") != head)
        ]
    except Exception:  # noqa: BLE001
        return ""
    return recent_verdicts(bodies)
