"""What decided, and what kind of failure it was, as names (crew#449, crew#550).

A block, a return, a hold or a disposition used to record only a sentence for a
person ("Sprint escalation budget exhausted (1/1)"). Which guard, gate or budget
decided it wasn't a field, so nothing could count by it, and a replay that tried
to learn the crew's judgements couldn't (discussion 552). Each now carries a
`rule` from `Rule`, beside the sentence, and a failure carries its `kind`.

The two answer different questions. `kind` is what happened to the work: the
call didn't return, the answer was cut off, the form refused it. `rule` is what
the crew's own code decided about it: retry, return, block. "Failed" alone
reads as a model call failing, and usually wasn't one (the Sponsor, 2026-10-08).

Names, never text: both may leave for telemetry (ADR 0010).
"""

from __future__ import annotations

import ast
import inspect
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    """What kind of failure it was (crew#550)."""

    CALL_FAILED = "call_failed"
    """The model call didn't return: a connection, a timeout, a server error."""
    CUT_OFF = "cut_off"
    """The model answered, and its answer was cut off at the output limit."""
    EMPTY = "empty"
    """The model answered nothing, typically after thinking (#312)."""
    FORM_REFUSED = "form_refused"
    """The model answered, and the crew's form refused the answer."""
    EDIT_NOT_APPLIED = "edit_not_applied"
    """An accepted answer's edit couldn't be applied to the files."""
    GUARD_REFUSED = "guard_refused"
    """A guard refused the change: the regression guard, a protected path."""
    TESTS_FAILED = "tests_failed"
    """The change was applied, and lint or tests failed."""
    GATE_RETURNED = "gate_returned"
    """A gate sent the work back: review, QA, or the criteria check."""
    UNDECIDED = "undecided"
    """The role asked a question or decided nothing."""


class Rule(StrEnum):
    """Which of the crew's rules decided a disposition, block, return or hold."""

    # --- the escalation policy (escalation.py) ---
    SCHEMA_LOCAL_REPAIR = "schema.local_repair"
    """A refused answer, retried with the refusal shown, up to the local limit."""
    SCHEMA_PERSISTED = "schema.persisted"
    """Refused past the local limit: a prompt or schema defect, filed, never escalated."""
    SCOPE_RETURN = "scope.return"
    """The story is the problem, not the code: returned to refinement."""
    CAPABILITY_UNJUSTIFIED = "capability.unjustified"
    """A claim of being beyond reach with no real justification: treated as scope."""
    VERIFY_LOCAL_REPAIR = "verify.local_repair"
    """Lint or tests failed, repaired locally before any escalation."""
    REGRESSION_LOCAL_REPAIR = "regression.local_repair"
    """It changed a contract merged code depends on; retried with the contracts named."""
    REGRESSION_PERSISTED = "regression.persisted"
    """It kept changing contracts after being told which to keep: blocked for a person."""
    ESCALATION_BUDGET = "escalation.budget"
    """Eligible for escalation, with the sprint's budget spent: blocked."""
    ESCALATION_ALLOWED = "escalation.allowed"
    """Escalated to Claude, within the sprint's budget."""
    ESCALATION_UNRESOLVED = "escalation.unresolved"
    """Escalated, and still failing afterwards: blocked."""
    USAGE_LIMIT = "escalation.usage_limit"
    """The escalation hit the subscription's usage limit: parked, not failed."""

    # --- delivery's guards and blocks (delivery.py) ---
    CONFLICT_WITH_MAIN = "delivery.conflict_with_main"
    """The branch conflicts with main: resolving it is for a person."""
    RECORD_UNREADABLE = "delivery.record_unreadable"
    """The project's record can't be read, so its rules are unknown."""
    OPEN_PULL_REQUEST = "delivery.open_pull_request"
    """A pull request is still open on the branch; re-delivering would overwrite it."""
    PUSH_FAILED = "delivery.push_failed"
    """The work was done, and the branch couldn't be pushed."""
    GUARD_OVERWRITE = "guard.overwrite"
    """It kept rewriting existing files whole instead of editing them."""
    GUARD_PROTECTED = "guard.protected"
    """It kept changing what the project protects."""
    GUARD_WORKFLOW_PERMISSION = "guard.workflow_permission"
    """It changes a workflow the crew has no permission to push."""
    GUARD_NO_CHANGE = "guard.no_change"
    """The implementation produced no change."""
    EDIT_NOT_APPLIED = "edit.not_applied"
    """Its edits couldn't be applied, past the local limit."""
    GATES_DISAGREE = "gate.reviewer_and_developer_disagree"
    """Answered without a change and sent back again: for a person to settle."""

    # --- gates ---
    REVIEW_CHANGES_REQUESTED = "gate.review_changes_requested"
    """The Code Reviewer or DevOps Engineer requested changes."""
    QA_UNPROVEN = "gate.qa_unproven"
    """QA found criteria the work doesn't prove."""
    CRITERIA_CANNOT_ALL_PASS = "gate.criteria_cannot_all_pass"
    """A split's criteria contradict each other or a merged test."""

    # --- a story sent back to be split again (story_problem.py) ---
    RETURN_GATE_ROUND_TRIPS = "story.gate_round_trips"
    """The gates kept returning it."""
    RETURN_PINNED_TESTS = "story.pinned_tests"
    """It keeps breaking merged tests it didn't write."""
    RETURN_REVIEW_CONFLICT = "story.review_conflicts_with_criterion"
    """The review asks for what a criterion forbids."""

    # --- holds ---
    HOLD_SIBLING = "hold.sibling"
    """It waits for an earlier story in the same epic."""
    HOLD_BUILDS_ON = "hold.builds_on"
    """It waits for a story it builds on."""
    HOLD_WIP_LIMIT = "hold.wip_limit"
    """The next column is at its limit."""


