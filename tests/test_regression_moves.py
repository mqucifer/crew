"""A definition moved to another module is not removed (#202).

The Architect's first refactor, sprint-metrics#123, splits one module by
concern. Its first story moved `Card` and `parse_card` into `card.py`, and the
contract check refused it twice as "removed entirely": it read one file at a
time, so a move looked like a deletion. The gate at `done` reads the whole branch
(crew#778).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.tools.regression import (
    REMOVED,
    describe_contracts,
    draft_breaks,
    merged_base,
    without_moves,
)
from tests.test_regression import Ed, change, git

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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The module merged on main, and a story branch off it."""
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / OLD).write_text(MODULE)
    (tmp_path / PKG / "__init__.py").write_text(
        "from sprint_metrics.crew_performance import Card, calculate_throughput, parse_card\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_cards.py").write_text(
        "from sprint_metrics.crew_performance import Card\n\n\ndef test_card():\n    Card('x')\n"
    )
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/125")
    return tmp_path


def delete(name: str, path: str = OLD) -> Ed:
    return Ed(path, "delete", name)


def import_back(statement: str, path: str = OLD) -> Ed:
    return Ed(path, "import", "", statement)


def check(repo: Path, *edits: Ed) -> dict:
    """The contract gate's findings once `edits` are on the branch, moves excused."""
    for ed in edits:
        if ed.operation == "import":
            path = repo / ed.path
            path.write_text(ed.source + "\n" + path.read_text())
        elif ed.operation == "write":
            (repo / ed.path).write_text(ed.source)
        else:
            change(repo, ed)
    merged = merged_base(repo)
    return without_moves(repo, draft_breaks(repo, merged), merged)


def moved_to_card(*extra: Ed, card: str = CARD) -> list[Ed]:
    return [Ed(f"{PKG}/card.py", "write", "", card), delete("Card"), delete("parse_card"), *extra]


def test_a_definition_moved_and_imported_back_is_kept(repo: Path):
    """What sprint-metrics#125 should have been allowed to do."""
    edits = moved_to_card(import_back("from sprint_metrics.card import Card, parse_card"))
    assert check(repo, *edits) == {}


def test_a_relative_import_back_counts_too(repo: Path):
    assert check(repo, *moved_to_card(import_back("from .card import Card, parse_card"))) == {}


def test_a_move_the_old_module_doesnt_import_back_is_still_a_removal(repo: Path):
    """The test file still imports Card from the old module; it would break."""
    broken = check(repo, *moved_to_card())
    assert set(broken) == {f"{OLD}::Card", f"{OLD}::parse_card"}
    now = broken[f"{OLD}::Card"][1]
    assert now.startswith(REMOVED) and "doesn't import it back" in now
    assert f"add `from sprint_metrics.card import Card` to `{OLD}`" in now
    assert f"still gets it from `{OLD}`" in now


def test_moving_is_no_way_round_keeping_a_shape(repo: Path):
    reshaped = CARD.replace("    started: str | None = None\n", "")
    edits = moved_to_card(
        import_back("from sprint_metrics.card import Card, parse_card"), card=reshaped
    )
    assert set(check(repo, *edits)) == {f"{OLD}::Card"}


def test_a_real_removal_is_still_refused(repo: Path):
    broken = check(repo, delete("calculate_throughput"))
    (now,) = [now for _was, now in broken.values()]
    assert now.startswith(REMOVED) and "no other module defines `calculate_throughput`" in now
    assert set(broken) == {f"{OLD}::calculate_throughput"}


def test_a_name_still_asked_of_the_old_module_is_a_removal(repo: Path):
    """The package re-homes it, but the tests still import it from the old module."""
    (repo / PKG / "__init__.py").write_text(
        "from .card import Card, parse_card\nfrom .crew_performance import calculate_throughput\n"
    )
    (repo / PKG / "card.py").write_text(CARD)
    assert f"{OLD}::Card" in check(repo, delete("Card"))


def test_importing_the_old_module_whole_keeps_every_name_it_had(repo: Path):
    (repo / "tests/test_cards.py").write_text(
        "from sprint_metrics import crew_performance\n\n\ndef test_card():\n"
        "    crew_performance.Card('x')\n"
    )
    (repo / PKG / "__init__.py").write_text("from .card import Card, parse_card\n")
    (repo / PKG / "card.py").write_text(CARD)
    assert f"{OLD}::Card" in check(repo, delete("Card"))


def test_the_package_rehoming_it_is_enough_once_nobody_asks_the_old_module(repo: Path):
    """What emptying the old module (sprint-metrics#132) needs. Other names may stay."""
    (repo / "tests/test_cards.py").write_text(
        "from sprint_metrics import Card\n\n\ndef test_card():\n    Card('x')\n"
    )
    (repo / PKG / "__init__.py").write_text(
        "from .card import Card, parse_card\nfrom .crew_performance import calculate_throughput\n"
    )
    (repo / PKG / "card.py").write_text(CARD)
    assert check(repo, delete("Card"), delete("parse_card")) == {}


def test_the_finding_says_how_to_move_a_definition():
    text = describe_contracts({f"{OLD}::Card": ("class() dataclass {created}", REMOVED)})
    assert "Moving a definition to another module is fine" in text
    assert "import it back" in text and "Never keep both copies" in text


def test_a_signature_change_says_nothing_about_moving():
    text = describe_contracts({f"{OLD}::parse_card": ("def(raw)", "parameters (raw) became ()")})
    assert "Moving a definition" not in text


def test_a_method_deleted_from_a_class_that_stays_is_still_a_removal(repo: Path):
    broken = check(repo, delete("Card.is_completed"))
    assert set(broken) == {f"{OLD}::Card"}
    assert "is_completed" in broken[f"{OLD}::Card"][1]


# --- a refused move says which step is missing (sprint-metrics#127) ---------------------------


def test_a_move_whose_shape_changed_says_how(repo: Path):
    reshaped = CARD.replace("    started: str | None = None\n", "")
    broken = check(
        repo, *moved_to_card(import_back("from .card import Card, parse_card"), card=reshaped)
    )
    now = broken[f"{OLD}::Card"][1]
    assert f"`{PKG}/card.py` defines `Card`, but it" in now and "fields" in now


def test_the_finding_gives_the_reason(repo: Path):
    text = describe_contracts(check(repo, *moved_to_card()))
    assert "doesn't import it back" in text and "Moving a definition" in text
