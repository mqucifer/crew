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

import re
from dataclasses import dataclass, field

from crew_org.crews.retro_crew import ProcessDefect, Retro
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows.artifacts import link_references, signed
from crew_org.flows.standup import STANDUP_LABEL
from crew_org.tools.github_issues import IssueClient

ROLE = "Scrum Master"
RETRO_LABEL = "retro"
FINDING_LABEL = "retro-finding"
# How much of the crew's open-issue list the retro is shown, like the standups:
# whole lines, newest first, and the omission said.
MAX_KNOWN_CHARS = 12_000
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
    # (subject, the known issue that already explains it): cited, not filed.
    explained: list[tuple[str, int]] = field(default_factory=list)


def known_issues(issues: IssueClient, crew_repo: str) -> tuple[set[int], str]:
    """The crew's open issues, as the retro is shown them: numbers, and text.

    The first live retro (#123) filed three defects whose causes were already
    filed, because it saw only symptoms (#124). Written as `crew#N`, so the
    retro copies a form that links correctly (#118). Newest first and bounded;
    the retro and standup records are not defects and are left out.
    """
    shown: set[int] = set()
    lines: list[str] = []
    spent = 0
    open_issues = issues.open_issues(crew_repo)
    for issue in open_issues:
        labels = {label.get("name") for label in issue.get("labels") or []}
        if labels & {RETRO_LABEL, STANDUP_LABEL}:
            continue
        line = f"- {crew_repo}#{issue['number']} — {issue.get('title', '')}"
        # A crew issue's own references mean crew issues. Written `crew#N`,
        # so the retro cannot copy a bare number that then links elsewhere.
        opening = re.sub(
            r"(?<![\w/#])#(\d+)\b", rf"{crew_repo}#\1", _opening(issue.get("body") or "")
        )
        if opening:
            line += f" — {opening}"
        if spent + len(line) > MAX_KNOWN_CHARS and lines:
            lines.append(f"- _{len(open_issues) - len(shown)} older issues omitted for length._")
            break
        lines.append(line)
        shown.add(issue["number"])
        spent += len(line)
    return shown, "\n".join(lines)


def _opening(body: str) -> str:
    """The first line of an issue that says something, without markup."""
    for raw in body.splitlines():
        # Emphasis and a heading's or quote's leading marks, but not the `#` of
        # a reference: "#56" stays a reference, not the number 56.
        line = re.sub(r"<!--.*?-->|[*_`]", "", raw)
        line = re.sub(r"^\s*(?:#+\s|>\s?)", "", line).strip()
        if line:
            return line[:200]
    return ""


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
    known: set[int] | frozenset[int] = frozenset(),
) -> RetroRecord:
    """File each defect where it belongs, then the retro issue naming them all.

    A defect the model says a known issue already explains is cited, not filed,
    but only if that issue is one it was actually shown: a number it invented
    must not be able to suppress a real finding.
    """
    record = RetroRecord()
    routed = [route(d, crew_repo=crew_repo, delivery_repos=delivery_repos) for d in retro.defects]
    for repo in sorted({crew_repo, *(r for r, _ in routed)}):
        for name, (color, description) in _LABELS.items():
            issues.ensure_label(repo, name, color=color, description=description)

    lines: list[str] = []
    for defect, (repo, note) in zip(retro.defects, routed, strict=True):
        if defect.explained_by is not None and defect.explained_by in known:
            record.explained.append((defect.subject, defect.explained_by))
            lines.append(
                f"- {defect.subject} — explained by {crew_repo}#{defect.explained_by}, "
                "not filed again"
            )
            continue
        checked = sorted(n for n in defect.checked_against if n in known)
        try:
            body = link_references(
                _defect_body(defect, sprint, note, checked, crew_repo),
                owner=issues.owner,
                home=repo,
                delivery=delivery_repos,
                known=[crew_repo],
            )
            issue = issues.create(
                repo,
                f"{defect.subject}: {defect.problem}"[:120],
                signed(body, ROLE),
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
        signed(
            link_references(
                _retro_body(retro, sprint, lines, standup, crew_repo),
                owner=issues.owner,
                home=crew_repo,
                delivery=delivery_repos,
            ),
            ROLE,
        ),
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
                "explained": [f"{crew_repo}#{n}" for _s, n in record.explained],
            },
        )
    )
    return record


def _ref(owner: str, repo: str, number: int, crew_repo: str) -> str:
    """How to link an issue from the crew repository. Another repository needs
    `owner/repo#N`; a bare `repo#N` is not a link."""
    # `crew#N`, not `#N`: the retro body is rewritten for the crew repository
    # and a bare number there is taken for a delivery card (#118).
    return f"{crew_repo}#{number}" if repo == crew_repo else f"{owner}/{repo}#{number}"


def _defect_body(
    defect: ProcessDefect, sprint: str, note: str, checked: list[int], crew_repo: str
) -> str:
    parts = [
        f"Found by the retro of **{sprint}**.",
        "",
        f"**Problem.** {defect.problem}",
        "",
        f"**Change.** {defect.change}",
        "",
        # So a reviewer can see it is not a duplicate of something already filed.
        "**Checked against:** "
        + (", ".join(f"{crew_repo}#{n}" for n in checked) or "no known issue named"),
    ]
    if note:
        parts += ["", f"> {note}"]
    return "\n".join(parts)


def _retro_body(
    retro: Retro, sprint: str, lines: list[str], standup: int | None, crew_repo: str
) -> str:
    read = f" It read the sprint's standups, {crew_repo}#{standup}." if standup else ""
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
