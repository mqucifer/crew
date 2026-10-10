"""The epic's record: what is decided for an epic, kept in its body (crew#583, ADR 0023).

The epic's body keeps the text the Sponsor approved untouched, then one section
the code owns, under `## Refinement conclusion`. Its tables are the record:
decisions (R rows), questions still open (Q), what infra has to provide (I) and
the panel's notes dismissed (N). Every refinement and design step reads it whole;
the steps that build and judge a story read the rows the story cites. Comments
are the audit trail of each change, never what a step reads to learn a decision.

It is a configuration baseline under configuration management (ISO 10007,
IEEE 828, CMMI's Configuration Management process area), applied to decisions,
with its four functions as the checks here:

- **Identification:** every row has an ID and its attributes (status, set by,
  date, replaces, source), after ISO/IEC/IEEE 29148's requirement attributes. A
  new row without them is refused.
- **Change control:** a binding row is never deleted or edited in place. A later
  row replaces it, naming it, set by its authority: the Sponsor any row, every
  other author only the rows it set. A question is settled the same way, by a
  row that replaces it, set by whoever the question names or the Sponsor.
- **Status accounting:** each row says whether it binds or what replaced it, and
  each edit posts one comment saying which rows changed, by whom, for which card.
- **Audit** is the gates judging a story against the rows it cites, and the
  context review checking the record against what was decided.

Conclusions written before this (six columns, "accepted", "Design note") are read
as they are; nothing is re-settled. The next edit writes them in this form.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from crew_org.events import EventSink
from crew_org.flows import artifacts

HEADER = "## Refinement conclusion"
NOTHING_RAISED = "Ready to split: the panel raised nothing."
# On every comment that records a change, naming the rows it changed.
CHANGE = "<!-- crew:record-change {} -->"
TO_LOG = "<!-- crew:to-log {} -->"
_TO_LOG = re.compile(r"<!-- crew:to-log (R\d+(?:,R\d+)*) -->")

BINDING = "binding"
# What the settle wrote before the record had a status of its own.
_LEGACY_BINDING = "accepted"
_REPLACED = re.compile(r"^replaced by (R\d+)$")

SPONSOR = "Sponsor"
PRODUCT_OWNER = "Product Owner"
ARCHITECT = "Architect"
BUSINESS_ANALYST = "Business Analyst"
AUTHORS = (SPONSOR, PRODUCT_OWNER, ARCHITECT, BUSINESS_ANALYST)
# Who settles an open question: the Architect, or whoever builds it, whose
# choice no gate checks (Shape Up's latitude for the people doing the work).
IMPLEMENTER = "Implementer"
SETTLERS = (ARCHITECT, IMPLEMENTER)
OWN_CALL = "Product Owner's call"
NONE = "—"

_DECISIONS = (
    "| ID | Status | Context | Decision | Consequences | Set by | Date | Replaces | Source |",
    "|---|---|---|---|---|---|---|---|---|",
)
_OPEN = ("| ID | Open question | Impact | Settled by |", "|---|---|---|---|")
_INFRA = ("| ID | For infra: what the deployed runtime has to provide |", "|---|---|")
_DISMISSED = ("| Note | Dismissed because |", "|---|---|")
FOOTER = (
    "_Settled by the Product Owner from the panel's notes, which stay as the epic's panel "
    "comment. A binding row holds until a later row replaces it._"
)

_CELLS = re.compile(r"(?<!\\)\|")
_ID = re.compile(r"^([RQINC])(\d+)$")
_TESTS = (
    "| ID | Test | What it asserts after | Declared by | Status |",
    "|---|---|---|---|---|",
)
# A C row's status: a story changes the test, or the split dropped it, with why.
DECLARED = "declared"


class RecordRefused(ValueError):
    """An edit that breaks the record's change control. Nothing was written."""


class RecordChanged(RuntimeError):
    """The epic's body changed while the edit was being made. Nothing was written."""


@dataclass(frozen=True)
class Decision:
    id: str
    context: str
    decision: str
    consequences: str
    source: str
    set_by: str
    date: str
    replaces: str = ""
    status: str = BINDING

    @property
    def binding(self) -> bool:
        return self.status == BINDING

    @property
    def own_call(self) -> bool:
        return self.source.startswith(OWN_CALL)


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    impact: str
    settled_by: str = ARCHITECT


