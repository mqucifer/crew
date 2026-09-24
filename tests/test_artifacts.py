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


# --- references link to the card they name (#118) -----------------------------

from crew_org.flows.artifacts import link_references  # noqa: E402

CREW, SM = "crew", "sprint-metrics"


def linked(text, *, home=CREW, delivery=(SM,), known=()):
    return link_references(text, owner="mqucifer", home=home, delivery=list(delivery), known=known)


def test_a_bare_card_number_on_the_crew_repo_links_to_the_delivery_card():
    """The first standup's "#31" linked to crew#31, a different issue."""
    assert linked("#31 — PR #67 was behind main") == (
        "mqucifer/sprint-metrics#31 — PR mqucifer/sprint-metrics#67 was behind main"
    )


def test_a_repository_qualified_name_becomes_a_link():
    """`sprint-metrics#32` is what Card.name(qualify=True) writes, and GitHub does
    not link it; `owner/repo#N` it does."""
    assert linked("sprint-metrics#32") == "mqucifer/sprint-metrics#32"


def test_in_its_own_repository_a_reference_stays_bare():
    assert linked("#31 and sprint-metrics#32", home=SM) == "#31 and #32"


def test_the_crews_own_issue_is_not_mistaken_for_a_delivery_card():
    assert linked("crew#9") == "#9"
    assert linked("see crew#9", home=SM, known=[CREW]) == "see mqucifer/crew#9"


def test_what_is_not_a_reference_is_left_alone():
    text = "## Sprint 4\ncolour #12abc, PR#5, mqucifer/sprint-metrics#5, issue#3"
    assert linked(text) == text


def test_with_several_delivery_repositories_a_bare_number_is_not_guessed():
    assert linked("#31", delivery=(SM, "other")) == "#31"
    assert linked("other#4", delivery=(SM, "other")) == "mqucifer/other#4"
