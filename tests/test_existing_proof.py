"""QA is shown the criteria a delivery said existing tests prove (#217).

A first attempt had to write a new test per criterion (#172), so the split's
"the full existing test suite continues to pass" got invented
`test_full_suite_passes` bodies. The one-call Developer's proof by existing tests
is on stories it delivered; QA still reads it there.
"""

from __future__ import annotations

from crew_org.flows.acceptance import EXISTING_PROOF_MARKER
from tests.test_already_done import qa

# --- through delivery ----------------------------------------------------------------


# --- through QA ----------------------------------------------------------------------


def test_qa_is_shown_the_criteria_existing_tests_prove(monkeypatch, tmp_path):
    """#217, criterion 2."""
    delivered = [
        {
            "body": "Implemented in #170 on `feat/132`. Lint and tests pass.\n\n"
            f"{EXISTING_PROOF_MARKER}\n**Proven by existing tests:**\n"
            "- The full existing test suite continues to pass: `tests/test_report.py::test_x`"
        }
    ]
    seen, _, board = qa(monkeypatch, tmp_path, delivered, accepted=True)
    assert "## Criteria the Developer says the existing tests prove" in seen["story"]
    assert "`tests/test_report.py::test_x`" in seen["story"]
    assert board.moves == [("S143", "Merging")], "a story with a PR still merges"


def test_a_later_delivery_without_the_proof_shows_qa_none(monkeypatch, tmp_path):
    comments = [
        {"body": f"Implemented in #170.\n\n{EXISTING_PROOF_MARKER}\n- old: `t::x`"},
        {"body": "Implemented in #171. Lint and tests pass."},
    ]
    seen, _, _ = qa(monkeypatch, tmp_path, comments, accepted=True)
    assert "existing tests prove" not in seen["story"]
