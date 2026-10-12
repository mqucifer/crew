"""The board's work types, and which of them the crew delivers."""

GOAL = "Goal"
EPIC = "Epic"
STORY = "Story"
BUG = "Bug"

# Work the crew builds, reviews, proves and merges: a story, or a bug a
# collaborator filed (ADR 0027). A check that means "a story of an epic", such
# as the split's or the backlog order's, still names STORY.
DELIVERED = frozenset({STORY, BUG})
