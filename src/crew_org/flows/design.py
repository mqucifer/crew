"""`crew design`: the Architect proposes a project's design section (#144).

The flow decides whether a proposal may be opened; the roles only propose and
judge. A proposal is refused, and the Architect told why, when:

- a check it names isn't one CI runs today and it doesn't say so. A project
  that already has a toolchain keeps it, and a difference is stated as a
  change, with why, where the Sponsor will read it. Mechanical, from the
  project's workflows.
- the Code Reviewer finds it contradicts a guideline: crew-wide (§19) or the
  project's own. A judgement, by a role that didn't write the proposal.

It gets one retry with the reasons. A second refusal ends it with the reasons
named, and no pull request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crew_org.flows.record_pr import RecordChange, propose
from crew_org.project import RECORD_PATH, Design, ProjectRecord, brief
from crew_org.tools import ci_guard

ATTEMPTS = 2

DESIGN = RecordChange(
    issue_title="Design this project: the Architect's section of its record",
    issue_body=(
        f"The project's Architect proposes the `design` section of `{RECORD_PATH}`: its "
        "language, dependencies, sandbox needs, the commands that enforce its definition "
        "of done, and how a release happens, within the Sponsor's intent and the "
        "crew-wide guidelines.\n\nSee mqucifer/crew#144."
    ),
    branch_summary="project-design",
    commit_message="chore(design): the Architect's design for this project",
    pr_title="chore: this project's design",
    updated_by="`crew design`",
)


@dataclass
class Designed:
    """How a design proposal ended."""

    record: ProjectRecord | None = None  # the record with its design, if accepted
    proposal: Any = None
    refused: list[str] = field(default_factory=list)
    attempts: int = 0


def to_design(proposal: Any) -> Design:
    def value(choice: Any) -> str | None:
        return choice.value if choice else None

    return Design(
        language=value(proposal.language),
        dependencies=value(proposal.dependencies),
        sandbox=value(proposal.sandbox),
        checks=[c.value for c in proposal.checks],
        release_how=value(proposal.release_how),
    )


def workflows(root: Path) -> dict[str, str]:
    folder = root / ci_guard.WORKFLOWS
    if not folder.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(folder.glob("*.y*ml"))
    }


def undeclared(proposal: Any, ci: dict[str, str]) -> list[str]:
    """Checks CI doesn't run today that the proposal doesn't state as a change.

    Only judged when the project has CI: with none, every check is new and
    there is nothing existing to keep.
    """
    if not ci:
        return []
    return [
        f"`{check.value}` isn't a check CI runs today. If it should be, list it in "
        "`changes` (what, what the project does now, and why); if CI already runs it "
        "under another command, name that command instead."
        for check in proposal.checks
        if not ci_guard.enforced(check.value, ci)
        and not any(check.value in change.what for change in proposal.changes)
    ]


def design(
    record: ProjectRecord,
    *,
    repository: str,
    ci: dict[str, str],
    propose_design: Callable[..., Any],
    review_design: Callable[..., Any],
    reason: str = "",
) -> Designed:
    """The Architect's design for `record`'s project, or why it was refused."""
    project = brief(record.model_copy(update={"design": None}))
    current = (
        yaml.safe_dump(
            record.design.model_dump(exclude_none=True, exclude_defaults=True), sort_keys=False
        )
        if record.design
        else ""
    )
    feedback = ""
    ended = Designed()
    for attempt in range(1, ATTEMPTS + 1):
        ended.attempts = attempt
        proposal = propose_design(
            project=project,
            repository=repository,
            current=current,
            reason=reason,
            feedback=feedback,
        )
        ended.proposal = proposal
        reasons = undeclared(proposal, ci)
        if not reasons:
            candidate = to_design(proposal)
            shown = yaml.safe_dump(
                candidate.model_dump(exclude_none=True, exclude_defaults=True), sort_keys=False
            )
            review = review_design(project=project, design=shown)
            reasons = [f"{c.choice} contradicts {c.guideline}: {c.why}" for c in review.conflicts]
            if not reasons:
                ended.record = record.model_copy(update={"design": candidate})
                ended.refused = []
                return ended
        ended.refused = reasons
        feedback = "\n".join(f"- {r}" for r in reasons)
    return ended


def open_design_pr(
    ws: Any, issues: Any, repo: str, designed: Designed, *, base: str, reason: str = ""
) -> str:
    """Propose the accepted design to the project as a pull request. Returns its URL."""
    proposal = designed.proposal
    assert designed.record is not None, "only an accepted design is proposed"

    def line(label: str, choice: Any) -> str:
        return f"- **{label}:** {choice.value}  \n  _based on: {choice.basis}_" if choice else ""

    choices = [
        line("Language", proposal.language),
        line("Dependencies", proposal.dependencies),
        line("Sandbox", proposal.sandbox),
        *[line("Check", c) for c in proposal.checks],
        line("Release", proposal.release_how),
    ]
    changes = (
        "\n".join(f"- **{c.what}**: was {c.was}. {c.why}" for c in proposal.changes)
        if proposal.changes
        else "None. This records what the project already does."
    )

    def body(number: int) -> str:
        return (
            f"Closes #{number}\n\n"
            f"The Architect's design for this project, in the `design` section of "
            f"`{RECORD_PATH}` (mqucifer/crew#144)."
            + (f" Revisited because: {reason}" if reason else "")
            + f"\n\n{proposal.summary}\n\n## Choices\n\n"
            + "\n".join(c for c in choices if c)
            + f"\n\n## Changes from what the project does today\n\n{changes}\n\n"
            "## Verification\n\n"
            "- Every check CI doesn't already run is stated as a change above.\n"
            "- The Code Reviewer checked the design against the crew-wide guidelines "
            "(constitution §19) and the project's own, and found no conflict."
            + (f" It took {designed.attempts} proposals." if designed.attempts > 1 else "")
        )

    return propose(
        ws,
        issues,
        repo,
        designed.record,
        DESIGN,
        base=base,
        body=body,
        update_note=f"\n\n{proposal.summary}",
    )
