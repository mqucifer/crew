"""Undoing a merged change.

Nothing the crew merged could be taken back by the crew (#83), and a change
that cannot be undone is a reason not to land it at all. This is the
mechanism, not a policy: deciding a change should not have landed stays with
the Sponsor, who asks with `crew revert`.

A revert is a pull request like any other. It is opened on its own
`revert/<pr>-…` branch, the review phase judges it on the same terms, and the
delivery pass lands it once approved. Its body carries a marker naming the pull
request it undoes and the card that work belonged to, because a revert has no
card of its own and the branch alone cannot say which card to return.

When a revert lands, however it lands, the card goes back to Needs Refinement
for a person: the work is not done, it has been undone, and a card left in
Done would be a lie `crew capability` reads as delivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crew_org.columns import BLOCKED, DONE
from crew_org.columns import NEEDS_REFINEMENT as REFINEMENT
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.moves import move_card
from crew_org.git_ops import BRANCH_PATTERN, RevertConflict, Workspace, branch_name
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

REVERT_KIND = "revert"
NEEDS_HUMAN = "needs:human"
BLOCKED_LABEL = "blocked"

# In the revert pull request's body. `card=-` when the work had no card.
_MARKER = re.compile(r"<!-- crew:revert pr=(?P<pr>\d+) card=(?P<card>[\w.-]+#\d+|-) -->")
# On the card's issue, once its revert has returned it. What keeps a card that
# is later re-delivered and Done again from being returned a second time.
LANDED_MARKER = "<!-- crew:revert-landed pr={pr} -->"

# GitHub's word for "this branch and main have both changed the same lines".
CONFLICTED = "dirty"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class RevertRequest:
    """What `crew revert` produced: a pull request, or the reason there is none."""

    reverted: int
    card: Card | None = None
    pr: int | None = None
    conflict: list[str] = field(default_factory=list)
    refused: str | None = None


@dataclass
class RevertLanding:
    # (revert pull request, the pull request it undid)
    merged: list[tuple[int, int]] = field(default_factory=list)
    # Cards returned to Needs Refinement because their revert landed.
    returned: list[int] = field(default_factory=list)
    awaiting_approval: list[int] = field(default_factory=list)
    unapprovable: list[int] = field(default_factory=list)
    conflicted: list[int] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)


def marker(reverted: int, card: Card | None) -> str:
    target = f"{card.repo}#{card.number}" if card is not None else "-"
    return f"<!-- crew:revert pr={reverted} card={target} -->"


def parse_marker(body: str | None) -> tuple[int, tuple[str, int] | None] | None:
    """(the pull request reverted, the card's key) from a revert's body."""
    match = _MARKER.search(body or "")
    if not match:
        return None
    if match["card"] == "-":
        return int(match["pr"]), None
    repo, number = match["card"].rsplit("#", 1)
    return int(match["pr"]), (repo, int(number))


def is_revert(pull: dict) -> bool:
    head = (pull.get("head") or {}).get("ref", "")
    return head.startswith(f"{REVERT_KIND}/") and parse_marker(pull.get("body")) is not None


def card_for_pull(pull: dict, cards: list[Card], repo: str) -> Card | None:
    """The card whose work a pull request carried, read off its branch name.

    Delivery names the branch `<kind>/<card>-<summary>`, so the number is the
    card's. A branch that does not follow the convention was nobody's card.
    """
    match = BRANCH_PATTERN.match((pull.get("head") or {}).get("ref", ""))
    if not match or match["type"] == REVERT_KIND:
        return None
    number = int(match["issue"])
    return next((c for c in cards if c.key == (repo, number)), None)


def request_revert(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Workspace,
    *,
    cards: list[Card],
    repo: str,
    pull_number: int,
    reason: str,
) -> RevertRequest:
    """Open a pull request undoing merged pull request `pull_number`.

    Nothing lands here. The revert goes through review like any other change,
    and the delivery pass merges it once approved.
    """
    request = RevertRequest(reverted=pull_number)
    pull = issues.pull(repo, pull_number)
    request.card = card = card_for_pull(pull, cards, repo)

    refusal = _refusal(issues, pull, reason, repo=repo)
    if refusal:
        request.refused = refusal
        _emit(sink, EventKind.REVERT_REFUSED, request, repo=repo, reason=reason, why=refusal)
        return request

    branch = revert_branch(pull)
    workspace = ws.for_repo(repo)
    try:
        workspace.open(branch)
        workspace.revert(pull["merge_commit_sha"])
    except RevertConflict as conflict:
        workspace.close()
        request.conflict = conflict.files
        request.refused = str(conflict)
        _emit(sink, EventKind.REVERT_REFUSED, request, repo=repo, reason=reason, why=str(conflict))
        if card is not None:
            _block(board, issues, sink, card, repo=repo, pull_number=pull_number, conflict=conflict)
        return request

    try:
        workspace.push()
    finally:
        workspace.close()

    opened = issues.create_pull(
        repo,
        title=f'Revert "{pull["title"]}"',
        head=branch,
        base=pull["base"]["ref"],
        body=_render_body(pull, card, reason),
    )
    request.pr = opened["number"]
    _emit(sink, EventKind.REVERT_OPENED, request, repo=repo, reason=reason)
    if card is not None:
        artifacts.comment(
            issues,
            sink,
            repo=card.repo or repo,
            number=card.number or 0,
            body=f"**A revert is open.** PR #{request.pr} undoes PR #{pull_number}, "
            f"which carried this card's work.\n\n> {reason}\n\n"
            "It goes through review like any other change. When it lands, this card "
            f"returns to {REFINEMENT} for a decision on what should happen instead.",
            by=None,
        )
    return request


def revert_branch(pull: dict) -> str:
    return branch_name(pull["number"], pull["title"], kind=REVERT_KIND)


def _refusal(issues: IssueClient, pull: dict, reason: str, *, repo: str) -> str | None:
    if not reason.strip():
        return "a revert needs a reason; it is what the audit trail is for"
    if not pull.get("merged"):
        return f"PR #{pull['number']} was never merged, so there is nothing to revert"
    if not pull.get("merge_commit_sha"):
        return f"PR #{pull['number']} has no merge commit to revert"
    default = ((pull.get("base") or {}).get("repo") or {}).get("default_branch")
    if default and pull["base"]["ref"] != default:
        return f"PR #{pull['number']} merged into {pull['base']['ref']}, not {default}"
    if issues.pull_for_branch(repo, revert_branch(pull)) is not None:
        return f"a revert of PR #{pull['number']} is already open"
    return None


def _render_body(pull: dict, card: Card | None, reason: str) -> str:
    card_line = (
        f"The work belonged to {card.name(qualify=True)}. When this lands, that card "
        f"returns to {REFINEMENT} for a person to decide what happens next."
        if card is not None
        else "The reverted pull request carried no card."
    )
    return "\n".join(
        [
            marker(pull["number"], card),
            f"Reverts #{pull['number']} ({pull['merge_commit_sha'][:7]}).",
            "",
            "**Why**",
            "",
            f"> {reason}",
            "",
            card_line,
        ]
    )


def _block(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    pull_number: int,
    conflict: RevertConflict,
) -> None:
    """A revert that cannot apply cleanly stops for a person. It is never forced.

    The issue is reopened as well: a closed card is put back in Done by the
    board's own workflow, and this one's work is now in question.
    """
    card_repo, number = card.repo or repo, card.number or 0
    if card.state == "CLOSED":
        issues.reopen(card_repo, number)
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=BLOCKED,
        by=None,
        card=number,
        frm=card.status,
        summary=f"revert of PR #{pull_number} conflicts",
        kind=EventKind.CARD_BLOCKED,
    )
    artifacts.label(
        issues, sink, repo=card_repo, number=number, by=None, add=[BLOCKED_LABEL, NEEDS_HUMAN]
    )
    files = "\n".join(f"- `{f}`" for f in conflict.files) or "- (git named no files)"
    artifacts.comment(
        issues,
        sink,
        repo=card_repo,
        number=number,
        body=f"**Blocked — the revert of PR #{pull_number} does not apply cleanly.** "
        "The code has moved on since it landed, so the right end state is a decision "
        "for a person, not something to force.\n\n"
        f"Conflicting paths:\n{files}\n\n"
        "Resolve the revert by hand, or decide the change should stay and close this "
        "issue again.",
        by=None,
    )


def land_reverts(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    *,
    cards: list[Card],
    repos: set[str],
) -> RevertLanding:
    """Merge approved reverts, and return the cards of every revert that landed.

    Two passes. The first lands what review has approved, as `merge_approved`
    lands a story. The second reads merged reverts, whoever merged them, so a
    revert a person landed by hand returns its card all the same.
    """
    result = RevertLanding()
    for repo in sorted(repos):
        # Merged moments ago, so `closed_pulls` may not list them yet. Returned
        # from the pull request in hand rather than waiting a tick.
        merged = [
            pull
            for pull in issues.open_pulls(repo)
            if is_revert(pull) and _land(issues, result, pull, repo=repo)
        ]
        merged += [p for p in issues.closed_pulls(repo) if p.get("merged_at") and is_revert(p)]
        seen: set[int] = set()
        for pull in merged:
            if pull["number"] not in seen:
                seen.add(pull["number"])
                _return_card(board, issues, sink, result, pull, cards=cards, repo=repo)
    return result


def _land(issues: IssueClient, result: RevertLanding, pull: dict, *, repo: str) -> bool:
    """Merge one open revert if review has approved it. True if it merged.

    A conflict here is reported, not blocked on: the revert is already a pull
    request a person can see, and main having moved under it is the ordinary
    state of an open pull request, not a failure of the revert.
    """
    number = pull["number"]
    if not any(r.get("state") == "APPROVED" for r in issues.pull_reviews(repo, number)):
        result.awaiting_approval.append(number)
        return False
    if issues.review_decision(repo, number) == REVIEW_REQUIRED:
        result.unapprovable.append(number)
        return False
    detail = issues.pull(repo, number)
    if detail.get("mergeable_state") == CONFLICTED or detail.get("mergeable") is False:
        result.conflicted.append(number)
        return False
    try:
        issues.merge_pull(repo, number)
    except Exception as exc:  # noqa: BLE001
        result.failed.append((number, str(exc)[:120]))
        return False
    parsed = parse_marker(pull.get("body"))
    result.merged.append((number, parsed[0] if parsed else 0))
    return True


def _return_card(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    result: RevertLanding,
    pull: dict,
    *,
    cards: list[Card],
    repo: str,
) -> None:
    parsed = parse_marker(pull.get("body"))
    if parsed is None or parsed[1] is None:
        return
    reverted, key = parsed
    card = next((c for c in cards if c.key == key), None)
    if card is None:
        return
    card_repo, number = key
    done_marker = LANDED_MARKER.format(pr=pull["number"])
    if issues.has_comment_marked(card_repo, number, done_marker):
        return

    if card.state == "CLOSED":
        issues.reopen(card_repo, number)
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=REFINEMENT,
        by=None,
        card=number,
        frm=card.status,
        summary=f"revert PR #{pull['number']} landed — the work is undone",
    )
    artifacts.label(issues, sink, repo=card_repo, number=number, by=None, add=[NEEDS_HUMAN])
    artifacts.comment(
        issues,
        sink,
        repo=card_repo,
        number=number,
        body=f"{done_marker}\n**Returned — this card's work was reverted.** PR #{pull['number']} "
        f"undid PR #{reverted} and has landed, so the card is back in {REFINEMENT} rather "
        f"than {DONE}.\n\nDecide what should happen instead: re-scope it, re-deliver it, "
        "or close it as not planned.",
        by=None,
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.REVERT_LANDED,
            card=number,
            summary=f"PR #{pull['number']} undid PR #{reverted}",
            detail={
                "repo": card_repo,
                "pr": pull["number"],
                "reverted": reverted,
                "card": f"{card_repo}#{number}",
            },
        )
    )
    result.returned.append(number)


def _emit(
    sink: EventSink,
    kind: EventKind,
    request: RevertRequest,
    *,
    repo: str,
    reason: str,
    why: str | None = None,
) -> None:
    card = request.card
    sink.emit(
        CrewEvent(
            kind=kind,
            card=card.number if card is not None else None,
            summary=(
                f"PR #{request.pr} reverts PR #{request.reverted}"
                if request.pr
                else f"revert of PR #{request.reverted} not opened: {why}"
            )[:100],
            detail={
                "repo": repo,
                "reverted": request.reverted,
                "pr": request.pr,
                "card": f"{card.repo}#{card.number}" if card is not None else None,
                "reason": reason,
                **({"why": why} if why else {}),
                **({"conflict": request.conflict} if request.conflict else {}),
            },
        )
    )
