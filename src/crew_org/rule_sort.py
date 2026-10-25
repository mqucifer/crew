"""What each rule became when the Developer began working in a tool-driven session (crew#778).

The Sponsor decided on mqucifer/crew#778 that the Developer becomes a session of tool
calls in a workspace container per story (decisions 1 to 4b), and that its rules are
sorted by when they apply (decision 5):

- **workspace:** safety the container enforces (paths outside the repository or into
  `.git`, running on the host, network after setup, secrets). Kept as config, with
  nothing told and no refusals.
- **feedback:** a tool's reply during the session (the file doesn't parse, the quoted
  text isn't there, the name exists, lint). Never a discarded answer or a counted
  failure.
- **gate:** checked on the branch's diff at `done` (protected paths and workflows,
  scope, tests proving the criteria, changed merged tests declared, docs, CI), then
  review and QA.
- **retired:** exists only because an answer had to be complete in one shot.

A rule that doesn't bear on the Developer's session is **outside**, with why: another
role's own one-shot answer, the board's flow around the session, or a setting the
session doesn't change. A rule several roles share is sorted by what it becomes for
the Developer; `SHARED` says what stays for the others.

The sort was the plan for Stage E, which retired the one-call Developer: its retired
rules left the catalog, and so did the one-call copies of the rest, whose rules now
live in the session's tools, the gates at `done` and the container. What stays here is
each remaining rule the Developer meets, by what it is now. A retired rule still here
is one other roles keep (`SHARED`). Nothing in the tick reads this; a test holds every
rule the Developer meets to a kind.
"""

from __future__ import annotations

from typing import Any

WORKSPACE, FEEDBACK, GATE, RETIRED, OUTSIDE = "workspace", "feedback", "gate", "retired", "outside"
KINDS = (WORKSPACE, FEEDBACK, GATE, RETIRED)

