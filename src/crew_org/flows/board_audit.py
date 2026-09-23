"""Invariants the board should hold, stated so they can be checked.

A closed card sitting in `Sprint Backlog` is not an opinion — it is a fact the
board contradicts about itself, and `crew capability` reads those columns as
occupied. Thirty-three of them accumulated before anybody counted.

These are deliberately *claims about the board*, not about the code, so they
cannot be a test: the board changes without the repository changing. They are
computed on demand instead, which is the same rule the final-state outline
applies to everything else — a gap that can be computed should be computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_org.columns import DONE
from crew_org.tools.github_project import Card


@dataclass
class BoardAudit:
    """Cards whose column contradicts what the card itself says."""

    # Closed, and not in Done. The column says the work is waiting; the issue
    # says it is finished. `.github/workflows/board.yml` prevents new ones.
    finished_but_waiting: list[Card] = field(default_factory=list)
    # In Done, and still open. The inverse, and the one a close-triggered
    # workflow cannot catch — reopening an issue moves nothing.
    waiting_but_finished: list[Card] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.finished_but_waiting or self.waiting_but_finished)

    @property
    def total(self) -> int:
        return len(self.finished_but_waiting) + len(self.waiting_but_finished)


def audit(cards: list[Card]) -> BoardAudit:
    """Read the board against itself.

    Both directions, because they fail for different reasons and only one of
    them has a workflow behind it. An item closed moves to Done automatically;
    an item *reopened* does not move back, so a card can sit in Done with its
    issue open and nothing will ever say so.
    """
    out = BoardAudit()
    for card in cards:
        if card.state == "CLOSED" and card.status != DONE:
            out.finished_but_waiting.append(card)
        elif card.state == "OPEN" and card.status == DONE:
            out.waiting_but_finished.append(card)
    out.finished_but_waiting.sort(key=lambda c: (c.repo or "", c.number or 0))
    out.waiting_but_finished.sort(key=lambda c: (c.repo or "", c.number or 0))
    return out
