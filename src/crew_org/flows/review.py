"""The review pass: every open pull request gets a verdict.

Deliberately not limited to the crew's own pull requests. A human contributor's
change is reviewed on the same terms — and because the crew acts as a distinct
bot identity, it *can* approve a pull request the Sponsor opened, which is the
same property that lets the Sponsor approve the crew's.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from crew_org import log
from crew_org.columns import IN_PROGRESS, QAING, REVIEWING
from crew_org.crews.deploy_review_crew import review_deploy
from crew_org.crews.review_crew import Finding, ReviewVerdict, review_diff
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import story_problem
from crew_org.flows.artifacts import signed
from crew_org.flows.board_flow import STORY_PROBLEM_MARKER
from crew_org.flows.conclusion import story_rows
from crew_org.flows.history import latest_answer, past_reviews
from crew_org.flows.moves import move_card
from crew_org.flows.presentation_notes import story_notes
from crew_org.git_ops import branch_name
from crew_org.llm import reraise_if_down
from crew_org.project import DEFAULT_DOCS, RECORD_PATH, parse
from crew_org.tools.deploy_evidence import deploy_evidence, touches_runnable
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.tools.review_evidence import (
    checks_section,
    imported_code,
    importers_section,
    undocumented_options,
)

REVIEW_MARKER = "<!-- crew:review -->"
CRITERIA = "## Acceptance criteria"


@dataclass
class ReviewOutcome:
    pr: int
    approved: bool
    findings: int = 0
    skipped: str | None = None
    # What GitHub was actually told, which is not always the verdict reached.
    # Reporting the verdict alone is how a run printed "approved" over a review
    # GitHub had recorded as COMMENTED, and left the merge waiting on an
    # approval nobody knew was missing.
    event: str | None = None


@dataclass
class ReviewResult:
    reviewed: list[ReviewOutcome] = field(default_factory=list)
    skipped: list[ReviewOutcome] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    # (story, epic) sent back to refinement over a conflict with its own criteria.
    returned: list[tuple[int, int]] = field(default_factory=list)


def render_review(verdict: ReviewVerdict, deploy: ReviewVerdict | None = None) -> str:
    """The Code Reviewer's verdict, and the DevOps Engineer's where it judged too (#335)."""
    body = signed(_verdict_lines([REVIEW_MARKER], verdict), "Code Reviewer")
    if deploy is None:
        return body
    return f"{body}\n\n" + signed(_verdict_lines(["## DevOps review"], deploy), "DevOps Engineer")


def _verdict_lines(lines: list[str], verdict: ReviewVerdict) -> str:
    lines = [*lines, verdict.summary, ""]
    blocking = [f for f in verdict.findings if f.blocking]
    if blocking:
        lines += ["## Findings", ""]
        for finding in blocking:
            lines += [f"**`{finding.file}`** — {finding.concern}", f"→ {finding.action}", ""]
            if finding.conflicts_with.strip():
                lines += [f"Conflicts with the story's criterion: {finding.conflicts_with}", ""]
    if verdict.notes:
        lines += ["## Notes, not blocking", ""]
        for finding in verdict.notes:
            lines += [f"**`{finding.file}`** — {finding.concern}", f"→ {finding.action}", ""]
    if verdict.approve and not verdict.findings:
        lines.append("No findings.")
    return "\n".join(lines).strip()


def already_reviewed(reviews: list[dict], bot_login: str, head: str = "") -> bool:
    """Has the crew already put a verdict on *this* revision?

    The docstring said "this revision" and the code meant "ever": any review
    carrying the marker disqualified the pull request for good. So the crew
    requested changes and then refused to read the answer — a repaired branch
    was never looked at again, which is why sprint-metrics #31 and #32 could
    not recover on their own.

    The same defect #15 fixed for QA, in the gate it did not touch. GitHub
    records the commit each review judged, so no new instrumentation is needed:
    a verdict belongs to its `commit_id`, and a head nobody has judged is new
    work whatever else sits on the pull request.

    Without a head to compare against there is no honest answer, and the safe
    one is "yes" — reviewing again costs a model call and posts a duplicate
    verdict. Callers have the head; it is on the pull request.
    """
    ours = [
        r
        for r in reviews
        if (r.get("user") or {}).get("login") == bot_login
        and REVIEW_MARKER in (r.get("body") or "")
    ]
    if not ours:
        return False
    if not head:
        return True
    return any(r.get("commit_id") == head for r in ours)


def approved_at(reviews: list[dict], bot_login: str, head: str) -> bool:
    """Is the crew's latest verdict on `head` an approval?"""
    ours = [
        r
        for r in reviews
        if (r.get("user") or {}).get("login") == bot_login
        and REVIEW_MARKER in (r.get("body") or "")
        and head
        and r.get("commit_id") == head
    ]
    return bool(ours) and ours[-1].get("state") == "APPROVED"


def cards_by_branch(cards: list[Card]) -> dict[str, Card]:
    """The cards waiting for review, indexed by the branch carrying their work.

    Review reads pull requests rather than the board, deliberately: a human's
    change is reviewed on the same terms as the crew's, and a human's change has
    no card. So the board is what a verdict is applied *to*, not what is
    iterated — a pull request with no card is still reviewed, and simply moves
    nothing.
    """
    return {
        branch_name(c.number or 0, c.title): c
        for c in cards
        if c.status == REVIEWING and c.state != "CLOSED"
    }


def review_open_pulls(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    bot_login: str,
    board: ProjectClient | None = None,
    cards: list[Card] | None = None,
    writer: IssueClient | None = None,
    clone: Path | None = None,
) -> ReviewResult:
    """Review every open pull request that the crew has not yet judged.

    Moves the card a pull request belongs to out of `Reviewing`: on to `QAing`
    when the diff is approved, back to `In Progress` when changes are requested.
    A queue a phase never drains is not a queue, and until this existed review
    was the one phase that read the board without ever touching it — invisible
    to the orchestrator, and uncapped while the columns either side were not.

    `writer` is the identity that hands a story back to refinement (#252): the
    epic's label and evidence, and closing the pull request, are delivery's
    acts, not a review. Defaults to `issues`.

    `clone` is the repository at its base branch, where the reviewer is shown
    who imports the names a diff changes (#215). Without one it isn't shown.
    """
    result = ReviewResult()
    waiting = cards_by_branch(cards or []) if board is not None else {}

    for pull in issues.open_pulls(repo):
        # One Code Reviewer run per pull request, under the card it closes (crew#449).
        story = waiting.get((pull.get("head") or {}).get("ref", ""))
        with log.run("Code Reviewer", card=story.number if story is not None else None, repo=repo):
            number = pull["number"]
            author = (pull.get("user") or {}).get("login", "")

            if pull.get("draft"):
                result.skipped.append(ReviewOutcome(pr=number, approved=False, skipped="draft"))
                continue

            head = (pull.get("head") or {}).get("sha", "")
            reviews = issues.pull_reviews(repo, number)
            if already_reviewed(reviews, bot_login, head):
                # The crew's own approval already covers this head, and the card
                # still waits in Reviewing. GitHub carries an approval forward to a
                # commit with the same tree, so the empty commit that answers a QA
                # return without a change (#161) lands already approved, and the
                # card was never moved on (crew#321, sprint-metrics#261).
                card = waiting.get((pull.get("head") or {}).get("ref", ""))
                if card is not None and board is not None and approved_at(reviews, bot_login, head):
                    move_card(
                        board,
                        sink,
                        item_id=card.item_id,
                        to=QAING,
                        by="Code Reviewer",
                        card=card.number,
                        frm=REVIEWING,
                        summary="its approval stands at this head",
                    )
                result.skipped.append(
                    ReviewOutcome(
                        pr=number, approved=False, skipped="already reviewed at this head"
                    )
                )
                continue

            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_STARTED,
                    role="Code Reviewer",
                    card=story.number if story is not None else None,
                    summary=f"PR #{number} by {author}: {pull['title'][:40]}",
                    detail={"pr": number},
                )
            )
            try:
                diff = issues.pull_diff(repo, number)
                base = (pull.get("base") or {}).get("ref") or "main"
                story = waiting.get((pull.get("head") or {}).get("ref", ""))
                verdict = attributed(
                    review_diff,
                    card=story.number if story is not None else None,
                    repo=repo,
                    purpose="review" if story is not None else f"review PR #{number}",
                )(
                    pull["title"],
                    diff,
                    prior_verdicts=_with_answer(
                        past_reviews(issues, repo, number, marker=REVIEW_MARKER, head=head),
                        latest_answer(issues, repo, number),
                    ),
                    checks=_with_guard_notice(
                        checks_section(issues.check_runs(repo, head), pull.get("body") or ""),
                        issues,
                        repo,
                        head,
                        diff,
                    ),
                    imported=imported_code(
                        _base_reader(issues, repo, base, clone),
                        diff,
                        read_head=lambda path, ref=head: issues.file_at(repo, path, ref),
                    ),
                    design_note=story_notes(issues, story, repo) if story is not None else "",
                    acceptance_criteria=story_criteria(issues, story, repo),
                    decisions=story_rows(issues, story, repo),
                    importers=_importers(clone, diff),
                )
            except Exception as exc:  # noqa: BLE001
                reraise_if_down(exc)
                result.failed.append((number, f"{type(exc).__name__}: {exc}"))
                sink.emit(
                    CrewEvent(
                        kind=EventKind.AGENT_FAILED,
                        role="Code Reviewer",
                        card=story.number if story is not None else None,
                        summary=str(exc)[:80],
                        detail={"pr": number},
                    )
                )
                continue

            # A mechanical finding, not the model's (#191): an option the diff adds
            # that the project's user docs never mention.
            verdict = with_docs_finding(
                verdict,
                diff,
                user_docs(issues, repo, head),
                lambda p, ref=head: issues.file_at(repo, p, ref),
            )

            # Anything that runs or deploys is also judged as it will be run (#335).
            # One review carries both verdicts: a later review from the same
            # identity would replace the first as GitHub's decision.
            deploy: ReviewVerdict | None = None
            if touches_runnable(diff):
                try:
                    # The DevOps Engineer's own run, inside the Code Reviewer's.
                    with log.run(
                        "DevOps Engineer",
                        card=story.number if story is not None else None,
                        repo=repo,
                    ):
                        deploy = _deploy_review(
                            issues, sink, repo=repo, pull=pull, story=story, diff=diff, clone=clone
                        )
                except Exception as exc:  # noqa: BLE001
                    reraise_if_down(exc)
                    result.failed.append((number, f"deploy review: {type(exc).__name__}: {exc}"))
                    sink.emit(
                        CrewEvent(
                            kind=EventKind.AGENT_FAILED,
                            role="DevOps Engineer",
                            card=story.number if story is not None else None,
                            summary=str(exc)[:80],
                            detail={"pr": number},
                        )
                    )
                    continue
            code = verdict
            verdict = with_deploy(code, deploy)

            # GitHub refuses an approval from the identity that opened the pull
            # request. The crew reviews as a second app for exactly this reason, so
            # this downgrade now only fires where it should: a pull request the
            # reviewing identity opened itself.
            event = "COMMENT" if author == bot_login else verdict.event
            issues.create_review(repo, number, event=event, body=render_review(code, deploy))

            result.reviewed.append(
                ReviewOutcome(
                    pr=number,
                    approved=event == "APPROVE",
                    findings=len(verdict.findings),
                    event=event,
                )
            )
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FINISHED,
                    role="Code Reviewer",
                    card=story.number if story is not None else None,
                    summary=f"{event} — {len(verdict.findings) - len(verdict.notes)} findings"
                    + (f", {len(verdict.notes)} notes" if verdict.notes else ""),
                    # Counted by the retro: notes left on approved work (#214).
                    detail={
                        "pr": number,
                        "approved": event == "APPROVE",
                        "notes": len(verdict.notes),
                    },
                )
            )

            card = waiting.get(pull.get("head", {}).get("ref", ""))
            if card is not None and board is not None:
                # A COMMENT verdict is the crew reviewing its own pull request,
                # which GitHub will not let it approve. That is not a judgement, so
                # the card stays where it is rather than being moved on an opinion
                # nobody was allowed to record.
                if event == "APPROVE":
                    move_card(
                        board,
                        sink,
                        item_id=card.item_id,
                        to=QAING,
                        by="Code Reviewer",
                        card=card.number,
                        frm=REVIEWING,
                        summary="diff approved",
                    )
                elif (
                    event == "REQUEST_CHANGES"
                    and verdict.conflicts
                    and _to_product_owner(
                        card,
                        verdict,
                        number,
                        board=board,
                        issues=writer or issues,
                        sink=sink,
                        repo=repo,
                    )
                ):
                    result.returned.append((card.number or 0, card.parent or 0))
                elif event == "REQUEST_CHANGES":
                    move_card(
                        board,
                        sink,
                        item_id=card.item_id,
                        to=IN_PROGRESS,
                        by="Code Reviewer" if not code.approve else "DevOps Engineer",
                        card=card.number,
                        frm=REVIEWING,
                        summary=f"changes requested — {len(verdict.findings)} findings",
                        # What the retro counts the return as (#254).
                        finding=next(
                            (f"{f.file}: {f.concern}" for f in verdict.findings if f.blocking), ""
                        )[:300],
                    )

    return result


