"""The Senior Engineer diagnoses the crew's own code and never changes it (#9)."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from crew_org.crews.diagnosis_crew import Diagnosis, FileRequest, Finding
from crew_org.flows import diagnose as flow
from crew_org.flows.diagnose import (
    DIAGNOSIS_LABEL,
    failure_of,
    file_finding,
    read_chosen,
    readable,
    render,
    resolve,
    run_diagnosis,
    source_index,
)
from crew_org.permissions import Capability, Permissions

HEALTH = (
    "def health():\n"
    "    status = probe()\n"
    "    # A 401 means the proxy is up and refused us.\n"
    "    if status != 200:\n"
    "        return False, 'not answering'\n"
    "    return True, 'ok'\n"
)


def crew(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "crew_org"
    src.mkdir(parents=True)
    (src / "llm.py").write_text(HEALTH)
    (src / "events.py").write_text("class CrewEvent:\n    pass\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ways-of-working.md").write_text("# The constitution\n")
    (tmp_path / "secrets.env").write_text("TOKEN=x\n")
    return tmp_path


def fingerprint(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


# --- 2: it can't write, enforced in code ------------------------------------------------


def test_the_role_may_diagnose_and_comment_and_nothing_else():
    perms = Permissions.from_agents()
    granted = {c for c in Capability if perms.allows("Senior Engineer", c)}
    assert granted == {Capability.DIAGNOSE_CREW, Capability.COMMENT}


def test_diagnosis_can_reach_nothing_that_writes_runs_git_or_starts_a_process():
    """Structural, not advice: neither module imports anything that could."""
    forbidden = {"subprocess", "os", "shutil", "crew_org.git_ops", "crew_org.tools.workspace"}
    forbidden |= {"crew_org.tools.claude_code", "crew_org.tools.sandbox"}
    for module in ("crew_org/flows/diagnose.py", "crew_org/crews/diagnosis_crew.py"):
        tree = ast.parse((Path(flow.__file__).parents[2] / module).read_text())
        imported = {
            name
            for node in ast.walk(tree)
            for name in (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
        }
        assert not imported & forbidden, (module, imported & forbidden)


def test_only_offered_files_in_readable_folders_are_read(tmp_path):
    root = crew(tmp_path)
    offered = set(readable(root))
    assert "secrets.env" not in offered
    assert resolve(root, "src/crew_org/llm.py", offered) is not None
    for path in ("secrets.env", "src/crew_org/../../secrets.env", "/etc/passwd", "nope.py"):
        assert resolve(root, path, offered) is None, path


def test_a_diagnosis_leaves_the_crews_files_as_they_were(tmp_path):
    root = crew(tmp_path)
    before = fingerprint(root)
    run_diagnosis(root, "health said not answering", choose=choose, diagnose=diagnose)
    assert fingerprint(root) == before


# --- 1 and 3: findings tied to a line it was shown ----------------------------------------


def choose(**context):
    assert "`src/crew_org/llm.py`: health" in context["index"], "the map names definitions"
    return FileRequest(files=["src/crew_org/llm.py", "secrets.env"], why="health()")


FOUND = Finding(
    file="src/crew_org/llm.py",
    line=4,
    wrong="A 401 from an authenticated proxy is reported as 'not answering'",
    change="Return (True, 'answering, but refused our key') for 401",
    evidence="The comment on line 3 says what the value on line 4 ignores",
)


def diagnose(**context):
    assert "    4     if status != 200:" in context["files"], "shown with line numbers"
    return Diagnosis(
        summary="health() calls a refusal an outage.",
        findings=[
            FOUND,
            FOUND.model_copy(update={"line": 400}),
            FOUND.model_copy(update={"file": "src/crew_org/cli.py"}),
        ],
    )


def test_a_finding_names_a_line_it_was_shown_or_is_left_out(tmp_path):
    report = run_diagnosis(crew(tmp_path), "health", choose=choose, diagnose=diagnose)
    assert report.read.refused == ["secrets.env"]
    assert report.findings == [FOUND]
    assert len(report.dropped) == 2, "a line past the end, and a file it never saw"


def test_the_report_says_what_it_read_found_and_left_out(tmp_path):
    report = run_diagnosis(crew(tmp_path), "health", choose=choose, diagnose=diagnose)
    text = render(report, repo="crew", card=9)
    assert "## 1. `src/crew_org/llm.py:4`" in text
    assert "**Change:** Return (True" in text
    assert "Refused" in text and "secrets.env" in text
    assert "2 finding(s) named a file or line it wasn't shown" in text


def test_nothing_readable_means_no_diagnosis(tmp_path):
    report = run_diagnosis(
        crew(tmp_path),
        "x",
        choose=lambda **_: FileRequest(files=["secrets.env"], why="?"),
        diagnose=lambda **_: (_ for _ in ()).throw(AssertionError("not called")),
    )
    assert report.diagnosis is None
    assert "No diagnosis" in render(report, repo="crew", card=9)


def test_files_past_the_budget_are_named_not_cut(tmp_path, monkeypatch):
    root = crew(tmp_path)
    monkeypatch.setattr(flow, "MAX_CHARS", 150)
    read = read_chosen(root, ["src/crew_org/llm.py", "src/crew_org/events.py"], set(readable(root)))
    assert list(read.shown) == ["src/crew_org/events.py"]
    assert read.left_out == ["src/crew_org/llm.py"]


def test_the_index_covers_the_source_and_the_docs(tmp_path):
    text = source_index(crew(tmp_path))
    assert "- `src/crew_org/events.py`: CrewEvent" in text
    assert "- `docs/ways-of-working.md`" in text


# --- what it's shown about the failure ----------------------------------------------------


class Issues:
    def __init__(self):
        self.created, self.labels = [], []

    def comments(self, repo, number):
        return [
            {"body": "a person's comment"},
            {"body": "<!-- crew:qa -->\nnot accepted"},
        ]

    def ensure_label(self, repo, name, *, color, description):
        self.labels.append(name)

    def create(self, repo, title, body, labels=None):
        self.created.append((repo, title, body, labels))
        return {"number": 300}


def test_the_failure_is_the_cards_events_and_the_crews_comments(tmp_path):
    import json

    events = tmp_path / "events"
    events.mkdir()
    (events / "tick.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"at": "2026-09-26T13:00:00", "kind": "card.blocked", "card": 145, "summary": "x"},
                {"at": "2026-09-26T13:01:00", "kind": "llm.finished", "card": 145},
                {"at": "2026-09-26T13:02:00", "kind": "card.moved", "card": 99, "summary": "y"},
            ]
        )
    )
    text = failure_of(events, Issues(), "sprint-metrics", 145)
    assert "card.blocked" in text and "llm.finished" not in text and "card.moved" not in text
    assert "not accepted" in text and "a person's comment" not in text


# --- 4: an accepted finding becomes a card ------------------------------------------------


def test_an_accepted_finding_is_filed_carrying_it_as_its_body():
    issues = Issues()
    file_finding(issues, "crew", FOUND, repo="sprint-metrics", card=145)
    ((repo, title, body, labels),) = issues.created
    assert repo == "crew" and labels == [DIAGNOSIS_LABEL]
    assert title.startswith("A 401 from an authenticated proxy")
    assert "`src/crew_org/llm.py:4`" in body and FOUND.change in body
