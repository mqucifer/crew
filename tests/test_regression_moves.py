"""A definition moved to another module is not removed (#202).

The Architect's first refactor, sprint-metrics#123, splits one module by
concern. Its first story moved `Card` and `parse_card` into `card.py`, and the
contract check refused it twice as "removed entirely": it read one file at a
time, so a move looked like a deletion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import FileEdit, FileWrite, Implementation
from crew_org.tools.regression import (
    REMOVED,
    broken_contracts,
    describe_contracts,
    without_moves,
)

PKG = "src/sprint_metrics"
OLD = f"{PKG}/crew_performance.py"

CARD = """from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    created: str
    started: str | None = None

    @property
    def is_completed(self) -> bool:
        return False


def parse_card(raw):
    return Card(created=raw["created"])
"""

MODULE = (
    '"""Everything, in one module."""\n'
    + CARD
    + """

def calculate_throughput(cards):
    return sum(1 for c in cards if c.is_completed)
"""
)


def impl(**parts) -> Implementation:
    return Implementation(summary="Move card handling into card.py", **parts)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / OLD).write_text(MODULE)
    (tmp_path / PKG / "__init__.py").write_text(
        "from sprint_metrics.crew_performance import Card, calculate_throughput, parse_card\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_cards.py").write_text(
        "from sprint_metrics.crew_performance import Card\n\n\ndef test_card():\n    Card('x')\n"
    )
    return tmp_path


def delete(name: str, path: str = OLD) -> FileEdit:
    return FileEdit(path=path, operation="delete", target=name)


def import_back(source: str, path: str = OLD) -> FileEdit:
    return FileEdit(path=path, operation="add_import", target="card", source=source)


def check(repo: Path, implementation: Implementation) -> dict:
    broken = broken_contracts(repo, implementation.all_edits)
    return without_moves(repo, implementation, broken)


def moved_to_card(*extra: FileEdit, card: str = CARD) -> Implementation:
    return impl(
        new_files=[FileWrite(path=f"{PKG}/card.py", content=card)],
        edits=[delete("Card"), delete("parse_card"), *extra],
    )


def test_a_definition_moved_and_imported_back_is_kept(repo: Path):
    """What sprint-metrics#125 should have been allowed to do."""
    implementation = moved_to_card(import_back("from sprint_metrics.card import Card, parse_card"))
    assert check(repo, implementation) == {}


def test_a_relative_import_back_counts_too(repo: Path):
    assert check(repo, moved_to_card(import_back("from .card import Card, parse_card"))) == {}


def test_a_move_the_old_module_doesnt_import_back_is_still_a_removal(repo: Path):
    """The test file still imports Card from the old module; it would break."""
    broken = check(repo, moved_to_card())
    assert set(broken) == {f"{OLD}::Card", f"{OLD}::parse_card"}
    assert all(now == REMOVED for _was, now in broken.values())


def test_moving_is_no_way_round_keeping_a_shape(repo: Path):
    reshaped = CARD.replace("    started: str | None = None\n", "")
    implementation = moved_to_card(
        import_back("from sprint_metrics.card import Card, parse_card"), card=reshaped
    )
    assert set(check(repo, implementation)) == {f"{OLD}::Card"}


def test_a_real_removal_is_still_refused(repo: Path):
    broken = check(repo, impl(edits=[delete("calculate_throughput")]))
    assert [now for _was, now in broken.values()] == [REMOVED]
    assert set(broken) == {f"{OLD}::calculate_throughput"}


def test_a_name_still_asked_of_the_old_module_is_a_removal(repo: Path):
    """The package re-homes it, but the tests still import it from the old module."""
    (repo / PKG / "__init__.py").write_text(
        "from .card import Card, parse_card\nfrom .crew_performance import calculate_throughput\n"
    )
    (repo / PKG / "card.py").write_text(CARD)
    assert f"{OLD}::Card" in check(repo, impl(edits=[delete("Card")]))


def test_importing_the_old_module_whole_keeps_every_name_it_had(repo: Path):
    (repo / "tests/test_cards.py").write_text(
        "from sprint_metrics import crew_performance\n\n\ndef test_card():\n"
        "    crew_performance.Card('x')\n"
    )
    (repo / PKG / "__init__.py").write_text("from .card import Card, parse_card\n")
    (repo / PKG / "card.py").write_text(CARD)
    assert f"{OLD}::Card" in check(repo, impl(edits=[delete("Card")]))


