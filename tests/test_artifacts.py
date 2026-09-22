"""Everything the crew writes says which role wrote it."""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows import artifacts


class FakeIssues:
    def __init__(self):
        self.comments_: list[tuple[int, str]] = []
        self.added: list[tuple[int, list[str]]] = []
        self.removed: list[tuple[int, str]] = []

    def comment(self, repo, number, body):
        self.comments_.append((number, body))

    def add_labels(self, repo, number, labels):
        self.added.append((number, labels))

    def remove_label(self, repo, number, label):
        self.removed.append((number, label))


def run(fn, **kwargs):
    issues, sink, seen = FakeIssues(), EventSink(None), []
    sink.subscribe(seen.append)
    fn(issues, sink, repo="sprint-metrics", number=6, **kwargs)
    return issues, seen


# --- comments ------------------------------------------------------------


def test_a_comment_is_reported_as_well_as_written():
    _, seen = run(artifacts.comment, body="x", by="Developer")

    assert seen[0].role == "Developer"
    assert seen[0].detail["artifact"] == "comment"


# --- labels --------------------------------------------------------------


def test_a_label_change_records_its_role_in_the_log():
    """GitHub's timeline attributes a label change to the bot and offers nowhere
    else to put an actor, so this half can only land in the event log. That is a
    limitation of the platform, not a shortcut."""
    issues, seen = run(artifacts.label, by="Developer", add=["blocked"])

    assert issues.added == [(6, ["blocked"])]
    assert seen[0].role == "Developer"
    assert seen[0].detail == {
        "artifact": "label",
        "added": ["blocked"],
        "removed": [],
        "repo": "sprint-metrics",
    }


def test_labels_can_be_added_and_removed_together():
    issues, seen = run(artifacts.label, by="Architect", add=["needs:design"], remove=["blocked"])

    assert issues.added == [(6, ["needs:design"])]
    assert issues.removed == [(6, "blocked")]
    assert len(seen) == 1, "one change, one event"


def test_a_label_change_nobody_made_claims_nothing():
    _, seen = run(artifacts.label, by=None, remove=["needs:rework"])

    assert seen[0].role is None


def test_changing_no_labels_says_nothing():
    """A no-op must not write an event claiming something happened."""
    issues, seen = run(artifacts.label, by="Developer")

    assert issues.added == [] and issues.removed == []
    assert seen == []
