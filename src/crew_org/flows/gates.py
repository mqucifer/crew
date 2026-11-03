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
- **the changelog entry** (crew#816): a story or a bug that changes what the project
  ships adds a line under `## [Unreleased]`, since a release ships what it holds.

Every gate runs, and every finding goes back together, since a new session starts
from all of them (P2). A test per criterion and the docs aren't mechanical yet: review
and QA judge them (crew#790).

That the tests `done` names exist isn't a gate: `done` checks it itself, and a
session can't end with a `done` that fails it (`missing_named_tests`).
"""

from __future__ import annotations

import ast
import difflib
import fnmatch
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.flows import delivery, role_tasks
from crew_org.flows.attempts import repair_report
from crew_org.profiles import _TITLE, profile_for
from crew_org.project import ProjectRecord
from crew_org.rulebook import says
from crew_org.session.tools import ACCEPTED
from crew_org.tools import bounds, regression, reproduction, services
from crew_org.tools.ast_edit import definitions
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, check, shown_test
from crew_org.work_types import BUG, STORY


@dataclass(frozen=True)
class Finding:
    """One reason the branch goes back, with the rule that decided it (ADR 0031)."""

    rule: str
    says: str
    # How many things it names, where the retro counts them (crew#839's tests at `done`).
    count: int = 0


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
    # Merged tests a workspace Task's services turn on that fail, where the branch caused
    # none of them: the Developer's bug once it merges, not the Task's (crew#839).
    for_developer: dict[str, str] = field(default_factory=dict)
    # A bug fixed in its tests alone: the merged tests it changes or removes, for the
    # Code Reviewer to judge that the defect is theirs (crew#1005).
    fixed_in_tests: list[str] = field(default_factory=list)

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
    recorded: role_tasks.TurnedOnRecord | None = None,
) -> Verdict:
    """Run every gate on the branch in `worktree`, given what `done` said.

    `said` is `done`'s statement: `criteria` [{criterion, test}], `changed_tests`
    [{test, why}], `docs` and `summary`. `note` hears what a gate couldn't check.
    `recorded` is a bug's own record, when the crew filed it from the tests a workspace
    turned on (`role_tasks.turned_on_record`).
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
    if role is not None and (beyond := role_tasks.outside_scope(role, paths, card.repo or "")):
        verdict.findings.append(
            Finding(
                "guard.role_tasks.out_of_scope",
                says(
                    "guard.role_tasks.out_of_scope",
                    role=role.name,
                    paths=", ".join(f"`{p}`" for p in beyond),
                    scope=", ".join(f"`{g}`" for g in role.scope_for(card.repo or "")),
                ),
            )
        )
    if (
        role is role_tasks.ARCHITECT
        and role_tasks.DIAGRAM_PATH in paths
        and (wrong := role_tasks.diagram_problems_in(worktree))
    ):
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
    verdict.findings += no_changelog_entry(worktree, card)
    if merged is not None:
        verdict.findings += reads_only_unreleased(worktree, merged, paths)
    verdict.check = run_checks(worktree)
    if merged is not None and role_tasks.changes_workspace(role, worktree, paths):
        _turned_on(worktree, merged, paths, verdict)
        # Ignored by the start, so only here does a field no specification defines stop
        # it: `setupCommands` was read as nothing, 8 rounds running (crew#839).
        if unknown := role_tasks.unknown_fields(worktree):
            verdict.findings.append(
                Finding(
                    "check.role_tasks.workspace_checks",
                    says(
                        "check.role_tasks.workspace_checks",
                        problems="\n".join(f"- {p}" for p in unknown),
                    ),
                )
            )
    _checked(verdict)
    if merged is not None:
        _skipped(worktree, merged, paths, verdict)
    if card.work_type == BUG and merged is not None:
        name = f"reproduce-{card.repo or ''}-{card.number}"
        if recorded is not None and recorded.tests:
            _as_recorded(worktree, recorded, name, verdict)
        else:
            missing = recorded.missing if recorded is not None else ""
            _reproduced(
                worktree, merged, named, verdict, note, name=name, missing=missing, paths=paths
            )
    return verdict


def entries(said: dict[str, Any], key: str) -> list[dict[str, str]]:
    """`done`'s list `key` in its schema's shape: objects, each with its fields as strings.

    Anything else is left out, so a reader finds it missing, never an exception: on
    mqucifer/sprint-metrics#820, `changed_tests` came as one object, and walking its keys
    blocked the card (crew#926). `done`'s reply refuses that form; this is for the rest.
    """
    fields = ACCEPTED["done"]["properties"][key]["items"]["properties"]
    found = said.get(key)
    return [
        {f: v for f, v in item.items() if f in fields and isinstance(v, str)}
        for item in (found if isinstance(found, list) else [])
        if isinstance(item, dict)
    ]


def text(said: dict[str, Any], key: str) -> str:
    """`done`'s string `key`, or "" for anything else (crew#926)."""
    found = said.get(key)
    return found if isinstance(found, str) else ""


def _named(said: dict[str, Any]) -> list[str]:
    """The tests `done` names for its criteria, one per criterion, "" where it names none."""
    return [p.get("test") or "" for p in entries(said, "criteria")]


# The pull request's section of changed merged tests the story doesn't name (decision 6).
QUESTIONS_HEADING = "## For the Code Reviewer: merged tests changed that the story doesn't name"
# A bug's fix that changes only tests, which nothing failing on main proved (crew#1005).
FIXED_IN_TESTS_HEADING = "## For the Code Reviewer: a bug fixed in its tests"


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
    if verdict.for_developer:
        lines += ["", role_tasks.TURNED_ON_HEADING, ""]
        lines += [
            "Not this Task's: filed for the Developer as one bug when this merges.",
            "",
        ]
        lines += [f"- `{key}`: {line}" for key, line in sorted(verdict.for_developer.items())]
    if verdict.fixed_in_tests:
        lines += [
            "",
            FIXED_IN_TESTS_HEADING,
            "",
            "This bug's fix changes only tests, so no test of it failed on main, as a "
            "product bug's has to. The gates held that its named tests pass and that each "
            "merged test it keeps still makes every check it made, as it was or rewritten. "
            "Judge whether the defect is in these tests, as the report says, and not in the "
            "code they test, and whether a rewritten check still checks what it did:",
            "",
        ]
        lines += [f"- `{key}`" for key in verdict.fixed_in_tests]
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
    why = _declared_why(said)
    changes = delivery.declares_a_change(story)
    named = {_as_key(t) for t in delivery.declared_contract(story)}
    changed = changed_tests(worktree, merged, paths)
    undeclared = {key: f"`{key}` ({how})" for key, how in _undeclared(changed, why).items()}
    matched: set[str] = set()
    for key, how in sorted(changed.items()):
        if key in undeclared:
            continue
        declared = [entry for entry in why if _names(entry, key)]
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


def _declared_why(said: dict[str, Any]) -> dict[str, str]:
    """`done`'s changed tests, by key, each with its why. One with no why isn't declared."""
    why = {
        _as_key(t.get("test") or ""): (t.get("why") or "").strip()
        for t in entries(said, "changed_tests")
    }
    return {key: reason for key, reason in why.items() if key and reason}


def _undeclared(changed: dict[str, str], why: dict[str, str]) -> dict[str, str]:
    """The changed merged tests no declared entry names, `key` -> "changed" or "removed".

    The gate's check and `done`'s own both ask this, so a `done` that passes its own
    passes the gate's (crew#839).
    """
    return {
        key: how
        for key, how in sorted(changed.items())
        if not any(_names(entry, key) for entry in why)
    }


def undeclared_changed_tests(worktree: Path, said: dict[str, Any]) -> list[Finding]:
    """`done`'s check of itself: each merged test the branch changed is declared (crew#839).

    The gate's own computation, asked before the gate runs, so a session declares
    them or puts them back without spending a gate round. The gate still checks.
    """
    merged = regression.merged_base(worktree)
    if merged is None:
        return []
    changed = changed_tests(worktree, merged, reproduction._changed(worktree, merged.sha))
    undeclared = _undeclared(changed, _declared_why(said))
    if not undeclared:
        return []
    return [
        Finding(
            "check.gates.these_merged_tests_changed",
            says(
                "check.gates.these_merged_tests_changed",
                "done",
                tests=", ".join(f"`{key}` ({how})" for key, how in undeclared.items()),
            ),
            count=len(undeclared),
        )
    ]


def one_outcome(worktree: Path, said: dict[str, Any]) -> list[Finding]:
    """`done`'s check of itself on QA's Task: `justified` and `no_test` change nothing, so
    one with a changed file, or a test named in `criteria` or `changed_tests`, says two
    outcomes (crew#944). Session 2 of mqucifer/qa-suite#8 sent `justified` with a changed
    test; asked at the reply, the session says which it means."""
    ending = next(
        (k for k in (role_tasks.JUSTIFIED, role_tasks.NO_TEST) if str(said.get(k) or "").strip()),
        None,
    )
    if ending is None:
        return []
    merged = regression.merged_base(worktree)
    paths = reproduction._changed(worktree, merged.sha) if merged is not None else []
    named = [e.get("test") or "" for e in entries(said, "criteria")] + [
        e.get("test") or "" for e in entries(said, "changed_tests")
    ]
    changes = [f"changes {', '.join(f'`{p}`' for p in paths[:10])}"] if paths else []
    changes += [f"names {', '.join(f'`{t}`' for t in named if t)}"] if any(named) else []
    if not changes:
        return []
    return [
        Finding(
            "check.qa_tasks.one_outcome",
            says("check.qa_tasks.one_outcome", "justified", changes=" and ".join(changes))
            if ending == role_tasks.JUSTIFIED
            else says("check.qa_tasks.one_outcome", "no_test", changes=" and ".join(changes)),
        )
    ]


# What a release ships, said by what it doesn't (crew#816): the tests, the docs and the
# repository's own settings change nothing a user runs. The changelog is the entry.
CHANGELOG = "CHANGELOG.md"
NOT_SHIPPED_DIRS = ("docs/", ".github/", ".crew/", ".devcontainer/")
NOT_SHIPPED_SUFFIXES = (".md", ".rst")
NOT_SHIPPED_FILES = frozenset({".gitignore", ".gitattributes", ".dockerignore"})
_HEADING = re.compile(r"^##\s+(.*)$")


def ships(path: str) -> bool:
    """Whether a changed path changes what the project ships: anything but its tests, its
    docs, its repository settings and the changelog itself."""
    return not (
        path == CHANGELOG
        or profile_for(path).is_test_path(path)
        or path.startswith(NOT_SHIPPED_DIRS)
        or path.endswith(NOT_SHIPPED_SUFFIXES)
        or path in NOT_SHIPPED_FILES
    )


def unreleased(text: str | None) -> list[str]:
    """The entries under a changelog's `## [Unreleased]`, one per list item."""
    found: list[str] = []
    inside = False
    for line in (text or "").splitlines():
        heading = _HEADING.match(line)
        if heading:
            inside = heading.group(1).strip().lower().startswith("[unreleased]")
            continue
        if inside and line.strip().startswith(("- ", "* ")):
            found.append(line.strip())
    return found


def no_changelog_entry(
    worktree: Path, card: Card | None = None, which: str = "default"
) -> list[Finding]:
    """A story or a bug that changes what the project ships adds its `[Unreleased]` entry
    (the Sponsor, 2026-10-20; crew#816, ADR 0019).

    A release ships what `[Unreleased]` holds, so a story that adds nothing ships
    unlisted. Not a role's Task: DevOps's release moves the entries. Not a project with no
    changelog: there's nothing to add to. `which` is the message, the gate's or `done`'s.
    """
    if card is not None and (role_tasks.role_of(card) or card.work_type not in (STORY, BUG)):
        return []
    merged = regression.merged_base(worktree)
    if merged is None:
        return []
    before = merged.text(CHANGELOG)
    if before is None:
        return []
    shipped = [p for p in reproduction._changed(worktree, merged.sha) if ships(p)]
    if not shipped:
        return []
    target = worktree / CHANGELOG
    after = target.read_text(encoding="utf-8", errors="ignore") if target.is_file() else None
    if Counter(unreleased(after)) - Counter(unreleased(before)):
        return []
    return [
        Finding(
            "check.gates.no_changelog_entry",
            says(
                "check.gates.no_changelog_entry",
                which,
                paths=", ".join(f"`{p}`" for p in shipped[:5])
                + (f" and {len(shipped) - 5} more" if len(shipped) > 5 else ""),
            ),
        )
    ]


# What a test reads that is the changelog's structure, not an entry: a heading, or a
# subheading's name. A release keeps these where they are.
_SUBHEADINGS = frozenset({"added", "changed", "deprecated", "removed", "fixed", "security"})
# A JavaScript test's check that the text holds a string: `toContain("...")`.
_JS_HOLDS = re.compile(r"""\.(?:toContain|includes|toMatch|match)\(\s*(['"`])(.*?)\1""")
_JS_UNRELEASED = re.compile(r"""(?<!replace\()(?<!replaceAll\()(['"`/])[^'"`/\n]*Unreleased""")


def reads_only_unreleased(
    worktree: Path, merged: regression.Merged, paths: list[str]
) -> list[Finding]:
    """Each test the branch adds or changes that reads a changelog entry only from
    `## [Unreleased]` (crew#1014).

    A release moves `[Unreleased]`'s entries to its dated section and changes no test, so
    such a test fails at the next release: sprint-metrics' 4.5.0 failed on two in a day
    (mqucifer/sprint-metrics#969, mqucifer/sprint-metrics#977). Read from the test's code,
    not run: a test that finds the `[Unreleased]` section, by a string naming it or a
    helper in its file that returns it, and checks that what it found holds a string.
    A string that's a heading or a subheading's name is the changelog's structure, which a
    release keeps, so a test of that (that `[Unreleased]` comes first, its subheadings'
    order) goes on.
    """
    pinned: list[str] = []
    for path in paths:
        if not profile_for(path).is_test_path(path) or not (worktree / path).is_file():
            continue
        now = (worktree / path).read_text(encoding="utf-8", errors="ignore")
        was = merged.text(path) or ""
        if path.endswith(".py"):
            pinned += [f"{path}::{name}" for name in _py_pinned(now, was)]
        else:
            pinned += [f"{path}::{title}" for title in _js_pinned(now, was)]
    if not pinned:
        return []
    return [
        Finding(
            "check.gates.reads_only_unreleased",
            says(
                "check.gates.reads_only_unreleased",
                tests="\n".join(f"- `{t}`" for t in pinned),
            ),
            count=len(pinned),
        )
    ]


def _structure(text: str) -> bool:
    """Is a string a test checks for the changelog's structure, not an entry?"""
    said = text.strip().lstrip("#").strip().strip("[]").lower()
    return text.strip().startswith("#") or "unreleased" in said or said in _SUBHEADINGS


def _names_unreleased(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "unreleased" in node.value.lower()
    )


def _from_unreleased(func: ast.FunctionDef | ast.AsyncFunctionDef, helpers: set[str]) -> set[str]:
    """The names in `func` that hold what it found of `[Unreleased]`: assigned from an
    expression with a string naming it or a call of a helper that returns it, or from one
    another, and each the function asserts is the `[Unreleased]` heading. A `replace` of
    the heading writes a changelog; it reads nothing from one."""
    written = {
        id(arg)
        for call in ast.walk(func)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in ("replace", "replaceAll")
        for arg in call.args
    }
    held: set[str] = set()

    def reads(expr: ast.AST | None) -> bool:
        return expr is not None and (
            _uses(expr, held, helpers)
            or any(_names_unreleased(n) and id(n) not in written for n in ast.walk(expr))
        )

    def hold(target: ast.AST) -> None:
        held.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))

    while True:
        before = len(held)
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and reads(node.value):
                for target in node.targets:
                    hold(target)
            elif (isinstance(node, ast.AnnAssign | ast.AugAssign) and reads(node.value)) or (
                isinstance(node, ast.For | ast.AsyncFor | ast.comprehension) and reads(node.iter)
            ):
                hold(node.target)
            elif (
                isinstance(node, ast.Assert)
                and isinstance(node.test, ast.Compare)
                and all(isinstance(op, ast.Eq) for op in node.test.ops)
            ):
                # `assert first.group(1) == "Unreleased"`: `first` is that section. Not
                # `"## [Unreleased]" in readme`, which reads nothing from the changelog.
                sides = [node.test.left, *node.test.comparators]
                if any(_names_unreleased(s) for s in sides):
                    for side in sides:
                        hold(side)
        if len(held) == before:
            return held


