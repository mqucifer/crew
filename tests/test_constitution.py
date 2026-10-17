"""What each role is told of the constitution (crew#756)."""

from __future__ import annotations

from crew_org import calls, constitution


def test_a_marked_passage_is_told_only_to_the_roles_it_names():
    text = (
        "a\n\n<!-- told to: Senior Engineer -->\n**x** y\n<!-- /told to -->\n\n"
        "<!-- told to: nobody -->\n**z**\n<!-- /told to -->\n\nb\n"
    )
    assert constitution.for_role(text, "Senior Engineer") == "a\n\n**x** y\n\nb\n"
    assert constitution.for_role(text, "Product Owner") == "a\n\nb\n"


def test_the_crews_own_mechanics_reach_only_the_senior_engineer():
    """The Sponsor, 2026-10-17: cut for the roles that can't act on them; the Senior
    Engineer diagnoses the crew's own code, and keeps them."""
    mechanics = ("No agent may move a card out of a human gate", "A short plan first")
    senior = calls.system_text(calls.role_spec("senior_engineer"))
    owner = calls.system_text(calls.role_spec("product_owner"))
    master = calls.system_text(calls.role_spec("scrum_master"))
    assert all(line in senior for line in mechanics)
    assert not any(line in owner or line in master for line in mechanics)
    assert "Stopping starting" not in master
    assert "<!--" not in senior + owner + master


def test_a_line_kept_only_as_documentation_is_still_in_the_document():
    assert "**Columns are queues.**" in constitution.CONSTITUTION.read_text(encoding="utf-8")
    assert "**Columns are queues.**" not in constitution.excerpt(17, role="Scrum Master")
