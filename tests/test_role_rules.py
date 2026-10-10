"""Each role carries the standing rules it acts on, and no others (crew#583, step D4).

The context review found the Developer and the Architect each carrying the
escalation policy, about 2.5k characters, though the crew's code escalates and
they don't, and the Developer carrying the branch and commit conventions, though
the crew commits. A rule a role can't act on is bulk in every one of its calls.
"""

from __future__ import annotations

import re

import pytest

from crew_org.agents import build_agent
from crew_org.permissions import load_agents

_SECTION = re.compile(r"^## (\d+)\. ", re.M)


def system_sections(key: str) -> set[int]:
    """The constitution's sections in the role's system text, by their headings."""
    return {int(n) for n in _SECTION.findall(build_agent(key).backstory)}


@pytest.mark.parametrize("key", sorted(load_agents()))
def test_a_roles_system_text_holds_only_its_sections(key):
    assert system_sections(key) == set(load_agents()[key].get("constitution", []))


@pytest.mark.parametrize("key", ["developer", "architect"])
def test_a_role_the_crews_code_escalates_for_carries_no_escalation_policy(key):
    assert 9 not in system_sections(key)


def test_the_developer_carries_no_branch_or_commit_conventions():
    """The crew names the branch, commits and opens the pull request."""
    assert 8 not in system_sections("developer")


def test_text_edits_says_what_the_instructions_use_it_for():
    """The field said "not Python" while the instructions used it for a Python file's imports."""
    from crew_org.crews.delivery_crew import Implementation

    said = Implementation.model_fields["text_edits"].description or ""
    assert "outside any function or class" in said and "imports" in said
