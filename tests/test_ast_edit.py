"""Editing by name.

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
    Operation,
    apply_edit,
    apply_edits,
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
    out = apply_edit(
        SOURCE, Edit(Operation.REPLACE, "existing", "def existing(cards):\n    return 0")
    )
    assert "return 0" in out
    assert "class Card" in out and "VERSION = 1" in out
    assert parses(out)


def test_replacing_something_that_does_not_exist_says_what_does():
    with pytest.raises(EditError, match="no definition named"):
        apply_edit(SOURCE, Edit(Operation.REPLACE, "absent", "def absent():\n    pass"))


def test_the_error_lists_the_available_names():
    with pytest.raises(EditError) as exc:
        apply_edit(SOURCE, Edit(Operation.REPLACE, "absent", "x = 1"))
    assert "existing" in str(exc.value)


def test_replacing_with_nothing_is_refused():
    """Silently emptying a definition is how code disappears."""
    with pytest.raises(EditError, match="use delete"):
        apply_edit(SOURCE, Edit(Operation.REPLACE, "existing", "   \n"))


# --- add -----------------------------------------------------------------


def test_adding_a_name_that_exists_is_refused():
    with pytest.raises(EditError, match="already exists"):
        apply_edit(SOURCE, Edit(Operation.ADD, "existing", "def existing():\n    pass"))


def test_adding_a_method_to_a_missing_class_is_refused():
    with pytest.raises(EditError, match="no class named"):
        apply_edit(SOURCE, Edit(Operation.ADD_METHOD, "Absent.thing", "def thing(self):\n    pass"))


# --- imports -------------------------------------------------------------


def test_an_import_lands_after_the_existing_imports():
    out = apply_edit(SOURCE, Edit(Operation.ADD_IMPORT, "date", "from datetime import date"))
    lines = out.splitlines()
    assert lines.index("from datetime import date") > lines.index("import json")
    assert parses(out)


def test_adding_an_import_twice_is_not_an_error():
    once = apply_edit(SOURCE, Edit(Operation.ADD_IMPORT, "json", "import json"))
    assert once.count("import json") == 1


def test_an_import_into_a_file_with_none_lands_after_the_docstring():
    source = '"""Doc."""\n\n\ndef f():\n    return 1\n'
    out = apply_edit(source, Edit(Operation.ADD_IMPORT, "os", "import os"))
    assert parses(out)
    assert out.index("import os") > out.index('"""Doc."""')


def test_an_empty_import_is_refused():
    with pytest.raises(EditError, match="needs the import statement"):
        apply_edit(SOURCE, Edit(Operation.ADD_IMPORT, "x", "  "))


# --- delete --------------------------------------------------------------


# --- sequences -----------------------------------------------------------


def test_spans_do_not_go_stale_between_edits():
    """The file is re-parsed per edit, so an earlier splice cannot shift a later one."""
    out = apply_edits(
        SOURCE,
        [
            Edit(Operation.ADD_IMPORT, "os", "import os"),
            Edit(Operation.ADD_IMPORT, "sys", "import sys"),
            Edit(Operation.REPLACE, "Card.age", "def age(self):\n    return 7"),
        ],
    )
    assert parses(out)
    assert "return 7" in out


def test_a_failing_edit_does_not_corrupt_the_file():
    with pytest.raises(EditError):
        apply_edits(SOURCE, [Edit(Operation.REPLACE, "absent", "def absent():\n    pass")])
    assert parses(SOURCE)