# The Developer's rules, each with what it becomes and why. A rule the Developer meets
# that isn't here fails the test: every one is sorted by hand.
SORTED: dict[str, tuple[str, str]] = {
    # --- the call layer: what every answer is held to ------------------------------------
    "call.schema": (FEEDBACK, "`done` and each tool call are still JSON in a shape: a reply"),
    "call.the_answer_was_empty_you": (
        FEEDBACK,
        "a turn with no tool call gets a reply, and counts toward 'no progress'",
    ),
    "call.the_answer_ran_on_as": (
        FEEDBACK,
        "a tool call can still run away as whitespace; the reply says where",
    ),
    "call.check_not_cut_at_a_quote": (
        RETIRED,
        "a long one-shot JSON answer cut at a quote; code goes in tool arguments, short",
    ),
    # --- the edit applier `replace_definition` calls ----------------------------------
    "edit.ast_edit.apply_edit": (FEEDBACK, "no such definition, it exists, it doesn't parse"),
    # --- checks and guards on the Developer's work ------------------------------------
    "check.delivery.lint_or_tests_failed": (
        GATE,
        "verify on the whole branch at `done`; `run` during the session is feedback",
    ),
    "check.delivery.these_named_tests_dont_exist": (
        FEEDBACK,
        "named tests exist: `done` checks it, as its reply (the Sponsor, crew#778)",
    ),
    "check.delivery.workflows_not_permitted": (GATE, "workflow files, policy at `done`"),
    "guard.bounds.out_of_bounds": (GATE, "protected paths and CI weakened, on the diff"),
    "guard.role_tasks.out_of_scope": (GATE, "a role's Task beyond its role's paths, on the diff"),
    "check.role_tasks.diagram_renders": (GATE, "the Architect's diagram renders, at `done`"),
    "check.role_tasks.setup_failed_at_start": (
        FEEDBACK,
        "a DevOps workspace Task's own failing setup, told at its session's start",
    ),
    "check.role_tasks.workspace_checks": (
        FEEDBACK,
        "DevOps's `workspace_check`: the gate's checks on the workspace, before `done`",
    ),
    "guard.regression.broken_contracts": (GATE, "merged code removed or reshaped, on the diff"),
    "guard.reproduce.check": (GATE, "a bug's tests reproduce it, on the branch"),
    # --- the decisions that end or route a delivery ------------------------------------
    "check.gates.a_service_wont_start": (GATE, "a declared service not running, at `done`"),
    "check.gates.the_checks_timed_out": (GATE, "the checks hung or ran past their bound"),
    "check.gates.the_branch_changes_nothing": (GATE, "`done` with an empty diff, at `done`"),
    "check.gates.these_tests_skipped": (GATE, "a skipped test CI runs, at `done`"),
    "check.gates.these_merged_tests_changed": (GATE, "changed merged tests declared, at `done`"),
    "check.gates.no_changelog_entry": (GATE, "a story's `[Unreleased]` entry, at `done`"),
    "check.gates.the_business_analyst_ruled_that": (
        GATE,
        "the contract ruling, at `done`, when declaration and story disagree",
    ),
    "gate.ci_before_review": (GATE, "CI green before review"),
    "gate.review_changes_requested": (GATE, "review"),
    "gate.qa_unproven": (GATE, "QA"),
    # --- the project's own fixers, before the checks at `done` -------------------------
    "repair.workspace.autofix": (GATE, "the project's fixers, run before the checks at `done`"),
    "repair.unsafe_fix.B905": (GATE, "an allowed unsafe fix, run with the fixers"),
    "repair.unsafe_fix.SIM102": (GATE, "an allowed unsafe fix, run with the fixers"),
    # --- the session's own tools (crew#778, Stage C) ------------------------------------
    "session.arguments": (FEEDBACK, "a call's arguments: the reply lists what it takes"),
    "session.view": (FEEDBACK, "no such file or definition, or not text"),
    "session.search": (FEEDBACK, "no pattern, or no such path"),
    "session.image_digest": (FEEDBACK, "a digest, an option or a variable as the image"),
    "session.skill": (FEEDBACK, "a skill the start doesn't name, refused with the ones there are"),
    "session.path": (
        FEEDBACK,
        "the tools read and write on the host, so their reply holds them to the worktree",
    ),
    "session.replace_text": (FEEDBACK, "the quoted lines aren't there once, whole"),
    "session.replace_definition": (FEEDBACK, "not an existing Python file"),
    "session.write_file": (FEEDBACK, "an existing file, without `overwrite`"),
    "session.delete_file": (FEEDBACK, "a folder, or no such file"),
    "session.parse_guard": (FEEDBACK, "an edit that would stop the file parsing isn't applied"),
    "session.run": (FEEDBACK, "no tests named, or no pytest or lint among the checks"),
    "session.tests_for": (FEEDBACK, "no coverage map: Python only"),
    "session.git": (
        FEEDBACK,
        "a git command that isn't a read or restore, or an option that writes, reads outside "
        "the repository or runs a program: the reply lists what runs",
    ),
    "session.restore": (FEEDBACK, "no `--source` or files, or a file that can't be written"),
    # --- settings ------------------------------------------------------------------------
    "config.org.sandbox.engine": (WORKSPACE, "the container's engine"),
    "config.org.sandbox.image": (WORKSPACE, "the default image, before a project's own"),
    "config.org.sandbox.mode": (WORKSPACE, "no host fallback: the container is required"),
    "config.const.sandbox.DEFAULT_TIMEOUT": (WORKSPACE, "a command's limit in the container"),
    "config.const.sandbox.PROBE_TIMEOUT": (WORKSPACE, "the engine's probe"),
    "config.const.workspace.DEFAULT_TIMEOUT": (WORKSPACE, "a test run's limit"),
    "config.const.workspace.HANG_AFTER": (WORKSPACE, "one test's limit before it's a hang"),
    "config.agents.developer.can": (
        WORKSPACE,
        "what the role may do: in a session, its tool list and the container",
    ),
    "config.const.workspace.MAX_FAILURE_REPORT_CHARS": (FEEDBACK, "how much a reply carries"),
    "config.const.attempts.REPAIR_REPORT_CHARS": (FEEDBACK, "how much `run` reports"),
    "config.const.attempts.EXAMPLE_CODE_LINES": (FEEDBACK, "a cause's example, in `run`'s reply"),
    "config.const.attempts.EXAMPLE_ERROR_LINES": (FEEDBACK, "a cause's example, in `run`'s reply"),
    "config.const.attempts.OTHER_OUTPUT_CHARS": (FEEDBACK, "lint's output, in `run`'s reply"),
    "config.const.regression.RESTORE_CHAR_CEILING": (GATE, "the contract gate's report"),
    "config.const.calls.TOOL_ROUNDS": (
        RETIRED,
        "a cap on reads before one answer; a session ends on progress or the backstop",
    ),
    "config.const.repo_context.CONTEXT_CHAR_CEILING": (RETIRED, "the whole repository shown"),
    "config.const.repo_context.FOCUS_ABOVE_CHARS": (RETIRED, "when to focus what's shown"),
    "config.const.repo_context.FOCUSED_CHAR_CEILING": (RETIRED, "how much is shown at once"),
    "config.const.repo_context.READ_FIRST_CHAR_CEILING": (RETIRED, "shown before reading"),
    "config.const.repo_context.READING_CHAR_CEILING": (RETIRED, "one call's reading budget"),
    "config.const.repo_context.READ_MIN_CHARS": (RETIRED, "room left to offer reading"),
    "config.const.repo_context.READ_ROOM_CHARS": (RETIRED, "room kept to read"),
    "config.const.repo_context.WHOLE_FILE_CHARS": (RETIRED, "`view` reads a range instead"),
    "config.const.repo_context.CLOSE_LIMIT": (
        RETIRED,
        "files named as close to the work; the start's hints and `find` replace it",
    ),
    # --- what the Developer is told ------------------------------------------------------
    "§19.1": (WORKSPACE, "generated code runs sandboxed: the container"),
    "§19 a_digest_cant_be_invented": (GATE, "an invented digest, on the diff"),
    "§19.5": (GATE, "checks never weakened, on the diff"),
    "§19.8": (GATE, "an image proven as it runs: CI and the deploy review"),
    "§7 done_means_merged_and_green": (GATE, "the gates and CI"),
    "§7 documentation_is_the_exception": (GATE, "docs, at `done` and in review"),
    "§7.2": (GATE, "the whole suite and lint, at `done`"),
    "§7.3": (GATE, "review"),
    "§7.4": (GATE, "CI and branch protection"),
    # --- the constitution's text for the Developer's rules, told to no role -------------
    "§15.1": (GATE, "nothing already there is deleted: the contract gate, on the diff"),
    "§15.2": (GATE, "nothing changed silently: the contract gate"),
    "§15.3": (GATE, "declared changes are fine: the contract gate"),
    "§15 a_move_is_one_step_not": (GATE, "the gate reads a move in the diff as a move"),
    "§15 moving_a_definition_is_not_deleting": (GATE, "the contract gate, on the diff"),
    "§15 a_name_a_module_passes_along": (GATE, "the contract gate, on the diff"),
    "§15 a_story_can_declare_it_too": (GATE, "declared contract changes"),
    "§15 retiring_on_purpose": (GATE, "retired tests declared at `done`"),
}

