"""The session replay's tools, endings and start, with a fake container and model (crew#778)."""

from __future__ import annotations

import itertools
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
from tools import Tools, first_failure, is_text  # noqa: E402

from crew_org.tools.workspace import AUTOFIX, VERIFY, Toolchain  # noqa: E402


class FakeHeld:
    def __init__(self, results: list[Result] | None = None) -> None:
        self.tools = Toolchain(image=None, setup=None, autofix=AUTOFIX, verify=VERIFY, problem=None)
        self.results = list(results or [])
        self.ran: list[list[str]] = []

    def exec(self, command: list[str], timeout: int = 0, stdin: str | None = None) -> Result:
        self.ran.append(command)
        if "pytest" in command and self.results:
            return self.results.pop(0)
        if stdin is not None:
            # ruff on stdin: one F401 per `import os` line, as its concise format writes it.
            found = [
                f"src/calc.py:{n}:1: F401 `os` imported but unused"
                for n, line in enumerate(stdin.splitlines(), 1)
                if line == "import os"
            ]
            return Result(" ".join(command), 1 if found else 0, "\n".join(found))
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


def test_replace_text_replaces_whole_lines_found_once(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    reply = tools.replace_text(
        "src/calc.py", "    return min(100, a * 100 // b)\n", "    return a * 100 // b\n"
    )
    assert reply.kind == "ok"
    assert "min(" not in (repo / "src" / "calc.py").read_text()
    assert tools.edited == ["src/calc.py"]


def test_replace_text_refuses_part_of_a_line_and_shows_it(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    reply = tools.replace_text("src/calc.py", "min(100, a * 100 // b)", "a * 100 // b")
    assert reply.kind == "refused"
    assert "line 2: `return min(100, a * 100 // b)`" in reply.text


def test_replace_text_names_every_line_a_repeated_quote_is_at(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    (repo / "src" / "calc.py").write_text("x = 1\ny = 2\nx = 1\n")
    reply = tools.replace_text("src/calc.py", "x = 1", "x = 3")
    assert reply.kind == "refused" and "at lines 1, 3" in reply.text


def test_replace_text_not_found_shows_the_closest_block(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    reply = tools.replace_text(
        "src/calc.py", "def rate(a, c):\n    return min(100, a * 100 // c)", "x"
    )
    assert reply.kind == "refused" and "closest block starts at line 1" in reply.text
    assert "    2|     return min(100, a * 100 // b)" in reply.text


def test_an_edit_that_stops_a_file_parsing_is_not_applied(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    before = (repo / "src" / "calc.py").read_text()
    reply = tools.replace_text("src/calc.py", "def other():", "def other(:")
    assert reply.kind == "refused" and "NOT been applied" in reply.text
    assert "def other(:" in reply.text and "    5| def other():" in reply.text
    assert (repo / "src" / "calc.py").read_text() == before and tools.edited == []


def test_without_the_parse_guard_a_broken_edit_is_applied(repo: Path) -> None:
    tools = Tools(repo, FakeHeld(), parse_guard=False)
    reply = tools.replace_text("src/calc.py", "def other():", "def other(:")
    assert reply.kind == "ok" and "doesn't parse" in reply.text


def test_an_edit_reply_shows_the_edited_lines_numbered_with_four_either_side(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    (repo / "src" / "calc.py").write_text("".join(f"v{n} = {n}\n" for n in range(1, 21)))
    reply = tools.replace_text("src/calc.py", "v10 = 10", "v10 = 0")
    assert "   10| v10 = 0" in reply.text
    assert "    6| v6 = 6" in reply.text and "   14| v14 = 14" in reply.text
    assert "    5| v5" not in reply.text and "   15| v15" not in reply.text


def test_lint_reports_only_what_the_edit_added(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    (repo / "src" / "calc.py").write_text("import os\n\n\ndef other():\n    return 1\n")
    reply = tools.replace_text("src/calc.py", "    return 1", "    return 2")
    assert "Lint finds nothing new." in reply.text
    reply = tools.replace_text("src/calc.py", "def other():", "import os\n\n\ndef other():")
    assert "new with this edit: F401" in reply.text


def test_lint_is_the_gates_never_a_check_only_autofix(repo: Path) -> None:
    held = FakeHeld()
    held.tools = Toolchain(
        image="x@sha256:1",
        setup=None,
        autofix=(["ruff", "format", "--check", "."],),
        verify=(["python", "-m", "pytest", "-v"],),
        problem=None,
    )
    tools = Tools(repo, held)
    assert tools.run("lint").kind == "refused" and tools.lint_cmds == []


def test_a_failure_with_no_known_error_leads_with_its_first_error_line(repo: Path) -> None:
    out = (
        "unformatted: File would be reformatted\n --> build.py:114:9\n2 files would be reformatted"
    )
    assert first_failure(out) == "unformatted: File would be reformatted"
    assert first_failure("nothing\nat the end") == "at the end"
    held = FakeHeld([Result("pytest -q", 1, out)])
    reply = Tools(repo, held).run("tests", ["tests/test_calc.py"])
    assert "failed: unformatted: File would be reformatted" in reply.summary


def test_search_honours_path_takes_a_pattern_and_falls_back_to_text(repo: Path) -> None:
    tools = Tools(repo, FakeHeld())
    reply = tools.search(r"def (rate|other)", path="src")
    assert reply.text.startswith("2 lines in `src` match")
    assert "src/calc.py:\n  1: def rate(a, b):" in reply.text
    assert "test_calc" not in tools.search("rate", path="src").text
    assert "as plain text (it isn't a valid pattern)" in tools.search("rate(").text
    assert tools.search("x", path="nowhere").kind == "refused"


def test_many_search_hits_are_counted_by_file_src_and_tests_first(repo: Path) -> None:
    (repo / ".package").mkdir()
    (repo / ".package" / "data.txt").write_text("hit\n" * 60)
    (repo / "src" / "more.py").write_text("hit = 1\n")
    reply = Tools(repo, FakeHeld()).search("hit")
    assert "61 lines match" in reply.text and "in 2 files" in reply.text
    assert reply.text.index("src/more.py: 1 lines") < reply.text.index(".package/data.txt: 60")


def test_text_is_told_by_content_not_extension(tmp_path: Path) -> None:
    (tmp_path / "design-system.version").write_text("v0.1.0\n")
    (tmp_path / "logo.bin").write_bytes(b"PNG\0\x01")
    assert is_text(tmp_path / "design-system.version")
    assert not is_text(tmp_path / "logo.bin")


def test_a_tool_refuses_arguments_it_does_not_take(repo: Path) -> None:
    reply = Tools(repo, FakeHeld()).call("search", {"text": "rate"})
    assert reply.kind == "refused"
    assert "doesn't take `text`" in reply.text and "needs `pattern`" in reply.text


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


_ids = itertools.count()


def _calls(*made: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{next(_ids)}-{name}",
                            "function": {"name": name, "arguments": json.dumps(a)},
                        }
                        for name, a in made
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
                {
                    "path": "src/calc.py",
                    "old": "    return min(100, a * 100 // b)",
                    "new": "    return a * 100 // b",
                },
            )
        ),
        _calls(("run", {"what": "suite"})),
        _calls(("done", {"criteria": [], "docs": "none", "summary": "removed the cap"})),
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


def test_the_whole_rule_keeps_replies_until_one_compaction(repo: Path) -> None:
    script = [
        _calls(("view", {"path": "src/calc.py"})),
        _calls(("view", {"path": "src/calc.py", "from_line": 4})),
        _calls(("search", {"pattern": "rate"})),
        _calls(("done", {"criteria": [], "docs": "none", "summary": "s"})),
    ]
    sent: list[dict[str, Any]] = []
    s: session_mod.Session

    def send(body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        if len(sent) == 3:
            # The next prompt is estimated past the limit, so it's compacted before it's sent.
            s.compact_at = 1
        return {**script[len(sent) - 1], "usage": {"prompt_tokens": 100}}

    s = session_mod.Session(Tools(repo, FakeHeld()), "system", "start", send=send, context="whole")
    s.run()
    third = [m["content"] for m in sent[2]["messages"] if m["role"] == "tool"]
    assert not any(t.startswith("(older)") for t in third)
    assert s.compacted["before_call"] == 4 and s.compacted["cut"] == 1
    fourth = [m["content"] for m in sent[3]["messages"] if m["role"] == "tool"]
    # The first view of the file is the one cut; the newer view and the search stay.
    assert fourth[0].startswith("(older)") and not fourth[1].startswith("(older)")


def test_an_empty_answer_is_recorded_with_its_prompt_size(repo: Path) -> None:
    empty = {"choices": [{"message": {"content": ""}}], "usage": {"prompt_tokens": 7}}
    s = session_mod.Session(Tools(repo, FakeHeld()), "system", "start", send=lambda b: empty)
    s.run()
    assert s.troubles[0] == {"call": 1, "kind": "empty", "prompt_tokens": 7}
