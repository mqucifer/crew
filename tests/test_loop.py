"""A tick runs the whole loop, not a quarter of it."""

from __future__ import annotations

import pytest

from crew_org.events import EventSink
from crew_org.flows import loop
from crew_org.process import ProcessRules


class FakeBoard:
    """Only what the loop itself touches: the per-pass board read that seeds
    the live view's swimlanes."""

    def __init__(self, counts=None):
        self._counts = counts or {"Ready": 2, "In Progress": 1}

    def cards(self):
        return []

    def counts(self, cards=None):
        return dict(self._counts)


ORG = {
    "board": {
        "columns": ["Ready", "In Progress", "Reviewing", "QAing", "Merging", "Done"],
        "blocked_column": "Blocked",
        "human_gates": [],
    },
    "wip_limits": {"In Progress": 3, "Reviewing": 3},
    "sprint": {"blocked_aging_days": 3},
}


@pytest.fixture
def crew(counts=None):
    """A Crew whose every dependency is unused: the phases are all faked.

    `rules` is real, because the loop itself asks it whether any column is over
    its limit — that is the loop's own behaviour, not a phase's.
    """
    return loop.Crew(
        board=FakeBoard(counts),
        issues=None,
        sink=EventSink(None),
        ws=None,
        sandbox=None,
        rules=ProcessRules.from_config(ORG),
        policy=None,
        ledger=None,
        org={},
        repo="sprint-metrics",
        repos={"sprint-metrics"},
        sprint="S1",
        capacity=20,
        reviewer=None,
        reviewer_login="approver[bot]",
    )


def phases(monkeypatch, *specs):
    """Replace the phase table with `(name, moved-or-raises)` pairs."""
    calls: list[str] = []

    def make(name, behaviour):
        def run(_crew):
            calls.append(name)
            if isinstance(behaviour, Exception):
                raise behaviour
            moved = behaviour.pop(0) if isinstance(behaviour, list) else behaviour
            return loop.PhaseOutcome(name, moved=moved, summary=f"{name} ran")

        return (name, run)

    monkeypatch.setattr(loop, "PHASES", tuple(make(n, b) for n, b in specs))
    return calls


def test_every_phase_runs_in_dependency_order(crew, monkeypatch):
    """Drain first: work already started is pushed forward before new work is
    claimed, so a story does not branch from a main missing its predecessors."""
    calls = phases(
        monkeypatch,
        ("refine", False),
        ("admit", False),
        ("review", False),
        ("qa", False),
        ("deliver", False),
    )
    loop.run(crew)

    assert calls == ["refine", "admit", "review", "qa", "deliver"]


def test_it_keeps_going_while_anything_moves(crew, monkeypatch):
    """AC1: until nothing further can move. Delivery moving means refinement
    may have something new to do, so quiescence is the only stopping point."""
    calls = phases(monkeypatch, ("refine", [True, True, False]), ("deliver", False))
    result = loop.run(crew)

    assert result.passes == 3, "two passes that moved, then one that did not"
    assert calls.count("refine") == 3


def test_a_pass_that_moves_nothing_ends_the_tick(crew, monkeypatch):
    phases(monkeypatch, ("refine", False))
    result = loop.run(crew)

    assert result.passes == 1
    assert result.settled is True


def test_a_board_that_will_not_settle_is_capped(crew, monkeypatch):
    """A pass that keeps moving forever is a bug, not a busy board."""
    phases(monkeypatch, ("refine", True))
    result = loop.run(crew, max_passes=3)

    assert result.passes == 3
    assert result.settled is False, "stopped, not stable — saying otherwise tells the "
    "Sponsor the work is finished"


def test_a_failing_phase_does_not_abort_the_pass(crew, monkeypatch):
    """AC2: later phases still run for the cards they can act on."""
    calls = phases(
        monkeypatch,
        ("refine", RuntimeError("model unavailable")),
        ("qa", False),
        ("deliver", False),
    )
    result = loop.run(crew)

    assert calls == ["refine", "qa", "deliver"], "the pass continued"
    assert [o.name for o in result.failed] == ["refine"]
    assert "model unavailable" in result.failed[0].error


