"""A test declares the pytest fixtures it uses (#196).

`F821 Undefined name tmp_path` failed first attempts at sprint-metrics #11,
#30 and #75: a test used pytest's fixture without taking it as a parameter.
"""

from __future__ import annotations

import ast

import pytest

from crew_org.crews.delivery_crew import CriterionTest, FileEdit, FileWrite
from crew_org.tools.fixtures import declare_fixtures


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("def test_a():\n    p = tmp_path / 'x'  # kept\n", "def test_a(tmp_path):"),
        (
            "def test_b(capsys):\n    monkeypatch.setattr(x, 'y', 1)\n",
            "def test_b(capsys, monkeypatch):",
        ),
        ("    def test_c(self):\n        capsys.readouterr()\n", "    def test_c(self, capsys):"),
        ("def test_d(a, b,):\n    caplog\n", "def test_d(a, b, caplog):"),
    ],
)
def test_a_used_fixture_is_added_to_the_signature(given, expected):
    out = declare_fixtures(given)
    assert out.splitlines()[0] == expected
    assert "# kept" in out or "# kept" not in given, "edited in place, comments survive"


def test_a_signature_over_several_lines_stays_well_formed():
    one_a_line = declare_fixtures("def test_e(\n    a,\n    b,\n):\n    tmp_path\n")
    assert one_a_line.splitlines()[3] == "    tmp_path,"
    packed = declare_fixtures("def test_f(\n    a, b\n):\n    capsys\n")
    assert packed.splitlines()[1] == "    a, b, capsys"
    for source in (one_a_line, packed):
        ast.parse(source)


@pytest.mark.parametrize(
    "given",
    [
        "def test_g(tmp_path):\n    tmp_path.mkdir()\n",  # already declared
        "def test_h():\n    tmp_path = make()\n    tmp_path.mkdir()\n",  # its own
        "tmp_path = 1\n\ndef test_i():\n    tmp_path\n",  # the module's
        "def helper():\n    tmp_path\n",  # not a test
        "def test_j(:\n",  # won't parse: left alone
    ],
)
def test_what_is_bound_or_not_a_test_is_left_alone(given):
    assert declare_fixtures(given) == given


def test_every_way_a_test_is_written_declares_its_fixtures():
    body = "def test_k():\n    tmp_path.mkdir()\n"
    criterion = CriterionTest(criterion="c", path="tests/test_x.py", test="test_k", source=body)
    new = FileWrite(path="tests/test_x.py", content=f"import pytest\n\n\n{body}")
    edit = FileEdit(path="tests/test_x.py", operation="add", target="test_k", source=body)
    assert "def test_k(tmp_path):" in criterion.source
    assert "def test_k(tmp_path):" in new.content
    assert "def test_k(tmp_path):" in edit.source


def test_production_code_is_never_touched():
    body = "def test_like_name():\n    tmp_path\n"
    assert FileWrite(path="src/pkg/m.py", content=body).content == body
    assert FileEdit(path="src/pkg/m.py", operation="add", target="x", source=body).source == body