def _uses(expr: ast.AST, held: set[str], helpers: set[str]) -> bool:
    """Does `expr` use what was read of `[Unreleased]`: a name holding it, or a helper's
    call?"""
    return any(
        (isinstance(n, ast.Name) and n.id in held)
        or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in helpers)
        for n in ast.walk(expr)
    )


def _returns(func: ast.FunctionDef | ast.AsyncFunctionDef, helpers: set[str]) -> bool:
    """Does a helper return what it read of `[Unreleased]`?"""
    held = _from_unreleased(func, helpers)
    return any(
        isinstance(r, ast.Return) and r.value is not None and _uses(r.value, held, helpers)
        for r in ast.walk(func)
    )


def _holds_an_entry(func: ast.AST, held: set[str], helpers: set[str]) -> bool:
    """Does `func` check that what it read of `[Unreleased]` holds a string that isn't
    the changelog's structure: `"x" in section`, `assertIn("x", section)`?"""

    def entry(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and not _structure(node.value)
        )

    checks = [n.test for n in ast.walk(func) if isinstance(n, ast.Assert)]
    checks += [
        n
        for n in ast.walk(func)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr.startswith("assert")
    ]
    for made in checks:
        for node in ast.walk(made):
            if isinstance(node, ast.Compare) and entry(node.left):
                if any(
                    isinstance(op, ast.In) and _uses(c, held, helpers)
                    for op, c in zip(node.ops, node.comparators, strict=True)
                ):
                    return True
            elif isinstance(node, ast.Call) and len(node.args) >= 2:
                called = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if (
                    called in ("assertIn", "search", "match", "fullmatch", "findall")
                    and entry(node.args[0])
                    and _uses(node.args[1], held, helpers)
                ):
                    return True
    return False