def with_deploy(verdict: ReviewVerdict, deploy: ReviewVerdict | None) -> ReviewVerdict:
    """One decision from both gates: approved only when each approves."""
    if deploy is None:
        return verdict
    return ReviewVerdict(
        summary=verdict.summary,
        approve=verdict.approve and deploy.approve,
        findings=[*verdict.findings, *deploy.findings],
    )


def _deploy_review(
    issues: IssueClient,
    sink: EventSink,
    *,
    repo: str,
    pull: dict,
    story: Card | None,
    diff: str,
    clone: Path | None,
) -> ReviewVerdict:
    """The DevOps Engineer's verdict on a change to something that runs or deploys (#335)."""
    number = pull["number"]
    head = (pull.get("head") or {}).get("sha", "")
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_STARTED,
            role="DevOps Engineer",
            card=story.number if story is not None else None,
            summary=f"deploy review of PR #{number}",
            detail={"pr": number},
        )
    )
    verdict = attributed(
        review_deploy,
        card=story.number if story is not None else None,
        repo=repo,
        purpose="deploy review" if story is not None else f"deploy review PR #{number}",
    )(
        pull["title"],
        diff,
        evidence=deploy_evidence(clone, diff, lambda path: issues.file_at(repo, path, head)),
        acceptance_criteria=story_criteria(issues, story, repo),
        decisions=story_rows(issues, story, repo),
        release=release_brief(issues, repo, head),
        prior_verdicts=past_reviews(issues, repo, number, marker=REVIEW_MARKER, head=head),
        checks=checks_section(issues.check_runs(repo, head), pull.get("body") or ""),
    )
    blocking = [f for f in verdict.findings if f.blocking]
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role="DevOps Engineer",
            card=story.number if story is not None else None,
            summary=f"{verdict.event} — {len(blocking)} findings"
            + (f", {len(verdict.notes)} notes" if verdict.notes else ""),
            detail={"pr": number, "approved": verdict.approve, "notes": len(verdict.notes)},
        )
    )
    return verdict


