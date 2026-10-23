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

    # --- delivery's guards and blocks (delivery.py) ---
    CONFLICT_WITH_MAIN = "delivery.conflict_with_main"
    """The branch conflicts with main: resolving it is for a person."""
    RECORD_UNREADABLE = "delivery.record_unreadable"
    """The project's record can't be read, so its rules are unknown."""
    OPEN_PULL_REQUEST = "delivery.open_pull_request"
    """A pull request is still open on the branch; re-delivering would overwrite it."""
    PUSH_FAILED = "delivery.push_failed"
    """The work was done, and the branch couldn't be pushed."""
    WORKSPACE_UNREADY = "delivery.workspace_unready"
    """The story's workspace container didn't start, or its setup failed (crew#778)."""
    ROUND_TRIPS_NO_EPIC = "delivery.round_trips_no_epic"
    """The gates kept returning it, and it has no epic to be split again: for a person."""
    ROUTE_FAILED = "delivery.route_failed"
    """Where a session's ending sends the story couldn't be settled: blocked for a person."""
    PULL_REQUEST_FAILED = "delivery.pull_request_failed"
    """The branch was pushed, and its pull request couldn't be opened."""

    # --- gates ---
    CI_BEFORE_REVIEW = "gate.ci_before_review"
    """CI failed on the pull request's head: back to the Developer with its log, unreviewed."""
    REVIEW_CHANGES_REQUESTED = "gate.review_changes_requested"
    """The Code Reviewer or DevOps Engineer requested changes."""
    QA_UNPROVEN = "gate.qa_unproven"
    """QA found criteria the work doesn't prove."""
    CRITERIA_CANNOT_ALL_PASS = "gate.criteria_cannot_all_pass"
    """A split's criteria contradict each other or a merged test."""

    GATE_FINDINGS = "gate.findings_at_done"
    """The gates at `done` found problems: the Developer works on in a new session."""
    CRITERIA_REPAIR = "gate.criteria_repair"
    """The criteria check flagged stories whose criteria can't all pass: they're rewritten."""
    CRITERIA_RESPLIT = "gate.criteria_resplit"
    """The criteria check's findings named no story to repair: the epic is split again whole."""
    MERGE_CHANGES_REQUESTED = "gate.merge_changes_requested"
    """Changes were requested on the head that would merge: back to its role (crew#839)."""
    MERGE_CI_FAILED = "gate.merge_ci_failed"
    """CI failed at merge, or the merge queue removed the pull request: back to the Developer."""
    MERGE_CONFLICT = "gate.merge_conflict"
    """The pull request conflicts with main at merge: the Developer rebuilds it on main."""

    # --- work sent back after it merged, or to the design (crew#862) ---
    SENT_BACK_EPIC_REWORK = "sent_back.epic_rework"
    """The Sponsor sent an epic back (`needs:rework`): it's split again."""
    MAIN_RED = "sent_back.main_red"
    """The default branch went red after a merge: a technical epic to fix it."""
    RELEASE_GAPS = "sent_back.release_gaps"
    """The release check found gaps: a technical epic to close them."""
    REVERTED = "sent_back.reverted"
    """A merged change was reverted: its card goes back to Needs Refinement."""
    DESIGN_FAULTS = "sent_back.design_faults"
    """Blocked stories found faults in the design: the Architect revises it."""
    REBUILD_STRAIN = "sent_back.rebuild_strain"
    """Rebuilds kept straining one file: the Architect revises the design."""
    DESIGN_REFUSED = "sent_back.design_refused"
    """The Code Reviewer refused the Architect's design: it's proposed again."""

    # --- a story sent back to be split again (story_problem.py) ---
    RETURN_GATE_ROUND_TRIPS = "story.gate_round_trips"
    """The gates kept returning it."""
    RETURN_PINNED_TESTS = "story.pinned_tests"
    """It keeps breaking merged tests it didn't write."""
    RETURN_REVIEW_CONFLICT = "story.review_conflicts_with_criterion"
    """The review asks for what a criterion forbids."""
    RETURN_SESSION_BLOCKED = "story.session_blocked"
    """Its Developer session ended blocked on a missing file or fact, or was stopped."""
    RETURN_CONTRACT_RULING = "story.contract_ruling"
    """At `done`, the Business Analyst ruled it shouldn't change the merged tests it changed."""

    # --- a Developer session's ending (session_end.py) ---
    SESSION_CHOICE = "session.choice"
    """Its session ended blocked on a choice no one has made: for the Sponsor."""
    SESSION_NO_EPIC = "session.no_epic"
    """Its session ended blocked, and it has no epic to be split again: for a person."""

    # --- holds ---
    HOLD_SIBLING = "hold.sibling"
    """It waits for an earlier story in the same epic."""
    HOLD_BUILDS_ON = "hold.builds_on"
    """It waits for a story it builds on."""
    HOLD_WIP_LIMIT = "hold.wip_limit"
    """The next column is at its limit."""
    HOLD_NO_SESSION = "hold.no_session"
    """A role's Task waits for the milestone that brings its role's session."""


# A story's return reason (`story.returned`'s fixed vocabulary) as its rule.
RETURN_RULES = {
    "gate round trips": Rule.RETURN_GATE_ROUND_TRIPS,
    "pinned tests": Rule.RETURN_PINNED_TESTS,
    "review conflicts with a criterion": Rule.RETURN_REVIEW_CONFLICT,
    "session blocked": Rule.RETURN_SESSION_BLOCKED,
    "contract ruling": Rule.RETURN_CONTRACT_RULING,
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


# CrewAI's error for an answer with nothing in it, read from logs written before the
# crew made its own calls (ADR 0025).
_EMPTY = "none or empty"


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
    # The crew's own call layer (ADR 0025): an empty answer is a refusal with that reason.
    if kind == "llm.refused":
        return Kind.EMPTY if detail.get("empty") else Kind.FORM_REFUSED
    if kind == "escalation.decided":
        # An empty answer reaches the policy as a refused form; it was neither.
        if _EMPTY in str(detail.get("error") or "").lower():
            return Kind.EMPTY
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
