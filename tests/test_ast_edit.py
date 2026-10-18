"""Replacing by name: the session's `replace_definition`.

The point of this format is that the model reproduces nothing: it names a
definition and supplies the new source. Everything it does not name is
untouched by construction, which is why deletion and drift stop being possible
rather than being detected afterwards.
"""

from __future__ import annotations

import ast

import pytest

from crew_org.tools.ast_edit import (
    Edit,
    EditError,
    apply_edit,
)

SOURCE = '''"""A module."""
import json

VERSION = 1


class Card:
    """A card."""

    def is_done(self):
        return True

    def age(self):
        return 0


def existing(cards):
    return len(cards)
'''


def parses(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


# --- addressing ----------------------------------------------------------


# --- replace -------------------------------------------------------------


def test_replacing_a_function_leaves_everything_else_alone():
    out = apply_edit(SOURCE, Edit("existing", "def existing(cards):\n    return 0"))
    assert "return 0" in out
    assert "class Card" in out and "VERSION = 1" in out
    assert parses(out)


def test_replacing_something_that_does_not_exist_says_what_does():
    with pytest.raises(EditError, match="no definition named"):
        apply_edit(SOURCE, Edit("absent", "def absent():\n    pass"))


def test_the_error_lists_the_available_names():
    with pytest.raises(EditError) as exc:
        apply_edit(SOURCE, Edit("absent", "x = 1"))
    assert "existing" in str(exc.value)


def test_replacing_with_nothing_is_refused():
    """Silently emptying a definition is how code disappears."""
    with pytest.raises(EditError, match="use `replace_text`"):
        apply_edit(SOURCE, Edit("existing", "   \n"))


# --- add -----------------------------------------------------------------


# --- imports -------------------------------------------------------------


# --- delete --------------------------------------------------------------


# --- sequences -----------------------------------------------------------


# --- a constant with a type annotation is addressable too -----------------------------------------

ANNOTATED = """DEFAULT_THRESHOLDS: dict[str, float] = {
    "cycle_time": 5.0,
}


def calculate_flags(cards):
    return DEFAULT_THRESHOLDS
"""


def test_an_annotated_constant_can_be_replaced_by_name():

    source = 'DEFAULT_THRESHOLDS: dict[str, float] = {"cycle_time": 3.0}'
    after = apply_edit(ANNOTATED, Edit(target="DEFAULT_THRESHOLDS", source=source))
    assert '{"cycle_time": 3.0}' in after and '"cycle_time": 5.0' not in after


# --- an added definition goes before the file's __main__ block (crew#497) ----------------------
