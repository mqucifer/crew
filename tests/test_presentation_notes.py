"""The UX Designer's presentation note (#377)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.presentation_note_crew import (
    PresentationNote,
    ReaderCriterion,
    StoryCriteria,
    criterion_lines,
    render,
)
from crew_org.events import EventSink
from crew_org.flows import presentation_notes as pn
from crew_org.flows.board_flow import STORY_SPLIT_MARKER
from crew_org.flows.review import CRITERIA
from crew_org.tools.github_project import Card

STORY = """As a **reader**, I want **a summary**, so that **I know where to look**.

## Acceptance criteria

1. **Given** a sprint with one blocked card
   **When** the standup report renders
   **Then** it names the blocked card

2. **Given** a sprint with no cards
   **When** the standup report renders
   **Then** it says there's no work

**Existing tests** — unchanged: the new behaviour is opt-in

**Estimate** — 3 points

---

Split from #62 *(Lead with a summary)* by the Business Analyst."""


def crit(then="the first section is headed `Health` and lists 2 anomalies"):
    return ReaderCriterion(given="a sprint with 2 anomalies", when="the report renders", then=then)


def note(*numbers, beyond=None):
    return PresentationNote(
        reader="the Sponsor at standup, asking what needs attention",
        sample="## Health\n\n2 anomalies: #12 blocked 3 days, #19 over its estimate",
        stories=[StoryCriteria(story=n, criteria=[crit(), crit()]) for n in numbers],
        looked_at=["src/sprint_metrics/report.py"],
        beyond_reach=beyond,
    )


# --- what the model may say ------------------------------------------------------------


@pytest.mark.parametrize("then", ["the summary is clear", "it reads easily at a glance"])
def test_a_criterion_that_needs_judgement_is_refused(then):
    with pytest.raises(ValidationError, match="asks for judgement"):
        crit(then)


def test_a_word_containing_a_judgement_word_is_not_refused():
    assert crit("the section lists nuclear, cleanup and simplex cards").then


def test_a_story_needs_two_criteria():
    with pytest.raises(ValidationError, match="at least two"):
        StoryCriteria(story=1, criteria=[crit()])


def test_the_note_carries_a_sample_that_the_criteria_outrank():
    shown = render(note(63))
    assert "~~~\n## Health" in shown
    assert "where the two differ, follow the criteria" in shown


# --- the criteria join the story's own ------------------------------------------------------


def test_reader_criteria_continue_the_story_s_numbering_inside_its_criteria():
    body = pn.with_reader_criteria(STORY, [crit(), crit()], criterion_lines)
    section = body[body.find(CRITERIA) :].split("\n**Estimate**", 1)[0]
    assert "3. **Given** a sprint with 2 anomalies" in section
    assert "4. **Given** a sprint with 2 anomalies" in section
    # Before the lines that end the section, so every reader of it sees them.
    assert body.index("4. **Given**") < body.index("**Existing tests**")


def test_a_story_is_never_given_the_criteria_twice():
    once = pn.with_reader_criteria(STORY, [crit(), crit()], criterion_lines)
    assert pn.with_reader_criteria(once, [crit(), crit()], criterion_lines) == once


# --- the flow ----------------------------------------------------------------------------


def epic(labels=("needs:ux",), number=62):
    return Card(
        item_id=f"E{number}",
        number=number,
        title="Lead with a summary",
        status="Ready",
        state="OPEN",
        work_type="Epic",
        repo="sprint-metrics",
        labels=frozenset(labels),
        parent=48,
    )


class Issues:
    def __init__(self, stories=(63, 64), comments=None):
        self.bodies = {n: STORY for n in stories} | {48: "Five-second scan", 62: "Epic body"}
        self.stories = [{"number": n, "title": f"Story {n}", "state": "open"} for n in stories]
        self._comments = list(comments or [])
        self.edited: list[int] = []
        self.labelled: list[tuple[int, list[str]]] = []

    def comments(self, repo, number):
        return [{"body": b} for b in self._comments]

    def comment(self, repo, number, body):
        self._comments.append(body)

    def sub_issues(self, repo, number):
        return self.stories

    def get(self, repo, number):
        return {"title": f"#{number}", "body": self.bodies.get(number, "")}

    def edit_issue(self, repo, number, *, body):
        self.bodies[number] = body
        self.edited.append(number)

    def add_labels(self, repo, number, labels):
        self.labelled.append((number, labels))


class Workspace:
    def __init__(self, path):
        self.path = path

    def for_repo(self, repo):
        return self

    def current(self):
        return self.path


def run(issues, cards, tmp_path, writes):
    replies = iter(writes)
    return pn.write_notes(
        issues,
        EventSink(None),
        Workspace(tmp_path),
        cards,
        default_repo="sprint-metrics",
        repos={"sprint-metrics"},
        write=lambda **kw: next(replies),
        render=render,
        line=criterion_lines,
    )


def test_a_labelled_epic_gets_its_note_and_its_stories_their_criteria(tmp_path):
    issues = Issues()
    result = run(issues, [epic()], tmp_path, [note(63, 64)])
    assert result.written == [62]
    assert pn.NOTE_MARKER in issues._comments[-1]
    assert issues.edited == [63, 64]
    assert pn.CRITERIA_MARKER in issues.bodies[63]


def test_an_epic_without_the_label_is_untouched(tmp_path):
    issues = Issues()
    result = run(issues, [epic(labels=())], tmp_path, [])
    assert result.written == [] and issues.edited == [] and issues._comments == []


def test_an_epic_with_a_note_since_its_split_is_not_written_again(tmp_path):
    issues = Issues(comments=[STORY_SPLIT_MARKER, f"{pn.NOTE_MARKER}\n## Presentation note"])
    assert run(issues, [epic()], tmp_path, []).written == []


def test_a_note_that_misses_a_story_is_retried_then_blocked(tmp_path):
    issues = Issues()
    result = run(issues, [epic()], tmp_path, [note(63), note(63)])
    assert result.blocked and "#64 has no criteria" in result.blocked[0][1]
    assert issues.labelled == [(62, ["blocked", "needs:human"])]
    assert issues.edited == []


def test_a_retry_that_covers_every_story_is_written(tmp_path):
    issues = Issues()
    result = run(issues, [epic()], tmp_path, [note(63), note(63, 64)])
    assert result.written == [62] and issues.edited == [63, 64]


def test_a_decision_beyond_reach_blocks_the_epic(tmp_path):
    issues = Issues()
    result = run(issues, [epic()], tmp_path, [note(63, 64, beyond="no report exists yet")])
    assert result.blocked and "no report exists yet" in result.blocked[0][1]


def test_its_stories_wait_until_the_note_exists():
    waiting = Issues()
    assert pn.awaiting_presentation(waiting, [epic()], "sprint-metrics") == {("sprint-metrics", 62)}
    written = Issues(comments=[STORY_SPLIT_MARKER, pn.NOTE_MARKER])
    assert pn.awaiting_presentation(written, [epic()], "sprint-metrics") == set()


def test_a_story_is_shown_its_epic_s_presentation_note():
    issues = Issues(comments=[STORY_SPLIT_MARKER, f"{pn.NOTE_MARKER}\n## Presentation note"])
    story = Card(item_id="S63", number=63, title="Story", repo="sprint-metrics", parent=62)
    assert "## Presentation note" in pn.story_notes(issues, story, "sprint-metrics")
