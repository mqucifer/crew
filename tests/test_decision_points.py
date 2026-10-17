"""Every decision point in the tick's code is a declared rule's, or reviewed as not one (crew#756).

The rules list finds rules by the shapes they take, and missed one in a shape it
didn't know (crew#773's hold). This is the independent check: a new branch that
ends or redirects a card's handling fails here until its code names its rule or
the table says what it is.
"""

from __future__ import annotations

from crew_org import decision_points, rulebook


def test_every_decision_point_is_a_rules_or_reviewed():
    """Name its rule in the code, or enter it in config/rules/decision_points.yaml."""
    missing = [
        f"{p.path}:{p.line} {p.kind} if {p.condition[:80]}" for p in decision_points.unresolved()
    ]
    assert not missing, missing


def test_the_table_has_no_entry_for_a_point_that_is_gone():
    assert not decision_points.stale()


def test_each_entry_is_a_rule_or_says_why_it_isnt():
    for point, entry in decision_points.table().items():
        said = {k for k in entry if k in ("rule", "not_a_rule")}
        assert len(said) == 1, point
        assert entry.get("rule") in rulebook.catalog() or entry.get("not_a_rule", "").strip(), point


def test_crew773s_hold_is_found_as_a_decision_point():
    """The shape the list once missed: a hold said only in words."""
    found = [
        p
        for p in decision_points.points()
        if p.function == "panel_step"
        and "builds_on_unsplit" not in p.condition
        and p.kind == "result:skipped"
    ]
    assert any("skip.refine_panel.waits_for_to_be_split_it" in p.evidence for p in found)


def test_a_card_filter_is_a_decision_point():
    """A silent filter decides which cards a phase takes: approved_epics is one."""
    filters = [
        p for p in decision_points.points() if p.kind == "filter" and p.function == "approved_epics"
    ]
    assert filters


def test_every_flow_decision_is_counted_by_its_rule():
    """crew#756: a flow decision names its rule on its event, or as a hold for the tick's end."""
    assert decision_points.unnamed("flow") == []