# Rules that don't fit the four kinds, though the Developer meets them, and why.
UNFIT: dict[str, str] = {
    "story.return_to_refinement": "becomes `blocked`, routed to the Business Analyst",
    "config.attempts": (
        "mixed: the Developer's repair count is retired by decision 3, while the backlog "
        "order's, settle's and the merge's counts stay"
    ),
    "§19.6": "'failure is loud': craft the session keeps telling, not a check",
    "§19.9": "a release can be run again: the DevOps Engineer's, not the Developer's",
    "§7.6": "the card's audit trail: the crew writes it, not the Developer",
    "config.agents.developer.llm": "its model: stays a setting",
    "config.agents.developer.llm_params": "its model's limits: stay a setting",
    "config.const.calls.ATTEMPTS": "the call layer's retries: every other role keeps them",
    "config.const.calls.ECHO_CHARS": "the call layer's echo: every other role keeps it",
    "config.const.calls.EMPTY_LIMIT": "the call layer's empty limit: other roles keep it",
    "config.const.calls.RUNAWAY_CHARS": "the call layer's runaway cut: other roles keep it",
    "config.const.calls.RUNAWAY_TAIL": "the call layer's runaway cut: other roles keep it",
    "config.const.repo_context.SELECTION_TEXT_CHARS": "the event log's record of a selection",
    "config.const.attempts.RECURRING_CARDS": "the retro's: a cause on two cards is the crew's",
    "§15 why_this_is_a_check_and": "the rationale for the contract check, not a rule",
    "§16 the_projects_record_comes_first": (
        "what a step is shown first; the session's start keeps it (decision 4)"
    ),
    "§16 what_this_does_not_license": "the rationale for ceilings, not a rule",
    "§16 why_this_is_a_rule_and": "the rationale for ceilings, not a rule",
    "§16.2": "a ceiling is a guard: the principle stays for every role's ceilings",
    "§16.3": "a ceiling that fires fails loudly: stays for every role",
}