def _with_guard_notice(checks: str, issues: IssueClient, repo: str, head: str, diff: str) -> str:
    """The checks, plus which changed files no regression guard read (#404)."""
    from crew_org import profiles  # noqa: PLC0415
    from crew_org.tools.review_evidence import unguarded_section  # noqa: PLC0415

    try:
        text = issues.file_at(repo, RECORD_PATH, head)
        profiles.set_project(parse(text) if text else None)
    except Exception:  # noqa: BLE001
        profiles.clear()
    notice = unguarded_section(diff)
    return "\n\n".join(part for part in (checks, notice) if part)


def release_brief(issues: IssueClient, repo: str, head: str) -> str:
    """How the project says it's released and checked, from its record at the head."""
    try:
        text = issues.file_at(repo, RECORD_PATH, head)
        record = parse(text) if text else None
    except Exception:  # noqa: BLE001
        return ""
    if record is None:
        return ""
    release = record.intent.release
    lines = [
        f"- A release is {record.release_is}" + (f": {release.where}" if release.where else ".")
    ]
    if record.design is not None:
        if record.design.release_how:
            lines.append(f"- How: {record.design.release_how.strip()}")
        if record.design.checks:
            lines.append("- Checks: " + "; ".join(f"`{c}`" for c in record.design.checks))
        if record.design.ci_checks:
            lines += ["- CI proves:", *(f"  - {c}" for c in record.design.ci_checks)]
    return "\n".join(lines)


