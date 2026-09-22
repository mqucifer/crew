"""What the crew computes, reaching somebody.

Four things were computed, or computable, and reached nobody. Each was the
last hop of something already built — the threshold configured, the function
written, the event kind defined — and what was missing in every case was the
call.

- `process.aging_blocked()` returns blocked cards past the threshold, and
  `blocked_aging_days: 3` is configured with the comment "raise to Sponsor at
  sprint review beyond this". Nothing called it. On 2026-09-19
  sprint-metrics#12 had been blocked most of a day and the close did not raise
  it; the retro mentioned it only because the model noticed it in the board
  data it was handed.
- `process.over_limit()` detects a column over its WIP limit. Nothing called
  it either.
- `events.bridge_crewai()` forwards CrewAI's bus onto the sink and was called
  from nowhere, so no real run's log held a model call, a token count or a
  tool invocation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crew_org.events import (
    CrewEvent,
    EventKind,
    EventSink,
    _usage,
    blocked_since,
    replay_dir,
)
from crew_org.process import ProcessRules

BLOCKED = "Blocked"


def rules(**over) -> ProcessRules:
    org = {
        "board": {
            "columns": ["Ready", "In Progress", "Reviewing", "Done"],
            "blocked_column": BLOCKED,
            "human_gates": [],
        },
        "wip_limits": {"In Progress": 3},
        "sprint": {"blocked_aging_days": 3},
    }
    org.update(over)
    return ProcessRules.from_config(org)


def moved(card: int, frm: str, to: str, at: datetime) -> CrewEvent:
    return CrewEvent(at=at, kind=EventKind.CARD_MOVED, card=card, detail={"from": frm, "to": to})


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


# --- when did this card become blocked? ----------------------------------


def test_the_log_says_when_a_card_was_blocked():
    """The board says a card *is* blocked and never since when — an issue's
    `updated` moves for any comment, so it cannot answer this."""
    events = [moved(12, "In Progress", BLOCKED, days_ago(4))]
    assert blocked_since(events, blocked_column=BLOCKED) == {12: days_ago(4)}


def test_a_card_that_was_freed_is_not_blocked():
    events = [
        moved(12, "In Progress", BLOCKED, days_ago(4)),
        moved(12, BLOCKED, "In Progress", days_ago(2)),
    ]
    assert blocked_since(events, blocked_column=BLOCKED) == {}


def test_a_card_blocked_twice_counts_from_the_latest_time():
    """What "how long has this been blocked" means to the person being asked
    to deal with it."""
    events = [
        moved(12, "In Progress", BLOCKED, days_ago(9)),
        moved(12, BLOCKED, "In Progress", days_ago(7)),
        moved(12, "In Progress", BLOCKED, days_ago(4)),
    ]
    assert blocked_since(events, blocked_column=BLOCKED) == {12: days_ago(4)}


def test_moves_that_are_not_about_blocking_are_ignored():
    events = [moved(12, "In Progress", "Reviewing", days_ago(4))]
    assert blocked_since(events, blocked_column=BLOCKED) == {}


# --- and which of them are past the threshold ----------------------------


def test_a_card_past_the_threshold_is_raised():
    since = blocked_since([moved(12, "In Progress", BLOCKED, days_ago(4))], blocked_column=BLOCKED)
    assert rules().aging_blocked(since, now=NOW) == {12: 4}


def test_a_card_blocked_yesterday_is_not():
    since = blocked_since([moved(12, "In Progress", BLOCKED, days_ago(1))], blocked_column=BLOCKED)
    assert rules().aging_blocked(since, now=NOW) == {}


# --- reading the whole history, not one command's log --------------------


def test_history_is_read_across_every_log(tmp_path):
    """One log per command, so a card blocked by `deliver` is read about by
    `close` — neither file answers the question alone."""
    deliver = EventSink(tmp_path / "deliver.jsonl")
    deliver.emit(moved(12, "In Progress", BLOCKED, days_ago(4)))
    tick = EventSink(tmp_path / "tick.jsonl")
    tick.emit(moved(19, "Ready", BLOCKED, days_ago(5)))

    assert blocked_since(replay_dir(tmp_path), blocked_column=BLOCKED) == {
        12: days_ago(4),
        19: days_ago(5),
    }


def test_history_is_ordered_across_logs(tmp_path):
    """A free recorded in one file must win over a block recorded earlier in
    another, whichever file is read first."""
    EventSink(tmp_path / "a-deliver.jsonl").emit(moved(12, BLOCKED, "In Progress", days_ago(1)))
    EventSink(tmp_path / "z-tick.jsonl").emit(moved(12, "In Progress", BLOCKED, days_ago(4)))
    assert blocked_since(replay_dir(tmp_path), blocked_column=BLOCKED) == {}


def test_a_corrupt_line_does_not_lose_the_history(tmp_path):
    path = tmp_path / "tick.jsonl"
    EventSink(path).emit(moved(12, "In Progress", BLOCKED, days_ago(4)))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert blocked_since(replay_dir(tmp_path), blocked_column=BLOCKED) == {12: days_ago(4)}


def test_no_history_at_all_is_not_an_error(tmp_path):
    assert replay_dir(tmp_path / "nothing") == []


# --- a column that is over, rather than at, its limit --------------------


def test_a_column_over_its_limit_is_named():
    """The limits are enforced on the way in, so a column cannot be *pushed*
    over. It can still *be* over, after a limit is lowered or cards are moved
    by hand, and nothing said so."""
    assert rules().over_limit({"In Progress": 5}) == {"In Progress": (5, 3)}


def test_a_column_at_its_limit_is_not_a_breach():
    """At the limit is the system working, not failing."""
    assert rules().over_limit({"In Progress": 3}) == {}


# --- what a model call cost ----------------------------------------------


class Usage:
    def __init__(self, usage):
        self.usage = usage


def test_token_counts_are_recorded():
    assert _usage(Usage({"prompt_tokens": 100, "completion_tokens": 20})) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }


def test_provider_spellings_are_normalised():
    """The crew's own event model is the contract; provider names do not leak
    into the log."""
    assert _usage(Usage({"input_tokens": 100, "output_tokens": 20})) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }


def test_reasoning_tokens_are_picked_out_of_nested_details():
    """A generation that spends its whole budget reasoning and emits no text is
    what hung an epic split six times, and it is invisible in a total."""
    usage = Usage({"total_tokens": 16386, "completion_tokens_details": {"reasoning_tokens": 16386}})
    assert _usage(usage) == {"total_tokens": 16386, "reasoning_tokens": 16386}


def test_a_shape_nobody_anticipated_yields_nothing_rather_than_raising():
    assert _usage(Usage("not a mapping")) == {}
    assert _usage(Usage(None)) == {}