# What stays for other roles of a rule sorted by what it becomes for the Developer.
SHARED: dict[str, str] = {
    "call.schema": "every role's form",
    "call.the_answer_was_empty_you": "every role's one-shot answer",
    "call.the_answer_ran_on_as": "every role's one-shot answer",
    "call.check_not_cut_at_a_quote": "every role whose form has long text cells",
    "config.const.calls.TOOL_ROUNDS": "the split's and QA's reading",
    "config.const.repo_context.FOCUS_ABOVE_CHARS": "the split's selection",
    "config.const.repo_context.FOCUSED_CHAR_CEILING": "the split's selection",
    "config.const.repo_context.READ_FIRST_CHAR_CEILING": "the split's and QA's reading",
    "config.const.repo_context.READING_CHAR_CEILING": "the split's and QA's reading",
    "config.const.repo_context.READ_MIN_CHARS": "the split's and QA's reading",
    "config.const.repo_context.WHOLE_FILE_CHARS": "the split's and QA's reading",
    "gate.qa_unproven": "QA's own verdict",
}

# The modules whose rules bear on the Developer's session, with or without its name.
_DEVELOPER_MODULES = (
    "flows/delivery.py",
    "flows/attempts.py",
    "tools/workspace.py",
    "tools/ast_edit.py",
    "tools/sandbox.py",
    "tools/bounds.py",
    "tools/regression.py",
    "tools/repo_context.py",
    "tools/repo_read.py",
)


def developers(entry: Any) -> bool:
    """Whether a rule bears on the Developer's session: it meets it, or its code is the
    Developer's delivery."""
    return (
        any(role.startswith("Developer") for role in entry.roles)
        or any(module in entry.where for module in _DEVELOPER_MODULES)
        or entry.id in SORTED
        or entry.id in UNFIT
    )


def kind(entry: Any) -> tuple[str, str]:
    """What a rule becomes in the Developer's session, and why."""
    if entry.id in SORTED:
        return SORTED[entry.id]
    if entry.id in UNFIT:
        return OUTSIDE, UNFIT[entry.id]
    if entry.group in ("decision", "decides", "skip"):
        return OUTSIDE, "the board's flow around the session"
    if developers(entry):
        return "", "not sorted"
    if entry.group == "config":
        return OUTSIDE, "a setting the session doesn't change"
    roles = ", ".join(sorted(entry.roles)) or "no role"
    return OUTSIDE, f"another role's own answer ({roles})"