def test_a_failure_is_reported_rather_than_raised(crew, monkeypatch):
    """A tick that aborts on the first error leaves the board partway through a
    state nobody chose."""
    phases(monkeypatch, ("deliver", RuntimeError("github is down")))
    result = loop.run(crew)

    assert result.failed, "recorded"
    assert result.last("deliver").error.startswith("RuntimeError")
    assert not result.moved


def test_a_failing_phase_alone_does_not_keep_the_loop_spinning(crew, monkeypatch):
    """A phase that fails every pass has not moved anything, so it must not
    read as progress — that is an infinite loop wearing a failure's clothes."""
    phases(monkeypatch, ("refine", RuntimeError("still down")))
    assert loop.run(crew).passes == 1


# --- a dry pass is not progress ------------------------------------------


class FakeDeliveryResult:
    def __init__(self, **kw):
        self.landed = kw.get("landed", [])
        self.delivered = kw.get("delivered", [])
        self.blocked = kw.get("blocked", [])
        self.recovered = kw.get("recovered", [])
        self.reworked = kw.get("reworked", [])
        self.awaiting_approval = kw.get("awaiting_approval", [])
        self.unapprovable = kw.get("unapprovable", [])
        self.unmergeable = kw.get("unmergeable", [])
        self.conflicted = kw.get("conflicted", [])
        self.waiting_on_a_sibling = kw.get("waiting_on_a_sibling", [])
        self.would_land = kw.get("would_land", [])


def deliver_returning(monkeypatch, **kw):
    import crew_org.flows.delivery as delivery_mod

    monkeypatch.setattr(delivery_mod, "deliver", lambda *a, **k: FakeDeliveryResult(**kw))


def test_a_real_delivery_is_movement(crew, monkeypatch):
    deliver_returning(monkeypatch, delivered=[1])

    assert loop._deliver(crew).moved is True


# --- the panel is seeded from the board ----------------------------------


def test_each_pass_reports_the_board_as_it_stands(crew, monkeypatch):
    """The live view seeds its swimlanes from this. Without it the lanes only
    ever showed the deltas of whatever moved while someone was watching."""
    seen = []
    crew.sink.subscribe(seen.append)
    phases(monkeypatch, ("refine", [True, False]))
    loop.run(crew)

    seeds = [e for e in seen if e.detail.get("counts")]
    assert len(seeds) == 2, "once per pass, not once per run"
    assert seeds[0].detail["counts"] == {"Ready": 2, "In Progress": 1}


def test_a_board_that_cannot_be_read_does_not_abort_the_tick(crew, monkeypatch):
    """A panel that cannot be seeded is worth less than a tick, and every phase
    reads the board for itself anyway."""

    class BrokenBoard:
        def cards(self):
            raise RuntimeError("github is down")

        def counts(self, cards=None):
            raise RuntimeError("github is down")

    crew.board = BrokenBoard()
    seen = []
    crew.sink.subscribe(seen.append)
    phases(monkeypatch, ("refine", False))
    result = loop.run(crew)

    assert result.passes == 1, "the pass ran"
    assert any("could not read the board" in e.summary for e in seen), "and said why not"


# --- the report is about the run --------------------------------------------


def test_a_phase_reports_the_run_not_the_last_pass(crew, monkeypatch):
    """A run whose first pass admitted a story and whose second admitted none
    reported zero. Work happened and the report denied it."""
    calls = {"n": 0}

    def admit(_crew):
        calls["n"] += 1
        first = calls["n"] == 1
        return loop.PhaseOutcome(
            "admit", moved=first, counts={"stories": 1 if first else 0, "points": 5 if first else 0}
        )

    monkeypatch.setattr(loop, "PHASES", (("admit", admit),))
    result = loop.run(crew)

    assert result.last("admit").counts["stories"] == 0, "the last pass really did admit none"
    assert result.totals("admit") == {"stories": 1, "points": 5}, "and the run admitted one"


def test_what_a_phase_could_not_do_is_carried(crew, monkeypatch):
    """merge_approved works these out and the tick printed none of them."""
    deliver_returning(
        monkeypatch,
        awaiting_approval=[(31, 36)],
        waiting_on_a_sibling=[(32, 31)],
    )
    outcome = loop._deliver(crew)

    assert any("#31" in h and "approving review" in h for h in outcome.held)
    assert any("#32" in h and "waits for #31" in h for h in outcome.held)


