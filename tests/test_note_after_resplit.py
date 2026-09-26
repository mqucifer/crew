"""A re-split epic gets a new design note that follows its latest decision (#244).

sprint-metrics#59's note told the reviewer not to assert stdout was empty,
while #145's criterion required it; the gates returned #145 six times. The
Product Owner then decided the criterion wins, and the old note would have
told the re-split stories otherwise.
"""

from __future__ import annotations

from crew_org.flows.board_flow import (
    PRODUCT_ANSWER_MARKER,
    PRODUCT_QUESTION_MARKER,
    STORY_SPLIT_MARKER,
)
from crew_org.flows.design_notes import NOTE_MARKER, decided, note_for

OLD_NOTE = f"{NOTE_MARKER}\nDon't assert stdout is empty."


class Issues:
    def __init__(self, *bodies):
        self.bodies = list(bodies)

    def comments(self, repo, number):
        return [{"body": b} for b in self.bodies]


def test_a_note_from_before_the_latest_split_is_stale():
    issues = Issues(
        f"{STORY_SPLIT_MARKER}\nfirst split", OLD_NOTE, f"{STORY_SPLIT_MARKER}\nre-split"
    )
    assert note_for(issues, "r", 59) == ""


def test_a_note_written_since_the_split_counts():
    issues = Issues(f"{STORY_SPLIT_MARKER}\nsplit", OLD_NOTE)
    assert note_for(issues, "r", 59) == OLD_NOTE


def test_the_architect_is_given_the_product_owners_answer():
    issues = Issues(
        f"{PRODUCT_ANSWER_MARKER}\n**Decided:** stdout stays empty on errors.\n\n"
        "<!-- crew:by Product Owner -->"
    )
    assert decided(issues, "r", 59) == "**Decided:** stdout stays empty on errors."


def test_or_the_sponsors_reply_to_its_question():
    issues = Issues(f"{PRODUCT_QUESTION_MARKER}\nstdout or stderr?", "stderr, and stdout empty.")
    assert decided(issues, "r", 59) == "stderr, and stdout empty."


def test_no_decision_means_nothing_extra():
    assert decided(Issues(f"{STORY_SPLIT_MARKER}"), "r", 59) == ""


def test_a_story_sent_back_to_ready_does_not_block_its_epics_re_split():
    """#145 went back to Ready; its branch stayed; the rework gate refused the re-split."""
    from crew_org.flows.board_flow import started
    from crew_org.tools.github_project import Card

    class Branches:
        def branches(self, repo):
            return [{"name": "feat/145-prior-sprint"}, {"name": "feat/146-errors"}]

    cards = [
        Card(item_id="a", number=145, status="Ready", work_type="Story"),
        Card(item_id="b", number=146, status="In Progress", work_type="Story"),
    ]
    assert started(Branches(), "r", cards) == {146}
