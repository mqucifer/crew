"""The Developer's session: its tools, signals, history, endings and records (crew#778).

The workspace container is stood in for by `Fake`, which answers each command from a
table, and the proxy by `Proxy`, which answers each model call in turn. The real
session, through LiteLLM and in a held container, is in the pull request.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from crew_org import calls, events, telemetry
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.rulebook import Refusal
from crew_org.session import conversation, history, signals
from crew_org.session.container import GONE, Gone
from crew_org.session.history import Turn
from crew_org.session.tools import Reply, Tools

APP = "def f():\n    return 1\n\n\nx = 1\n"
TEST = "from app import f\n\n\ndef test_f():\n    assert f() == 1\n"
CHECKS = SimpleNamespace(verify=(("uv", "run", "pytest"), ("uv", "run", "ruff", "check", ".")))


@dataclass
class Ran:
    command: str
    code: int
    output: str
    seconds: float = 0.0


class Fake:
    """The held container: each command answered by the first entry its line starts with."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.ran: list[tuple[list[str], str | None]] = []

    def start(self):
        return Ran("start", 0, "")

    def setup(self, timeout=900):
        return None

    def exec(self, command, timeout=300, stdin=None):
        self.ran.append((command, stdin))
        line = " ".join(command)
        for key, answer in self.answers.items():
            if line.startswith(key):
                code, output = answer(stdin) if callable(answer) else answer
                return Ran(line, code, output)
        return Ran(line, 0, "")

    def stop(self):
        pass


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text(APP)
    (tmp_path / "tests" / "test_app.py").write_text(TEST)
    return tmp_path


@pytest.fixture
def seen():
    found: list = []
    sink = EventSink(None)
    sink.subscribe(found.append)
    events.reset_bridge()
    events._TARGETS.append((sink, None))
    yield found
    events.reset_bridge()


def tools(root, fake=None, coverage=None):
    return Tools(root, fake or Fake(), CHECKS, coverage)


# --- the tools ----------------------------------------------------------------------------


def test_a_file_is_text_by_its_content_not_its_name(root):
    """Pass 1 refused crew-presentation's one-line `site/css/design-system.version`."""
    (root / "site").mkdir()
    (root / "site" / "design-system.version").write_text("1.4.0\n")
    (root / "site" / "logo.bin").write_bytes(b"\x89PNG\0\0")
    t = tools(root)
    assert "1.4.0" in t.call("view", {"path": "site/design-system.version"}).text
    binary = t.call("view", {"path": "site/logo.bin"})
    assert (binary.kind, binary.rule, binary.empty) == ("refused", "session.view", False)


def test_a_lookup_for_what_isnt_there_found_nothing(root):
    t = tools(root)
    missing = t.call("view", {"path": "src/nope.py"})
    assert (missing.rule, missing.empty) == ("session.view", True)
    assert t.call("search", {"pattern": "zebra"}).empty
    assert t.call("find", {"name": "nope"}).empty
    assert t.call("tests_for", {"definition": "f"}).empty  # no map: Python only
    assert not t.call("search", {"pattern": "return"}).empty


def test_search_honours_its_path_and_takes_text_that_isnt_a_pattern(root):
    """Pass 1 dropped `path` silently, and `def f(` found nothing as a regex."""
    reply = tools(root).call("search", {"pattern": "def f(", "path": "src"})
    assert "as plain text (it isn't a valid pattern)" in reply.text
    assert "src/app.py" in reply.text and "tests/" not in reply.text


def test_every_tool_refuses_an_argument_it_doesnt_take(root):
    reply = tools(root).call("search", {"pattern": "x", "glob": "*.py"})
    assert (reply.kind, reply.rule) == ("refused", "session.arguments")
    assert "`glob`" in reply.text and "`path`" in reply.text


def test_a_path_outside_the_worktree_is_refused(root):
    for given in ("../secrets", "/etc/passwd", ".git/config"):
        reply = tools(root).call("view", {"path": given})
        assert reply.rule == "session.path", given


# --- symlinks: the tools run on the host (crew#778) -----------------------------------------