@dataclass(frozen=True)
class TestRow:
    """A merged test the epic changes (step A4): the split declares it, or a story problem.

    The baseline for the tests as for the decisions: a re-split carries each into a
    story or drops it with why, and a row is never deleted.
    """

    id: str
    test: str
    asserts: str
    declared_by: str
    status: str = DECLARED

    @property
    def declared(self) -> bool:
        return self.status == DECLARED


@dataclass(frozen=True)
class Item:
    """A line of the infra or the dismissed table: its ID and its one cell."""

    id: str
    text: str


@dataclass(frozen=True)
class Record:
    decisions: tuple[Decision, ...] = ()
    open: tuple[Question, ...] = ()
    for_infra: tuple[Item, ...] = ()
    dismissed: tuple[Item, ...] = ()
    to_log: tuple[str, ...] = ()
    nothing_raised: bool = False
    tests: tuple[TestRow, ...] = ()

    def row(self, row_id: str) -> Decision | None:
        return next((d for d in self.decisions if d.id == row_id), None)

    def binding(self) -> list[Decision]:
        return [d for d in self.decisions if d.binding]

    def binding_ids(self) -> list[str]:
        return [d.id for d in self.binding()]

    def for_architect(self) -> list[Question]:
        return [q for q in self.open if q.settled_by == ARCHITECT]

    def declared_tests(self) -> list[str]:
        """The merged tests a story of this epic still has to change, as `path::test`."""
        return [t.test for t in self.tests if t.declared]

    def declare(self, test: str, asserts: str, declared_by: str) -> Record:
        """This record with a merged test the epic changes; one row per test."""
        if any(t.test == test for t in self.tests):
            return self
        row = TestRow(self.next_id("C"), test, asserts, declared_by)
        return replace(self, tests=(*self.tests, row), nothing_raised=False)

    def drop(self, test: str, why: str) -> Record:
        """This record with a declared test dropped, saying why. Never deleted."""
        return replace(
            self,
            tests=tuple(
                replace(t, status=f"dropped: {why}") if t.test == test and t.declared else t
                for t in self.tests
            ),
        )

    def next_id(self, kind: str) -> str:
        """The next free ID of a kind: numbers are never reused, even once replaced."""
        ids = [d.id for d in self.decisions] + [q.id for q in self.open]
        ids += [t.id for t in self.tests]
        # A settled question leaves the table; the row that replaced it still names it.
        ids += [d.replaces for d in self.decisions if d.replaces]
        used = [int(m.group(2)) for i in ids if (m := _ID.match(i)) and m.group(1) == kind]
        return f"{kind}{max(used, default=0) + 1}"

    def add(self, row: Decision) -> Record:
        """This record with `row` added, numbered next, and what it replaces marked so.

        Nothing is checked here: `check_change` judges the whole edit.
        """
        row = replace(row, id=row.id or self.next_id("R"))
        decisions = tuple(
            replace(d, status=f"replaced by {row.id}") if d.id == row.replaces else d
            for d in self.decisions
        )
        return replace(
            self,
            decisions=(*decisions, row),
            open=tuple(q for q in self.open if q.id != row.replaces),
            nothing_raised=False,
        )


_CHANGE = re.compile(r"<!-- crew:record-change [^ ]*(?: (\w+))? -->")


def has_change(bodies: list[str], kind: str) -> bool:
    """Whether any of these comments records a change of this kind (`answer`, `sponsor`)."""
    return any(m.group(1) == kind for b in bodies for m in _CHANGE.finditer(b))


def has_record(body: str) -> bool:
    return HEADER in body


def split(body: str) -> tuple[str, str]:
    """The epic's own text, and its record (empty if it has none)."""
    head, found, tail = body.partition(HEADER)
    if not found:
        return body, ""
    return head.rstrip(), f"{HEADER}{tail}".strip()


def _cells(line: str) -> list[str]:
    return [c.strip().replace("\\|", "|") for c in _CELLS.split(line.strip())[1:-1]]


def _blank(cell: str) -> str:
    return "" if cell in (NONE, "-") else cell