def _py_pinned(now: str, was: str) -> list[str]:
    """The Python tests in `now`, added or changed since `was`, that read an entry only
    from `[Unreleased]`."""
    try:
        tree = ast.parse(now)
    except SyntaxError:
        return []
    before = _tests_in(was) if was else {}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    helpers: set[str] = set()
    # A helper returns the section if what it returns is read from it, and one calling
    # such a helper does too.
    while found := {
        f.name
        for f in functions
        if not f.name.startswith("test") and f.name not in helpers and _returns(f, helpers)
    }:
        helpers |= found
    pinned = []
    for name, node in _tests_in(now).items():
        if name in before and ast.dump(before[name]) == ast.dump(node):
            continue
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _holds_an_entry(node, _from_unreleased(node, helpers), helpers):
            pinned.append(name)
    return pinned


def _js_tests(source: str) -> dict[str, str]:
    """A JavaScript test file's tests, by title, each with its text up to the next."""
    found = list(_TITLE.finditer(source))
    return {
        m.group(2): source[m.start() : found[i + 1].start() if i + 1 < len(found) else None]
        for i, m in enumerate(found)
    }


def _js_pinned(now: str, was: str) -> list[str]:
    """The tests in a test file in another language, added or changed since `was`, that
    find `[Unreleased]` by a string naming it and check a string that isn't a heading.

    Read as text: the crew has no parser for them (crew#404).
    """
    before = _js_tests(was)
    return [
        title
        for title, text in _js_tests(now).items()
        if before.get(title) != text
        and _JS_UNRELEASED.search(text)
        and any(not _structure(m.group(2)) for m in _JS_HOLDS.finditer(text))
    ]