def _base_reader(issues: IssueClient, repo: str, base: str, clone: Path | None):
    """Read a file as it is on the base branch: from the clone when there is one.

    Looking up an imported module tries several paths that mostly don't exist.
    Through GitHub's API, each miss was a request and a 404 (#283's traces); on
    disk it's a stat.
    """
    if clone is None:
        return lambda path: issues.file_at(repo, path, base)

    def read(path: str) -> str | None:
        target = clone / path
        if not target.is_file() or not target.resolve().is_relative_to(clone.resolve()):
            return None
        return target.read_text(encoding="utf-8", errors="ignore")

    return read


def _importers(clone: Path | None, diff: str) -> str:
    if clone is None:
        return ""
    try:
        return importers_section(clone, diff)
    except Exception:  # noqa: BLE001
        return ""


def user_docs(issues: IssueClient, repo: str, head: str) -> list[str]:
    """Where the project's user docs live, from its record at the head; else the README."""
    try:
        text = issues.file_at(repo, RECORD_PATH, head)
        return parse(text).user_docs if text else [DEFAULT_DOCS]
    except Exception:  # noqa: BLE001
        return [DEFAULT_DOCS]


def with_docs_finding(
    verdict: ReviewVerdict, diff: str, docs: list[str], read_head
) -> ReviewVerdict:
    """The verdict, plus a blocking finding for each option no user doc mentions (#191).

    sprint-metrics shipped `--sprint-range`, `--thresholds` and four output
    formats in a day with a 14-line README. The Definition of Done asked for
    docs in the same pull request; nothing checked.
    """
    try:
        missing = undocumented_options(diff, docs, read_head)
    except Exception:  # noqa: BLE001
        return verdict
    if not missing:
        return verdict
    named = ", ".join(f"`{d}`" for d in docs)
    found = [
        Finding(
            file=docs[0],
            concern=f"`{option}` is added by this change, and no user doc ({named}) mentions it",
            action=f"Document `{option}` in {named} in this pull request: what it does, and an "
            "example of using it",
        )
        for option in missing
    ]
    return ReviewVerdict(
        summary=verdict.summary,
        approve=False,
        findings=[*verdict.findings, *found],
    )