def _settler(cell: str) -> str:
    """Who settles a question. "Design note", the old form, meant the Architect."""
    text = cell.removeprefix("Settled by:").strip()
    return IMPLEMENTER if text.lower().startswith("implement") else ARCHITECT


def parse(text: str) -> Record:
    """The record in an epic's body, in either form."""
    decisions: list[Decision] = []
    questions: list[Question] = []
    infra: list[Item] = []
    dismissed: list[Item] = []
    tests: list[TestRow] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        found = _ID.match(cells[0]) if cells else None
        if not found:
            continue
        kind = found.group(1)
        if kind == "R" and len(cells) == 9:
            row_id, status, context, decision, consequences, set_by, date, replaces, source = cells
            decisions.append(
                Decision(
                    row_id,
                    context,
                    decision,
                    consequences,
                    source,
                    set_by,
                    _blank(date),
                    _blank(replaces),
                    BINDING if status == _LEGACY_BINDING else status,
                )
            )
        elif kind == "R" and len(cells) == 6:
            # The old form: the settle wrote every row, so the Product Owner set it.
            row_id, _, context, decision, consequences, source = cells
            decisions.append(
                Decision(row_id, context, decision, consequences, source, PRODUCT_OWNER, "")
            )
        elif kind == "Q" and len(cells) == 4:
            questions.append(Question(cells[0], cells[1], cells[2], _settler(cells[3])))
        elif kind == "I" and len(cells) == 2:
            infra.append(Item(cells[0], cells[1]))
        elif kind == "N" and len(cells) == 2:
            dismissed.append(Item(cells[0], cells[1]))
        elif kind == "C" and len(cells) == 5:
            test = cells[1].strip("`")
            tests.append(TestRow(cells[0], test, cells[2], cells[3], cells[4]))
    marked = _TO_LOG.search(text)
    return Record(
        tuple(decisions),
        tuple(questions),
        tuple(infra),
        tuple(dismissed),
        tuple(marked.group(1).split(",")) if marked else (),
        NOTHING_RAISED in text,
        tuple(tests),
    )


def _cell(text: str) -> str:
    """One table cell on one line. Links and references are the writer's to make."""
    return _CELLS.sub(r"\\|", " ".join(text.split()))


def _decision_line(d: Decision) -> str:
    return (
        f"| {d.id} | {d.status} | {_cell(d.context)} | {_cell(d.decision)} | "
        f"{_cell(d.consequences)} | {d.set_by} | {d.date or NONE} | {d.replaces or NONE} | "
        f"{_cell(d.source)} |"
    )


def bottom_line(record: Record) -> str:
    binding = record.binding()
    own = sum(1 for d in binding if d.own_call)
    replaced = len(record.decisions) - len(binding)
    return (
        f"Ready to split: {len(binding)} decided"
        + (f" ({own} the Product Owner's call)" if own else "")
        + (f", {replaced} replaced" if replaced else "")
        + f", {len(record.for_architect())} open for the Architect, "
        f"{len(record.open) - len(record.for_architect())} left to the implementer, "
        f"{len(record.for_infra)} for infra, {len(record.dismissed)} dismissed"
        + (f", {len(record.declared_tests())} merged tests it changes." if record.tests else ".")
    )


def render(record: Record) -> str:
    """The record as the epic's body carries it."""
    if record.nothing_raised:
        return f"{HEADER}\n\n{NOTHING_RAISED}"
    lines = [HEADER, "", bottom_line(record), ""]
    if record.decisions:
        lines += [*_DECISIONS, *(_decision_line(d) for d in record.decisions), ""]
    if record.open:
        lines += [
            *_OPEN,
            *(
                f"| {q.id} | {_cell(q.question)} | {_cell(q.impact)} | {q.settled_by} |"
                for q in record.open
            ),
            "",
        ]
    if record.tests:
        lines += [
            "Merged tests this epic changes:",
            "",
            *_TESTS,
            *(
                f"| {t.id} | `{t.test}` | {_cell(t.asserts)} | {_cell(t.declared_by)} | "
                f"{_cell(t.status)} |"
                for t in record.tests
            ),
            "",
        ]
    if record.for_infra:
        lines += [*_INFRA, *(f"| {i.id} | {_cell(i.text)} |" for i in record.for_infra), ""]
    if record.dismissed:
        lines += [*_DISMISSED, *(f"| {n.id} | {_cell(n.text)} |" for n in record.dismissed), ""]
    lines.append(FOOTER)
    if record.to_log:
        lines.append(TO_LOG.format(",".join(record.to_log)))
    return "\n".join(lines)


