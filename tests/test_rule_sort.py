"""Every rule is sorted by what it becomes in the Developer's session (crew#778)."""

from __future__ import annotations

from crew_org import rule_list, rule_sort


def test_every_rule_has_a_kind_or_says_why_it_has_none():
    unsorted = [e.id for e in rule_list.entries() if rule_sort.kind(e)[0] == ""]
    assert not unsorted, f"the Developer meets these; sort them in rule_sort: {unsorted}"


def test_the_table_names_only_rules_the_catalog_has():
    ids = {e.id for e in rule_list.entries()}
    stale = (set(rule_sort.SORTED) | set(rule_sort.UNFIT) | set(rule_sort.SHARED)) - ids
    assert not stale, sorted(stale)


def test_each_sorted_rule_is_one_of_the_four_kinds_with_a_reason():
    for rule, (kind, why) in rule_sort.SORTED.items():
        assert kind in rule_sort.KINDS and why.strip(), rule
    assert not set(rule_sort.SORTED) & set(rule_sort.UNFIT)