def test_the_package_rehoming_it_is_enough_once_nobody_asks_the_old_module(repo: Path):
    """What emptying the old module (sprint-metrics#132) needs. Other names may stay."""
    (repo / "tests/test_cards.py").write_text(
        "from sprint_metrics import Card\n\n\ndef test_card():\n    Card('x')\n"
    )
    (repo / PKG / "__init__.py").write_text(
        "from .card import Card, parse_card\nfrom .crew_performance import calculate_throughput\n"
    )
    (repo / PKG / "card.py").write_text(CARD)
    implementation = impl(edits=[delete("Card"), delete("parse_card")])
    assert check(repo, implementation) == {}


def test_a_change_that_wont_apply_leaves_the_removals_standing(repo: Path):
    implementation = moved_to_card(delete("no_such_thing"))
    assert set(check(repo, implementation)) == {f"{OLD}::Card", f"{OLD}::parse_card"}


def test_the_developer_is_told_how_to_move_a_definition():
    text = describe_contracts({f"{OLD}::Card": ("class() dataclass {created}", REMOVED)})
    assert "Moving a definition to another module is fine" in text
    assert "import it back" in text and "Never keep both copies" in text


def test_a_signature_change_says_nothing_about_moving():
    text = describe_contracts({f"{OLD}::parse_card": ("def(raw)", "parameters (raw) became ()")})
    assert "Moving a definition" not in text


def test_the_move_is_judged_on_a_copy_and_the_worktree_is_untouched(repo: Path):
    check(repo, moved_to_card(import_back("from .card import Card, parse_card")))
    assert (repo / OLD).read_text() == MODULE and not (repo / PKG / "card.py").exists()


# --- the shapes sprint-metrics#125 actually sent -------------------------------------------------


def test_a_class_and_each_of_its_methods_deleted_is_one_move(repo: Path):
    """#125 deleted `Card`, then `Card.is_completed`, and was refused for both."""
    implementation = moved_to_card(
        delete("Card.is_completed"),
        import_back("from sprint_metrics.card import Card, parse_card"),
    )
    assert check(repo, implementation) == {}


def test_a_method_deleted_from_a_class_that_stays_is_still_a_removal(repo: Path):
    broken = check(repo, impl(edits=[delete("Card.is_completed")]))
    assert set(broken) == {f"{OLD}::Card.is_completed"}


def test_a_quoted_deletion_a_named_delete_already_made_is_done(repo: Path):
    """#125 also quoted `@dataclass(frozen=True)` for deletion after deleting `Card` by name."""
    from crew_org.crews.delivery_crew import TextEdit
    from crew_org.tools.workspace import apply_implementation

    implementation = moved_to_card(
        import_back("from sprint_metrics.card import Card, parse_card"),
    )
    implementation.text_edits = [
        TextEdit(path=OLD, find="@dataclass(frozen=True)\nclass Card:\n", replace="")
    ]
    apply_implementation(repo, implementation)
    assert "class Card" not in (repo / OLD).read_text()


def test_a_quoted_change_to_text_that_is_gone_is_still_refused(repo: Path):
    from crew_org.crews.delivery_crew import TextEdit
    from crew_org.tools.ast_edit import EditError
    from crew_org.tools.workspace import apply_implementation

    implementation = moved_to_card(import_back("from .card import Card, parse_card"))
    implementation.text_edits = [
        TextEdit(path=OLD, find="@dataclass(frozen=True)\n", replace="@dataclass\n")
    ]
    with pytest.raises(EditError, match="not in the file"):
        apply_implementation(repo, implementation)


def test_naming_a_files_entry_block_points_at_text_edits():
    """#133 aimed at `__main__` and `__init__` by name, three times."""
    from crew_org.tools.ast_edit import Edit, EditError, Operation, apply_edits

    main = 'import sys\n\nfrom x import main\n\nif __name__ == "__main__":\n    sys.exit(main())\n'
    with pytest.raises(EditError, match="quote the lines in `text_edits`"):
        apply_edits(main, [Edit(operation=Operation.REPLACE, target="__main__", source="x")])
