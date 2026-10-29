"""A red CI check reaches the Developer with its log (#325).

The crew's own checks run in a sandbox with no network: lint and tests. What
only CI can run, such as an image build or a release workflow, was proven
nowhere the crew could see. The Code Reviewer was shown a check's name and
its outcome; the merge queue removing a pull request for a failed check was
handled like a conflict and rebuilt from `main`, with no word on what failed.
A Docker story would retry blind until it blocked.

So a failed check returns the card the way a review's requested changes do:
the failing job's log tail on the pull request, marked with the head it
judged, which delivery reads as a verdict and as the sign a rework is due.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org import sendbacks
from crew_org.columns import IN_PROGRESS, MERGING
from crew_org.events import EventSink
from crew_org.flows.moves import move_card
from crew_org.rules import Rule
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

# On the pull request, naming the head that failed: delivery re-works a card
# whose current head carries it, and a new commit clears it.
CI_MARKER = "<!-- crew:ci head={head} -->"
CI_MARKER_PREFIX = "<!-- crew:ci head="
# Generous, not a budget (§16): the end of a log is where the failure is.
LOG_TAIL_CHARS = 6_000
MAX_JOBS = 3

_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?", re.MULTILINE)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_JOB = re.compile(r"/job/(\d+)")
# What the runner logs of a job's service containers (crew#839).
SERVICE_LOG_LINES = 10
_STARTING = re.compile(r"##\[group\]Starting (\S+) service container")
_DOCKER_START = re.compile(r"##\[command\]\S*docker start (\w+)")
_STATE = re.compile(r"(\S+) service is (\w+)")
_DOCKER_LOGS = re.compile(r"##\[command\]\S*docker logs (?:--details )?(\w+)")


def services_seen(lines: list[str]) -> str:
    """Each service the runner started for the job, as its log says: its last state, the
    errors of its start-up, and the end of its own log. "" for a job with no services.

    The runner starts each in a `##[group]Starting <label> service container`, checks
    its health in "Waiting for all services to be ready" (the run page's "Initialize
    containers"), and prints its `docker logs` after the job's steps. The failing step
    alone left all of that out: mqucifer/sprint-metrics#748's session read "can't
    resolve `postgres`" as a container that hadn't started, when it was healthy (crew#839).
    """
    labels: list[str] = []
    ids: dict[str, str] = {}
    states: dict[str, str] = {}
    errors: list[str] = []
    logs: dict[str, list[str]] = {}
    label = printing = None
    for line in lines:
        started = _STARTING.match(line)
        if started:
            label = started.group(1)
            labels.append(label)
            continue
        if line.startswith("##[group]Run "):
            label = None
        if label is not None:
            ran = _DOCKER_START.match(line)
            if ran:
                ids[ran.group(1)] = label
            if line.startswith("##[error]"):
                errors.append(line)
            state = _STATE.search(line)
            if state and state.group(1) in labels:
                states[state.group(1)] = state.group(2)
        printed = _DOCKER_LOGS.match(line)
        if printed:
            printing = ids.get(printed.group(1))
            if printing is not None:
                logs[printing] = []
            continue
        if printing is not None:
            if not line.startswith(" ") or line.startswith("##["):
                printing = None
            else:
                logs[printing].append(line[1:].rstrip())
    if not labels:
        return ""
    parts = ["Services, as the runner started them:"]
    parts += [f"- `{name}`: {states.get(name, 'started')}" for name in labels]
    parts += errors
    for name in labels:
        own = [line for line in logs.get(name, []) if line.strip()][-SERVICE_LOG_LINES:]
        if own:
            parts += [f"The end of `{name}`'s own log:", *own]
    return "\n".join(parts)


def log_tail(text: str) -> str:
    """The failing step of a job log: from its header to the error, cleaned, after what
    the job's services did (`services_seen`).

    A job log ends with post-job cleanup, so its last lines are rarely the
    failure. GitHub marks the failure `##[error]`, and the step that ran it
    starts at the `##[group]` before. Without a marker, the log's end. A job with
    no services gets the failing step alone, as before.
    """
    lines = _ANSI.sub("", _STAMP.sub("", text)).strip().splitlines()
    services = services_seen(lines)
    if len(services) > LOG_TAIL_CHARS // 2:
        # The services' part never takes more than half: the failure is the rest.
        services = "…\n" + services[-(LOG_TAIL_CHARS // 2 - 2) :].split("\n", 1)[-1]
    error = next((i for i, line in enumerate(lines) if line.startswith("##[error]")), None)
    if error is not None:
        start = max(
            (i for i, line in enumerate(lines[:error]) if line.startswith("##[group]")),
            default=0,
        )
        errors = [line for line in lines[error:] if line.startswith("##[error]")]
        lines = lines[start:error] + errors
    clean = "\n".join(lines).strip()
    room = LOG_TAIL_CHARS - (len(services) + 2 if services else 0)
    if len(clean) > room:
        cut = clean[-(room - 2) :]
        clean = "…\n" + cut.split("\n", 1)[-1]
    return f"{services}\n\n{clean}" if services else clean


def last_lines(text: str, chars: int) -> str:
    """The end of `text` in whole lines within `chars`, saying how many earlier lines
    were left out. A cut by characters opened mqucifer/qa-suite#8's CI section with
    "veloper.**" (crew#944)."""
    if len(text) <= chars:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        if kept and size + len(line) + 1 > chars:
            break
        kept.append(line)
        size += len(line) + 1
    kept.reverse()
    left = len(lines) - len(kept)
    return f"({left} earlier line{'s' if left != 1 else ''} left out)\n" + "\n".join(kept)


def job_id(run: dict[str, Any]) -> int | None:
    """The Actions job behind a check run or a job record."""
    found = _JOB.search(run.get("details_url") or run.get("html_url") or "")
    if found:
        return int(found.group(1))
    return run.get("id")


def report(issues: IssueClient, repo: str, failed: list[dict[str, Any]]) -> str:
    """Each failed job by name, with the tail of its log."""
    parts: list[str] = []
    for run in failed[:MAX_JOBS]:
        name = run.get("name") or "a check"
        outcome = run.get("conclusion") or "failed"
        ident = job_id(run)
        try:
            log = issues.job_log(repo, ident) if ident is not None else ""
        except Exception:  # noqa: BLE001
            log = ""
        parts.append(f"### `{name}`: {outcome}")
        if log:
            parts += ["", "```", log_tail(log), "```"]
        else:
            parts += [
                "",
                "_Its log couldn't be read: it has expired, or the crew's GitHub App "
                "lacks Actions read access._",
            ]
        parts.append("")
    if len(failed) > MAX_JOBS:
        rest = ", ".join(f"`{r.get('name')}`" for r in failed[MAX_JOBS:])
        parts.append(f"Also failed, not shown: {rest}.")
    return "\n".join(parts).strip()


def return_for_ci(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    pull: dict,
    failed: list[dict[str, Any]],
    where: str,
    frm: str = MERGING,
    rule: str = "",
) -> None:
    """Send a card back to whoever built it with what CI said: approved, or before review.
    A role's Task goes back to its role, not the Developer (crew#944)."""
    from crew_org.flows.role_tasks import role_of  # noqa: PLC0415

    number = card.number or 0
    role = role_of(card)
    who = f"the {role.name if role is not None else 'Developer'}"
    head = (pull.get("head") or {}).get("sha", "")
    marker = CI_MARKER.format(head=head)
    names = ", ".join(f"`{r.get('name')}`" for r in failed[:MAX_JOBS])
    try:
        bodies: list[str] | None = [
            c.get("body") or "" for c in issues.comments(repo, pull["number"])
        ]
    except Exception:  # noqa: BLE001
        bodies = None
    already = any(marker in b for b in bodies or [])
    if not already:
        issues.comment(
            repo,
            pull["number"],
            f"{marker}\n**CI failed {where}: returned to {who}.** {names} did not pass. "
            "The crew's own checks run lint and tests in a sandbox; this is what only CI "
            f"runs, and its log is below.\n\n{report(issues, repo, failed)}",
        )
    named: dict[str, Any] = {"rule": rule} if rule else {}
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=IN_PROGRESS,
        by=None,
        card=number,
        frm=frm,
        summary=f"PR #{pull['number']}: {names} failed {where}, returned with the log"[:120],
        **named,
    )
    # Its round: CI's returns of this pull request, this head's included, as its comments
    # count them; unknown when they can't be read. The marker is the same before review
    # and at merge, so both kinds count together, as the records reference says.
    sendbacks.record(
        sink,
        sendbacks.CI_BEFORE_REVIEW if rule == Rule.CI_BEFORE_REVIEW else sendbacks.MERGE_CI_FAILED,
        card=number,
        repo=repo,
        round_=None
        if bodies is None
        else sum(CI_MARKER_PREFIX in b and marker not in b for b in bodies) + 1,
        jobs=len(failed),
    )


def latest_ci_verdict(issues: IssueClient, repo: str, pull: int) -> str:
    """The last CI failure reported on a pull request, for the next delivery."""
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, pull)]
    except Exception:  # noqa: BLE001
        return ""
    found = [b for b in bodies if CI_MARKER_PREFIX in b]
    return found[-1] if found else ""
