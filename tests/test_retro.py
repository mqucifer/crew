"""The retro is recorded, and what it found becomes work (#50).

It used to go to a terminal and nowhere else. Now it is an issue per sprint on
the crew repository, and each defect it proposes is filed where the thing it
found lives: how the crew works in the crew repository, what it built in the
delivery repository that holds it.
"""

from __future__ import annotations

import pytest

import crew_org.flows.close as close_mod
from crew_org.crews.retro_crew import ProcessDefect, Retro
from crew_org.escalation import EscalationLedger
from crew_org.events import EventKind, EventSink
from crew_org.flows.close import close_sprint
from crew_org.flows.retro import (
    FINDING_LABEL,
    RETRO_LABEL,
    existing_retro,
    marker,
    record_retro,
    route,
)

SPRINT = "S4"
CREW = "crew"
PRODUCT = "sprint-metrics"


def process(subject="Story sizing") -> ProcessDefect:
    return ProcessDefect(
        subject=subject,
        problem="three stories needed escalation",
        change="stories must name the module a metric belongs in",
    )


def product(subject="#8 duplicate", repository=PRODUCT) -> ProcessDefect:
    return ProcessDefect(
        subject=subject,
        problem="two definitions of compute_wip_violations",
        change="delete the second definition",
        about="product",
        repository=repository,
    )


class FakeIssues:
    def __init__(self, existing=(), fail_on=()):
        self.owner = "mqucifer"
        self.created: list[dict] = []
        self.labels_ensured: set[tuple[str, str]] = set()
        self._existing = list(existing)
        self._fail_on = set(fail_on)
        self._next = 200

    def create(self, repo, title, body, labels=None):
        if any(s in title for s in self._fail_on):
            raise RuntimeError("403 Resource not accessible by integration")
        self._next += 1
        self.created.append(
            {"repo": repo, "number": self._next, "title": title, "body": body, "labels": labels}
        )
        return {"number": self._next}

    def ensure_label(self, repo, name, *, color, description):
        self.labels_ensured.add((repo, name))

    def labelled(self, repo, label):
        return [i for i in self._existing if label in i.get("labels", [])]


def record(retro, issues=None, repos=(PRODUCT,)):
    issues = issues or FakeIssues()
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    out = record_retro(
        issues, sink, retro, sprint=SPRINT, crew_repo=CREW, delivery_repos=list(repos)
    )
    return out, issues, seen


# --- where a defect goes ------------------------------------------------------


def test_a_process_defect_goes_to_the_crew():
    assert route(process(), crew_repo=CREW, delivery_repos=[PRODUCT]) == (CREW, "")


def test_a_product_defect_goes_to_the_repository_it_names():
    assert route(product(), crew_repo=CREW, delivery_repos=[PRODUCT, "other"]) == (PRODUCT, "")


def test_a_product_defect_naming_nothing_goes_to_the_only_delivery_repository():
    assert route(product(repository=None), crew_repo=CREW, delivery_repos=[PRODUCT]) == (
        PRODUCT,
        "",
    )


def test_a_product_defect_naming_a_repository_the_crew_does_not_deliver_is_not_lost():
    repo, note = route(product(repository="elsewhere"), crew_repo=CREW, delivery_repos=[PRODUCT])
    assert repo == CREW
    assert "`elsewhere`" in note and "move it" in note


# --- recording ------------------------------------------------------------------


def test_the_retro_becomes_an_issue_on_the_crew_repository():
    """Criterion 1: findable later without scrollback."""
    out, issues, _ = record(Retro(summary="Delivered two stories."))
    [retro] = issues.created
    assert retro["repo"] == CREW and retro["labels"] == [RETRO_LABEL]
    assert retro["title"] == f"Retro: {SPRINT}"
    assert marker(SPRINT) in retro["body"] and "Delivered two stories." in retro["body"]
    assert "None proposed." in retro["body"]
    assert out.issue == retro["number"]


def test_each_defect_is_filed_where_the_thing_it_found_lives():
    """Criterion 2, and the Sponsor's rule: one observation can be one of each."""
    out, issues, _ = record(Retro(summary="s", defects=[process(), product()]))
    *defects, retro = issues.created
    assert [(d["repo"], d["labels"]) for d in defects] == [
        (CREW, [FINDING_LABEL]),
        (PRODUCT, [FINDING_LABEL]),
    ]
    assert out.filed == [(CREW, 201), (PRODUCT, 202)]
    # Filed before the retro, so the retro can name them, qualified where needed.
    assert "#201" in retro["body"] and "mqucifer/sprint-metrics#202" in retro["body"]


def test_a_defect_is_signed_and_says_what_to_change():
    _, issues, _ = record(Retro(summary="s", defects=[process()]))
    body = issues.created[0]["body"]
    assert "the retro of **S4**" in body
    assert "stories must name the module" in body
    assert "Scrum Master" in body


