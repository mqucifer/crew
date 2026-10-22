"""The records reference's inventory of events, read from the code (crew#870).

The Sponsor, 2026-10-22: "we can't trim if we don't know what we even have." Each kind
says what it means, what it carries and whether delivery-history publishes it, and a
kind added without saying fails here.
"""

from __future__ import annotations

from enum import StrEnum

from crew_org import event_list
from crew_org.events import FIELDS, EventKind


def test_every_kind_says_what_it_means():
    assert event_list.undescribed() == []


class _Added(StrEnum):
    SAID = "said"
    """It means something."""
    UNSAID = "unsaid"


def test_a_kind_added_without_a_meaning_is_found():
    assert event_list.undescribed(_Added) == ["UNSAID"]  # type: ignore[arg-type]


def test_every_kind_declares_its_fields():
    assert set(FIELDS) == set(EventKind)


def test_every_field_a_writer_names_is_declared_for_its_kind():
    """A note's fields are its site's, listed by site on the page instead."""
    undeclared = {
        kind.value: sorted(set(found.fields) - set(FIELDS[kind]))
        for kind, found in event_list.written().items()
        if kind is not EventKind.NOTE and set(found.fields) - set(FIELDS[kind])
    }
    assert undeclared == {}


def test_every_rule_a_note_names_has_a_meaning():
    rules = {n.rule for n in event_list.notes() if n.rule is not None}
    assert rules, "notes name rules"
    assert sorted(r for r in rules if not event_list.rule_meaning(r)) == []


def test_every_part_of_delivery_history_is_reached_from_an_event_kind_or_named():
    """The package's mapping read whole: a part no kind reaches is one built from
    something else, and named on the page. `pull_merged` is GitHub's merges."""
    assert event_list.parts_from_elsewhere() == ["pull_merged"]


def test_what_delivery_history_makes_of_a_kind_is_its_own_mapping():
    published = event_list.published()
    assert published[EventKind.WORK_SENT_BACK] == "as `sent_back`"
    assert published[EventKind.AGENT_STARTED] == "as `work_started`"
    assert published[EventKind.SESSION_ENDED] == "as `session_ended`"
    assert published[EventKind.EPIC_RESPLIT] == "read by `_superseded_because`"
    assert EventKind.ROLE_TASK_OPENED not in published


def test_a_note_is_found_by_its_rule_or_its_words():
    notes = event_list.notes()
    assert any(n.rule == "decision.merge.conflict" for n in notes)
    said = [n for n in notes if n.rule is None and n.site.function == "comment"]
    assert said and said[0].words == "commented"
