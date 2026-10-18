"""The session replay's tools, endings and start, with a fake container and model (crew#778)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "session_replay"))

import cases  # noqa: E402
import session as session_mod  # noqa: E402
import start as start_mod  # noqa: E402
from container import Result  # noqa: E402
from tools import Tools  # noqa: E402

from crew_org.tools.workspace import AUTOFIX, VERIFY, Toolchain  # noqa: E402


class FakeHeld:
    def __init__(self, results: list[Result] | None = None) -> None:
        self.tools = Toolchain(image=None, setup=None, autofix=AUTOFIX, verify=VERIFY, problem=None)
        self.results = list(results or [])
        self.ran: list[list[str]] = []

    def exec(self, command: list[str], timeout: int = 0) -> Result:
        self.ran.append(command)
        if "pytest" in command and self.results:
            return self.results.pop(0)
        return Result(" ".join(command), 0, "All checks passed!")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "def rate(a, b):\n    return min(100, a * 100 // b)\n\n\ndef other():\n    return 1\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from src.calc import rate\n\n\ndef test_rate_capped():\n    assert rate(2, 1) == 100\n"
    )
    return tmp_path


def test_view_shows_a_definition_with_its_line_numbers(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    reply = tools.view("src/calc.py", definition="rate")
    assert "    1| def rate(a, b):" in reply.text
    assert "other" not in reply.text


def test_a_path_outside_the_worktree_is_refused(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    assert tools.view("../secret").kind == "refused"
    assert tools.write_file(".git/config", "x").kind == "refused"


def test_notes_named_by_the_start_are_viewed_as_files(repo: Path) -> None:
    tools = Tools(repo, FakeHeld(), notes={"@notes/x.md": "one\ntwo"})
    assert "    2| two" in tools.view("@notes/x.md").text


def test_replace_text_needs_text_found_once(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    assert tools.replace_text("src/calc.py", "return", "yield").kind == "refused"
    reply = tools.replace_text("src/calc.py", "min(100, a * 100 // b)", "a * 100 // b")
    assert reply.kind == "ok" and "It parses." in reply.text
    assert "min(" not in (repo / "src" / "calc.py").read_text()
    assert tools.edited == ["src/calc.py"]


def test_write_file_does_not_overwrite_unasked(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    assert tools.write_file("src/calc.py", "x = 1\n").kind == "refused"
    assert tools.write_file("src/new.py", "x = (\n").text.count("doesn't parse") == 1


def _failed(error: str) -> Result:
    return Result("pytest", 1, f"FAILED tests/test_calc.py::test_rate_capped - {error}")


def test_the_same_first_error_after_three_runs_with_edits_is_no_progress(repo: Path) -> None:
    tools = Tools(repo, FakeHeld([_failed("assert 200 == 100")] * 3))
    tools.run("tests", ["tests/test_calc.py"])
    for _ in range(2):
        tools.replace_text("src/calc.py", "def other():", "def other():  # x")
        tools.replace_text("src/calc.py", "def other():  # x", "def other():")
        tools.run("tests", ["tests/test_calc.py"])
    assert tools.no_progress()


def test_runs_without_edits_between_are_not_no_progress(repo: Path) -> None:
    tools = Tools(repo, FakeHeld([_failed("assert 200 == 100")] * 3))
    for _ in range(3):
        tools.run("tests", ["tests/test_calc.py"])
    assert not tools.no_progress()


def _calls(*made: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{i}-{name}",
                            "function": {"name": name, "arguments": json.dumps(a)},
                        }
                        for i, (name, a) in enumerate(made)
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_a_session_ends_at_done_and_shrinks_older_output(repo: Path) -> None:
    script = [
        _calls(("view", {"path": "src/calc.py"})),
        _calls(
            (
                "replace_text",
                {"path": "src/calc.py", "old": "min(100, a * 100 // b)", "new": "a * 100 // b"},
            )
        ),
        _calls(("run", {"what": "suite"})),
        _calls(("done", {"criteria": [], "summary": "removed the cap"})),
    ]
    sent: list[dict[str, Any]] = []

    def send(body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return script[len(sent) - 1]

    s = session_mod.Session(Tools(repo, FakeHeld()), "system", "start", send=send)
    ending = s.run()
    assert ending["how"] == "done" and ending["said"]["summary"] == "removed the cap"
    assert s.calls == 4 and s.usage["completion_tokens"] == 20
    tool_texts = [m["content"] for m in sent[-1]["messages"] if m["role"] == "tool"]
    # The view went stale when the file changed; the run is the latest and stays whole.
    assert tool_texts[0].startswith("(older) viewed")
    assert tool_texts[-1].startswith("Passed.")


def test_a_session_past_its_backstop_ends(repo: Path) -> None:
    s = session_mod.Session(
        Tools(repo, FakeHeld()), "system", "start", send=lambda b: {}, backstop_s=0
    )
    assert s.run()["how"] == "backstop"


def test_a_session_that_calls_no_tool_ends_after_the_nudges(repo: Path) -> None:
    quiet = {"choices": [{"message": {"content": "I think..."}}], "usage": {}}
    s = session_mod.Session(Tools(repo, FakeHeld()), "system", "start", send=lambda b: quiet)
    assert s.run()["how"] == "no tool call"
    assert s.calls == session_mod.QUIET_LIMIT


def test_the_start_declares_the_storys_tests_with_their_source(repo: Path) -> None:
    story = (
        "Remove the cap. `tests/test_calc.py::test_rate_capped` changes; add test_rate_uncapped."
    )
    ids, sources = start_mod.declared_tests(repo, story)
    assert "tests/test_calc.py::test_rate_capped" in ids
    assert "assert rate(2, 1) == 100" in sources
    assert "def rate" not in sources


def test_the_map_names_each_files_top_level_definitions(repo: Path) -> None:
    shown = start_mod.project_map(repo)
    assert "src/calc.py (" in shown and ": rate, other" in shown
    assert "tests/test_calc.py (" in shown and ": 1 tests" in shown


def test_the_story_is_the_text_before_the_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edits = [
        {"editedAt": "2026-10-12T00:07:45Z", "diff": "first"},
        {"editedAt": "2026-10-12T15:18:30Z", "diff": "after the attempt"},
        {"editedAt": "2026-10-12T01:36:06Z", "diff": "as attempted"},
    ]
    issue = {"title": "T", "body": "now", "userContentEdits": {"nodes": edits}}
    answer = json.dumps({"data": {"repository": {"issue": issue}}})
    monkeypatch.setattr(cases, "RUNS", tmp_path)
    monkeypatch.setattr(
        cases.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=answer),
    )
    text = cases.story(cases.CASES["sprint-metrics-596"])
    assert text == "# T\n\nas attempted"
