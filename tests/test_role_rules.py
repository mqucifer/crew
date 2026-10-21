"""Each role carries the standing rules it acts on, and no others (crew#583, step D4).

The context review found the Developer and the Architect each carrying the
escalation policy, about 2.5k characters, though the crew's code escalates and
they don't, and the Developer carrying the branch and commit conventions, though
the crew commits. A rule a role can't act on is bulk in every one of its calls.
"""

from __future__ import annotations

import re

import pytest

from crew_org import calls
from crew_org.permissions import load_agents

_SECTION = re.compile(r"^## (\d+)\. ", re.M)


def system_sections(key: str) -> set[int]:
    """The constitution's sections in the role's system text, by their headings."""
    return {int(n) for n in _SECTION.findall(calls.system_text(calls.role_spec(key)))}


@pytest.mark.parametrize("key", sorted(load_agents()))
def test_a_roles_system_text_holds_only_its_sections(key):
    assert system_sections(key) == set(load_agents()[key].get("constitution", []))


@pytest.mark.parametrize("key", ["developer", "architect"])
def test_a_role_the_crews_code_escalates_for_carries_no_escalation_policy(key):
    assert 9 not in system_sections(key)


def test_the_developer_carries_no_branch_or_commit_conventions():
    """The crew names the branch, commits and opens the pull request."""
    assert 8 not in system_sections("developer")


FIELDS = ("product_owner", "architect", "qa_engineer", "ux_designer", "devops_engineer")


@pytest.mark.parametrize("key", FIELDS)
def test_a_role_carries_its_fields_practice_into_every_step(key):
    """Declared once per role, and said in a one-call step and a session's step alike
    (ADR 0035, crew#846)."""
    from crew_org.session import steps

    spec = load_agents()[key]
    background = " ".join(spec["background"].split())
    own = [None, *(s for s, sub in spec.items() if isinstance(sub, dict) and "goal" in sub)]
    for step in own:
        said = " ".join(calls.system_text(calls.role_spec(key, step)).split())
        assert f"The practice of your field: {background}" in said
        assert background in " ".join(steps.system_text(key, step).split())


def test_a_practice_names_its_standard_and_states_no_rule():
    """Standing context, short: a named practice, never a rule a check would owe."""
    from crew_org.rule_list import _RULE_WORDS

    for key in FIELDS:
        background = load_agents()[key]["background"]
        assert len(background) < 400, key
        assert not _RULE_WORDS.search(background), key
