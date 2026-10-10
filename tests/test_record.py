"""The epic's record: identification, change control and status accounting (crew#583, ADR 0023).

Each test is one line of the plan's validation checklist, or a form the record is
read in. The record is a configuration baseline; these are its four functions.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from crew_org.events import EventSink
from crew_org.flows import record

OLD_FORM = (
    "## Refinement conclusion\n\n"
    "Ready to split: 2 decided, 1 open for the design note, 0 dismissed.\n\n"
    "| ID | Status | Context | Decision | Consequences | Source |\n"
    "|---|---|---|---|---|---|\n"
    "| R1 | accepted | History | Postgres \\| own schema | A client | Goal decision D1 |\n"
    "| R2 | accepted | Counting | Where it merges | Group by merge date | D3 |\n\n"
    "| ID | Open question | Impact | Settled by |\n|---|---|---|---|\n"
    "| Q1 | Which key? | The store | Design note |\n\n"
    "| ID | For infra: what the deployed runtime has to provide |\n|---|---|\n"
    "| I1 | A Postgres schema |\n\n"
    "| Note | Dismissed because |\n|---|---|\n"
    "| N4 | Wholly inside #186 |\n\n"
    "_Settled by the Product Owner from the panel's notes._\n"
    "<!-- crew:to-log R2 -->"
)


def decision(row_id="", *, by=record.PRODUCT_OWNER, replaces="", text="Flat keys"):
    return record.Decision(
        id=row_id,
        context="Field names",
        decision=text,
        consequences="Stories name them",
        source="The Goal",
        set_by=by,
        date="2026-10-10",
        replaces=replaces,
    )


def base() -> record.Record:
    return record.parse(OLD_FORM)


# --- reading both forms ---------------------------------------------------------------------


def test_the_old_form_is_read_as_it_is():
    found = base()
    assert [d.id for d in found.decisions] == ["R1", "R2"]
    first = found.decisions[0]
    assert first.decision == "Postgres | own schema"
    assert first.binding and first.set_by == record.PRODUCT_OWNER and first.date == ""
    assert found.open == (record.Question("Q1", "Which key?", "The store", record.ARCHITECT),)
    assert found.for_infra == (record.Item("I1", "A Postgres schema"),)
    assert found.dismissed == (record.Item("N4", "Wholly inside #186"),)
    assert found.to_log == ("R2",)


def test_the_new_form_reads_back_what_it_wrote():
    found = base().add(decision(replaces="Q1"))
    assert record.parse(record.render(found)) == found


def test_a_cells_pipe_survives_a_round_trip_unescaped_once():
    text = record.render(base())
    assert "Postgres \\| own schema" in text and "\\\\|" not in text
    assert record.parse(text).decisions[0].decision == "Postgres | own schema"


def test_the_panel_raising_nothing_is_a_record_too():
    nothing = record.Record(nothing_raised=True)
    text = record.render(nothing)
    assert text == f"{record.HEADER}\n\n{record.NOTHING_RAISED}"
    assert record.parse(text) == nothing


def test_the_bottom_line_counts_what_binds_what_was_replaced_and_who_settles_the_rest():
    after = base().add(decision(replaces="R1"))
    line = record.bottom_line(after)
    assert line.startswith("Ready to split: 2 decided, 1 replaced, 1 open for the Architect")


# --- identification -------------------------------------------------------------------------


def test_a_new_row_is_numbered_after_every_id_ever_used():
    settled = base().add(decision(replaces="Q1"))
    assert settled.decisions[-1].id == "R3"
    assert settled.next_id("R") == "R4"
    assert settled.next_id("Q") == "Q2", "Q1 left the table, and its number with it"


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"date": ""}, "no a date"),
        ({"source": " "}, "no a source"),
        ({"set_by": "Developer"}, "who sets no rows"),
        ({"status": "replaced by R9"}, "new and so binding"),
        ({"id": "Q7"}, "not a decision's ID"),
    ],
)
def test_a_new_row_without_its_attributes_is_refused(change, why):
    bad = replace(decision("R3"), **change)
    with pytest.raises(record.RecordRefused, match=why):
        record.check_change(base(), replace(base(), decisions=(*base().decisions, bad)))


def test_two_rows_with_one_id_are_refused():
    twin = replace(base(), decisions=(*base().decisions, decision("R2")))
    with pytest.raises(record.RecordRefused, match="share an ID"):
        record.check_change(base(), twin)


# --- change control -------------------------------------------------------------------------


def test_a_binding_row_is_never_deleted():
    with pytest.raises(record.RecordRefused, match="R1 was deleted"):
        record.check_change(base(), replace(base(), decisions=base().decisions[1:]))


def test_a_binding_row_is_never_edited_in_place():
    edited = replace(base().decisions[0], decision="SQLite")
    with pytest.raises(record.RecordRefused, match="R1 was changed in place"):
        record.check_change(base(), replace(base(), decisions=(edited, base().decisions[1])))


def test_its_author_may_replace_a_row_and_the_old_one_says_what_replaced_it():
    after = base().add(decision(replaces="R1"))
    record.check_change(base(), after)
    assert after.row("R1").status == "replaced by R3"
    assert after.binding_ids() == ["R2", "R3"]


def test_the_sponsor_may_replace_any_row():
    record.check_change(base(), base().add(decision(by=record.SPONSOR, replaces="R1")))


def test_another_role_may_not_replace_a_row_it_did_not_set():
    with pytest.raises(record.RecordRefused, match="only its author or the Sponsor"):
        record.check_change(base(), base().add(decision(by=record.ARCHITECT, replaces="R1")))


def test_a_replaced_row_cannot_be_replaced_again():
    once = base().add(decision(replaces="R1"))
    with pytest.raises(record.RecordRefused, match="no binding row"):
        record.check_change(once, once.add(decision(replaces="R1")))


def test_a_question_is_settled_by_a_row_from_whoever_it_names():
    settled = base().add(decision(by=record.ARCHITECT, replaces="Q1"))
    record.check_change(base(), settled)
    assert settled.open == ()
    with pytest.raises(record.RecordRefused, match="the Architect's to settle"):
        record.check_change(base(), base().add(decision(replaces="Q1")))
    record.check_change(base(), base().add(decision(by=record.SPONSOR, replaces="Q1")))


def test_a_question_does_not_just_disappear():
    with pytest.raises(record.RecordRefused, match="Q1 was removed"):
        record.check_change(base(), replace(base(), open=()))


def test_a_new_question_names_who_settles_it():
    asked = replace(base(), open=(*base().open, record.Question("Q2", "Port?", "Infra", "Nobody")))
    with pytest.raises(record.RecordRefused, match="not the Architect or the implementer"):
        record.check_change(base(), asked)


def test_the_implementers_questions_are_not_the_architects():
    left = replace(
        base(),
        open=(*base().open, record.Question("Q2", "Log format?", "None", record.IMPLEMENTER)),
    )
    ids, table = record.architect_questions(record.render(left))
    assert ids == ["Q1"] and "Q2" not in table


# --- what a story is shown ------------------------------------------------------------------


def test_a_story_citing_a_replaced_row_is_shown_what_replaced_it():
    after = base().add(decision(replaces="R1")).add(decision(replaces="R3", text="Nested"))
    shown = record.rows_for(record.render(after), ["R1"])
    assert [line.split("|")[1].strip() for line in shown.split("\n")[2:]] == ["R1", "R3", "R4"]
    assert "R2" not in shown


# --- the edit -------------------------------------------------------------------------------


class Issues:
    owner = "mqucifer"

    def __init__(self, body, *, moves=()):
        self.body = body
        self.comments: list[str] = []
        self.writes = 0
        # Bodies someone else writes between the edit's reads, in turn.
        self.moves = list(moves)

    def get(self, repo, number):
        if self.moves:
            self.body = self.moves.pop(0) or self.body
        return {"body": self.body}

    def edit_issue(self, repo, number, *, body):
        self.body = body
        self.writes += 1

    def comment(self, repo, number, body):
        self.comments.append(body)


EPIC = f"# Report points\n\nThe approved text.\n\n{OLD_FORM}\n"


def edit(issues, change, **kw):
    return record.edit(
        issues,
        EventSink(None),
        repo="sprint-metrics",
        epic=468,
        by=record.PRODUCT_OWNER,
        change=change,
        **kw,
    )


def test_an_edit_keeps_the_approved_text_and_rewrites_only_the_record():
    issues = Issues(EPIC)
    edit(issues, lambda r: r.add(decision(replaces="R1")), card=529)
    head, text = record.split(issues.body)
    assert head == "# Report points\n\nThe approved text."
    assert record.parse(text).row("R3").replaces == "R1"


def test_each_edit_posts_one_comment_naming_the_rows_who_and_the_card():
    issues = Issues(EPIC)
    edit(issues, lambda r: r.add(decision(replaces="R1")), card=529, why="Answering sm#529.")
    [note] = issues.comments
    assert note.startswith(record.CHANGE.format("R3"))
    assert "by the Product Owner, for mqucifer/sprint-metrics#529" in note
    assert "**R3** replaces R1: Field names: Flat keys" in note
    assert "Answering sm#529." in note


def test_an_edit_that_changes_nothing_writes_nothing():
    issues = Issues(EPIC)
    edit(issues, lambda r: r)
    assert issues.writes == 0 and issues.comments == []


def test_a_refused_edit_writes_nothing():
    issues = Issues(EPIC)
    with pytest.raises(record.RecordRefused):
        edit(issues, lambda r: replace(r, decisions=r.decisions[1:]))
    assert issues.writes == 0 and issues.comments == []


def test_a_body_changed_since_the_caller_read_it_is_not_overwritten():
    issues = Issues(EPIC)
    with pytest.raises(record.RecordChanged):
        edit(issues, lambda r: r.add(decision()), expected="# Report points\n\nOlder text.")
    assert issues.writes == 0


def test_a_body_changed_while_the_edit_was_made_is_not_overwritten():
    issues = Issues(EPIC, moves=[None, EPIC + "\nA Sponsor's edit.\n"])
    with pytest.raises(record.RecordChanged):
        edit(issues, lambda r: r.add(decision()))
    assert issues.writes == 0


def test_the_first_record_is_written_as_any_edit_is():
    issues = Issues("# Report points\n\nThe approved text.")
    edit(issues, lambda _: record.Record(decisions=(decision("R1"),)))
    assert record.parse(record.split(issues.body)[1]).binding_ids() == ["R1"]
    assert issues.comments[0].startswith(record.CHANGE.format("R1"))
