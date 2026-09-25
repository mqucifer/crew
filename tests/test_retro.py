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

    def open_issues(self, repo):
        return getattr(self, "open_", [])


def record(retro, issues=None, repos=(PRODUCT,), known=frozenset()):
    issues = issues or FakeIssues()
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    out = record_retro(
        issues,
        sink,
        retro,
        sprint=SPRINT,
        crew_repo=CREW,
        delivery_repos=list(repos),
        known=known,
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
    blocked = process("Blocked").model_copy(update={"title": "Blocked cards stall the sprint"})
    retro = Retro(summary="s", defects=[blocked, product()])
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


# --- the retro knows what is already filed (#124) ----------------------------------

from crew_org.flows.retro import known_issues  # noqa: E402


def open_issue(number, title, body="", labels=()):
    return {"number": number, "title": title, "body": body, "labels": [{"name": n} for n in labels]}


def test_the_retro_is_shown_the_crews_open_issues_as_crew_references():
    """Criterion 1. Written crew#N, so what it copies links correctly (#118)."""
    issues = FakeIssues()
    issues.open_ = [
        open_issue(
            116,
            "An approved PR behind main fails to merge",
            "<!-- x -->\nAs the **Sponsor**, I want...",
        ),
        open_issue(115, "Standup: Sprint 4", labels=["standup"]),
        open_issue(123, "Retro: Sprint 4", labels=["retro"]),
    ]
    shown, text = known_issues(issues, CREW)
    assert shown == {116}
    assert (
        text == "- crew#116 — An approved PR behind main fails to merge — As the Sponsor, I want..."
    )


def test_the_known_list_is_bounded_and_says_what_it_left_out(monkeypatch):
    import crew_org.flows.retro as retro_mod

    monkeypatch.setattr(retro_mod, "MAX_KNOWN_CHARS", 60)
    issues = FakeIssues()
    issues.open_ = [open_issue(n, "x" * 40) for n in (130, 129, 128)]
    shown, text = known_issues(issues, CREW)
    assert shown == {130} and "2 older issues omitted" in text


def explained(subject, by, **kw):
    return ProcessDefect(subject=subject, problem="p", change="c", explained_by=by, **kw)


def test_a_symptom_a_known_issue_explains_is_cited_not_filed():
    """Criterion 2. Sprint 4 filed #120, #121 and sprint-metrics#78 for causes
    already filed as #116 and #119."""
    out, issues, seen = record_known(
        Retro(summary="s", defects=[explained("#31 merge", 116)]), {116}
    )
    retro_issue = issues.created[-1]
    assert out.filed == [] and out.explained == [("#31 merge", 116)]
    assert "explained by #116, not filed again" in retro_issue["body"]
    assert seen[-1].detail["explained"] == ["crew#116"]


def test_a_known_issue_the_model_invented_cannot_suppress_a_finding():
    out, _, _ = record_known(Retro(summary="s", defects=[explained("real", 999)]), {116})
    assert out.explained == [] and len(out.filed) == 1


def test_a_new_finding_says_what_it_was_checked_against():
    """Criterion 3. Only issues it was shown are named."""
    defect = ProcessDefect(subject="new", problem="p", change="c", checked_against=[116, 119, 999])
    _, issues, _ = record_known(Retro(summary="s", defects=[defect]), {116, 119})
    assert "**Checked against:** #116, #119\n" in issues.created[0]["body"] + "\n"
    assert "999" not in issues.created[0]["body"]


def test_crew_references_in_the_retro_stay_on_the_crew_repository():
    """#126 wrote a crew-filed defect and the standup as bare #N, and the retro
    body's link rewrite then pointed them at sprint-metrics."""
    out, issues, _ = record_known(Retro(summary="s", defects=[process()]), set(), standup=115)
    body = issues.created[-1]["body"]
    assert "It read the sprint's standups, #115." in body
    assert "- #201 — Story sizing (process)" in body
    assert "sprint-metrics#201" not in body and "sprint-metrics#115" not in body


def record_known(retro, known, standup=None):
    issues = FakeIssues()
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    out = record_retro(
        issues,
        sink,
        retro,
        sprint=SPRINT,
        crew_repo=CREW,
        delivery_repos=[PRODUCT],
        known=known,
        standup=standup,
    )
    return out, issues, seen


def test_the_opening_line_keeps_its_references():
    from crew_org.flows.retro import _opening

    assert _opening("## Why\n") == "Why"
    assert _opening("> what #56 set out to record") == "what #56 set out to record"


def test_references_in_a_known_issue_are_shown_as_crew_references():
    """What the retro is shown, it copies. A bare "#56" copied into the retro
    would be linked to the delivery repository."""
    issues = FakeIssues()
    issues.open_ = [open_issue(117, "Bridge", "so that what #56 set out to record is there")]
    assert "what crew#56 set out" in known_issues(issues, CREW)[1]