def conflicts_left(worktree: Path, files: list[str]) -> list[Finding]:
    """A resolution's `done` with a conflict's markers still in a file it was resolving
    (crew#933). Text with markers can pass every check: a changelog is never run."""
    from crew_org.git_ops import unresolved  # noqa: PLC0415

    left = unresolved(worktree, files)
    if not left:
        return []
    return [
        Finding(
            "check.gates.conflicts_left",
            says("check.gates.conflicts_left", paths=", ".join(f"`{p}`" for p in left)),
        )
    ]


def _skipped(worktree: Path, merged: regression.Merged, paths: list[str], verdict: Verdict) -> None:
    """The gate refuses a skipped test that CI runs (the Sponsor, 2026-10-21).

    What CI runs is checkable through the project's declaration: a test its workspace
    file declares CI-only, by its id or its skip's reason (Docker, say), is one CI skips
    too, and anything else would run there. One the branch causes goes back: a test it
    adds or changes, or any test in a module it changes or under a `conftest.py` it
    changes, where a `pytestmark`, a fixture or a `skipif` can skip tests it never
    touched. So does one the report can't place in a file. One already merged isn't
    this Developer's to fix: it's DevOps's, whose declaration it is. A project with no
    file can't say what's CI-only yet, so its merged skips wait for DevOps's first
    workspace Task, which covers them.
    """
    skipped = verdict.check.skipped if verdict.check is not None else None
    if not skipped:
        return
    declared = services.at_main(worktree)
    run_in_ci = {
        test: reason for test, reason in skipped.items() if not declared.skips_in_ci(test, reason)
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


def _turned_on(
    worktree: Path, merged: regression.Merged, paths: list[str], verdict: Verdict
) -> None:
    """A workspace Task's own services turn on merged tests that had skipped, and some
    fail or hang (the Sponsor, 2026-10-23). Fixing them is the Developer's, outside the
    Task's scope: on mqucifer/sprint-metrics#739's test copy 41 rotted database tests
    fail and 4 hang, and would have sent DevOps's Task back for ever. So when only tests
    failed, the hung ones set aside before the run (`own_checks`), and the branch caused
    none of them, by the test `_skipped` uses, they're the Developer's bug, not a
    refusal. One the branch caused, or any other failure, goes back as today's do.
    """
    check = verdict.check
    if check is None or check.hung or not (check.ok or check.only_tests_failed):
        return
    # By file, as `own_checks` set them aside: the hung file's other tests didn't all run.
    hung = {
        path: f"hung {said}; the file's other tests weren't all run"
        for path, said in check.deselected.items()
    }
    tests = {**check.failed, **hung}
    if not tests:
        return
    branch_caused = caused_by(worktree, merged, paths)
    if any(branch_caused(t) for t in tests):
        return
    # Only what the workspace turned on: each skipped, or never ran, in the default
    # branch's own workspace. One that passed there the branch broke, through its image,
    # `containerEnv` or lockfile, and goes back to the Task (the review of crew#877).
    on_main = check.on_main
    if not isinstance(on_main, dict):
        return
    ran = [*check.failed, *(check.aside.get(path, path) for path in hung)]
    if any(on_main.get(t) not in ("skipped", "not run") for t in ran):
        return
    verdict.for_developer = tests


def caused_by(worktree: Path, merged: regression.Merged, paths: list[str]) -> Callable[[str], bool]:
    """Whether the branch caused a test's outcome, `path::Class.name` or a node id: it adds
    or changes the test, or changes its module or a `conftest.py` above it (crew#860)."""
    touched = added_or_changed(worktree, merged, paths)
    changed = set(paths)

    def caused(test: str) -> bool:
        shown = shown_test(test)
        return shown.split("[", 1)[0] in touched or _caused(
            worktree, shown.partition("::")[0], changed
        )

    return caused


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
    if verdict.check is None or verdict.check.ok or verdict.for_developer:
        return
    if verdict.check.timed_out:
        # Its own finding, never counted as a test that failed (crew#839): a run that
        # hung says nothing of the tests after the one it hung in.
        hung = verdict.check.hung
        aside = verdict.check.deselected
        lines = [
            f"- `{shown_test(t)}`: hung, waiting in {where}" for t, where in sorted(hung.items())
        ]
        if aside:
            last = list(aside)[-1]
            moved = [t for t in hung if t.partition("::")[0] != last]
            lines += ["", "Set aside before this run, by file:"]
            lines += [f"- `{path}`: hung {said}" for path, said in aside.items()]
            if moved:
                lines += [
                    "",
                    f"It hung again in `{moved[0].partition('::')[0]}` after `{last}` was set "
                    "aside: the test leaving the state may be in an earlier file.",
                ]
        verdict.findings.append(
            Finding(
                "check.gates.the_checks_timed_out",
                says(
                    "check.gates.the_checks_timed_out",
                    tests="\n".join(lines) or "- no test named: the run stopped at its bound",
                ),
            )
        )
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
    *,
    name: str = "",
    missing: str = "",
    paths: list[str] | None = None,
) -> None:
    """A bug's criteria tests have to fail on the merged code (crew#407, B3), run in the
    default branch's workspace when it has one (crew#839). `missing` says why a bug the
    crew filed from turned-on tests has no record to stand in.

    A bug whose fix changes only tests is fixed in them: its defect is a test
    (mqucifer/sprint-metrics#868's was stale after a release), or its failure shows only
    off main (mqucifer/crew-presentation#300's, on a pin's branch). Nothing it does can
    fail on main, so its proof is the run above, which with only tests changed is the
    branch itself: every named test runs and passes, and each merged test it keeps still
    makes every check it made, so it can't pass by checking less (the Sponsor, crew#1005). It
    has to change a merged test in a named test's file: a branch that only adds tests
    fixes nothing, and still has to show the bug."""
    tests = [profile_for(t.partition("::")[0]).split_test_id(t) for t in named if t]
    if not tests:
        return
    found = reproduction.reproduces(worktree, merged, tests, name=name)
    if found.unchecked:
        if note is not None:
            note(f"reproduction not checked: {found.unchecked}")
        return
    refusal = found.refusal()
    fixed = _fixed_in_tests(worktree, merged, tests, paths or [])
    if refusal and fixed and not (found.skipped or found.hung):
        if weaker := checks_less(worktree, merged, paths or []):
            verdict.findings.append(
                Finding(
                    "guard.reproduce.check",
                    says(
                        "guard.reproduce.check",
                        "checks_less",
                        tests="\n".join(
                            f"- `{key}`: `{made}`"
                            for key, gone in sorted(weaker.items())
                            for made in gone
                        ),
                    ),
                )
            )
        else:
            verdict.fixed_in_tests = fixed
        return
    if refusal:
        why = f"{refusal}. {missing}" if missing else refusal
        verdict.findings.append(
            Finding("guard.reproduce.check", says("guard.reproduce.check", why=why))
        )


