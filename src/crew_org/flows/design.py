"""`crew design`: the Architect proposes a project's design section (#144).

The flow decides whether a proposal may be opened; the roles only propose and
judge. A proposal is refused, and the Architect told why, when:

- a check it names isn't one CI runs today and it doesn't say so. A project
  that already has a toolchain keeps it, and a difference is stated as a
  change, with why, where the Sponsor will read it. Mechanical, from the
  project's workflows.
- the Code Reviewer finds it contradicts a guideline: crew-wide (§19) or the
  project's own. A judgement, by a role that didn't write the proposal.

Each check gives one retry with its reasons. A second refusal by the same check
ends it with the reasons named, and no pull request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crew_org.flows.board_moves import BOARD_WORKFLOW
from crew_org.flows.record_pr import RecordChange, propose
from crew_org.project import RECORD_PATH, Design, Part, ProjectRecord, brief
from crew_org.tools import ci_guard

ATTEMPTS = 2

# On a design pull request: its declared changes, so the crew can turn the ones
# that need work into the right place once it merges, whoever merged it (#192, crew#439).
CHANGES_MARKER = "<!-- crew:design-changes {} -->"
_CHANGES = re.compile(r"<!-- crew:design-changes (.*?) -->", re.DOTALL)
# On the issue a revisit's pull request closes, and on a revisit that changed
# nothing: when the Architect last looked, so evidence already weighed isn't
# counted again.
REVISIT_LABEL = "design:revisit"
# On a revisit's pull request: the crew merges it, not the Sponsor.
REVISIT_MARKER = "<!-- crew:design-revisit -->"
# Every design pull request opens with this, by hand or by revisit (#144).
DESIGN_PR = "The Architect's design for this project"

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
    by="Architect",
)

REVISIT = RecordChange(
    issue_title="Revisit this project's design",
    issue_body=(
        "The Architect revisits the `design` section of `.crew/project.yaml` because "
        "delivery shows the design under strain. The crew reviews and merges the "
        "revision itself: how the project is built is the Architect's call, and what "
        "it is for stays the Sponsor's.\n\nSee mqucifer/crew#192."
    ),
    branch_summary="design-revisit",
    commit_message="chore(design): the Architect revisits this project's design",
    pr_title="chore: revisit this project's design",
    updated_by="the Architect's revisit",
    by="Architect",
    labels=(REVISIT_LABEL,),
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
        sandbox_image=value(getattr(proposal, "sandbox_image", None)),
        setup=value(getattr(proposal, "setup", None)),
        autofix=[c.value for c in getattr(proposal, "autofix", None) or []],
        parts=[
            Part(path=p.path, language=p.language, tests=list(p.tests))
            for p in getattr(proposal, "parts", None) or []
        ],
        ci_checks=[c.value for c in getattr(proposal, "ci_checks", None) or []],
        release_how=value(proposal.release_how),
        structure=value(getattr(proposal, "structure", None)),
        docs=doc_paths(getattr(proposal, "docs", None)),
    )


def doc_paths(choice: Any) -> list[str]:
    """The paths in the Architect's docs choice, which names them among its prose."""
    if not choice:
        return []
    return list(dict.fromkeys(re.findall(r"[\w./-]+\.(?:md|rst|txt)\b", choice.value)))


def workflows(root: Path) -> dict[str, str]:
    """The project's CI workflows: what it runs today.

    Not `board.yml`, the board automation onboarding installs. It runs no
    checks, and counted as CI it made crew-presentation, a repository with no
    code, keep a toolchain it didn't have: two designs refused (crew#516).
    """
    folder = root / ci_guard.WORKFLOWS
    if not folder.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(folder.glob("*.y*ml"))
        if path.name != BOARD_WORKFLOW
    }


# Run by Node, which the crew's default sandbox (Python and uv) doesn't have.
NODE_TOOLS = frozenset({"npx", "npm", "node", "pnpm", "yarn"})


def needs_an_image(proposal: Any) -> list[str]:
    """Why a design that needs more than Python must name its sandbox image (crew#516).

    Left empty, a project is built and tested in the crew's Python image. A
    crew-presentation proposal named `npx playwright test` and a JavaScript part
    with no image, so its first story's checks would have found no Node.
    """
    if getattr(proposal, "sandbox_image", None) is not None:
        return []
    needs = [
        f"the check `{c.value}`"
        for c in proposal.checks
        if c.value.split() and c.value.split()[0] in NODE_TOOLS
    ] + [
        f"the {p.language} part `{p.path or '(the whole project)'}`"
        for p in getattr(proposal, "parts", None) or []
        if p.language.strip().lower() != "python"
    ]
    if not needs:
        return []
    return [
        f"{', '.join(needs)} needs more than the crew's default sandbox, which has only "
        "Python and uv. Name the image they run in, with every tool the checks need, in "
        "`sandbox_image` (registry/name:tag), and its install command in `setup`."
    ]