def test_nothing_to_do_is_not_the_same_as_nothing_allowed(crew, monkeypatch):
    """A pass can move nothing because there is nothing to do, or because
    nothing it could do was allowed. Those are opposite states."""
    phases(monkeypatch, ("refine", False))
    assert loop.run(crew).stuck is False

    def blocked(_crew):
        return loop.PhaseOutcome("deliver", moved=False, held=["#31 — waiting on an approval"])

    monkeypatch.setattr(loop, "PHASES", (("deliver", blocked),))
    result = loop.run(crew)

    assert result.settled is True, "nothing moved"
    assert result.stuck is True, "but not because there was nothing to do"


# --- a block is an event, not a state ------------------------------------


def test_a_card_blocked_mid_run_is_still_reported(crew, monkeypatch):
    """#31 blocked on an exhausted escalation budget in pass 1. Pass 2 could not
    see it — the card was no longer claimable — so the summary reported only
    that its sibling was waiting, and never that anything had blocked."""
    calls = {"n": 0}

    def deliver(_crew):
        calls["n"] += 1
        if calls["n"] == 1:
            return loop.PhaseOutcome("deliver", moved=True, blocked=["#31 — budget exhausted"])
        return loop.PhaseOutcome("deliver", moved=False, held=["#32 — waits for #31"])

    monkeypatch.setattr(loop, "PHASES", (("deliver", deliver),))
    result = loop.run(crew)

    assert result.blocked("deliver") == ["#31 — budget exhausted"], "the run remembers"
    assert result.held("deliver") == ["#32 — waits for #31"], "and state is still now"
    assert result.blocked_any is True
    assert result.blocked_count == 1


def test_the_same_block_reported_twice_is_named_once(crew, monkeypatch):
    """A phase can report one block from the outcome and again from the card
    move."""

    def deliver(_crew):
        return loop.PhaseOutcome("deliver", moved=False, blocked=["#31 — budget exhausted"])

    monkeypatch.setattr(loop, "PHASES", (("deliver", deliver),))
    assert loop.run(crew).blocked("deliver") == ["#31 — budget exhausted"]


def test_a_clean_run_blocked_nothing(crew, monkeypatch):
    phases(monkeypatch, ("refine", False))
    result = loop.run(crew)

    assert result.blocked_any is False
    assert result.blocked_count == 0


# --- a breach is reported, not merely prevented --------------------------


def test_a_column_over_its_limit_is_reported_on_a_tick(monkeypatch):
    """`over_limit` has always been able to answer this and nothing asked it,
    so a column that was already over — a limit lowered, cards moved by hand —
    looked exactly like a column at its limit, which is healthy."""
    phases(monkeypatch)
    over = loop.Crew(
        board=FakeBoard({"In Progress": 5}),
        issues=None,
        sink=EventSink(None),
        ws=None,
        sandbox=None,
        rules=ProcessRules.from_config(ORG),
        policy=None,
        ledger=None,
        org={},
        repo="sprint-metrics",
        repos={"sprint-metrics"},
        sprint="S1",
        capacity=20,
        reviewer=None,
        reviewer_login="approver[bot]",
    )
    seen = []
    over.sink.subscribe(seen.append)
    result = loop.run(over)

    assert result.over_limit == {"In Progress": (5, 3)}
    assert any("over its WIP limit" in e.summary for e in seen)


def test_a_column_at_its_limit_is_not_reported(crew, monkeypatch):
    phases(monkeypatch)
    seen = []
    crew.sink.subscribe(seen.append)
    result = loop.run(crew)

    assert result.over_limit == {}
    assert not any("over its WIP limit" in e.summary for e in seen)


def test_a_tick_bridges_the_model_bus_onto_its_own_log(crew, monkeypatch):
    """The bridge existed and nothing called it, so every real run's log was
    blind to everything the model did."""
    from crew_org import events as events_mod

    events_mod.reset_bridge()
    phases(monkeypatch)
    loop.run(crew)
    assert crew.sink in events_mod.bridged_sinks()