def _fixed_in_tests(
    worktree: Path, merged: regression.Merged, tests: list[tuple[str, str]], paths: list[str]
) -> list[str]:
    """The merged tests a bug's fix changes or removes, when it changes only tests and
    some are in a named test's file; else none (crew#1005)."""
    if not paths or not all(profile_for(p).is_test_path(p) for p in paths):
        return []
    files = {path for path, _ in tests}
    changed = changed_tests(worktree, merged, paths)
    return sorted(key for key in changed if key.partition("::")[0] in files)


# A call that checks, by its name: pytest's context managers, and a mock's or unittest's
# `assert...` methods.
_CHECKING_CALLS = frozenset({"raises", "warns", "deprecated_call"})
# How alike a rewritten check is to the one it replaces: difflib's own cutoff for a close
# match (`get_close_matches`).
_REWRITTEN = 0.6


def _checks(node: ast.AST) -> list[str]:
    """The checks a test makes, written in it, as source, in order: each `assert` that can
    fail, without its message, which checks nothing, and each call that checks. `assert
    True` can't fail, so it isn't one."""
    found: list[ast.expr | ast.stmt] = []
    for each in ast.walk(node):
        if isinstance(each, ast.Assert) and not isinstance(each.test, ast.Constant):
            found.append(each)
        elif isinstance(each, ast.Call):
            called = each.func
            name = (
                called.attr
                if isinstance(called, ast.Attribute)
                else called.id
                if isinstance(called, ast.Name)
                else ""
            )
            if name.startswith("assert") or name in _CHECKING_CALLS:
                found.append(each)
    return [
        f"assert {ast.unparse(n.test)}" if isinstance(n, ast.Assert) else ast.unparse(n)
        for n in sorted(found, key=lambda n: (n.lineno, n.col_offset))
    ]