def test_labels_exist_in_every_repository_something_is_filed_in():
    _, issues, _ = record(Retro(summary="s", defects=[product()]))
    assert {(CREW, RETRO_LABEL), (PRODUCT, FINDING_LABEL)} <= issues.labels_ensured


def test_the_log_records_the_retro_and_each_defect():
    """Criterion 3: the close used to leave no event for a retro that succeeded."""
    _, _, seen = record(Retro(summary="s", defects=[process(), product()]))
    kinds = [e.kind for e in seen]
    assert kinds == [EventKind.DEFECT_FILED, EventKind.DEFECT_FILED, EventKind.RETRO_RECORDED]
    assert all(e.role == "Scrum Master" for e in seen)
    assert seen[-1].detail["filed"] == ["crew#201", "sprint-metrics#202"]


def test_a_defect_that_cannot_be_filed_is_still_in_the_retro():
    """Losing a defect's issue is recoverable; losing the defect is not."""
    retro = Retro(summary="s", defects=[process("Blocked"), product()])
    out, issues, _ = record(retro, FakeIssues(fail_on=["Blocked"]))
    assert out.filed == [(PRODUCT, 201)]
    assert [s for s, _ in out.failed] == ["Blocked"]
    assert "**Not filed**" in issues.created[-1]["body"]
    assert "Blocked" in issues.created[-1]["body"]


def test_an_existing_retro_is_found_by_its_sprint():
    issues = FakeIssues(
        existing=[
            {"number": 9, "labels": [RETRO_LABEL], "body": marker("S3")},
            {"number": 12, "labels": [RETRO_LABEL], "body": marker(SPRINT)},
        ]
    )
    assert existing_retro(issues, CREW, SPRINT) == 12
    assert existing_retro(issues, CREW, "S9") is None


# --- in the sprint close -----------------------------------------------------------


class CloseBoard:
    def cards(self):
        return []


@pytest.fixture
def ledger(tmp_path):
    return EscalationLedger(tmp_path / "escalations.jsonl")


def close(issues, ledger, *, crew_repo=CREW):
    return close_sprint(
        CloseBoard(),
        issues,
        EventSink(None),
        ledger,
        sprint=SPRINT,
        repo=PRODUCT,
        crew_repo=crew_repo,
        delivery_repos=[PRODUCT],
    )


def test_a_close_records_the_retro_it_wrote(monkeypatch, ledger):
    monkeypatch.setattr(
        close_mod, "write_retro", lambda *a, **k: Retro(summary="s", defects=[process()])
    )
    issues = FakeIssues()
    result = close(issues, ledger)
    assert result.retro_record is not None and result.retro_record.issue == 202
    assert not result.retro_already


def test_a_close_run_again_does_not_write_or_file_a_second_retro(monkeypatch, ledger):
    """One retro per sprint. Running the close twice used to be harmless because
    nothing was recorded; now it would file every defect twice."""

    def must_not_run(*a, **k):
        raise AssertionError("the retro was written again")

    monkeypatch.setattr(close_mod, "write_retro", must_not_run)
    issues = FakeIssues(existing=[{"number": 12, "labels": [RETRO_LABEL], "body": marker(SPRINT)}])
    result = close(issues, ledger)
    assert result.retro_already and result.retro_record.issue == 12
    assert issues.created == []


def test_without_a_crew_repository_the_retro_is_only_printed(monkeypatch, ledger):
    monkeypatch.setattr(close_mod, "write_retro", lambda *a, **k: Retro(summary="s"))
    issues = FakeIssues()
    result = close(issues, ledger, crew_repo=None)
    assert result.retro is not None and result.retro_record is None
    assert issues.created == []


def test_a_retro_still_rejects_asking_for_a_bigger_budget():
    """The schema grew two fields; the rule it already enforced still holds."""
    with pytest.raises(ValueError, match="budget"):
        ProcessDefect(
            subject="S4",
            problem="three escalations",
            change="increase the escalation budget",
            about="process",
        )


def test_the_retro_and_a_crew_defect_link_to_the_delivery_cards_they_name():
    """#118: the Sprint 4 retro cited "#31" and "#32" on the crew repository."""
    defect = ProcessDefect(
        subject="#31 escalated", problem="#31 needed a VERIFY escalation", change="x"
    )
    _, issues, _ = record(Retro(summary="#32 is done; #31 escalated.", defects=[defect]))
    *defects, retro = issues.created
    assert "mqucifer/sprint-metrics#31 needed a VERIFY escalation" in defects[0]["body"]
    assert "mqucifer/sprint-metrics#32 is done" in retro["body"]
