"""The retro, recorded where it can be found again (#50).

A retro used to go to the terminal and nowhere else. It is now an issue on the
crew repository, one per sprint, labelled `retro` and found the way the crew's
own work is found: from the Issues view, not the board. Each defect it proposes
is filed as its own issue, in the repository of the thing it found: how the
crew works goes to the crew, what the crew built goes to the delivery
repository that holds it. That is the Sponsor's rule of 2026-09-19, recorded in
§18 of ways-of-working.md.

Defects are filed before the retro issue, so the retro can name each one, and
GitHub's cross-reference puts a link back to the retro on every defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_org.crews.retro_crew import ProcessDefect, Retro
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows.artifacts import signed
from crew_org.tools.github_issues import IssueClient

ROLE = "Scrum Master"
RETRO_LABEL = "retro"
FINDING_LABEL = "retro-finding"
_LABELS = {
    RETRO_LABEL: ("5319e7", "A sprint's retro, written by the crew at sprint close"),
    FINDING_LABEL: ("d93f0b", "A defect a sprint retro found"),
}


def marker(sprint: str) -> str:
    return f"<!-- crew:retro sprint={sprint} -->"


@dataclass
class RetroRecord:
    issue: int | None = None
    # (repository, issue number) of each defect filed.
    filed: list[tuple[str, int]] = field(default_factory=list)
    # Defects that could not be filed, and why. The retro issue lists them too,
    # so a failure to file loses the defect's issue, never the defect.
    failed: list[tuple[str, str]] = field(default_factory=list)


def existing_retro(issues: IssueClient, crew_repo: str, sprint: str) -> int | None:
    """The retro already recorded for this sprint, if there is one."""
    for issue in issues.labelled(crew_repo, RETRO_LABEL):
        if marker(sprint) in (issue.get("body") or ""):
            return issue["number"]
    return None


def route(defect: ProcessDefect, *, crew_repo: str, delivery_repos: list[str]) -> tuple[str, str]:
    """Where a defect is filed, and a line saying why if it is not the obvious place."""
    if defect.about == "process":
        return crew_repo, ""
    if defect.repository in delivery_repos:
        return str(defect.repository), ""
    if defect.repository is None and len(delivery_repos) == 1:
        return delivery_repos[0], ""
    named = f"`{defect.repository}`" if defect.repository else "no repository"
    return crew_repo, (
        f"A product defect naming {named}, which is not a repository the crew delivers "
        f"to ({', '.join(delivery_repos) or 'none'}). Filed here so it is not lost; "
        "move it to where the thing it found lives."
    )


def record_retro(
    issues: IssueClient,
    sink: EventSink,
    retro: Retro,
    *,
    sprint: str,
    crew_repo: str,
    delivery_repos: list[str],
    standup: int | None = None,
) -> RetroRecord:
    """File each defect where it belongs, then the retro issue naming them all."""
    record = RetroRecord()
    routed = [route(d, crew_repo=crew_repo, delivery_repos=delivery_repos) for d in retro.defects]
    for repo in sorted({crew_repo, *(r for r, _ in routed)}):
        for name, (color, description) in _LABELS.items():
            issues.ensure_label(repo, name, color=color, description=description)

    lines: list[str] = []
    for defect, (repo, note) in zip(retro.defects, routed, strict=True):
        try:
            issue = issues.create(
                repo,
                f"{defect.subject}: {defect.problem}"[:120],
                signed(_defect_body(defect, sprint, note), ROLE),
                labels=[FINDING_LABEL],
            )
        except Exception as exc:  # noqa: BLE001
            record.failed.append((defect.subject, str(exc)[:200]))
            lines.append(f"- **Not filed** ({exc.__class__.__name__}): {defect.subject}")
            continue
        record.filed.append((repo, issue["number"]))
        ref = _ref(issues.owner, repo, issue["number"], crew_repo)
        lines.append(f"- {ref} — {defect.subject} ({defect.about})")
        sink.emit(
            CrewEvent(
                kind=EventKind.DEFECT_FILED,
                role=ROLE,
                card=issue["number"],
                summary=f"{defect.about} defect: {defect.subject}"[:100],
                detail={"repo": repo, "sprint": sprint, "about": defect.about},
            )
        )

    issue = issues.create(
        crew_repo,
        f"Retro: {sprint}",
        signed(_retro_body(retro, sprint, lines, standup), ROLE),
        labels=[RETRO_LABEL],
    )
    record.issue = issue["number"]
    sink.emit(
        CrewEvent(
            kind=EventKind.RETRO_RECORDED,
            role=ROLE,
            card=record.issue,
            summary=f"retro for {sprint}: {len(record.filed)} defects filed"[:100],
            detail={
                "repo": crew_repo,
                "sprint": sprint,
                "filed": [f"{r}#{n}" for r, n in record.filed],
                "failed": [s for s, _ in record.failed],
            },
        )
    )
    return record


def _ref(owner: str, repo: str, number: int, crew_repo: str) -> str:
    """How to link an issue from the crew repository. Another repository needs
    `owner/repo#N`; a bare `repo#N` is not a link."""
    return f"#{number}" if repo == crew_repo else f"{owner}/{repo}#{number}"


def _defect_body(defect: ProcessDefect, sprint: str, note: str) -> str:
    parts = [
        f"Found by the retro of **{sprint}**.",
        "",
        f"**Problem.** {defect.problem}",
        "",
        f"**Change.** {defect.change}",
    ]
    if note:
        parts += ["", f"> {note}"]
    return "\n".join(parts)


def _retro_body(retro: Retro, sprint: str, lines: list[str], standup: int | None) -> str:
    read = f" It read the sprint's standups, #{standup}." if standup else ""
    return "\n".join(
        [
            marker(sprint),
            f"The retro for **{sprint}**, written at sprint close.{read}",
            "",
            retro.summary,
            "",
            "## Defects filed",
            "",
            *(lines or ["None proposed."]),
        ]
    )