# A story's return reason (`story.returned`'s fixed vocabulary) as its rule.
RETURN_RULES = {
    "gate round trips": Rule.RETURN_GATE_ROUND_TRIPS,
    "pinned tests": Rule.RETURN_PINNED_TESTS,
    "review conflicts with a criterion": Rule.RETURN_REVIEW_CONFLICT,
}

# What each failure class an attempt is judged under means, as a kind.
_CLASS_KINDS = {
    "SCHEMA": Kind.FORM_REFUSED,
    "EDIT": Kind.EDIT_NOT_APPLIED,
    "VERIFY": Kind.TESTS_FAILED,
    "REGRESSION": Kind.GUARD_REFUSED,
    "OVERWRITE": Kind.GUARD_REFUSED,
    "BOUNDS": Kind.GUARD_REFUSED,
    "CAPABILITY": Kind.UNDECIDED,
}


def call_kind(error: str) -> Kind:
    """A failed model call's kind, from its error: cut off, refused, or no answer."""
    text = error.lower()
    if "length limit" in text or "max_tokens" in text or "maximum context" in text:
        return Kind.CUT_OFF
    if "validation error" in text or "could not parse" in text:
        return Kind.FORM_REFUSED
    return Kind.CALL_FAILED


def kind_of(kind: str, summary: str, detail: dict[str, Any]) -> Kind | None:
    """The kind of failure an event records, or None for one that isn't a failure.

    Derived from what the event already says, so every path that writes one is
    covered, old and new. A SCOPE return's kind is what happened to the work,
    which only its site knows: it's given there.
    """
    if kind in ("llm.failed", "task.failed"):
        return call_kind(str(detail.get("error") or summary))
    if kind == "llm.empty":
        return Kind.EMPTY
    if kind == "escalation.decided":
        # "EDIT — retry_local": the class is the part before the dash.
        named = str(detail.get("failure_class") or "")
        shown = summary.partition(" — ")[0].strip()
        return _CLASS_KINDS.get(shown) or _CLASS_KINDS.get(named)
    if kind == "story.returned":
        return Kind.GATE_RETURNED
    return None


def meanings(names: type[StrEnum]) -> dict[str, str]:
    """Each member's meaning, from the docstring written under it, for the reference."""
    tree = ast.parse(inspect.getsource(names))
    body = tree.body[0].body  # type: ignore[attr-defined]
    found: dict[str, str] = {}
    for here, after in zip(body, body[1:], strict=False):
        if (
            isinstance(here, ast.Assign)
            and isinstance(after, ast.Expr)
            and isinstance(after.value, ast.Constant)
            and isinstance(here.targets[0], ast.Name)
        ):
            found[here.targets[0].id] = " ".join(str(after.value.value).split())
    return found