def rows_for(text: str, ids: list[str]) -> str:
    """The decisions table with just these rows, and any row that has replaced one.

    A story cites the rows it follows when it is split. A row replaced since then
    is shown with the row that replaced it, so the story is built to what holds now.
    A row the record doesn't have is left out; none left gives nothing.
    """
    record = parse(text)
    wanted = set(ids)
    grew = True
    while grew:
        more = {d.id for d in record.decisions if d.replaces in wanted} - wanted
        grew = bool(more)
        wanted |= more
    picked = [d for d in record.decisions if d.id in wanted]
    return "\n".join([*_DECISIONS, *(_decision_line(d) for d in picked)]) if picked else ""


def architect_questions(text: str) -> tuple[list[str], str]:
    """The questions the record leaves for the Architect: their IDs, and their table.

    A question left to the implementer is not the Architect's: no gate checks it.
    """
    asked = parse(text).for_architect()
    if not asked:
        return [], ""
    table = [
        *_OPEN,
        *(f"| {q.id} | {_cell(q.question)} | {_cell(q.impact)} | {q.settled_by} |" for q in asked),
    ]
    return [q.id for q in asked], "\n".join(table)


def check_change(before: Record, after: Record) -> None:
    """Refuse an edit that breaks change control, naming each problem."""
    problems: list[str] = []
    now = {d.id: d for d in after.decisions}
    if len(now) != len(after.decisions):
        problems.append("two rows share an ID")
    for old in before.decisions:
        new = now.get(old.id)
        if new is None:
            problems.append(f"{old.id} was deleted; a row is only ever replaced")
            continue
        if new == old:
            continue
        marked = _REPLACED.match(new.status)
        by = now.get(marked.group(1)) if marked else None
        if not (
            old.binding and new == replace(old, status=new.status) and by and by.replaces == old.id
        ):
            problems.append(f"{old.id} was changed in place; a later row replaces it instead")
    tests_now = {t.id: t for t in after.tests}
    for old_test in before.tests:
        kept = tests_now.get(old_test.id)
        if kept is None:
            problems.append(f"{old_test.id} was deleted; a test row is only ever dropped, with why")
        elif kept.test != old_test.test:
            problems.append(f"{old_test.id} names another test now; add a row instead")
    before_ids = {d.id for d in before.decisions}
    asked = {q.id: q for q in before.open}
    still = {q.id for q in after.open}
    added = [d for d in after.decisions if d.id not in before_ids]
    for d in added:
        problems += _identified(d)
        if not d.replaces:
            continue
        if d.replaces.startswith("R"):
            target = before.row(d.replaces)
            if target is None or not target.binding:
                problems.append(f"{d.id} replaces {d.replaces}, which is no binding row")
            elif d.set_by not in (SPONSOR, target.set_by):
                problems.append(
                    f"{d.id}, set by the {d.set_by}, replaces {target.id}, set by the "
                    f"{target.set_by}: only its author or the Sponsor may"
                )
            elif now[target.id].status != f"replaced by {d.id}":
                problems.append(f"{target.id} isn't marked as replaced by {d.id}")
        else:
            question = asked.get(d.replaces)
            if question is None:
                problems.append(f"{d.id} replaces {d.replaces}, which is no open question")
            elif d.set_by not in (SPONSOR, question.settled_by):
                problems.append(
                    f"{d.id}, set by the {d.set_by}, settles {question.id}, which is the "
                    f"{question.settled_by}'s to settle"
                )
            elif question.id in still:
                problems.append(f"{question.id} is still open after {d.id} settled it")
    settled = {d.replaces for d in added}
    problems += [
        f"{q} was removed; a question is settled by a row that replaces it"
        for q in asked
        if q not in still and q not in settled
    ]
    problems += [
        f"{q.id} names {q.settled_by!r} to settle it, not the Architect or the implementer"
        for q in after.open
        if q.id not in asked and q.settled_by not in SETTLERS
    ]
    if problems:
        raise RecordRefused("; ".join(problems))


