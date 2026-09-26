"""The Developer can move a definition in one step (#216).

In the Architect's split of sprint-metrics every story was moves, and the
Developer dropped or doubled a step on each: both copies kept (#125, #126), a
class then each of its methods deleted (#125), a helper moved with nothing
left to import it (#126).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import Implementation, Move
from crew_org.tools.ast_edit import EditError
from crew_org.tools.move_code import module_of
from crew_org.tools.workspace import apply_implementation

A = """\
\"\"\"Everything, in one module.\"\"\"

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Card:
    created: date


def _parse_date(raw):
    return date.fromisoformat(raw)


def parse_card(raw):
    return Card(created=_parse_date(raw["created"]))


def main():
    return Card(created=date(2024, 1, 1))
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "src/pkg"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text(A)
    (pkg / "__init__.py").write_text("from pkg.a import Card, main\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text(
        "from pkg.a import parse_card\n\n\n"
        "def test_parse():\n    assert parse_card({'created': '2024-01-02'})\n"
    )
    return tmp_path


def moves(*names: str, to: str = "src/pkg/card.py") -> Implementation:
    return Implementation(
        summary="card.py owns the Card model",
        moves=[Move(name=n, from_path="src/pkg/a.py", to_path=to) for n in names],
    )


def ruff(path: Path) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F", "--no-cache", str(path)],
        capture_output=True,
        text=True,
    )
    return done.stdout + done.stderr if done.returncode else ""


def test_a_move_is_whole_imports_its_needs_imports_back_and_lints_clean(repo: Path):
    """What #125 needed: Card, _parse_date and parse_card into card.py."""
    apply_implementation(repo, moves("Card", "_parse_date", "parse_card"))
    card = (repo / "src/pkg/card.py").read_text()
    old = (repo / "src/pkg/a.py").read_text()

    assert "class Card" in card and "def parse_card" in card and "def _parse_date" in card
    assert "from dataclasses import dataclass" in card and "from datetime import date" in card
    assert "class Card" not in old and "def parse_card" not in old, "not both copies"
    assert "from pkg.card import Card as Card" in old, "main still uses it"
    assert "from pkg.card import parse_card as parse_card" in old, "a test imports it from here"
    assert "_parse_date" not in old, "nothing asks for the private helper here"
    assert "from dataclasses import dataclass" not in old, "left unused, lint would refuse it"
    assert "from datetime import date" in old, "main still uses date"
    assert ruff(repo / "src/pkg/a.py") == "" and ruff(repo / "src/pkg/card.py") == ""

    sys.path.insert(0, str(repo / "src"))
    try:
        import importlib

        import pkg.a

        importlib.reload(pkg.a)
        assert pkg.a.parse_card({"created": "2024-01-02"}).created.day == 2
    finally:
        sys.path.remove(str(repo / "src"))
        for name in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
            del sys.modules[name]


def test_moving_a_definition_without_what_it_uses_is_refused(repo: Path):
    with pytest.raises(EditError, match=r"uses `Card`, `_parse_date` .* Move those"):
        apply_implementation(repo, moves("parse_card"))


def test_moving_onto_an_existing_definition_is_refused(repo: Path):
    (repo / "src/pkg/card.py").write_text("class Card:\n    pass\n")
    with pytest.raises(EditError, match="already defines it"):
        apply_implementation(repo, moves("Card"))


def test_a_move_into_an_existing_module_is_appended(repo: Path):
    (repo / "src/pkg/card.py").write_text('"""Cards."""\n')
    apply_implementation(repo, moves("Card"))
    card = (repo / "src/pkg/card.py").read_text()
    assert card.startswith('"""Cards."""') and "class Card" in card


def test_a_missing_name_or_same_file_is_refused(repo: Path):
    with pytest.raises(EditError, match="no top-level definition"):
        apply_implementation(repo, moves("Nope"))
    with pytest.raises(ValueError, match="already in"):
        Move(name="Card", from_path="src/pkg/a.py", to_path="src/pkg/a.py")


def test_module_names_come_from_paths():
    assert module_of("src/sprint_metrics/card.py") == "sprint_metrics.card"
    assert module_of("pkg/__init__.py") == "pkg"


def test_the_regression_guards_accept_a_move_as_it_stands(repo: Path):
    """Nothing is lost: what the old module provided, it still provides."""
    from crew_org.tools.regression import broken_contracts, lost_names

    implementation = moves("Card", "_parse_date", "parse_card")
    assert broken_contracts(repo, implementation.all_edits) == {}
    assert lost_names(repo, implementation) == {}
