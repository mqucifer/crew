"""Which earlier stories in its epic a story waits for: one reading for planning and
delivery (crew#756).

The planner and delivery each wrote their own test for an earlier sibling, and the
two could drift: the flow review found the planner admitting a whole epic's stories
that delivery then held one by one. Both now read the siblings here; each decides
what it does with them.
"""

from __future__ import annotations

from crew_org.columns import DONE
from crew_org.tools.github_project import Card
from crew_org.work_types import TASK

STORY_TYPE = "Story"


def unlanded_earlier_siblings(cards: list[Card], story: Card) -> list[Card]:
    """The earlier stories in this story's epic that haven't landed yet.

    Stories in one epic extend each other. sprint-metrics #31 created `scrape.py`
    with an HTTP server on it; #32, whose story was "handle unavailable data **at the
    scrape endpoint**", was claimed 100 seconds later, could not see `scrape.py`, and
    built a second server in another module. Both passed their own tests.

    Earlier means a lower card number, which is the order the Business Analyst
    proposed the stories: `refine_epics` creates their issues in that order, so the
    numbers ascend in it. Landed means Done or closed: an open pull request is not in
    anyone's base.

    A role's Task under an epic, DevOps's CI and release (crew#839), waits for every
    story of the epic, whatever its number: a re-split's stories are numbered after it,
    and the release ships what they add.
    """
    if story.parent is None:
        return []
    every = story.work_type == TASK
    return [
        c
        for c in cards
        # The repository as well as the parent number. Both are plain numbers,
        # so a story in one repository could be held back by a sibling of an
        # epic in another whenever the epic numbers happened to line up.
        if c.repo == story.repo
        and c.parent == story.parent
        and c.work_type == STORY_TYPE
        and (every or (c.number or 0) < (story.number or 0))
        and c.state != "CLOSED"
        and c.status != DONE
    ]
