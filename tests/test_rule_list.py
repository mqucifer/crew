"""Every rule a role meets, listed from the code (crew#756).

The list is generated, so a rule added anywhere shows up on it, and the committed
page fails this test until `crew rules` is run.
"""

from __future__ import annotations

import ast

from crew_org import rule_list
from crew_org.rules import Rule


def ids() -> set[str]:
    return {e.id for e in rule_list.entries()}


def test_the_committed_list_is_the_codes():
    """Run `crew rules` after adding or changing a rule, or what a role is told."""
    assert rule_list.PAGE.read_text(encoding="utf-8") == rule_list.render(), (
        "docs/reference/rules.md is stale: run `crew rules`"
    )


def test_every_rule_id_is_unique_and_stable():
    found = [e.id for e in rule_list.entries()]
    assert len(found) == len(set(found))
    assert all(" " not in i or i.startswith("§") for i in found)


def test_every_decision_the_crew_records_is_listed_with_where_its_made():
    entries = {e.id: e for e in rule_list.entries()}
    for rule in Rule:
        assert rule.value in entries
    # Named outside the enum, in the return reasons' vocabulary.
    assert entries[Rule.RETURN_PINNED_TESTS.value].checked


def test_every_validator_on_a_form_a_role_answers_is_listed():
    for form in rule_list.answered():
        decorators = form.__pydantic_decorators__
        for dec in [*decorators.field_validators.values(), *decorators.model_validators.values()]:
            func = getattr(dec.func, "__func__", dec.func)
            owner, _, name = func.__qualname__.rpartition(".")
            assert f"form.{owner}.{name.lstrip('_')}" in ids()


def test_every_refusal_a_crew_step_raises_is_on_the_list():
    """A refusal raised in a crew's module, or in the edit tools, belongs to some rule."""
    listed = {m for e in rule_list.entries() for m in e.messages}
    missing = []
    for source in rule_list.sources():
        if not source.module.startswith(
            ("crew_org.crews.", "crew_org.tools.workspace", "crew_org.tools.ast_edit")
        ):
            continue
        for node in ast.walk(source.tree):
            raised = rule_list._refusal(node)
            if raised is None:
                continue
            message = rule_list._template(raised.args[0], source.tree)
            if rule_list.pattern(message) is not None and message not in listed:
                missing.append(f"{source.path}:{node.lineno} {message[:60]}")
    assert not missing, missing


def test_each_kind_of_rule_is_found():
    found = ids()
    assert "form.CriterionTest.is_that_test" in found
    assert "call.check_not_cut_at_a_quote" in found
    assert "call.schema" in found
    assert "check.design.shelled" in found
    assert "edit.workspace.outside_definitions" in found
    assert "guard.overwrite.overwrites_existing" in found
    assert "skip.refine_panel.waits_for_to_be_split_it" in found, "a hold said only in words"
    assert any(i.startswith("told.developer.") for i in found)
    assert any(i.startswith("told.delivery_crew.") for i in found)
    assert "§19.4" in found or any("§19.4" in t for e in rule_list.entries() for t in e.told)


def test_a_field_description_tells_the_rule_that_checks_that_field():
    entry = next(e for e in rule_list.entries() if e.id == "form.Story.enough_criteria")
    assert "`Story.acceptance_criteria`'s description" in entry.told
    assert entry.roles == {"Business Analyst"}


def test_a_check_and_a_told_rule_join_where_the_code_cites_its_section():
    shelled = next(e for e in rule_list.entries() if e.id == "check.design.shelled")
    assert any(t.startswith("§19.4 (both cite §19.4") for t in shelled.told)
    assert shelled.roles == {"Architect"}


def test_a_link_found_only_by_wording_is_a_maybe():
    told = [e for e in rule_list.entries() if e.group == "told"]
    linked = [c for e in told for c in e.checked if not c.startswith(("maybe ", "named in"))]
    assert not linked, "a told rule linked by name belongs on the check's row"


def test_every_rule_that_refuses_or_decides_has_a_category():
    """Only what a role is told, or a setting, can be unplaced; a check is placed by the table."""
    unplaced = [
        e.id for e in rule_list.entries() if not e.category and e.group not in ("told", "config")
    ]
    assert not unplaced, unplaced
    assert {e.category for e in rule_list.entries()} <= {"", *rule_list.CATEGORIES}


def test_a_rule_is_placed_by_what_it_protects():
    placed = {e.id: e.category for e in rule_list.entries()}
    assert placed["call.schema"] == "answer_shape"
    assert placed["hold.sibling"] == "flow"
    assert placed["form.FileWrite.stays_in_the_repository"] == "guards"
    assert placed["form.CriterionTest.is_that_test"] == "evidence"
    assert placed["edit.workspace.plan_text_edits"] == "edits"
    assert placed["guard.regression.broken_contracts"] == "contracts"
    assert placed["form.Plan.one_step_per_file"] == "scope"
    assert placed["§19.6"] == "guards", "by its constitution section"
    assert placed["told.delivery_crew.functions_and_classes_always_change_with"] == "edits"


def test_every_site_in_the_told_table_places_a_line():
    """A site no told line sits at any more is a stale entry in the table."""
    sites = {rule_list._told_site(e) and e.where for e in rule_list.entries() if e.group == "told"}
    for site in rule_list._TOLD_SITES:
        module, _, name = site.partition(":")
        if module == "agents.yaml":
            assert any(f"agents.yaml:{name}." in s for s in sites if s), site
        else:
            assert any(f"/{module}.py:{name}" in s for s in sites if s), site


def test_a_hold_said_only_in_words_is_checked_by_what_its_branch_tests():
    """crew#773's hold: an epic waits for a sibling it builds on, by builds_on_unsplit."""
    hold = next(
        e for e in rule_list.entries() if e.id == "skip.refine_panel.waits_for_to_be_split_it"
    )
    assert hold.checked == {"src/crew_org/flows/refine_panel.py:builds_on_unsplit"}
    assert hold.category == "flow" and "crew#773" in hold.origin