def story_criteria(issues: IssueClient, card: Card | None, default_repo: str) -> str:
    """The story's acceptance criteria, as its issue states them.

    The reviewer was shown the epic's design note and never the story: on
    sprint-metrics#145 it asked, three times, for the assertion the story's own
    criterion required, because the note said so (#252).
    """
    if card is None:
        return ""
    try:
        body = issues.get(card.repo or default_repo, card.number or 0).get("body") or ""
    except Exception:  # noqa: BLE001
        return ""
    start = body.find(CRITERIA)
    if start == -1:
        return ""
    section = body[start + len(CRITERIA) :]
    for end in ("\n**Estimate**", "\n---"):
        section = section.split(end, 1)[0]
    return section.strip()


def _to_product_owner(
    card: Card, verdict: ReviewVerdict, pull: int, *, board, issues, sink, repo: str
) -> bool:
    """A finding the story's criteria contradict goes back to refinement at once (#252).

    Asking the author to break a criterion can't settle the story, and a repair
    round only finds that out slowly: #145 went round six times. The Product
    Owner decides which holds, and the epic is split again. True if it went back.
    """
    repo = card.repo or repo
    found = "\n\n".join(
        f"**`{f.file}`**: {f.concern}\n→ {f.action}\n\nThe criterion: {f.conflicts_with}"
        for f in verdict.conflicts
    )
    comment = (
        f"{STORY_PROBLEM_MARKER}\n"
        f"**#{card.number} went back to refinement: its review conflicts with its criteria.**"
        f"\n\nThe Code Reviewer asked for a change to *{card.title}* (PR #{pull}) that the "
        "story's own acceptance criterion rules out.\n\n"
        f"{found}\n\nSettle which holds before this epic is split again."
    )
    if not story_problem.return_to_refinement(
        board,
        issues,
        sink,
        card,
        repo=repo,
        cards=board.cards(),
        comment=comment,
        reason="review conflicts with a criterion",
    ):
        return False
    with contextlib.suppress(Exception):
        issues.comment(
            repo,
            pull,
            signed(
                f"Closed: #{card.number} went back to refinement. Its review asked for a "
                "change its own criteria rule out. See its epic.",
                "Code Reviewer",
            ),
        )
        issues.close_pull(repo, pull)
    return True


def _with_answer(prior: str, answer: str) -> str:
    """The Reviewer's earlier reviews, and the author's answer to the last, if any (#161)."""
    if not answer:
        return prior
    return (
        f"{prior}\n\n## The author's answer to your last review\n\n{answer}\n\n"
        "They answered that the code already satisfies your findings, with the evidence "
        "above, and made no change. Check that evidence against the code you are shown."
    )