def dropped_checks(was: list[str], now: list[str]) -> list[str]:
    """The checks in `was` that `now` has nothing for: neither the same check nor one
    rewritten from it, the closest left that's alike enough (crew#1005).

    Counting wasn't enough. mqucifer/crew-presentation#300's fix rewrote its test's 4
    attempt-chart checks in place and added 4: with any one merged check deleted, it
    still had more than it started with. The most alike are paired first, so what's left
    is what was dropped.
    """
    left = list(now)
    unmatched = []
    for made in was:
        if made in left:
            left.remove(made)
        else:
            unmatched.append(made)
    alike = sorted(
        (
            (_alike(made, other), i, j)
            for i, made in enumerate(unmatched)
            for j, other in enumerate(left)
        ),
        key=lambda pair: (-pair[0], pair[1], pair[2]),
    )
    paired: set[int] = set()
    taken: set[int] = set()
    for ratio, i, j in alike:
        if ratio < _REWRITTEN:
            break
        if i not in paired and j not in taken:
            paired.add(i)
            taken.add(j)
    return [made for i, made in enumerate(unmatched) if i not in paired]


def _alike(made: str, other: str) -> float:
    """How alike `other` is to `made`, as a rewrite of it: 0 when it checks the reverse.

    Two checks of one shape differ in their values, so their values are compared: on
    mqucifer/crew-presentation#300 each was `assert '...' in page`, and compared whole,
    `'attempt-row'` came out as like `'class="story-list"'` as a rewrite would be. Of two
    shapes, as `== 100` rewritten `<= 100` is, the whole check is compared.
    """
    if _opposite(made, other):
        return 0.0
    if (shape := _shape(made)) is not None and shape == _shape(other):
        return difflib.SequenceMatcher(a=repr(_values(made)), b=repr(_values(other))).ratio()
    return difflib.SequenceMatcher(a=made, b=other).ratio()


