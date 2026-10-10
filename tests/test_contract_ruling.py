"""One Business Analyst pass on merged tests a story broke and didn't declare (crew#584, A5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.contract_crew import ContractRuling
from crew_org.events import EventSink
from crew_org.flows import delivery, record
from crew_org.flows.board_flow import EXISTING_TESTS
from crew_org.tools.github_project import Card

TEST = "tests/test_schema_gen.py::test_single_sprint_schema_required_and_cycle_time"
AMEND = ContractRuling(
    decision="amend",
    tests=[TEST],
    asserts="required lists fifteen keys, the four new ones included",
    why="R3 adds the four keys to the single-sprint response.",
)


def test_an_amendment_names_its_tests_and_what_they_assert_after():
    with pytest.raises(ValidationError, match="names the tests"):
        ContractRuling(decision="amend", why="R3 adds the keys to the response.")
    with pytest.raises(ValidationError, match="path::test"):
        ContractRuling(decision="amend", tests=["the schema test"], asserts="x" * 12, why="y" * 12)
    assert ContractRuling(decision="return", why="No criterion changes it.").tests == []


class Issues:
    owner = "mqucifer"

    def __init__(self):
        self.bodies = {
            529: f"As a crew, I want keys.\n\n{EXISTING_TESTS} unchanged: the new behaviour is "
            "opt-in",
            468: "The epic.\n\n## Refinement conclusion\n\n| R1 | accepted | A | B | C | D |",
        }
        self.comments: list[tuple[int, str]] = []

    def get(self, repo, number):
        return {"body": self.bodies[number]}

    def edit_issue(self, repo, number, *, body):
        self.bodies[number] = body

    def comment(self, repo, number, body):
        self.comments.append((number, body))


def rule(monkeypatch, issues, ruling):
    monkeypatch.setattr("crew_org.crews.contract_crew.rule_on_contract", lambda **_: ruling)
    card = Card(item_id="S", number=529, title="Keys in /sprint", parent=468)
    return delivery.rule_on_contract(
        issues,
        EventSink(None),
        card=card,
        repo="sprint-metrics",
        story="S",
        rows="",
        failures={TEST: "assert 11 == 15"},
    )


def test_an_amendment_rewrites_the_storys_existing_tests_line(monkeypatch):
    issues = Issues()
    rule(monkeypatch, issues, AMEND)
    body = issues.bodies[529]
    assert body.count(EXISTING_TESTS) == 1
    assert "changes what they assert" in body and TEST in body
    assert any(n == 529 and "amended in delivery" in b for n, b in issues.comments)
    assert delivery.declared_contract(body) >= {TEST}


def test_an_amendment_becomes_a_row_of_the_epics_record(monkeypatch):
    issues = Issues()
    rule(monkeypatch, issues, AMEND)
    found = record.parse(record.split(issues.bodies[468])[1])
    assert found.declared_tests() == [TEST]
    assert "sprint-metrics#529" in found.tests[0].declared_by


def test_a_return_changes_neither_the_story_nor_the_record(monkeypatch):
    issues = Issues()
    before = dict(issues.bodies)
    ruling = rule(
        monkeypatch, issues, ContractRuling(decision="return", why="Nothing asks for it.")
    )
    assert ruling.decision == "return" and issues.bodies == before
