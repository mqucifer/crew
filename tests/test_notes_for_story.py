"""An epic's notes cut to one story (lean-prompts plan, L2).

On mqucifer/sprint-metrics#567 the two notes were 29.4k characters, more than half of it
other stories' directions and every story's reader criteria, its own already among its
acceptance criteria.
"""

from __future__ import annotations

from crew_org.flows.epic_notes import for_story

NOTES = """## Design note

The epic adds superseded metrics end to end.

**Interfaces and data shapes**
- `calculate_superseded_count(cards) -> int`

**How the stories divide the work**
- [o/r#565](https://x/o/r/issues/565): Extend the Card dataclass. Then parse and test.
- [o/r#567](https://x/o/r/issues/567): In report.py add the keys. Then the schema.
- #5670: A story whose number only starts the same. And more.

_Looked at: card.py, report.py_

## Presentation note

**Who reads it:** a script reading the JSON.

**The finished output, as it should read**

~~~
{"superseded_count": 1}
~~~

**What each story adds for the reader** (added to each story's criteria)

- #565
  - **Given** a card **When** run **Then** it shows
- #567
  - **Given** a file **When** run with --json **Then** the key is there

_Looked at: docs/formats.md_"""


def test_the_story_keeps_its_own_direction_and_the_shared_parts_whole():
    cut = for_story(NOTES, 567)
    assert "In report.py add the keys. Then the schema." in cut
    assert "`calculate_superseded_count(cards) -> int`" in cut
    assert '{"superseded_count": 1}' in cut and "**Who reads it:**" in cut


def test_other_stories_shrink_to_what_they_cover():
    cut = for_story(NOTES, 567)
    assert "Extend the Card dataclass.…" in cut and "Then parse and test" not in cut
    assert "#5670: A story whose number only starts the same.…" in cut


def test_the_per_story_reader_criteria_and_looked_at_lines_go():
    cut = for_story(NOTES, 567)
    assert "**Given**" not in cut and "Looked at" not in cut
    assert "_Each story's reader criteria are among its own acceptance criteria._" in cut


def test_without_a_story_number_the_notes_are_whole():
    assert for_story(NOTES, 0) == NOTES