def _shape(made: str) -> str | None:
    """A check's code with its values left out, or None if it doesn't parse."""
    try:
        tree = ast.parse(made)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            node.value = None
    return ast.dump(tree)


def _values(made: str) -> list[Any]:
    return [n.value for n in ast.walk(ast.parse(made)) if isinstance(n, ast.Constant)]


# A comparison and the one that checks its reverse.
_REVERSED = {
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}


def _opposite(made: str, other: str) -> bool:
    """Does `other` check the reverse of `made`: `not in` for `in`, `!=` for `==`, `not x`
    for `x`?"""
    tested = [_asserted(c) for c in (made, other)]
    a, b = tested
    if a is None or b is None:
        return False
    negated = [isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not) for t in tested]
    if negated[0] != negated[1]:
        return True
    if isinstance(a, ast.Compare) and isinstance(b, ast.Compare) and len(a.ops) == len(b.ops):
        return any(_REVERSED.get(type(x)) is type(y) for x, y in zip(a.ops, b.ops, strict=True))
    return False


def _asserted(made: str) -> ast.expr | None:
    """What an `assert` check tests, or None for a call that checks."""
    try:
        parsed = ast.parse(made).body
    except SyntaxError:
        return None
    return parsed[0].test if parsed and isinstance(parsed[0], ast.Assert) else None


