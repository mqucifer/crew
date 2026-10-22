"""The Goal's target diagram is checked for what keeps it from rendering (crew#858).

Every diagram here was run once through Mermaid's own parser (mermaid 11.4.1, by
hand, for crew#858's PR), so the check isn't only agreeing with itself. Mermaid
accepted both well-formed ones and refused every break below, except five it parses
and then draws wrong or not at all: an open quote, a relation without its label, a
relation to an alias nobody defined, an alias defined twice, and an alias with a
space. Those are caught here. A `;` in a sequence message, which Mermaid refused on the
first real look's diagram (mqucifer/sprint-metrics#666) and the check had passed, is
caught too.
"""

from __future__ import annotations

import pytest

from crew_org import mermaid

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


def test_a_well_formed_c4_diagram_has_no_problems():
    assert mermaid.c4_problems(CONTAINERS) == []
    assert mermaid.c4_problems(f"```mermaid\n{CONTAINERS}```") == []
    assert mermaid.aliases(CONTAINERS) == {"sponsor", "crew", "tick", "events", "github"}


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda d: d.replace("C4Container", "flowchart TD"), "not one of C4Context"),
        (lambda d: d.replace('Rel(tick, events, "Appends")', "tick --> events"), "isn't a C4"),
        (lambda d: d.replace("ContainerDb(events", "ContainerDatabase(events"), "isn't a C4"),
        (lambda d: d.replace('"Appends")', '"Appends)'), "quote or a parenthesis"),
        (lambda d: d.replace('Rel(tick, events, "Appends")', "Rel(tick, events)"), "needs 3"),
        (lambda d: d.replace('"The crew") {', '"The crew")'), "opens no `{`"),
        (lambda d: d.replace("\n}\n", "\n"), "boundary left open"),
        (lambda d: d + "}\n", "closes no boundary"),
        (lambda d: d.replace("Rel(tick, github", "Rel(tik, github"), "`tik`"),
        (lambda d: d.replace("System_Ext(github", "System_Ext(tick"), "defined twice"),
        (lambda d: d.replace("Person(sponsor", "Person(the sponsor"), "isn't an alias"),
    ],
)
def test_what_keeps_a_c4_diagram_from_rendering_is_named(change, said):
    found = mermaid.c4_problems(change(CONTAINERS))
    assert found and said in " ".join(found), found


def test_a_well_formed_sequence_diagram_has_no_problems():
    assert mermaid.sequence_problems(SEQUENCE) == []


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda d: d.replace("sequenceDiagram", "C4Dynamic"), "not `sequenceDiagram`"),
        (lambda d: d.replace("\nend\n", "\n"), "block left open"),
        (lambda d: d + "end\n", "closes no block"),
        (lambda d: d.replace("T->>G: read", "T => G read"), "isn't a sequence diagram line"),
        (lambda d: d + "else later\n", "outside any block"),
        # The first real look's line (sprint-metrics#666), which Mermaid refused.
        (lambda d: d.replace("read the Goal's epics", "distinct stories; reason counts"), "`;`"),
    ],
)
def test_what_keeps_a_sequence_diagram_from_rendering_is_named(change, said):
    found = mermaid.sequence_problems(change(SEQUENCE))
    assert found and said in " ".join(found), found