def _identified(d: Decision) -> list[str]:
    """What a new row is missing of its identification."""
    missing = [
        name
        for name, value in (
            ("an ID", d.id),
            ("a context", d.context),
            ("a decision", d.decision),
            ("a source", d.source),
            ("a date", d.date),
        )
        if not value.strip()
    ]
    found = [f"{d.id or 'a new row'} has no {', '.join(missing)}"] if missing else []
    if not _ID.match(d.id) or not d.id.startswith("R"):
        found.append(f"{d.id!r} is not a decision's ID")
    if d.set_by not in AUTHORS:
        found.append(f"{d.id} is set by {d.set_by!r}, who sets no rows")
    if d.status != BINDING:
        found.append(f"{d.id} is new and so binding, not {d.status!r}")
    return found


def change_note(
    before: Record, after: Record, *, by: str, ref: str = "", why: str = "", kind: str = ""
) -> str:
    """The one comment an edit posts: which rows changed, by whom, for which card."""
    before_ids = {d.id for d in before.decisions}
    added = [d for d in after.decisions if d.id not in before_ids]
    opened = [q for q in after.open if q.id not in {o.id for o in before.open}]
    lines = []
    for d in added:
        what = (
            f"replaces {d.replaces}"
            if d.replaces.startswith("R")
            else f"settles {d.replaces}"
            if d.replaces
            else "added"
        )
        lines.append(f"- **{d.id}** {what}: {d.context}: {d.decision}")
    lines += [f"- **{q.id}** opened, for the {q.settled_by}: {q.question}" for q in opened]
    old_tests = {t.id: t for t in before.tests}
    new_tests = [t for t in after.tests if t.id not in old_tests]
    dropped = [t for t in after.tests if t.id in old_tests and old_tests[t.id] != t]
    lines += [f"- **{t.id}** `{t.test}`, declared by {t.declared_by}" for t in new_tests]
    lines += [f"- **{t.id}** `{t.test}` {t.status}" for t in dropped]
    if not lines and after.nothing_raised:
        lines.append("- Nothing to record: the panel raised nothing.")
    changed = [d.id for d in added] + [q.id for q in opened]
    changed += [t.id for t in new_tests + dropped]
    return (
        f"{CHANGE.format(','.join(changed) + (f' {kind}' if kind else ''))}\n"
        f"**The epic's record changed**, by the {by}"
        + (f", for {ref}" if ref else "")
        + ".\n\n"
        + "\n".join(lines)
        + (f"\n\n{why}" if why else "")
    )


def edit(
    issues: Any,
    sink: EventSink,
    *,
    repo: str,
    epic: int,
    by: str,
    change: Callable[[Record], Record],
    expected: str | None = None,
    card: int | None = None,
    why: str = "",
    kind: str = "",
) -> Record:
    """Change the epic's record, check the change, write it, and say what changed.

    `expected` is the body as the caller read it before deciding the change (the
    model may have thought for minutes): if the body differs now, nothing is
    written. The body is read once more just before writing, for the same reason.
    An edit that changes nothing writes nothing. `kind` tags the change comment, so
    a step can find its own changes without reading what they say.
    """
    body = issues.get(repo, epic).get("body") or ""
    if expected is not None and body != expected:
        raise RecordChanged(f"the epic's body changed while its record was decided: {repo}#{epic}")
    head, text = split(body)
    before = parse(text) if text else Record()
    after = change(before)
    check_change(before, after)
    if after == before:
        return after
    if (issues.get(repo, epic).get("body") or "") != body:
        raise RecordChanged(f"the epic's body changed while its record was edited: {repo}#{epic}")
    issues.edit_issue(repo, epic, body=f"{head.rstrip()}\n\n{render(after)}\n")
    ref = f"{issues.owner}/{repo}#{card}" if card is not None else ""
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=epic,
        body=change_note(before, after, by=by, ref=ref, why=why, kind=kind),
        by=by,
    )
    return after
