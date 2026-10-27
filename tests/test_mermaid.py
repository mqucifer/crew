"""The Architect's diagrams are checked by Mermaid's own parser (crew#922).

Every diagram here runs through the real parser, in its pinned container, as the
crew's checks do. Mermaid refuses what won't render; five things it parses and then
draws wrong or not at all are checked after it, in code: an open quote, a relation
without its label, a relation to an alias nobody defined, an alias defined twice, and
an alias with a space (crew#858's notes).

The hand-written copy of Mermaid's grammar this replaced passed each new quirk until
it was patched: a `;` in a sequence message (mqucifer/sprint-metrics#666), a second
block nobody checked (crew#839), and a `title` line Mermaid's lexer refuses
(mqucifer/sprint-metrics#806).
"""

from __future__ import annotations

import pytest

from crew_org import mermaid
from crew_org.tools.sandbox import Sandbox

CONTAINERS = """\
C4Container
title The Goal's system
Person(sponsor, "Sponsor", "Reads the sprint's delivery")
System_Boundary(crew, "The crew") {
  Container(tick, "Tick", "Python", "Runs the crew's phases")
  ContainerDb(events, "Event log", "JSON lines", "What each step did")
}
System_Ext(github, "GitHub", "Issues, the board and pull requests")
Rel(tick, github, "Reads and writes cards", "HTTPS")
Rel(tick, events, "Appends")
Rel(sponsor, github, "Approves epics")
UpdateElementStyle(events, $borderColor="green")
"""

SEQUENCE = """\
sequenceDiagram
participant T as Tick
participant G as GitHub
T->>G: read the Goal's epics
alt every epic approved
  T->>G: post the Goal's record
else one waits
  Note over T: hold the Goal
end
"""


def test_well_formed_diagrams_have_no_problems():
    assert mermaid.problems([CONTAINERS, SEQUENCE]) == []
    assert mermaid.problems([f"```mermaid\n{CONTAINERS}```"]) == []
    assert mermaid.aliases(CONTAINERS) == {"sponsor", "crew", "tick", "events", "github"}


@pytest.mark.parametrize(
    ("change", "said"),
    [
        # Refused by Mermaid's parser, in its own words.
        (
            lambda d: d.replace("C4Container", "C4Containr"),
            "No diagram type detected matching given configuration for text: C4Containr",
        ),
        (
            lambda d: d.replace('Rel(tick, events, "Appends")', "tick --> events"),
            "Lexical error on line 10. Unrecognized text.",
        ),
        (
            lambda d: d.replace("ContainerDb(events", "ContainerDatabase(events"),
            "Lexical error on line 6. Unrecognized text.",
        ),
        (lambda d: d.replace('"The crew") {', '"The crew")'), "Expecting 'LBRACE'"),
        (lambda d: d.replace("\n}\n", "\n"), "Expecting 'RBRACE', got 'EOF'"),
        (lambda d: d + "}\n", "Expecting 'EOF', got 'RBRACE'"),
        # Parsed by Mermaid, and drawn wrong: checked after it.
        (lambda d: d.replace('"Appends")', '"Appends)'), "a quote is left open"),
        (lambda d: d.replace('Rel(tick, events, "Appends")', "Rel(tick, events)"), "no label"),
        (lambda d: d.replace("Rel(tick, github", "Rel(tik, github"), "`tik`"),
        (lambda d: d.replace("System_Ext(github", "System_Ext(tick"), "defined twice"),
        (lambda d: d.replace("Person(sponsor", "Person(the sponsor"), "isn't an alias"),
    ],
)
def test_what_keeps_a_c4_diagram_from_rendering_is_named(change, said):
    found = mermaid.problems([change(CONTAINERS)])
    assert found and said in " ".join(found), found
    assert all(p.startswith("block 1, ") for p in found), found


def test_a_title_mermaids_lexer_refuses_is_refused_in_its_words():
    """mqucifer/sprint-metrics#806: GitHub showed this, and the copy had passed it."""
    title = "title Goal (mqucifer/sprint-metrics#749) retirements and what replaces them"
    found = mermaid.problems([CONTAINERS.replace("title The Goal's system", title)])
    assert len(found) == 1, found
    assert found[0].startswith("block 1, C4Container: Lexical error on line 2. "), found
    assert "Unrecognized text. ...cifer/sprint-metrics#749) retirements an" in found[0]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.replace("\nend\n", "\n"),
        lambda d: d.replace("T->>G: read", "T => G read"),
        # The first real look's line (mqucifer/sprint-metrics#666), which Mermaid refused.
        lambda d: d.replace("read the Goal's epics", "distinct stories; reason counts"),
    ],
)
def test_what_keeps_a_sequence_diagram_from_rendering_is_named(change):
    found = mermaid.problems([CONTAINERS, change(SEQUENCE)])
    assert found and all(p.startswith("block 2, sequenceDiagram: ") for p in found), found


def test_every_block_is_checked_and_named_by_its_place():
    broken = CONTAINERS.replace('"The crew") {', '"The crew")')
    found = mermaid.problems([SEQUENCE, broken])
    assert found and all(p.startswith("block 2, C4Container: ") for p in found), found


def test_mermaids_message_is_kept_without_its_pointer_line():
    found = mermaid.problems([SEQUENCE.replace("read the Goal's epics", "a; b")])
    assert "Parse error on line 4" in found[0] and "---^" not in found[0], found


def test_a_parser_that_cant_run_is_a_problem_never_a_pass():
    # A diagram this process hasn't parsed: one it has is answered from memory.
    unseen = CONTAINERS + "%% not parsed before\n"
    found = mermaid.problems([unseen], sandbox=Sandbox(engine="no-such-engine"))
    assert len(found) == 1 and found[0].startswith("Mermaid's parser couldn't run: "), found
    assert "no-such-engine" in found[0]


def test_the_doctor_says_whether_the_parser_is_ready():
    why, fails = mermaid.unready(Sandbox(engine="no-such-engine")) or ("", False)
    assert "no-such-engine" in why and fails
    mermaid.problems([CONTAINERS])  # installs it, if it wasn't
    assert mermaid.unready() is None


@pytest.mark.parametrize(
    ("unready", "mark"),
    [(None, "pass"), (("not pulled yet", False), "warn"), (("no engine", True), "FAIL")],
)
def test_the_doctor_reports_the_parser(monkeypatch, unready, mark):
    from typer.testing import CliRunner

    from crew_org import cli, substrate

    monkeypatch.setattr(substrate, "run_all", lambda base_url, model, *, deep=False: [])
    monkeypatch.setattr(mermaid, "unready", lambda: unready)
    result = CliRunner().invoke(cli.app, ["doctor", "--base-url", "http://proxy"])
    row = next(line for line in result.output.splitlines() if "mermaid parser" in line)
    assert mark in row and (unready is None or unready[0] in row), row
    assert result.exit_code == (1 if mark == "FAIL" else 0)


def test_nothing_to_check_runs_nothing():
    assert mermaid.problems([], sandbox=Sandbox(engine="no-such-engine")) == []
