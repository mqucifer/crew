"""The gates at `done`: what a Developer session's branch passes before review (crew#778).

ADR 0032, decisions 5 and 6. A session's edits are drafts, and only `done` submits
them. Then today's checks run on the branch's diff against what's merged, where they
used to run on an answer before it was applied:

- **policy:** protected paths, a workflow made unsafe or that stops enforcing a design
  check, an invented base-image digest (`bounds.on_diff`), and workflow files the
  crew can't push;
- **scope:** merged code other work depends on, removed or reshaped (§15). The
  regression guard already compares every changed file with what's merged, so it
  runs with nothing left to apply;
- **the declared tests:** each merged test the diff changes is declared in `done`,
  and is read against the story's **Existing tests** line (decision 6);
- **the whole checks,** by the runner the caller passes: the session's container,
  once delivery runs sessions, with the services the project declares (crew#860);
- **a bug's tests** fail without the fix (crew#407);
- **no test CI runs skipped** (crew#860): one the branch adds or changes that skipped
  for a reason the project doesn't declare CI-only goes back. One already merged isn't
  the Developer's: it's DevOps's, once the project has a workspace file.

Every gate runs, and every finding goes back together, since a new session starts
from all of them (P2). A test per criterion and the docs aren't mechanical yet: review
and QA judge them (crew#790).

That the tests `done` names exist isn't a gate: `done` checks it itself, and a
session can't end with a `done` that fails it (`missing_named_tests`).
"""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.flows import delivery, role_tasks
from crew_org.flows.attempts import repair_report
from crew_org.profiles import profile_for
from crew_org.project import ProjectRecord
from crew_org.rulebook import says
from crew_org.tools import bounds, regression, reproduction, services
from crew_org.tools.ast_edit import definitions
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, check
from crew_org.work_types import BUG


@dataclass(frozen=True)
class Finding:
    """One reason the branch goes back, with the rule that decided it (ADR 0031)."""

    rule: str
    says: str


@dataclass
class Verdict:
    """What the gates found on a branch at `done`."""

    findings: list[Finding] = field(default_factory=list)
    # Merged tests the diff changes, by `path::name`, with `done`'s why. Declared and
    # named by the story: listed in the pull request, as today's declared changes are.
    declared: dict[str, str] = field(default_factory=dict)
    # For the Code Reviewer, as questions, not refusals (decision 6).
    questions: dict[str, str] = field(default_factory=dict)
    # Changed while the story says merged tests stay unchanged: the Business
    # Analyst's ruling settles them, once nothing else sends the branch back.
    disagree: dict[str, str] = field(default_factory=dict)
    # The skeleton's placeholders it replaced (crew#690), for the pull request.
    placeholders: list[str] = field(default_factory=list)
    check: CheckResult | None = None
    # An empty diff whose criteria existing tests prove: today's already-done path (crew#221).
    already_done: bool = False
    # Merged tests that skipped for a reason the project doesn't declare CI-only, by
    # `path::name`, with the reason: DevOps's to look at, not the Developer's (crew#860).
    for_devops: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.findings


def missing_named_tests(
    worktree: Path, said: dict[str, Any], *, scope: tuple[str, ...] = ()
) -> list[Finding]:
    """`done`'s one check of itself: the tests it names for criteria exist (crew#778).

    The Sponsor's decision (2026-10-18): one narrow check inside `done`, a fact and not
    a judgement. A session can't end with a `done` that fails it, so the gate doesn't
    repeat it. Whether a test proves its criterion stays with QA. The reply leads with
    writing the test: on mqucifer/sprint-metrics#662 no proving test existed under any
    name, and "name the test" first would push toward the nearest one, which proves
    nothing.

    `scope` is a role's Task's (crew#839): a file there that shows a criterion proves
    it, since the Task may change nothing else.
    """
    missing = [
        t
        for t in _named(said)
        if t
        and not delivery.names_a_test(worktree, t)
        and not (any(fnmatch.fnmatch(t, g) for g in scope) and (Path(worktree) / t).is_file())
    ]
    if not missing:
        return []
    return [
        Finding(
            "check.delivery.these_named_tests_dont_exist",
            says(
                "check.delivery.these_named_tests_dont_exist",
                "done",
                tests=", ".join(f"`{t}`" for t in dict.fromkeys(missing)),
            ),
        )
    ]


