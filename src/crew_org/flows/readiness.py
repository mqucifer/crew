"""Whether a repository is ready for the crew to work, and what is missing (crew#690).

It replaces the one precondition the crew had, a usable record (#132). Two levels
(ADR 0030), because some items can't pass before the project's walking skeleton
exists: CI can't be green with no code, and branch protection can't require a
check that has never run.

- **Level 1, ready for the skeleton:** the repository and the crew's access, the
  settings the crew's code relies on, `board.yml` and CI on pull requests (the
  template's `checks.yml`, or the project's own), a complete record, a merged
  design, and the deploy target when a release is a deployment. Only the
  skeleton Goal is worked.
- **Level 2, ready for any Goal:** CI has run green on the default branch, branch
  protection requires a check, and the lockfile the design names is there.

Below level 1 the repository isn't worked at all. Each missing item says who
fixes it: the Sponsor creates repositories and their rules, which the crew's App
has no admin for (crew#566), and the crew writes the record, design and skeleton.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crew_org.flows.board_moves import BOARD_WORKFLOW
from crew_org.flows.template import CHECKS_WORKFLOW
from crew_org.project import (
    RECORD_PATH,
    ProjectRecord,
    ProjectRecordError,
    parse,
    read_record,
)

# On the skeleton Goal: the one Goal worked below level 2 (ADR 0030).
SKELETON_LABEL = "skeleton"

SPONSOR = "the Sponsor"
CREW = "the crew"

# The settings the crew's code relies on, each with why (ADR 0030).
SETTINGS = (
    ("has_issues", "Issues are off: the board and every role work on issues"),
    ("allow_squash_merge", "Squash merging is off: the crew merges every pull request by squash"),
    (
        "delete_branch_on_merge",
        "Delete branch on merge is off: the crew never deletes its own branches",
    ),
)

# A release is a deployment to GitHub Pages when its target says so.
_PAGES = ("github.io", "github pages")


@dataclass(frozen=True)
class Missing:
    what: str
    who: str


@dataclass
class Readiness:
    repo: str
    level: int = 0
    missing: list[Missing] = field(default_factory=list)
    # What the crew's token can't read, said rather than guessed.
    unchecked: list[str] = field(default_factory=list)

    def why(self) -> str:
        """What is missing for the next level, for the standup."""
        return "; ".join(f"{m.what} ({m.who})" for m in self.missing)


def check(issues: Any, ws: Any, repo: str) -> Readiness:
    """How far `repo` is ready, and what keeps it from the next level."""
    found = Readiness(repo)
    try:
        repository = issues.repository(repo)
    except Exception as exc:  # noqa: BLE001
        found.missing.append(
            Missing(
                f"the crew can't read the repository ({exc}); give the crew's Apps access", SPONSOR
            )
        )
        return found
    if not issues.branches(repo):
        found.missing.append(Missing("the repository has no commits; push a first one", SPONSOR))
        return found
    branch = repository.get("default_branch") or "main"

    level_one: list[Missing] = [
        Missing(why, SPONSOR) for key, why in SETTINGS if repository.get(key) is not True
    ]
    found.unchecked.append(
        "Actions on: the crew's App can't read the Actions settings; CI running green proves it"
    )
    if issues.file_at(repo, f".github/workflows/{BOARD_WORKFLOW}", branch) is None:
        level_one.append(
            Missing(f"no `.github/workflows/{BOARD_WORKFLOW}`; `crew onboard {repo}` adds it", CREW)
        )
    ci = [n for n in issues.list_dir(repo, ".github/workflows", branch) if n != BOARD_WORKFLOW]
    if not ci:
        level_one.append(
            Missing(
                f"no CI on pull requests; `crew onboard {repo}` adds the template's "
                f"`{CHECKS_WORKFLOW}`",
                CREW,
            )
        )

    record: ProjectRecord | None = None
    try:
        record = read_record(ws.for_repo(repo).current())
    except ProjectRecordError as exc:
        level_one.append(
            Missing(f"its record is unusable: {exc}; finish it with `crew onboard {repo}`", SPONSOR)
        )
    except Exception as exc:  # noqa: BLE001
        level_one.append(Missing(f"its record couldn't be read: {exc}", CREW))
    else:
        if record is None:
            level_one.append(
                Missing(
                    f"it has no `{RECORD_PATH}`; onboard it with `crew onboard {repo}`", SPONSOR
                )
            )
        elif record.design is None or not record.design.checks:
            level_one.append(
                Missing(f"it has no design; `crew design {repo}`, and the Sponsor merges it", CREW)
            )
    if record is not None:
        level_one += _deploy_target(issues, repo, record)

    if level_one:
        found.missing = level_one
        return found
    found.level = 1

    assert record is not None and record.design is not None
    level_two: list[Missing] = []
    green = (
        run
        for run in issues.green_runs(repo, branch)
        if not (run.get("path") or "").endswith(f"/{BOARD_WORKFLOW}")
    )
    if not any(_ran_checks(issues, repo, run) for run in green):
        level_two.append(
            Missing(
                f"CI hasn't run green on `{branch}`: the skeleton Goal lands every check, "
                "and CI running them",
                CREW,
            )
        )
    protection = issues.branch_protection(repo, branch) or {}
    required = (protection.get("required_status_checks") or {}).get("contexts") or []
    if not required:
        level_two.append(
            Missing(
                f"branch protection on `{branch}` requires no check; once CI is green, "
                f"`scripts/new-project.sh {repo} --protect`",
                SPONSOR,
            )
        )
    lockfile = record.design.lockfile or (None if record.design.sandbox_image else "uv.lock")
    if lockfile is None:
        level_two.append(
            Missing(f"the design names no lockfile; `crew design {repo} --reason` to name it", CREW)
        )
    elif issues.file_at(repo, lockfile, branch) is None:
        level_two.append(Missing(f"the lockfile `{lockfile}` isn't on `{branch}`", CREW))

    if level_two:
        found.missing = level_two
        return found
    found.level = 2
    return found


def _deploy_target(issues: Any, repo: str, record: ProjectRecord) -> list[Missing]:
    """The deploy target, where the crew can read it: GitHub Pages today."""
    release = record.intent.release
    where = (release.where or "").lower()
    if not release.deploys or not any(p in where for p in _PAGES):
        return []
    pages = issues.pages(repo)
    if pages is None:
        return [
            Missing(
                "GitHub Pages isn't enabled; enable it with GitHub Actions as its source", SPONSOR
            )
        ]
    if pages.get("build_type") != "workflow":
        return [
            Missing("GitHub Pages builds from a branch; set its source to GitHub Actions", SPONSOR)
        ]
    return []


def _ran_checks(issues: Any, repo: str, run: dict[str, Any]) -> bool:
    """Whether a green run ran the design's checks.

    The template's `checks.yml` passes with nothing to check before the design is
    merged, so its green on an empty repository proves nothing (ADR 0030).
    """
    if not (run.get("path") or "").endswith(f"/{CHECKS_WORKFLOW}"):
        return True
    text = issues.file_at(repo, RECORD_PATH, run.get("head_sha") or "")
    try:
        record = parse(text) if text else None
    except ProjectRecordError:
        return False
    return bool(record and record.design and record.design.checks)