def undeclared(proposal: Any, ci: dict[str, str], ci_checks_now: list[str] = ()) -> list[str]:
    """Checks CI doesn't run today that the proposal doesn't state as a change.

    Only judged when the project has CI: with none, every check is new and
    there is nothing existing to keep. A CI proof the record doesn't already
    list is new work whether or not there is CI, so it needs a change that
    says so: otherwise the record claims CI proves something it doesn't (#335).
    """
    reasons = []
    new_proofs = [
        c.value for c in getattr(proposal, "ci_checks", None) or [] if c.value not in ci_checks_now
    ]
    if new_proofs and not any(change.needs_work for change in proposal.changes):
        reasons += [
            f"`{proof}` is a new CI proof, and no change says the work that makes CI "
            "prove it. List it in `changes` with `needs_work` and the work."
            for proof in new_proofs
        ]
    if not ci:
        return reasons
    return reasons + [
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
    # Counted per check (crew#513). Shared, a mechanical refusal spent the only
    # retry: crew-presentation's second proposal fixed it, and the review's five
    # findings ended the run without the Architect ever seeing them.
    refusals = {"mechanical": 0, "review": 0}
    while max(refusals.values()) < ATTEMPTS:
        ended.attempts += 1
        proposal = propose_design(
            project=project,
            repository=repository,
            current=current,
            reason=reason,
            feedback=feedback,
        )
        ended.proposal = proposal
        reasons = undeclared(
            proposal, ci, record.design.ci_checks if record.design is not None else []
        )
        reasons += needs_an_image(proposal)
        # The sandbox image is real and pinned: a guessed digest runs nothing (#403, #365).
        # The Architect names a tag, and the digest is read from the registry.
        pinned, unpinnable = pin_image(getattr(proposal, "sandbox_image", None))
        if pinned is not None:
            proposal.sandbox_image = pinned
        reasons += unpinnable or image_problems(pinned)
        if reasons:
            refusals["mechanical"] += 1
        else:
            candidate = to_design(proposal)
            shown = yaml.safe_dump(
                candidate.model_dump(exclude_none=True, exclude_defaults=True), sort_keys=False
            )
            declared = "\n".join(f"- {c.what} (was: {c.was}): {c.why}" for c in proposal.changes)
            review = review_design(
                project=project, design=shown, repository=repository, changes=declared
            )
            reasons = [f"{c.choice} contradicts {c.guideline}: {c.why}" for c in review.conflicts]
            # A new practice not declared as one (#154): declare it, with why.
            reasons += [
                f"{u.choice} is a new practice (the project does this today: {u.today}). "
                "List it in `changes`: what, what the project does now, and why."
                for u in getattr(review, "undeclared", [])
            ]
            if not reasons:
                ended.record = record.model_copy(update={"design": candidate})
                ended.refused = []
                return ended
            refusals["review"] += 1
        ended.refused = reasons
        feedback = "\n".join(f"- {r}" for r in reasons)
    return ended


def pin_image(choice: Any) -> tuple[Any, list[str]]:
    """The image the Architect named, pinned to the digest its tag names today (#404).

    A model can name a real tag; a digest it writes is one it made up. On the first
    live design of the static-site fixture with no design recorded, the Architect
    named `mcr.microsoft.com/playwright:v1.63.0-jammy` with a note in place of a
    digest, and a refusal asking for one invites an invention. The registry is
    asked instead. The reference is the value's first word: anything after it is
    commentary, and the record keeps the reference alone.
    """
    from crew_org.tools import base_images  # noqa: PLC0415

    words = (getattr(choice, "value", "") or "").split() if choice is not None else []
    ref = base_images.parse(words[0]) if words else None
    if ref is None:
        return choice, []
    if ref.digest:
        return choice.model_copy(update={"value": words[0]}), []
    try:
        digest = base_images.lookup(ref.host, ref.name, ref.tag)
    except base_images.Unreadable as exc:
        return choice, [f"the sandbox image `{ref.shown}` can't be pinned: {exc}"]
    if digest is None:
        return choice, [f"the sandbox image `{ref.shown}` doesn't exist: no such tag in {ref.host}"]
    return choice.model_copy(update={"value": f"{ref.shown}@{digest}"}), []


def image_problems(choice: Any) -> list[str]:
    """Why a proposed sandbox image can't be used, or nothing (#403)."""
    from crew_org.tools import base_images  # noqa: PLC0415

    if choice is None or not getattr(choice, "value", ""):
        return []
    ref = base_images.parse(choice.value.strip())
    if ref is None or not ref.digest:
        return [f"the sandbox image `{choice.value}` isn't pinned by digest (name@sha256:...)"]
    try:
        found = base_images.lookup(ref.host, ref.name, ref.digest)
    except base_images.Unreadable as exc:
        return [f"the sandbox image `{choice.value}` can't be verified: {exc}"]
    if found is None:
        return [f"the sandbox image `{choice.value}` doesn't exist: no such digest in {ref.host}"]
    return []


def changes_block(proposal: Any) -> str:
    """The declared changes, readable back from the pull request once it merges."""
    changes = [
        {
            "what": c.what,
            "was": c.was,
            "why": c.why,
            "needs_work": bool(c.needs_work),
            "work": getattr(c, "work", None),
        }
        for c in proposal.changes
    ]
    # `>` escaped so nothing in a change can close the comment early.
    return CHANGES_MARKER.format(json.dumps(changes).replace(">", "\\u003e"))


def declared_changes(body: str) -> list[dict[str, Any]]:
    """The changes a design pull request declared, or [] for one that has none."""
    match = _CHANGES.search(body or "")
    if match is None:
        return []
    try:
        changes = json.loads(match.group(1))
    except ValueError:
        return []
    return [c for c in changes if isinstance(c, dict) and c.get("what")]


def open_design_pr(
    ws: Any,
    issues: Any,
    repo: str,
    designed: Designed,
    *,
    base: str,
    reason: str = "",
    revisit: bool = False,
) -> str:
    """Propose the accepted design to the project as a pull request. Returns its URL.

    A revisit (#192) is merged by the crew once CI passes; a design run by hand
    with `crew design` is the Sponsor's to merge.
    """
    proposal = designed.proposal
    assert designed.record is not None, "only an accepted design is proposed"

    def line(label: str, choice: Any) -> str:
        return f"- **{label}:** {choice.value}  \n  _based on: {choice.basis}_" if choice else ""

    choices = [
        line("Language", proposal.language),
        line("Dependencies", proposal.dependencies),
        line("Sandbox", proposal.sandbox),
        line("Sandbox image", getattr(proposal, "sandbox_image", None)),
        line("Setup", getattr(proposal, "setup", None)),
        *[line("Autofix", c) for c in getattr(proposal, "autofix", None) or []],
        *[
            f"- **Part:** `{p.path or '(the whole project)'}` in {p.language}, tests "
            + (", ".join(f"`{t}`" for t in p.tests) or "none declared")
            + f"  _based on: {p.basis}_"
            for p in getattr(proposal, "parts", None) or []
        ],
        *[line("Check", c) for c in proposal.checks],
        *[line("CI proves", c) for c in getattr(proposal, "ci_checks", None) or []],
        line("Release", proposal.release_how),
        line("Structure", getattr(proposal, "structure", None)),
        line("User docs", getattr(proposal, "docs", None)),
    ]
    changes = (
        "\n".join(
            f"- **{c.what}**: was {c.was}. {c.why}"
            + (f"  \n  _The work:_ {c.work}" if getattr(c, "work", None) else "")
            for c in proposal.changes
        )
        if proposal.changes
        else "None. This records what the project already does."
    )

    def body(number: int) -> str:
        return (
            f"Closes #{number}\n\n"
            f"{DESIGN_PR}, in the `design` section of "
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
            + (
                "\n- The crew merges this itself once CI passes (mqucifer/crew#192). Each "
                "change above that needs work goes to the epic that delivers it, or becomes a "
                "technical epic when no epic does."
                if revisit
                else ""
            )
            + f"\n\n{changes_block(proposal)}"
            + (f"\n{REVISIT_MARKER}" if revisit else "")
        )

    return propose(
        ws,
        issues,
        repo,
        designed.record,
        REVISIT if revisit else DESIGN,
        base=base,
        body=body,
        update_note=f"\n\n{proposal.summary}",
    )