def at_done(
    worktree: Path,
    said: dict[str, Any],
    *,
    card: Card,
    story: str,
    record: ProjectRecord | None,
    run_checks: Callable[[Path], CheckResult] = check,
    permissions: dict[str, str] | None = None,
    note: Callable[[str], None] | None = None,
) -> Verdict:
    """Run every gate on the branch in `worktree`, given what `done` said.

    `said` is `done`'s statement: `criteria` [{criterion, test}], `changed_tests`
    [{test, why}], `docs` and `summary`. `note` hears what a gate couldn't check.
    That the tests it names exist is `done`'s own check, not the gate's.
    """
    verdict = Verdict()
    merged = regression.merged_base(worktree)
    paths = reproduction._changed(worktree, merged.sha) if merged is not None else []
    named = _named(said)
    if not paths:
        verdict.check = run_checks(worktree)
        if named and all(named) and verdict.check.ok:
            verdict.already_done = True
            return verdict
        verdict.findings.append(
            Finding(
                "check.gates.the_branch_changes_nothing",
                says("check.gates.the_branch_changes_nothing"),
            )
        )
        _checked(verdict)
        return verdict

    outside = bounds.on_diff(worktree, merged, paths, record) if merged is not None else []
    if outside:
        verdict.findings.append(
            Finding(
                "guard.bounds.out_of_bounds",
                says(
                    "guard.bounds.out_of_bounds",
                    reasons="\n".join(f"- {reason}" for reason in outside),
                    not_applied="",
                ),
            )
        )
    # A role's Task stays in its role's area (crew#839): anything else it needs is
    # its own finding, a bug or another Task.
    role = role_tasks.role_of(card)
    if role is not None and (beyond := role_tasks.outside_scope(role, paths)):
        verdict.findings.append(
            Finding(
                "guard.role_tasks.out_of_scope",
                says(
                    "guard.role_tasks.out_of_scope",
                    role=role.name,
                    paths=", ".join(f"`{p}`" for p in beyond),
                    scope=", ".join(f"`{g}`" for g in role.scope),
                ),
            )
        )
    if role is role_tasks.ARCHITECT and role_tasks.DIAGRAM_PATH in paths:
        path = worktree / role_tasks.DIAGRAM_PATH
        drawn = path.read_text(encoding="utf-8") if path.is_file() else None
        if wrong := role_tasks.diagram_problems(drawn):
            verdict.findings.append(
                Finding(
                    "check.role_tasks.diagram_renders",
                    says("check.role_tasks.diagram_renders", problems="; ".join(wrong)),
                )
            )
    unpushable = delivery.workflows_not_permitted(paths, permissions)
    if unpushable:
        verdict.findings.append(Finding("check.delivery.workflows_not_permitted", unpushable))

    broken = _contracts(worktree, merged)
    broken, verdict.placeholders = regression.without_placeholders(worktree, broken, merged)
    code = {key: value for key, value in broken.items() if not _is_test(key)}
    if code:
        verdict.findings.append(
            Finding("guard.regression.broken_contracts", regression.describe_contracts(code))
        )

    _declared_tests(worktree, merged, paths, said, story, verdict)
    verdict.check = run_checks(worktree)
    _checked(verdict)
    if merged is not None:
        _skipped(worktree, merged, paths, verdict)
    if card.work_type == BUG and merged is not None:
        _reproduced(worktree, merged, named, verdict, note)
    return verdict


def _named(said: dict[str, Any]) -> list[str]:
    """The tests `done` names for its criteria, one per criterion, "" where it names none."""
    return [p.get("test") or "" for p in said.get("criteria") or []]


# The pull request's section of changed merged tests the story doesn't name (decision 6).
QUESTIONS_HEADING = "## For the Code Reviewer: merged tests changed that the story doesn't name"


def pr_sections(verdict: Verdict) -> list[str]:
    """The merged tests the branch changes, in the pull request's sections (#316).

    Declared and named by the story, under the heading a rework reads back; the
    Code Reviewer's questions; and the skeleton's placeholders it replaced.
    """
    lines: list[str] = []
    if verdict.declared:
        lines += ["", delivery.DECLARED_HEADING, ""]
        lines += [f"- `{key}`: {why}" for key, why in sorted(verdict.declared.items())]
    if verdict.questions:
        lines += ["", QUESTIONS_HEADING, ""]
        lines += [f"- `{key}`: {asked}" for key, asked in sorted(verdict.questions.items())]
    if verdict.placeholders:
        lines += ["", delivery.PLACEHOLDERS_HEADING, ""]
        lines += [f"- `{key}`" for key in verdict.placeholders]
    return lines