@pytest.fixture
def outside(tmp_path_factory):
    """A folder beside the worktree, with a host file in it."""
    there = tmp_path_factory.mktemp("outside")
    (there / "secret.py").write_text("TOKEN = 'the host's'\n")
    return there


def test_a_link_to_a_folder_outside_is_refused_for_reading_and_writing(root, outside):
    (root / "src" / "out").symlink_to(outside, target_is_directory=True)
    t = tools(root)
    for name, args in [
        ("view", {"path": "src/out/secret.py"}),
        ("search", {"pattern": "TOKEN", "path": "src/out"}),
        ("write_file", {"path": "src/out/planted.py", "content": "x = 1\n"}),
        ("replace_text", {"path": "src/out/secret.py", "old": "TOKEN = 'the host's'", "new": "y"}),
        ("replace_definition", {"path": "src/out/secret.py", "name": "TOKEN", "source": "y"}),
    ]:
        assert t.call(name, args).rule == "session.path", name
    assert not (outside / "planted.py").exists()
    assert (outside / "secret.py").read_text() == "TOKEN = 'the host's'\n"


def test_a_link_to_a_file_outside_is_refused_for_reading_and_writing(root, outside):
    (root / "src" / "secret.py").symlink_to(outside / "secret.py")
    t = tools(root)
    assert t.call("view", {"path": "src/secret.py"}).rule == "session.path"
    reply = t.call("write_file", {"path": "src/secret.py", "content": "x\n", "overwrite": True})
    assert reply.rule == "session.path"
    assert (outside / "secret.py").read_text() == "TOKEN = 'the host's'\n"


def test_search_and_find_walk_past_links_without_following_them(root, outside):
    (root / "src" / "out").symlink_to(outside, target_is_directory=True)
    (root / "src" / "secret.py").symlink_to(outside / "secret.py")
    t = tools(root)
    assert t.call("search", {"pattern": "TOKEN"}).text.startswith("0 lines match")
    assert "secret.py" not in t.call("find", {"name": "TOKEN"}).text


def test_a_link_that_stays_inside_still_reads(root):
    (root / "lib").symlink_to(root / "src", target_is_directory=True)
    reply = tools(root).call("view", {"path": "lib/app.py"})
    assert reply.kind != "refused" and "def f():" in reply.text


def test_a_link_inside_is_still_not_edited_through(root):
    (root / "src" / "alias.py").symlink_to(root / "src" / "app.py")
    reply = tools(root).call(
        "write_file", {"path": "src/alias.py", "content": "x\n", "overwrite": True}
    )
    assert reply.rule == "session.path" and "symbolic link" in reply.text
    assert (root / "src" / "app.py").read_text() == APP


def test_a_link_swapped_in_after_the_check_fails_the_open(root, outside):
    """A process still running in the container can replace a folder with a link
    between the check and the read or write: the open follows no link."""
    t = tools(root)
    real, _ = t._path("src/app.py")
    (root / "src").rename(root / "src-moved")
    (root / "src").symlink_to(outside, target_is_directory=True)
    (outside / "app.py").write_text("HOST = 1\n")
    with pytest.raises(Refusal) as refused:
        t._read(real, "src/app.py")
    assert refused.value.rule == "session.path"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with pytest.raises(Refusal):
        t._open(real, flags, "src/app.py")
    assert (outside / "app.py").read_text() == "HOST = 1\n"


def test_replace_text_takes_whole_lines_only(root):
    """The probe's bare name, unique, landed its insert inside the `def` line."""
    t = tools(root)
    reply = t.call("replace_text", {"path": "src/app.py", "old": "def f", "new": "def g"})
    assert (reply.rule, (root / "src" / "app.py").read_text()) == ("session.replace_text", APP)
    assert "only part of a line" in reply.text and "line 1" in reply.text


def test_a_miss_shows_the_closest_block_numbered(root):
    reply = tools(root).call(
        "replace_text", {"path": "src/app.py", "old": "def f():\n    return 2", "new": "y = 9"}
    )
    assert "The closest block starts at line 1" in reply.text
    assert "    2|     return 1" in reply.text


