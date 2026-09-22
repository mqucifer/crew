"""Detecting an implementation that destroys existing work.

A model asked to add one metric will happily redesign the module it is adding
to. Observed on story #7, which rewrote story #6's merged code and renamed its
public functions; and again on story #9, which turned three properties into
methods and left thirteen merged tests failing.

This is checked mechanically because a prompt asking the model not to do it is a
request, not a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from crew_org.tools.regression import (
    broken_contracts,
    describe_contracts,
    signatures_for_context,
)


@dataclass
class Written:
    path: str
    content: str


BEFORE = '''
"""Story 6's module, merged and tested."""
from datetime import date

VERSION = 1


class Card:
    pass


def _parse_date(value):
    return None


def calculate_cycle_time_and_lead_time(card):
    return None, None


def format_performance_table(cards):
    return ""
'''


# --- what counts as public -----------------------------------------------


# --- the regression itself ----------------------------------------------


# --- against a worktree --------------------------------------------------


# --- what the Developer is told -----------------------------------------


# --- silent edits, not just deletions ------------------------------------


# --- contracts other code already depends on -----------------------------
#
# Story #9 is the case these are written from: asked to add one column, the
# Developer changed a frozen dataclass, turned three properties into methods,
# changed two return types from `int` to `float | None`, and gave
# format_performance_table five scalar arguments in place of two. The private
# helper consuming those values was never touched, so it summed integers over a
# list of None and thirteen merged tests died of a TypeError.

MODULE = """
from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    created: date
    started: date | None = None

    @property
    def cycle_time(self) -> int:
        return 0


def format_performance_table(cards, wip_limits=None) -> str:
    return "table"


def _mean_days(values):
    return sum(values) / len(values)
"""


@dataclass
class Ed:
    path: str
    operation: str
    target: str
    source: str = ""


@pytest.fixture
def module(tmp_path):
    (tmp_path / "m.py").write_text(MODULE)
    return tmp_path


def test_rewriting_a_body_is_allowed(module):
    """Stories are made of body edits. A check that refused them refuses
    everything."""
    edit = Ed(
        "m.py",
        "replace",
        "format_performance_table",
        "def format_performance_table(cards, wip_limits=None) -> str:\n"
        '    return "a table with one more column"\n',
    )
    assert broken_contracts(module, [edit]) == {}


def test_changing_a_parameter_list_is_refused(module):
    edit = Ed(
        "m.py",
        "replace",
        "format_performance_table",
        "def format_performance_table(cycle_time, lead_time, throughput) -> str:\n"
        '    return "table"\n',
    )
    broken = broken_contracts(module, [edit])
    assert "m.py::format_performance_table" in broken
    was, now = broken["m.py::format_performance_table"]
    assert "cards, wip_limits" in was
    assert "cycle_time" in now


def test_a_property_becoming_a_method_is_refused(module):
    """The exact change that broke story #9: callers write `card.cycle_time`,
    and afterwards they must write `card.cycle_time()`."""
    edit = Ed(
        "m.py",
        "replace",
        "Card.cycle_time",
        "def cycle_time(self) -> float | None:\n    return None\n",
    )
    broken = broken_contracts(module, [edit])
    assert "m.py::Card.cycle_time" in broken
    was, broke = broken["m.py::Card.cycle_time"]
    assert "property" in was
    assert "every caller has to change" in broke


def test_adding_a_field_to_a_dataclass_is_refused(module):
    """A dataclass's fields are its constructor, so a new one is a new call."""
    edit = Ed(
        "m.py",
        "replace",
        "Card",
        "@dataclass\nclass Card:\n    title: str\n    created: date\n"
        "    started: date | None = None\n",
    )
    assert "m.py::Card" in broken_contracts(module, [edit])


def test_deleting_a_public_definition_is_refused(module):
    broken = broken_contracts(module, [Ed("m.py", "delete", "format_performance_table")])
    assert broken["m.py::format_performance_table"][1] == "removed entirely"


def test_adding_a_new_definition_is_not_a_contract_break(module):
    edit = Ed(
        "m.py",
        "add",
        "calculate_blocked_aging",
        "def calculate_blocked_aging(cards):\n    return 0\n",
    )
    assert broken_contracts(module, [edit]) == {}


def test_a_private_helper_has_no_contract_to_break(module):
    """`_mean_days` is the author's business. Only names other code can import
    are promises."""
    edit = Ed(
        "m.py", "replace", "_mean_days", "def _mean_days(values, default=0):\n    return default\n"
    )
    assert broken_contracts(module, [edit]) == {}


def test_a_target_that_does_not_exist_is_not_reported_here(module):
    """Inventing a name is a different failure, and the edit machinery already
    reports it as one. Reporting it twice would spend two attempts on it."""
    assert broken_contracts(module, [Ed("m.py", "replace", "no_such_thing", "def x(): pass")]) == {}


def test_the_message_names_the_definition_and_both_shapes(module):
    edit = Ed(
        "m.py",
        "replace",
        "format_performance_table",
        "def format_performance_table(a) -> str:\n    return ''\n",
    )
    message = describe_contracts(broken_contracts(module, [edit]))
    assert "format_performance_table" in message
    assert "cards, wip_limits" in message
    assert "Adding is fine" in message


def test_the_context_shows_what_the_check_will_judge(module):
    """The model is told the contract before it writes, not only after it has
    broken one. A constraint never stated is not one the model can respect."""
    signatures = signatures_for_context(MODULE)
    assert signatures["format_performance_table"] == "(cards, wip_limits)"
    assert signatures["Card.cycle_time"] == "(self) [property]"
    assert "_mean_days" not in signatures


def test_adding_a_field_with_a_default_is_allowed(module):
    """The case a live run found, after this check refused the story it was
    written to protect. Story #9 reports aging measured from `blocked_since`,
    so `Card` cannot not grow that field. The model had listened — every
    property still a property, nothing else touched — and was told no anyway."""
    edit = Ed(
        "m.py",
        "replace",
        "Card",
        "@dataclass(frozen=True)\nclass Card:\n    created: date\n"
        "    started: date | None = None\n    blocked_since: date | None = None\n\n"
        "    @property\n    def cycle_time(self) -> int:\n        return 0\n",
    )
    assert broken_contracts(module, [edit]) == {}


def test_a_new_field_without_a_default_is_refused(module):
    """Every existing construction call omits it, so every one of them breaks."""
    edit = Ed(
        "m.py",
        "replace",
        "Card",
        "@dataclass(frozen=True)\nclass Card:\n    created: date\n"
        "    started: date | None = None\n    title: str\n\n"
        "    @property\n    def cycle_time(self) -> int:\n        return 0\n",
    )
    assert "no default" in broken_contracts(module, [edit])["m.py::Card"][1]


def test_reordering_existing_fields_is_refused(module):
    """A dataclass's field order is its positional constructor: reordering
    silently reassigns arguments at call sites that never changed."""
    edit = Ed(
        "m.py",
        "replace",
        "Card",
        "@dataclass(frozen=True)\nclass Card:\n    started: date | None = None\n"
        "    created: date = None\n\n"
        "    @property\n    def cycle_time(self) -> int:\n        return 0\n",
    )
    assert "order" in broken_contracts(module, [edit])["m.py::Card"][1]


def test_adding_a_property_to_an_existing_class_is_allowed(module):
    edit = Ed(
        "m.py",
        "replace",
        "Card",
        "@dataclass(frozen=True)\nclass Card:\n    created: date\n"
        "    started: date | None = None\n\n"
        "    @property\n    def cycle_time(self) -> int:\n        return 0\n\n"
        "    @property\n    def blocked_aging(self) -> int:\n        return 0\n",
    )
    assert broken_contracts(module, [edit]) == {}


def test_adding_an_optional_parameter_is_allowed(module):
    edit = Ed(
        "m.py",
        "replace",
        "format_performance_table",
        "def format_performance_table(cards, wip_limits=None, today=None) -> str:\n"
        '    return "table"\n',
    )
    assert broken_contracts(module, [edit]) == {}


def test_adding_a_required_parameter_is_refused(module):
    edit = Ed(
        "m.py",
        "replace",
        "format_performance_table",
        'def format_performance_table(cards, wip_limits, today) -> str:\n    return "table"\n',
    )
    assert (
        "required parameter"
        in broken_contracts(module, [edit])["m.py::format_performance_table"][1]
    )


def test_removing_a_method_from_a_class_is_refused(module):
    edit = Ed(
        "m.py",
        "replace",
        "Card",
        "@dataclass(frozen=True)\nclass Card:\n    created: date\n"
        "    started: date | None = None\n",
    )
    assert "removes" in broken_contracts(module, [edit])["m.py::Card"][1]
