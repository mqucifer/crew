"""What each rule becomes when the Developer works in a tool-driven session (crew#778).

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

An analysis for the Sponsor's review: nothing in the tick reads this, and no behaviour
changes until the session is built.
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
    "call.split_blocks": (RETIRED, "code as blocks after one JSON answer (ADR 0026)"),
    "call.join_blocks": (RETIRED, "code as blocks after one JSON answer (ADR 0026)"),
    "call.the_json_before_the_blocks": (RETIRED, "code as blocks after one JSON answer"),
    # --- the Developer's answer forms ----------------------------------------------------
    "form.path.stays_in_the_repository": (
        WORKSPACE,
        "the container sees only the worktree; `.git` only if it's mounted read-only",
    ),
    "form.FileEdit.stays_in_the_repository": (
        FEEDBACK,
        "replace_definition on a file that isn't Python: the tool's reply",
    ),
    "form.FileEdit.source_matches_the_operation": (
        FEEDBACK,
        "a definition replaced with nothing: the tool's reply",
    ),
    "form.FileWrite.is_plausible": (FEEDBACK, "an empty or oversized write_file: the reply"),
    "form.TextEdit.changes_something": (FEEDBACK, "a replace_text that changes nothing"),
    "form.CriterionMet.one_proof": (
        FEEDBACK,
        "`done` names one proof per criterion, a test or a CI step: a reply to `done`",
    ),
    "form.RetiredTest.a_test_file": (FEEDBACK, "`done` retires only tests: a reply to it"),
    "form.RetiredTest.says_why": (FEEDBACK, "`done` says why each test is retired"),
    "form.FirstAttempt.has_a_test": (GATE, "tests prove the criteria, judged at `done`"),
    "form.FirstOrDone.has_a_test": (GATE, "tests prove the criteria, judged at `done`"),
    "form.CriterionTest.a_name_not_its_code": (
        RETIRED,
        "a test written whole inside the answer; tests are written with the edit tools",
    ),
    "form.CriterionTest.is_that_test": (
        RETIRED,
        "the in-answer test's form; that it's a real test is the gate at `done`",
    ),
    "form.Implementation.does_something": (
        RETIRED,
        "a one-shot answer that changes nothing; at `done` it's guard.no_change",
    ),
    "form.Move.somewhere_else": (RETIRED, "the Move form; a move is edits in a session"),
    "form.Move.stays_in_the_repository": (RETIRED, "the Move form; a move is edits"),
    "form.Plan.one_step_per_file": (RETIRED, "stepped delivery, for answers too big"),
    "form.PlannedFile.says_what": (RETIRED, "stepped delivery, for answers too big"),
    "form.FileAsk.a_bare_path": (RETIRED, "asking for files to be shown; view and search"),
    "form.FileWrite.tests_declare_their_fixtures": (
        RETIRED,
        "repairs a one-shot test file; in a session, `run` shows the missing fixture",
    ),
    "form.Implementation.drops_doc_criteria_with_no_test": (
        RETIRED,
        "repairs a one-shot answer's criteria list; `done` lists what proves each",
    ),
    "form.Implementation.drops_tests_it_only_names": (
        RETIRED,
        "repairs a one-shot answer's criteria list; `done` lists what proves each",
    ),
    # --- the Developer's edits, applied ------------------------------------------------
    "edit.ast_edit.apply_edit": (FEEDBACK, "no such definition, it exists, it doesn't parse"),
    "edit.workspace.plan_text_edits": (FEEDBACK, "the quote isn't there, or twice; invalid"),
    "edit.workspace.apply_implementation": (FEEDBACK, "the file doesn't exist: the reply"),
    "edit.workspace.apply": (WORKSPACE, "a containment re-check; the container is it"),
    "edit.workspace.outside_definitions": (
        RETIRED,
        "exists so the contract check sees changes by name; the gate reads the diff",
    ),
    "edit.move_code.move_one": (
        RETIRED,
        "a move in one answer; what it protects (callers still import) is the contract gate",
    ),
    "edit.delivery.these_tests_arent_in_their": (GATE, "named tests written, at `done`"),
    # --- checks and guards on the Developer's work ------------------------------------
    "check.delivery.lint_or_tests_failed": (
        GATE,
        "verify on the whole branch at `done`; `run` during the session is feedback",
    ),
    "check.delivery.these_named_tests_dont_exist": (GATE, "named tests exist, at `done`"),
    "check.delivery.this_ci_evidence_doesnt_hold": (GATE, "CI evidence, at `done`"),
    "check.delivery.workflows_not_permitted": (GATE, "workflow files, policy at `done`"),
    "check.delivery.deliver_in_steps": (RETIRED, "stepped delivery, for answers too big"),
    "check.stepped.implement_file": (RETIRED, "stepped delivery, for answers too big"),
    "check.delivery.do_the_work_now_with": (RETIRED, "the limit on asking for files"),
    "check.attempts.repair_report": (
        FEEDBACK,
        "becomes how `run` reports: each cause once, every failing test named",
    ),
    "guard.bounds.out_of_bounds": (GATE, "protected paths and CI weakened, on the diff"),
    "guard.regression.broken_contracts": (GATE, "changed merged tests declared, on the diff"),
    "guard.scope.without_declared": (GATE, "scope and declared contract changes, on the diff"),
    "guard.reproduce.check": (GATE, "a bug's tests reproduce it, on the branch"),
    "guard.overwrite.overwrites_existing": (
        FEEDBACK,
        "write_file over an existing file: the reply says to edit it",
    ),
    # --- the decisions that end or route a delivery ------------------------------------
    "guard.protected": (GATE, "protected paths, policy at `done`"),
    "guard.workflow_permission": (GATE, "workflow files, policy at `done`"),
    "guard.no_change": (GATE, "`done` with an empty diff"),
    "check.gates.the_branch_changes_nothing": (GATE, "`done` with an empty diff, at `done`"),
    "check.gates.these_merged_tests_changed": (GATE, "changed merged tests declared, at `done`"),
    "check.gates.the_business_analyst_ruled_that": (
        GATE,
        "the contract ruling, at `done`, when declaration and story disagree",
    ),
    "guard.not_reproduced": (GATE, "a bug's tests reproduce it, at `done`"),
    "guard.overwrite": (FEEDBACK, "rewriting whole files: write_file's reply"),
    "gate.ci_before_review": (GATE, "CI green before review"),
    "gate.review_changes_requested": (GATE, "review"),
    "gate.qa_unproven": (GATE, "QA"),
    "gate.reviewer_and_developer_disagree": (GATE, "between review and QA"),
    "repair.local": (
        RETIRED,
        "counted local repairs; a session's failures are replies, and it stops on no progress",
    ),
    "repair.gave_up": (RETIRED, "the attempt limit; decision 3 replaces it"),
    # --- the editor's own fixes, and the looser matches --------------------------------
    "repair.workspace.autofix": (FEEDBACK, "the project's fixers, run on each edit's reply"),
    "repair.unsafe_fix.B905": (FEEDBACK, "an allowed unsafe fix, run with the fixers"),
    "repair.unsafe_fix.SIM102": (FEEDBACK, "an allowed unsafe fix, run with the fixers"),
    "repair.fallback.quote.indent": (FEEDBACK, "replace_text's tolerance for indentation"),
    "repair.fallback.quote.final_newline": (FEEDBACK, "replace_text's tolerance for a newline"),
    "repair.fallback.block.name": (RETIRED, "a block found under another name (ADR 0026)"),
    # --- settings ------------------------------------------------------------------------
    "config.org.sandbox.engine": (WORKSPACE, "the container's engine"),
    "config.org.sandbox.image": (WORKSPACE, "the default image, before a project's own"),
    "config.org.sandbox.mode": (WORKSPACE, "no host fallback: the container is required"),
    "config.const.sandbox.DEFAULT_TIMEOUT": (WORKSPACE, "a command's limit in the container"),
    "config.const.sandbox.PROBE_TIMEOUT": (WORKSPACE, "the engine's probe"),
    "config.const.workspace.DEFAULT_TIMEOUT": (WORKSPACE, "a test run's limit"),
    "config.agents.developer.can": (
        WORKSPACE,
        "what the role may do: in a session, its tool list and the container",
    ),
    "config.const.workspace.MAX_FAILURE_REPORT_CHARS": (FEEDBACK, "how much a reply carries"),
    "config.const.workspace.REPLACE_SHOWN_CHARS": (FEEDBACK, "how much a reply shows back"),
    "config.const.attempts.REPAIR_REPORT_CHARS": (FEEDBACK, "how much `run` reports"),
    "config.const.attempts.EXAMPLE_CODE_LINES": (FEEDBACK, "a cause's example, in `run`'s reply"),
    "config.const.attempts.EXAMPLE_ERROR_LINES": (FEEDBACK, "a cause's example, in `run`'s reply"),
    "config.const.attempts.OTHER_OUTPUT_CHARS": (FEEDBACK, "lint's output, in `run`'s reply"),
    "config.const.delivery_crew.MAX_FILE_BYTES": (FEEDBACK, "write_file's size limit"),
    "config.const.regression.RESTORE_CHAR_CEILING": (GATE, "the contract gate's report"),
    "config.const.stepped.MAX_FILES": (RETIRED, "stepped delivery"),
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
    "told.developer.you_never_push_to_main_and": (
        WORKSPACE,
        "the session has no git and no push; widening scope is the scope gate",
    ),
    "§19.1": (WORKSPACE, "generated code runs sandboxed: the container"),
    "told.delivery_crew.implement_only_this_story_work_belonging": (GATE, "scope"),
    "told.delivery_crew.configuration_may_change_when_the_story": (
        GATE,
        "checks never weakened, on the diff",
    ),
    "told.delivery_crew.that_is_the_only_way_a": (GATE, "changed merged tests declared"),
    "§19 a_digest_cant_be_invented": (GATE, "an invented digest, on the diff"),
    "§19.5": (GATE, "checks never weakened, on the diff"),
    "§19.8": (GATE, "an image proven as it runs: CI and the deploy review"),
    "§7 done_means_merged_and_green": (GATE, "the gates and CI"),
    "§7 documentation_is_the_exception": (GATE, "docs, at `done` and in review"),
    "§7.2": (GATE, "the whole suite and lint, at `done`"),
    "§7.3": (GATE, "review"),
    "§7.4": (GATE, "CI and branch protection"),
    "told.delivery_crew.do_not_redesign_the_module_layout": (RETIRED, "the repair prompt"),
    "told.delivery_crew.that_changes_what_you_return_return": (RETIRED, "the repair prompt"),
    "told.delivery_crew.dont_also_delete_it_or_write": (
        RETIRED,
        "the crew deleting retired tests for a one-shot answer",
    ),
    "told.delivery_crew.for_an_existing_file_that_is": (RETIRED, "which answer field to use"),
    "told.delivery_crew.functions_and_classes_always_change_with": (
        RETIRED,
        "which answer field to use",
    ),
    "told.delivery_crew.to_remove_a_file_entirely_list": (RETIRED, "which answer field"),
    "told.delivery_crew.you_never_reproduce_code_you_are": (
        RETIRED,
        "re-sending unchanged code in one answer",
    ),
    "told.delivery_crew.if_you_need_one_in_full": (RETIRED, "asking for files"),
    "told.delivery_crew.if_you_need_one_to_do": (RETIRED, "reading within one answer"),
    "told.delivery_crew.only_if_you_cant_read_it": (RETIRED, "asking for files"),
    "told.delivery_crew.the_repository_below_may_show_some": (RETIRED, "the listing shown"),
    "told.form.Implementation.need_files": (RETIRED, "asking for files"),
    "told.form.Implementation.new_files": (RETIRED, "which answer field to use"),
    "told.form.Rework.already_satisfied": (
        RETIRED,
        "a rework's answer that nothing needs changing; `done` with evidence",
    ),
    "told.stepped.change_only_the_file_named_below": (RETIRED, "stepped delivery"),
    "told.stepped.the_plan_already_says_which_test": (RETIRED, "stepped delivery"),
    "told.stepped.use_exactly_the_names_and_signatures": (RETIRED, "stepped delivery"),
    # --- the constitution's text for the Developer's rules, told to no role -------------
    "§15.1": (GATE, "nothing already there is deleted: the contract gate, on the diff"),
    "§15.2": (GATE, "nothing changed silently: the contract gate"),
    "§15.3": (GATE, "declared changes are fine: the contract gate"),
    "§15 a_move_is_one_step_not": (GATE, "the gate reads a move in the diff as a move"),
    "§15 moving_a_definition_is_not_deleting": (GATE, "the contract gate, on the diff"),
    "§15 a_name_a_module_passes_along": (GATE, "the contract gate, on the diff"),
    "§15 a_story_can_declare_it_too": (GATE, "declared contract changes"),
    "§15 retiring_on_purpose": (GATE, "retired tests declared at `done`"),
    "§15 tests_retired": (GATE, "retired tests shown to the Code Reviewer"),
    "§15 18x_the_tokens_and_12x_the": (RETIRED, "why one answer shouldn't rewrite files"),
    "§15 a_first_attempt_starts_with_its": (RETIRED, "the answer form's order"),
    "§15 the_code_comes_back_in_blocks": (RETIRED, "code as blocks (ADR 0026)"),
    "§16 the_developer_sees_the_files_its": (RETIRED, "files shown, the rest asked for"),
    "§16 what_it_reads_shares_one_ceiling": (RETIRED, "one call's reading ceiling"),
}

# Rules that don't fit the four kinds, though the Developer meets them, and why.
UNFIT: dict[str, str] = {
    "told.developer.when_a_card_turns_out_to": (
        "a card too big or vague: it becomes `blocked(what's missing)`, a session's end"
    ),
    "story.return_to_refinement": "becomes `blocked`, routed to the Business Analyst",
    "escalation.policy": "escalation in a session is a later decision (decision 3)",
    "config.attempts": (
        "mixed: the Developer's repair count is retired by decision 3, while the backlog "
        "order's, settle's and the merge's counts stay"
    ),
    "§19.6": "'failure is loud': craft the session keeps telling, not a check",
    "§19.9": "a release can be run again: the DevOps Engineer's, not the Developer's",
    "§7.6": "the card's audit trail: the crew writes it, not the Developer",
    "told.delivery_crew.dont_write_a_test_that_matches": (
        "craft for writing tests: guidance the session keeps telling"
    ),
    "config.agents.developer.constitution": "which sections it's told: stays a setting",
    "config.agents.developer.llm": "its model: stays a setting",
    "config.agents.developer.llm_params": "its model's limits: stay a setting",
    "config.const.calls.ATTEMPTS": "the call layer's retries: every other role keeps them",
    "config.const.calls.ECHO_CHARS": "the call layer's echo: every other role keeps it",
    "config.const.calls.EMPTY_LIMIT": "the call layer's empty limit: other roles keep it",
    "config.const.calls.RUNAWAY_CHARS": "the call layer's runaway cut: other roles keep it",
    "config.const.calls.RUNAWAY_TAIL": "the call layer's runaway cut: other roles keep it",
    "config.const.claude_code.DEFAULT_TIMEOUT": "escalation's, a later decision",
    "config.org.escalation.may_escalate": "escalation, a later decision",
    "config.org.escalation.never_escalate": "escalation, a later decision",
    "config.org.sprint.escalation_budget": "escalation, a later decision",
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
    "call.split_blocks": "the Architect's and the split's code blocks, if any",
    "call.join_blocks": "the Architect's and the split's code blocks, if any",
    "call.the_json_before_the_blocks": "any form answered with blocks",
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
    "crews/delivery_crew.py",
    "crews/stepped.py",
    "flows/delivery.py",
    "flows/attempts.py",
    "tools/workspace.py",
    "tools/ast_edit.py",
    "tools/move_code.py",
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
