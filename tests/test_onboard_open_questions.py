"""An onboarding interview's open questions outlive the session (#181).

On 2026-09-24 the Product Owner asked whether goal sprint-metrics#46 conflicts
with the record's out of scope. The Sponsor answered `later`, and the question
never left the local transcript: the take-away kept only required answers, and
the record's PR carried only the last session.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.crews.onboarding_crew import Question, Turn, turn_description
from crew_org.flows.onboard import (
    PROPOSED_MARKER,
    interview,
    sessions_since_proposed,
    still_open_section,
    takeaway,
)
from tests.test_onboard import COMPLETE, Script, Sponsor, answers, told

GOAL_46 = (
    "intent.scope.out_of_scope",
    "Does goal #46 (another board or input format) conflict with the out of scope?",
)
ELSEWHERE = ("goals", "Should #46 stay open at all?")


def turn(*questions: tuple[str, str], say: str = "Noted.") -> Turn:
    return Turn(
        answers=answers(),
        say=say,
        questions=[Question(about=a, question=q) for a, q in questions],
    )


def test_a_session_ending_early_keeps_every_open_question_optional_ones_included():
    """#181, criterion 1: `later`, and the scope question is still there."""
    po = Script(turn(GOAL_46, ELSEWHERE))
    ended = interview({}, repository="x", turn=po, ask=Sponsor("later"), tell=told()[1])
    assert ended.open[:2] == [GOAL_46, ELSEWHERE]
    assert ("intent.scope.purpose", "What is this project for, and who is it for?") in ended.open


def test_the_take_away_keeps_them_under_their_field_or_as_still_open(tmp_path: Path):
    text = takeaway(
        {"intent": {"scope": {"purpose": "p"}}},
        {},
        repo="sprint-metrics",
        path=tmp_path / "r.yaml",
        open_questions=[GOAL_46, ELSEWHERE],
    )
    lines = text.splitlines()
    at = lines.index(f"    # Open question: {GOAL_46[1]}")
    assert "out_of_scope" in lines[at + 2], "the question sits with its field"
    assert "# Still open, from the interview:" in text
    assert f"#   - {ELSEWHERE[1]} (about goals)" in text


def test_a_later_session_shows_the_product_owner_what_was_left_open():
    """#181, criterion 3: even on a complete record, which otherwise skips the turn."""
    po = Script(turn(say="Settled."))
    interview(
        COMPLETE,
        repository="x",
        turn=po,
        ask=Sponsor("later"),
        tell=told()[1],
        still_open=[GOAL_46],
    )
    assert GOAL_46[1] in po.shown[0]["still_open"]


def test_a_question_the_product_owner_judges_answered_is_no_longer_open():
    po = Script(turn(say="The Sponsor answered #46 above."))
    ended = interview(
        COMPLETE,
        repository="x",
        turn=po,
        ask=Sponsor("later"),
        tell=told()[1],
        still_open=[GOAL_46],
    )
    assert ended.open == []


def test_a_session_that_ends_before_the_product_owner_speaks_keeps_them():
    ended = interview(
        {},
        repository="x",
        turn=Script(),
        ask=Sponsor(None),
        tell=told()[1],
        still_open=[GOAL_46],
    )
    assert GOAL_46 in ended.open or ended.interrupted, "kept, or the stop was named"


def test_the_prompt_asks_again_what_is_still_open():
    text = turn_description(
        repository="",
        intent="",
        draft="",
        proposed="",
        missing={},
        problems=[],
        conversation="",
        still_open=f"- `{GOAL_46[0]}`: {GOAL_46[1]}",
    )
    assert "## Asked in an earlier session, and not answered then" in text
    assert "Ask again any that is still open" in text


def test_the_record_pr_carries_every_session_since_the_last_proposal():
    """#181, criterion 2."""
    log = (
        "\n## 2026-09-20 10:00\n\nold session\n"
        f"\n{PROPOSED_MARKER} https://github.com/o/r/pull/1\n"
        "\n## 2026-09-24 09:30\n\n**Product Owner:** #46?\n**Sponsor:** later\n"
        "\n## 2026-09-25 14:00\n\n**Sponsor:** yes\n"
    )
    since = sessions_since_proposed(log)
    assert "2026-09-24 09:30" in since and "2026-09-25 14:00" in since
    assert "old session" not in since


def test_the_record_pr_lists_what_is_still_open_on_its_own():
    section = still_open_section([GOAL_46])
    assert section.startswith("\n\n## Still open from the interview")
    assert f"- {GOAL_46[1]} (about `{GOAL_46[0]}`)" in section
    assert still_open_section([]) == ""