def test_an_edit_that_would_stop_a_file_parsing_isnt_applied(root):
    """The Sponsor's decision on crew#778: reverted and explained."""
    reply = tools(root).call(
        "replace_text", {"path": "src/app.py", "old": "x = 1", "new": "x = (1"}
    )
    assert (reply.kind, reply.rule) == ("refused", "session.parse_guard")
    assert "NOT been applied" in reply.text and "x = (1" in reply.text
    assert (root / "src" / "app.py").read_text() == APP


def test_an_edit_reports_only_the_lint_it_added(root):
    def ruff(stdin):
        found = [f"src/app.py:1:8: F401 `{m}` imported but unused" for m in ("os", "sys")]
        return 1, "\n".join(f for f in found if f"import {f.split('`')[1]}" in stdin)

    (root / "src" / "app.py").write_text("import sys\n" + APP)
    t = tools(root, Fake({"uv run ruff check": ruff}))
    reply = t.call("replace_text", {"path": "src/app.py", "old": "x = 1", "new": "import os"})
    assert reply.kind == "ok"
    assert "new with this edit: F401 `os` imported but unused." in reply.text
    assert "`sys`" not in reply.text
    assert "    6| import os" in reply.text  # the edited lines, numbered


def test_a_run_records_its_commands_codes_and_first_failure(root):
    failed = "FAILED tests/test_app.py::test_f - assert 1 == 2\nE   assert 1 == 2\n1 failed"
    t = tools(root, Fake({"uv run pytest": (1, failed)}))
    reply = t.call("run", {"what": "suite"})
    assert reply.kind == "failed" and reply.text.startswith("Failed: `uv run pytest`.")
    run = t.runs[-1]
    assert [c["code"] for c in run["commands"]] == [1, 0]
    assert run["failing"] == ["tests/test_app.py::test_f"]
    assert "assert 1 == 2" in run["first_error"]


def test_a_container_thats_gone_ends_the_tools(root):
    t = tools(root, Fake({"uv run pytest": (GONE, "the workspace container is gone")}))
    with pytest.raises(Gone):
        t.call("run", {"what": "tests", "tests": ["tests/test_app.py"]})


# --- the signals, and what's kept --------------------------------------------------------


def view(path, kind="ok"):
    return ("view", Reply("", kind, "", {"path": path}))


def edit(path):
    return ("replace_text", Reply("", "ok", "", {"path": path}))


def test_the_signals_count_reading_since_acting_and_views_since_an_edit():
    watch = signals.Signals()
    watch.after([view("a.py")], {})
    seen = watch.after([view("a.py"), view("b.py")], {"reasoning_tokens": 900})
    assert (seen["since_action"], seen["most_views"], seen["most_viewed"]) == (2, 2, "a.py")
    assert seen["reasoning_tokens"] == 900
    seen = watch.after([edit("a.py")], {})
    assert (seen["since_action"], seen["most_views"], seen["edits_since_run"]) == (0, 1, 1)
    seen = watch.after([("run", Reply("", "failed", ""))], {})
    assert seen["edits_since_run"] == 0


def test_a_ceiling_stops_reading_without_acting_and_circling_one_file(monkeypatch):
    monkeypatch.setattr(signals, "READING_CALLS", 3)
    monkeypatch.setattr(signals, "VIEWS_OF_ONE_FILE", 4)
    watch = signals.Signals()
    for _ in range(2):
        watch.after([view("a.py"), view("a.py")], {})
    assert watch.ceiling() == {"ceiling": "re_viewing", "file": "a.py", "views": 4}
    watch = signals.Signals()
    for _ in range(3):
        watch.after([], {})
    assert watch.ceiling() == {"ceiling": "reading", "calls": 3}


