"""A role the board has no owner option for never breaks a move (2026-09-29).

The DevOps Engineer returned sprint-metrics PR #330 on a board whose Owner
Agent field had no option for it: the card moved, the event was never
written, and the review pass stopped with a BoardError.
"""

from __future__ import annotations

from crew_org.events import EventKind, EventSink
from crew_org.flows.moves import move_card
from crew_org.tools.github_project import BoardError
from crew_org.tools.stack import board_roles


class Board:
    def __init__(self, options):
        self.options = options
        self.moves, self.owners = [], []

    def set_status(self, item, to):
        self.moves.append((item, to))

    def set_owner_agent(self, item, role):
        if role not in self.options:
            raise BoardError(f"'Owner Agent' has no option {role!r}")
        self.owners.append((item, role))

    @property
    def schema(self):
        board = self

        class Field:
            options = {name: f"id-{name}" for name in board.options}

        class Schema:
            def field(self, name):
                return Field()

        return Schema()


def test_a_move_by_a_role_the_board_lacks_still_moves_and_is_reported():
    board, seen = Board({"Code Reviewer"}), []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    move_card(board, sink, item_id="S307", to="In Progress", by="DevOps Engineer", card=307)
    assert board.moves == [("S307", "In Progress")] and board.owners == []
    assert any("owner not recorded" in e.summary for e in seen)
    assert any(e.kind is EventKind.CARD_MOVED and e.role == "DevOps Engineer" for e in seen)


def test_the_tick_names_each_role_the_board_has_no_option_for():
    check = board_roles(Board({"Developer", "Code Reviewer"}))
    assert not check.ok and "DevOps Engineer" in check.detail and "Senior Engineer" in check.detail


def test_a_board_with_every_role_passes():
    from crew_org.permissions import load_agents

    roles = {a["role"] for a in load_agents().values()}
    assert board_roles(Board(roles)).ok
