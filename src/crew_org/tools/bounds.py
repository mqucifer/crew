"""Whether a branch stays inside what the project's record allows (#131).

Checked by the gates at `done` (crew#778), like the contract check: a change to a
protected path, or one that stops CI enforcing a check, sends the branch back with
the path or the check named, and the next session is told why.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from crew_org.project import RECORD_PATH, ProjectRecord, is_protected, protected
from crew_org.rulebook import says
from crew_org.tools import ci_guard
from crew_org.tools.regression import Merged


def on_diff(
    worktree: Path, merged: Merged, paths: list[str], record: ProjectRecord | None
) -> list[str]:
    """Why the branch's diff against what's merged is out of bounds (crew#778, ADR 0032).

    A session's edits are already in the worktree, so each path as it stands is
    its after, and the merged commit is its before.
    """
    from crew_org.tools import base_images  # noqa: PLC0415

    workflows: tuple[dict[str, str], dict[str, str]] = ({}, {})
    if any(ci_guard.is_workflow(p) for p in paths):
        listed = subprocess.run(
            ["git", "ls-tree", "--name-only", merged.sha, ci_guard.WORKFLOWS],
            cwd=worktree,
            capture_output=True,
            text=True,
        ).stdout.split()
        before = {p: text for p in listed if (text := merged.text(p)) is not None}
        workflows = (before, _workflows_now(worktree))
    dockerfiles = {
        p: (worktree / p).read_text(encoding="utf-8")
        for p in paths
        if base_images.is_dockerfile(p) and (worktree / p).is_file()
    }
    return judged(paths, record, workflows=workflows, dockerfiles=dockerfiles)


def judged(
    paths: list[str],
    record: ProjectRecord | None,
    *,
    workflows: tuple[dict[str, str], dict[str, str]],
    dockerfiles: dict[str, str],
) -> list[str]:
    """Why a change to `paths` is out of bounds, one line per reason.

    `workflows` is every workflow file before and after the change, and
    `dockerfiles` what each changed Dockerfile holds after it.
    """
    rules = protected(record)
    reasons = [
        says(
            "guard.bounds.out_of_bounds",
            "protected",
            path=path,
            protected_by=rule,
            why=" is the record itself" if rule == RECORD_PATH else " is under never_touch",
            record=RECORD_PATH,
        )
        for path in paths
        if (rule := is_protected(path, rules))
    ]

    before, after = workflows
    changed = [p for p in paths if ci_guard.is_workflow(p)]
    if changed:
        # A crew-written workflow doesn't get what a pull request's code
        # shouldn't have (#279): secrets on untrusted code, broad writes, or
        # secrets the design doesn't name.
        allowed = set(record.design.secrets) if record and record.design else set()
        writes = ci_guard.RELEASE_WRITES
        if record is not None and record.intent.release.to_pages:
            writes = writes | ci_guard.PAGES_WRITES
        for path in changed:
            if path in after and after[path] != before.get(path):
                reasons += ci_guard.unsafe(path, after[path], allowed, writes)
        # A workflow may change with the code it runs (Sponsor, 2026-09-29,
        # crew#335). "A CI change travels alone" was so a review saw it for what
        # it was; the deploy review now judges every pull request touching one.
        # Alone, sprint-metrics#307 couldn't be delivered at all: an ENTRYPOINT
        # breaks the smoke job and the Docker tests that invoke the image, so
        # all three change together or the build is red between them.
    # A base image pinned by a digest its registry doesn't have was worked out,
    # not read: the sandbox can't read a registry (#364).
    from crew_org.tools import base_images  # noqa: PLC0415

    for path, text in dockerfiles.items():
        reasons += base_images.invented(path, text)

    checks = record.design.checks if record and record.design else []
    if checks and changed:
        reasons += [
            says("guard.bounds.out_of_bounds", "weakened", check=check, record=RECORD_PATH)
            for check in ci_guard.weakened(checks, before, after)
        ]
    return reasons


def _workflows_now(worktree: Path) -> dict[str, str]:
    """Every workflow file as the worktree holds it."""
    root = worktree.resolve()
    folder = root / ci_guard.WORKFLOWS
    if not folder.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(folder.glob("*.y*ml"))
    }