def changed_tests(worktree: Path, merged: regression.Merged, paths: list[str]) -> dict[str, str]:
    """Each merged test the branch changes, `path::name` -> "changed" or "removed".

    A Python test is compared definition by definition, so a test whose code is the
    same once parsed hasn't changed, however a formatter laid it out. A test file in
    another language is compared whole: its tests are named, not parsed, so a change
    to it is reported by `path` with each test it no longer has.
    """
    found: dict[str, str] = {}
    for path in paths:
        profile = profile_for(path)
        if not profile.is_test_path(path):
            continue
        before = merged.text(path)
        if before is None:
            continue  # a new file: nothing merged in it to change
        target = worktree / path
        after = target.read_text(encoding="utf-8", errors="ignore") if target.is_file() else None
        if after == before:
            continue
        if path.endswith(".py"):
            was = _tests_in(before)
            now = _tests_in(after or "")
            for name, node in was.items():
                if name not in now:
                    found[f"{path}::{name}"] = "removed"
                elif ast.dump(now[name]) != ast.dump(node):
                    found[f"{path}::{name}"] = "changed"
            continue
        kept = set(profile.tests_defined(after or ""))
        for name in profile.tests_defined(before):
            if name not in kept:
                found[f"{path}::{name}"] = "removed"
        if after is not None:
            found[path] = "changed"
    return found


def merged_tests_changed(worktree: Path) -> dict[str, str]:
    """Each merged test the worktree changes as it stands, committed or not."""
    merged = regression.merged_base(worktree)
    if merged is None:
        return {}
    return changed_tests(worktree, merged, reproduction._changed(worktree, merged.sha))


def unnamed_by_story(tests: list[str], story: str) -> list[str]:
    """The merged tests in `tests` that the story's **Existing tests** line doesn't name.

    All of them when the line says merged tests stay unchanged.
    """
    named = {_as_key(t) for t in delivery.declared_contract(story)}
    return [key for key in tests if not any(_names(entry, key) for entry in named)]


def _tests_in(source: str) -> dict[str, ast.stmt]:
    """A module's test functions and methods, by dotted name (`Class.test_x`)."""
    return {
        name: node
        for name, node in definitions(source).items()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and name.rpartition(".")[2].startswith("test")
    }


def _declared_tests(
    worktree: Path,
    merged: regression.Merged | None,
    paths: list[str],
    said: dict[str, Any],
    story: str,
    verdict: Verdict,
) -> None:
    """Merged tests the diff changes, against `done`'s declaration and the story's line.

    The operator's calls inside decision 6 (crew#778, 2026-10-18): a changed test the
    story's **Existing tests** line names is fine; one a line that says the story
    changes tests doesn't name is a question for review; and a line that says merged
    tests stay unchanged, while `done` changed some, is the disagreement the Business
    Analyst rules on.
    """
    if merged is None:
        return
    why = {
        _as_key(t.get("test") or ""): t.get("why") or "" for t in said.get("changed_tests") or []
    }
    why.pop("", None)
    changes = delivery.declares_a_change(story)
    named = {_as_key(t) for t in delivery.declared_contract(story)}
    undeclared: dict[str, str] = {}
    matched: set[str] = set()
    for key, how in sorted(changed_tests(worktree, merged, paths).items()):
        declared = [entry for entry in why if _names(entry, key)]
        if not declared:
            undeclared[key] = f"`{key}` ({how})"
            continue
        matched.update(declared)
        reason = why[declared[0]]
        if changes and any(_names(entry, key) for entry in named):
            verdict.declared[key] = reason
        elif changes:
            verdict.questions[key] = (
                f"{how}, and the story's Existing tests line doesn't name it. `done` says: {reason}"
            )
        else:
            verdict.disagree[key] = reason
    for entry in sorted(set(why) - matched):
        verdict.questions[entry] = (
            f"declared changed in `done`, and the diff doesn't change it. `done` says: {why[entry]}"
        )
    if undeclared:
        # What it would put back is shown, as merged, so it needn't look for it (#316).
        shown = regression.merged_tests_source(list(undeclared), merged)
        verdict.findings.append(
            Finding(
                "check.gates.these_merged_tests_changed",
                says(
                    "check.gates.these_merged_tests_changed",
                    tests=", ".join(undeclared.values()),
                )
                + (f"\n\nAs merged:\n\n{shown}" if shown else ""),
            )
        )


def _skipped(worktree: Path, merged: regression.Merged, paths: list[str], verdict: Verdict) -> None:
    """The gate refuses a skipped test that CI runs (the Sponsor, 2026-10-21).

    What CI runs is checkable through the project's declaration: a test that skips for
    a reason its workspace file declares CI-only (Docker, say) is one CI skips too, and
    anything else would run there. One the branch causes goes back: a test it adds or
    changes, or any test in a module it changes or under a `conftest.py` it changes,
    where a `pytestmark`, a fixture or a `skipif` can skip tests it never touched. So
    does one the report can't place in a file. One already merged isn't this
    Developer's to fix: it's DevOps's, whose declaration it is. A
    project with no file can't say what's CI-only yet, so its merged skips wait for
    DevOps's first workspace Task, which covers them.
    """
    skipped = verdict.check.skipped if verdict.check is not None else None
    if not skipped:
        return
    declared = services.at_main(worktree)
    run_in_ci = {
        test: reason
        for test, reason in skipped.items()
        if not any(ci in reason for ci in declared.ci_only)
    }
    touched = added_or_changed(worktree, merged, paths)
    changed = set(paths)
    mine = {
        t: r
        for t, r in run_in_ci.items()
        if t.split("[", 1)[0] in touched or _caused(worktree, t.partition("::")[0], changed)
    }
    if mine:
        verdict.findings.append(
            Finding(
                "check.gates.these_tests_skipped",
                says(
                    "check.gates.these_tests_skipped",
                    tests="\n".join(f"- `{t}`: {r}" for t, r in sorted(mine.items())),
                ),
            )
        )
    if declared.path is not None:
        verdict.for_devops = {t: r for t, r in run_in_ci.items() if t not in mine}