def test_compaction_keeps_each_files_latest_view_the_latest_run_and_the_newest_turn():
    def turn(name, path="", kind="ok"):
        return Turn(
            Reply(f"{name} {path} whole", kind, f"{name} {path}", {"path": path}), "", name, {}
        )

    said = {"role": "assistant", "content": ""}
    whole = [
        said, turn("view", "a.py"), turn("view", "b.py"), turn("run", kind="failed"),
        said, turn("view", "a.py"), turn("replace_text", "b.py"), turn("run", kind="passed"),
        said, turn("search"),
    ]  # fmt: skip
    kept = [not t.masked for t in history.compact(whole) if isinstance(t, Turn)]
    # a.py's first view is stale, b.py's view predates its edit, the first run is older.
    assert kept == [False, False, False, True, False, True, True]
    sent = history.messages("system", "start", history.compact(whole))
    assert sent[3]["content"] == "(older) view a.py"


# --- the session ---------------------------------------------------------------------------


def said(*made, content="", reasoning=0, prompt=1000, finish="tool_calls"):
    return {
        "id": "resp",
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": [
                        {
                            "id": f"call-{i}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(made)
                    ],
                },
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": 40 + reasoning,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
            "prompt_tokens_details": {"cached_tokens": prompt // 2},
        },
    }