def checks_less(
    worktree: Path, merged: regression.Merged, paths: list[str]
) -> dict[str, list[str]]:
    """Each merged Python test the branch keeps but changes, by `path::name`, with the
    checks it made as merged that it no longer does (crew#1005).

    A test removed whole isn't here: that's a declared change, ruled on as any other
    merged test's is (`_declared_tests`). A check rewritten, its value or wording changed,
    is the review's to judge.
    """
    found: dict[str, list[str]] = {}
    for key, how in changed_tests(worktree, merged, paths).items():
        path, _, name = key.partition("::")
        if how != "changed" or not name or not path.endswith(".py"):
            continue
        was = _tests_in(merged.text(path) or "").get(name)
        now = _tests_in((worktree / path).read_text(encoding="utf-8", errors="ignore")).get(name)
        gone = dropped_checks(_checks(was), _checks(now)) if was and now else []
        if gone:
            found[key] = gone
    return found


def _as_recorded(
    worktree: Path, recorded: role_tasks.TurnedOnRecord, name: str, verdict: Verdict
) -> None:
    """A bug the crew filed from the tests a workspace turned on is reproduced by its own
    record: they fail on the merged code, as recorded at merge (crew#839). The branch has
    to make each pass in the workspace, a hung file's tests every one."""
    wrong = reproduction.on_the_branch(worktree, sorted(recorded.tests), name)
    if not wrong:
        return
    tests = (
        f"- they couldn't be run: {wrong}"
        if isinstance(wrong, str)
        else "\n".join(f"- `{entry}`: {why}" for entry, why in sorted(wrong.items()))
    )
    verdict.findings.append(
        Finding(
            "guard.reproduce.check",
            says("guard.reproduce.check", "recorded", pull=recorded.pull, tests=tests),
        )
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