def _caused(worktree: Path, path: str, changed: set[str]) -> bool:
    """Could the branch have made a test in `path` skip, outside the test itself? True
    for a path that names no file in the worktree: the gate refuses rather than hands it
    on."""
    if not path.endswith(".py") or not (worktree / path).is_file() or path in changed:
        return True
    folder = Path(path).parent
    conftests = {
        (d / "conftest.py").as_posix().removeprefix("./") for d in [folder, *folder.parents]
    }
    return bool(conftests & changed)


def added_or_changed(worktree: Path, merged: regression.Merged, paths: list[str]) -> set[str]:
    """Each Python test the branch adds or changes, as `path::Class.name`."""
    found: set[str] = set()
    for path in paths:
        if not path.endswith(".py") or not profile_for(path).is_test_path(path):
            continue
        target = worktree / path
        if not target.is_file():
            continue
        was = _tests_in(merged.text(path) or "")
        for name, node in _tests_in(target.read_text(encoding="utf-8", errors="ignore")).items():
            if name not in was or ast.dump(was[name]) != ast.dump(node):
                found.add(f"{path}::{name}")
    return found


def _contracts(worktree: Path, merged: regression.Merged | None) -> dict[str, tuple[str, str]]:
    """Merged definitions the branch removes or reshapes, as the regression guard reads them:
    every file changed since the merged commit, compared with what's merged."""
    broken = regression.draft_breaks(worktree, merged)
    broken = regression.without_moves(worktree, broken, merged)
    broken |= regression.lost_names(worktree, merged)
    return broken


def _checked(verdict: Verdict) -> None:
    """The whole checks' finding, if they failed: each cause once, every failing test named.

    Before it, each declared service that isn't running (crew#860): its tests skip or
    fail for the workspace's reason, so that's said first, never left as a skip.
    """
    if verdict.check is not None and verdict.check.services_down:
        verdict.findings.append(
            Finding(
                "check.gates.a_service_wont_start",
                says(
                    "check.gates.a_service_wont_start",
                    services="\n".join(
                        f"- `{name}`: {why}"
                        for name, why in sorted(verdict.check.services_down.items())
                    ),
                ),
            )
        )
    if verdict.check is None or verdict.check.ok:
        return
    verdict.findings.append(
        Finding(
            "check.delivery.lint_or_tests_failed",
            says(
                "check.delivery.lint_or_tests_failed",
                amended="",
                repair="",
                report=repair_report(verdict.check.failure_report),
            ),
        )
    )


def _reproduced(
    worktree: Path,
    merged: regression.Merged,
    named: list[str],
    verdict: Verdict,
    note: Callable[[str], None] | None,
) -> None:
    """A bug's criteria tests have to fail on the merged code (crew#407, B3)."""
    tests = [profile_for(t.partition("::")[0]).split_test_id(t) for t in named if t]
    if not tests:
        return
    found = reproduction.reproduces(worktree, merged, tests)
    if found.unchecked:
        if note is not None:
            note(f"reproduction not checked: {found.unchecked}")
        return
    refusal = found.refusal()
    if refusal:
        verdict.findings.append(
            Finding("guard.reproduce.check", says("guard.reproduce.check", why=refusal))
        )


def _is_test(key: str) -> bool:
    path = key.partition("::")[0]
    return bool(profile_for(path).is_test_path(path))


def _as_key(test: str) -> str:
    """A test as `done` or a story names it, as a key: `path::Class.name`, unparametrised."""
    path, sep, rest = test.strip().strip("`").partition("::")
    return f"{path}{sep}{rest.split('[', 1)[0].replace('::', '.')}"


def _names(entry: str, key: str) -> bool:
    """Does a declared `entry`, a test or a whole file, name the changed test `key`?"""
    if entry == key:
        return True
    path, _, name = key.partition("::")
    where, sep, test = entry.partition("::")
    if not sep and "/" not in where and "." not in where:
        return where in {name, name.rpartition(".")[2]}  # a bare test name
    if not (path == where or path.endswith(f"/{where}")):
        return False
    return not test or test in {name, name.rpartition(".")[2]}