class Proxy:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[dict] = []

    def __call__(self, body):
        self.sent.append(json.loads(json.dumps(body)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


DONE = {"criteria": [{"criterion": "f is 2", "test": "tests/test_app.py::test_f"}]}
DONE |= {"docs": "none describe it", "summary": "f returns 2"}


def run_session(root, proxy, fake=None, **settings):
    s = conversation.session(root, "# Story\n\nf returns 2.", fake or Fake(), CHECKS, send=proxy)
    for key, value in settings.items():
        setattr(s, key, value)
    return s, s.run()


def kinds(found, kind):
    return [e for e in found if e.kind == kind]


def test_a_session_works_by_tools_until_done_and_records_every_call(root, seen):
    proxy = Proxy(
        said(("view", {"path": "src/app.py"}), ("search", {"pattern": "nothing-here"})),
        said(
            ("replace_text", {"path": "src/app.py", "old": "    return 1", "new": "    return 2"})
        ),
        said(("run", {"what": "suite"}), reasoning=300),
        said(("done", DONE)),
    )
    s, ending = run_session(root, proxy)
    assert (ending.how, ending.stop, ending.said["summary"]) == ("done", "", "f returns 2")
    assert ending.said["changed_tests"] == [] and ending.said["criteria"] == DONE["criteria"]
    assert "return 2" in (root / "src" / "app.py").read_text()
    # The tools the model was offered, and the conversation it was sent, grown whole.
    assert [t["function"]["name"] for t in proxy.sent[0]["tools"]][-2:] == ["done", "blocked"]
    assert [m["role"] for m in proxy.sent[1]["messages"]] == [
        "system", "user", "assistant", "tool", "tool",
    ]  # fmt: skip
    assert "## How this session works" in proxy.sent[0]["messages"][1]["content"]
    # The records (P5): each call's events, each tool call, and the signals after each.
    started = kinds(seen, EventKind.SESSION_STARTED)[0].detail
    assert started["start_chars"]["total"] > 0 and started["limits"]["reading_calls"] == 60
    assert [e.detail["turn"] for e in kinds(seen, EventKind.LLM_CALL_STARTED)] == [1, 2, 3, 4]
    tools_seen = kinds(seen, EventKind.TOOL_FINISHED)
    assert [e.detail["tool"] for e in tools_seen] == [
        "view", "search", "replace_text", "run", "done",
    ]  # fmt: skip
    search = tools_seen[1].detail
    assert (search["found_nothing"], search["looked_for"]) == (True, "nothing-here")
    run = tools_seen[3].detail
    assert [c["code"] for c in run["commands"]] == [0, 0] and run["result"] == "passed"
    assert run["run"] == "suite"
    turns = [e.detail for e in kinds(seen, EventKind.SESSION_TURN)]
    assert [t["since_action"] for t in turns] == [1, 0, 0, 1]
    assert turns[0]["empty_lookups"] == 1 and turns[2]["reasoning_tokens"] == 300
    ended = kinds(seen, EventKind.SESSION_ENDED)[0].detail
    assert (ended["how"], ended["model_calls"], ended["edits_applied"], ended["test_runs"]) == (
        "done",
        4,
        1,
        1,
    )
    assert ended["prompt_tokens"] == 4000 and ended["cached_tokens"] == 2000


def test_a_refused_ending_is_a_reply_and_the_session_goes_on(root, seen):
    proxy = Proxy(said(("done", {"summary": "x", "criteria": []})), said(("done", DONE)))
    _, ending = run_session(root, proxy)
    assert ending.how == "done"
    refused = kinds(seen, EventKind.TOOL_FINISHED)[0].detail
    assert (refused["result"], refused["rule"]) == ("refused", "session.arguments")
    assert "`docs`" in proxy.sent[1]["messages"][-1]["content"]


def test_blocked_ends_the_session_with_what_is_missing(root):
    missing = {"missing": "the rate's cap", "kind": "choice"}
    _, ending = run_session(root, Proxy(said(("blocked", missing))))
    assert (ending.how, ending.said, ending.stop) == ("blocked", missing, "")


def test_three_replies_that_call_no_tool_end_the_session_as_blocked(root):
    proxy = Proxy(*(said(content=f"thinking {i}") for i in range(3)))
    _, ending = run_session(root, proxy)
    assert (ending.how, ending.stop, ending.said["kind"]) == ("blocked", "no_tool_call", "stop")
    assert ending.evidence["stop"]["replies"] == ["thinking 0", "thinking 1", "thinking 2"]
    assert proxy.sent[1]["messages"][-1] == {"role": "user", "content": conversation.QUIET}


def test_the_same_first_error_after_three_runs_with_edits_between_stops_it(root):
    failed = "FAILED tests/test_app.py::test_f - assert 1 == 2\nE   assert 1 == 2"
    run = ("run", {"what": "tests", "tests": ["tests/test_app.py"]})

    def change(a, b):
        return said(("replace_text", {"path": "src/app.py", "old": f"x = {a}", "new": f"x = {b}"}))

    proxy = Proxy(said(run), change(1, 2), said(run), change(2, 3), said(run))
    _, ending = run_session(root, proxy, Fake({"uv run pytest": (1, failed)}))
    assert (ending.how, ending.stop, ending.said["kind"]) == ("blocked", "no_progress", "stop")
    assert "`tests/test_app.py::test_f` after 3 runs" in ending.said["missing"]
    assert ending.evidence["last_failure"]["failing"] == ["tests/test_app.py::test_f"]
    assert ending.evidence["tried"]["files_edited"] == ["src/app.py"]
    assert ending.evidence["signals"]["edits_since_run"] == 0


def test_reading_without_acting_stops_at_its_ceiling(root, monkeypatch):
    monkeypatch.setattr(signals, "READING_CALLS", 2)
    look = said(("view", {"path": "src/app.py"}))
    _, ending = run_session(root, Proxy(look, look))
    assert (ending.how, ending.stop) == ("blocked", "ceiling")
    assert ending.said == {"missing": "2 calls with no edit and no run", "kind": "stop"}
    assert ending.evidence["stop"] == {"ceiling": "reading", "calls": 2}
    assert ending.evidence["tried"]["files_read"] == ["src/app.py"]


def test_the_backstop_of_model_time_stops_it(root):
    _, ending = run_session(root, Proxy(), backstop_s=0)
    assert (ending.how, ending.stop) == ("blocked", "backstop")


def test_a_failed_call_interrupts_the_session_and_blames_nothing(root, seen):
    down = calls.ServiceUnavailableError("503: no backend")
    _, ending = run_session(root, Proxy(down))
    assert (ending.how, ending.why, ending.error) == ("interrupted", "call_failed", down)
    assert kinds(seen, EventKind.LLM_CALL_FAILED)


def test_a_container_thats_gone_interrupts_the_session(root):
    gone = Fake({"uv run pytest": (GONE, "the workspace container is gone")})
    _, ending = run_session(root, Proxy(said(("run", {"what": "suite"}))), gone)
    assert (ending.how, ending.why) == ("interrupted", "workspace_gone")


def test_the_history_is_compacted_once_when_a_prompt_would_pass_the_limit(root, seen):
    look = said(("view", {"path": "src/app.py", "from_line": 1, "to_line": 2}))
    s, ending = run_session(root, Proxy(look, look, look, said(("done", DONE))), compact_at=1)
    assert ending.how == "done" and s.compacted["turn"] == 2
    (compacted,) = kinds(seen, EventKind.SESSION_COMPACTED)
    assert compacted.detail["turn"] == 2 and compacted.detail["estimated_tokens"] >= 1


def test_a_worktree_with_a_host_made_venv_is_refused(root):
    assert conversation.unfit(root) is None
    (root / ".venv").mkdir()
    assert "crew#780" in (conversation.unfit(root) or "")


def test_telemetry_gets_the_sessions_counts_never_its_paths_or_text():
    event = CrewEvent(
        kind=EventKind.TOOL_FINISHED,
        summary="viewed src/app.py",
        detail={
            "tool": "search",
            "turn": 3,
            "target": "src/secret_plan.py",
            "result": "ok",
            "args_chars": 40,
            "reply_chars": 900,
            "found_nothing": True,
            "looked_for": "password",
            "commands": [{"command": "uv run pytest", "code": 1}],
            "first_error": "assert 1 == 2",
            "rule": "session.search",
        },
    )
    out = telemetry.view(event)
    assert {"tool", "turn", "args_chars", "reply_chars", "found_nothing", "rule"} <= set(out)
    assert not {"target", "looked_for", "commands", "first_error", "result", "summary"} & set(out)


def test_a_session_from_a_branch_with_work_is_told_that_where_the_hints_would_be(root, seen):
    """P2 and P3: review's findings and the last `done`, or the checkpoint's message."""
    told = "## What review returned it for\n\n- `f` still caps at 100"
    s = conversation.session(root, "# Story", Fake(), CHECKS, instead_of_hints=told)
    assert told in s.start and "## Likely relevant" not in s.start
    assert s.start.index(told) < s.start.index("## How this session works")
    sizes = kinds(seen, EventKind.SESSION_STARTED)[0].detail["start_chars"]
    assert sizes["instead_of_hints"] == len(told) and "hints" not in sizes
    assert "## Likely relevant" in conversation.session(root, "# Story", Fake(), CHECKS).start


def test_the_caller_is_given_each_run_once_it_has_run(root):
    """Delivery commits a checkpoint after a green run (P3)."""
    runs: list[dict] = []
    failed = "FAILED tests/test_app.py::test_f - assert 1 == 2"
    fake = Fake({"uv run pytest -q tests/test_app.py": (1, failed)})
    proxy = Proxy(
        said(("run", {"what": "tests", "tests": ["tests/test_app.py"]})),
        said(("run", {"what": "suite"}), ("run", {"what": "tests"})),  # the second is refused
        said(("done", DONE)),
    )
    s = conversation.session(root, "# Story", fake, CHECKS, send=proxy, after_run=runs.append)
    assert s.run().how == "done"
    assert [(r["what"], r["ok"], r["turn"]) for r in runs] == [
        ("tests", False, 1),
        ("suite", True, 2),
    ]


def test_a_failing_step_after_a_run_is_recorded_and_the_session_goes_on(root, seen):
    def commit(run):
        raise OSError("index.lock exists")

    proxy = Proxy(said(("run", {"what": "suite"})), said(("done", DONE)))
    s = conversation.session(root, "# Story", Fake(), CHECKS, send=proxy, after_run=commit)
    assert s.run().how == "done"
    assert s.after_run_errors == ["OSError: index.lock exists"]
    (failed,) = kinds(seen, EventKind.TOOL_FAILED)
    assert (failed.detail["tool"], failed.detail["turn"]) == ("after_run", 1)
    ended = kinds(seen, EventKind.SESSION_ENDED)[0].detail
    assert ended["after_run_errors"] == ["OSError: index.lock exists"]
