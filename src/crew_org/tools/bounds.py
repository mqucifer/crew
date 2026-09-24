"""Whether an implementation stays inside what the project's record allows (#131).

Checked before anything is written, like the overwrite and contract checks: a
change to a protected path, or one that stops CI enforcing a check, is refused
with the path or the check named, and the Developer is told why.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.project import RECORD_PATH, ProjectRecord, is_protected, protected
from crew_org.tools import ci_guard


def touched(implementation) -> list[str]:
    """Every path an implementation would write, in the order it names them."""
    paths = [f.path for f in implementation.new_files]
    paths += [e.path for e in implementation.edits]
    paths += [t.path for t in implementation.text_edits]
    return list(dict.fromkeys(paths))


def out_of_bounds(worktree: Path, implementation, record: ProjectRecord | None) -> list[str]:
    """Why this implementation may not be applied, one line per reason. Empty if it may."""
    rules = protected(record)
    reasons = [
        f"`{path}` is protected by the project's record (`{rule}`"
        + (" is the record itself" if rule == RECORD_PATH else " is under never_touch")
        + f" in {RECORD_PATH}). Leave it unchanged."
        for path in touched(implementation)
        if (rule := is_protected(path, rules))
    ]

    checks = record.design.checks if record and record.design else []
    if checks and any(ci_guard.is_workflow(p) for p in touched(implementation)):
        before, after = _workflows(worktree, implementation)
        reasons += [
            f"CI would no longer enforce `{check}`, a check the project's design requires "
            f"({RECORD_PATH}). Workflows may change, but every design check must still run "
            "there and still be able to fail: no `|| true`, no `continue-on-error`."
            for check in ci_guard.weakened(checks, before, after)
        ]
    return reasons


def _workflows(worktree: Path, implementation) -> tuple[dict[str, str], dict[str, str]]:
    from crew_org.tools.ast_edit import EditError  # noqa: PLC0415
    from crew_org.tools.workspace import plan_text_edits  # noqa: PLC0415

    root = worktree.resolve()
    folder = root / ci_guard.WORKFLOWS
    before = (
        {
            str(path.relative_to(root)): path.read_text(encoding="utf-8")
            for path in sorted(folder.glob("*.y*ml"))
        }
        if folder.exists()
        else {}
    )
    after = dict(before)
    after.update(
        {f.path: f.content for f in implementation.new_files if ci_guard.is_workflow(f.path)}
    )
    try:
        planned = plan_text_edits(worktree, implementation.text_edits)
    except EditError:
        planned = {}  # applying reports it; this check judges only what would apply
    after.update({p: t for p, t in planned.items() if ci_guard.is_workflow(p)})
    return before, after
