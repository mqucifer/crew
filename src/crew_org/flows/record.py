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

**The Goal's record** (crew#858) is the same baseline one level up: one marked
comment on the Goal, since the Goal's body is the Sponsor's. It is written once by
the Goal's look and settle, and each of its rows says which epics it binds: all of
them, or the ones it names. An epic sees the rows that bind it (`Record.for_epic`).
An epic split before it keeps the record in its own body until it closes: `locate`
finds whichever the epic has, so every step reads and edits the one that holds.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from crew_org.events import EventSink
from crew_org.flows import artifacts
from crew_org.rulebook import Refusal, refuse, says

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
# What the reader sees, set in the Goal's look and carried into the stories (crew#858).
UX_DESIGNER = "UX Designer"
AUTHORS = (SPONSOR, PRODUCT_OWNER, ARCHITECT, BUSINESS_ANALYST, UX_DESIGNER)
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
# Each row names who set it. "Settled by the Product Owner" stood under records the
# Product Owner had only turned into open questions (Sponsor, 2026-10-11).
FOOTER = (
    "_From the panel's notes, which stay as the epic's panel comment. Each row names who "
    "set it; a binding row holds until a later row replaces it._"
)

_CELLS = re.compile(r"(?<!\\)\|")
_ID = re.compile(r"^([RQINC])(\d+)$")
_TESTS = (
    "| ID | Test | What it asserts after | Declared by | Status |",
    "|---|---|---|---|---|",
)
# A C row's status: a story changes the test, or the split dropped it, with why.
DECLARED = "declared"

# The Goal's record: a comment on the Goal, its tables with one more column.
GOAL_RECORD_MARKER = "<!-- crew:goal-record -->"
GOAL_HEADER = "## The Goal's record"
# A row that binds every epic of the Goal, those added later too.
ALL = "all"
# The epics the Goal's look has read, so a later one knows what it still has to read.
_LOOKED = "<!-- crew:looked-at {} -->"
_LOOKED_AT = re.compile(r"<!-- crew:looked-at ([^ ]*) -->")
_GOAL_DECISIONS = (
    "| ID | Status | Context | Decision | Consequences | Set by | Date | Replaces | Source "
    "| Binds |",
    "|---|---|---|---|---|---|---|---|---|---|",
)
_GOAL_OPEN = ("| ID | Open question | Impact | Settled by | Binds |", "|---|---|---|---|---|")
_GOAL_TESTS = (
    "| ID | Test | What it asserts after | Declared by | Status | Binds |",
    "|---|---|---|---|---|---|",
)
GOAL_FOOTER = (
    "_From the Goal's look by the Architect and the UX Designer, settled by the Product "
    "Owner. Each row names who set it and which epics it binds; a binding row holds until "
    "a later row replaces it._"
)


class RecordRefused(Refusal):
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
    # In the Goal's record, the epics it binds: ALL, or their refs. Empty in an epic's own.
    binds: tuple[str, ...] = ()

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
    binds: tuple[str, ...] = ()


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
    binds: tuple[str, ...] = ()

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
    # The Goal's record, kept in a comment on the Goal, rather than an epic's own.
    on_goal: bool = False
    # The epics the Goal's look has read (the Goal's record only).
    looked_at: tuple[str, ...] = ()

    def for_epic(self, ref: str) -> Record:
        """The Goal's record as one epic sees it: the rows that bind it, or every epic.

        An epic's own record is all its own, so it comes back whole.
        """
        if not self.on_goal:
            return self

        def binds(row: Decision | Question | TestRow) -> bool:
            return ALL in row.binds or ref in row.binds

        return replace(
            self,
            decisions=tuple(d for d in self.decisions if binds(d)),
            open=tuple(q for q in self.open if binds(q)),
            tests=tuple(t for t in self.tests if binds(t)),
        )

    def bound(self, ref: str) -> Record:
        """This record with every row that binds nothing yet bound to `ref`.

        A step that adds a row for one epic adds it to the Goal's record, where it binds
        that epic: a Sponsor's reply, an answer to a story problem, a test a story
        changes. A row it names epics for keeps them.
        """
        if not self.on_goal:
            return self
        return replace(
            self,
            decisions=tuple(d if d.binds else replace(d, binds=(ref,)) for d in self.decisions),
            open=tuple(q if q.binds else replace(q, binds=(ref,)) for q in self.open),
            tests=tuple(t if t.binds else replace(t, binds=(ref,)) for t in self.tests),
        )

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


@dataclass(frozen=True)
class Place:
    """Where the record a card reads is kept: its own body, or its Goal's record comment."""

    repo: str
    # The card the record is read or edited for: an epic, or the Goal itself.
    number: int
    goal: int | None = None
    # The Goal's record comment, when the record is kept there.
    comment: int | None = None

    @property
    def on_goal(self) -> bool:
        return self.comment is not None


def goal_of(issue: dict[str, Any]) -> int | None:
    """The Goal above an epic, from its parent link, or None."""
    found = re.search(r"/issues/(\d+)$", issue.get("parent_issue_url") or "")
    return int(found.group(1)) if found else None


def goal_record_comment(issues: Any, repo: str, goal: int) -> dict[str, Any] | None:
    """The Goal's record comment, or None before the Goal's look has settled."""
    found = [c for c in issues.comments(repo, goal) if GOAL_RECORD_MARKER in (c.get("body") or "")]
    return found[-1] if found else None


def comment_text(body: str) -> str:
    return body.replace(GOAL_RECORD_MARKER, "", 1).strip()


def _locate(issues: Any, repo: str, number: int) -> tuple[Place, str, str]:
    """Where `number`'s record is kept, what holds it as read now, and the record's text.

    An epic's own body first: one split before the Goal had a record keeps its own
    until it closes (crew#858). Then its Goal's record comment, or the card's own
    comment when the card is the Goal. Nowhere yet: the card's body, empty.
    """
    issue = issues.get(repo, number)
    body = issue.get("body") or ""
    _, text = split(body)
    if text:
        return Place(repo, number), body, text
    goal = goal_of(issue)
    found = goal_record_comment(issues, repo, goal if goal is not None else number)
    if found is not None:
        held = found.get("body") or ""
        at = goal if goal is not None else number
        return Place(repo, number, at, int(found["id"])), held, comment_text(held)
    return Place(repo, number), body, ""


def locate(issues: Any, repo: str, number: int) -> Place:
    """Where the record `number` reads is kept (see `_locate`)."""
    return _locate(issues, repo, number)[0]


def text_for(issues: Any, repo: str, number: int) -> str:
    """The record as `number` sees it, rendered: an epic's own, or the rows of its Goal's
    record that bind it. Empty when it has none."""
    place, _, text = _locate(issues, repo, number)
    if not text:
        return ""
    if not place.on_goal or place.goal == number:
        return text
    return render(parse(text).for_epic(f"{issues.owner}/{repo}#{number}"))


def record_for(issues: Any, repo: str, number: int) -> Record | None:
    """The record `number` sees (`text_for`), parsed; None when it has none."""
    text = text_for(issues, repo, number)
    return parse(text) if text else None


def _cells(line: str) -> list[str]:
    return [c.strip().replace("\\|", "|") for c in _CELLS.split(line.strip())[1:-1]]


def _blank(cell: str) -> str:
    return "" if cell in (NONE, "-") else cell


def _settler(cell: str) -> str:
    """Who settles a question. "Design note", the old form, meant the Architect."""
    text = cell.removeprefix("Settled by:").strip()
    return IMPLEMENTER if text.lower().startswith("implement") else ARCHITECT


def _binds(cell: str) -> tuple[str, ...]:
    return tuple(r.strip() for r in cell.split(",") if r.strip())


def parse(text: str) -> Record:
    """The record in an epic's body, in either form, or the Goal's record."""
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
        if kind == "R" and len(cells) == 10:
            row_id, status, context, decision, consequences, set_by, date, replaces, source = cells[
                :9
            ]
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
                    status,
                    _binds(cells[9]),
                )
            )
        elif kind == "R" and len(cells) == 9:
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
        elif kind == "Q" and len(cells) == 5:
            questions.append(
                Question(cells[0], cells[1], cells[2], _settler(cells[3]), _binds(cells[4]))
            )
        elif kind == "Q" and len(cells) == 4:
            questions.append(Question(cells[0], cells[1], cells[2], _settler(cells[3])))
        elif kind == "I" and len(cells) == 2:
            infra.append(Item(cells[0], cells[1]))
        elif kind == "N" and len(cells) == 2:
            dismissed.append(Item(cells[0], cells[1]))
        elif kind == "C" and len(cells) == 6:
            test = cells[1].strip("`")
            tests.append(TestRow(cells[0], test, cells[2], cells[3], cells[4], _binds(cells[5])))
        elif kind == "C" and len(cells) == 5:
            test = cells[1].strip("`")
            tests.append(TestRow(cells[0], test, cells[2], cells[3], cells[4]))
    marked = _TO_LOG.search(text)
    looked = _LOOKED_AT.search(text)
    return Record(
        tuple(decisions),
        tuple(questions),
        tuple(infra),
        tuple(dismissed),
        tuple(marked.group(1).split(",")) if marked else (),
        NOTHING_RAISED in text,
        tuple(tests),
        on_goal=GOAL_HEADER in text,
        looked_at=_binds(looked.group(1)) if looked else (),
    )


def _cell(text: str) -> str:
    """One table cell on one line. Links and references are the writer's to make."""
    return _CELLS.sub(r"\\|", " ".join(text.split()))


def _decision_line(d: Decision, *, on_goal: bool = False) -> str:
    return (
        f"| {d.id} | {d.status} | {_cell(d.context)} | {_cell(d.decision)} | "
        f"{_cell(d.consequences)} | {d.set_by} | {d.date or NONE} | {d.replaces or NONE} | "
        f"{_cell(d.source)} |" + (f" {', '.join(d.binds)} |" if on_goal else "")
    )


def bottom_line(record: Record) -> str:
    binding = record.binding()
    own = sum(1 for d in binding if d.own_call)
    replaced = len(record.decisions) - len(binding)
    # The split refuses an epic with a question open for the Architect (ADR 0018), so
    # such a record isn't ready to split, whatever else it holds.
    ready = "Waiting for the Architect" if record.for_architect() else "Ready to split"
    return (
        f"{ready}: {len(binding)} decided"
        + (f" ({own} the Product Owner's call)" if own else "")
        + (f", {replaced} replaced" if replaced else "")
        + f", {len(record.for_architect())} open for the Architect, "
        f"{len(record.open) - len(record.for_architect())} left to the implementer, "
        f"{len(record.for_infra)} for infra, {len(record.dismissed)} dismissed"
        + (f", {len(record.declared_tests())} merged tests it changes." if record.tests else ".")
    )


def render(record: Record) -> str:
    """The record as the epic's body carries it, or the Goal's record as its comment does."""
    if record.on_goal:
        return _render_goal(record)
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


def _render_goal(record: Record) -> str:
    lines = [GOAL_HEADER, "", bottom_line(record), ""]
    if record.looked_at:
        lines += [f"Looked at: {', '.join(record.looked_at)}.", ""]
    if record.decisions:
        lines += [
            *_GOAL_DECISIONS,
            *(_decision_line(d, on_goal=True) for d in record.decisions),
            "",
        ]
    if record.open:
        lines += [
            *_GOAL_OPEN,
            *(
                f"| {q.id} | {_cell(q.question)} | {_cell(q.impact)} | {q.settled_by} | "
                f"{', '.join(q.binds)} |"
                for q in record.open
            ),
            "",
        ]
    if record.tests:
        lines += [
            "Merged tests the Goal's epics change:",
            "",
            *_GOAL_TESTS,
            *(
                f"| {t.id} | `{t.test}` | {_cell(t.asserts)} | {_cell(t.declared_by)} | "
                f"{_cell(t.status)} | {', '.join(t.binds)} |"
                for t in record.tests
            ),
            "",
        ]
    if record.for_infra:
        lines += [*_INFRA, *(f"| {i.id} | {_cell(i.text)} |" for i in record.for_infra), ""]
    if record.dismissed:
        lines += [*_DISMISSED, *(f"| {n.id} | {_cell(n.text)} |" for n in record.dismissed), ""]
    lines.append(GOAL_FOOTER)
    if record.looked_at:
        lines.append(_LOOKED.format(",".join(record.looked_at)))
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


def check_change(before: Record, after: Record) -> None:
    """Refuse an edit that breaks change control, naming each problem."""
    problems: list[str] = []
    now = {d.id: d for d in after.decisions}
    if len(now) != len(after.decisions):
        problems.append(says("check.record.check_change", "shared_id"))
    for old in before.decisions:
        new = now.get(old.id)
        if new is None:
            problems.append(says("check.record.check_change", "deleted", row=old.id))
            continue
        if new == old:
            continue
        marked = _REPLACED.match(new.status)
        by = now.get(marked.group(1)) if marked else None
        if not (
            old.binding and new == replace(old, status=new.status) and by and by.replaces == old.id
        ):
            problems.append(says("check.record.check_change", "in_place", row=old.id))
    tests_now = {t.id: t for t in after.tests}
    for old_test in before.tests:
        kept = tests_now.get(old_test.id)
        if kept is None:
            problems.append(says("check.record.check_change", "test_deleted", row=old_test.id))
        elif kept.test != old_test.test:
            problems.append(says("check.record.check_change", "test_renamed", row=old_test.id))
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
                problems.append(
                    says(
                        "check.record.check_change",
                        "not_binding_row",
                        row=d.id,
                        replaces=d.replaces,
                    )
                )
            elif d.set_by not in (SPONSOR, target.set_by):
                problems.append(
                    says(
                        "check.record.check_change",
                        "not_author",
                        row=d.id,
                        by=d.set_by,
                        target=target.id,
                        target_by=target.set_by,
                    )
                )
            elif now[target.id].status != f"replaced by {d.id}":
                problems.append(
                    says("check.record.check_change", "not_marked", target=target.id, row=d.id)
                )
        else:
            question = asked.get(d.replaces)
            if question is None:
                problems.append(
                    says("check.record.check_change", "no_question", row=d.id, replaces=d.replaces)
                )
            elif d.set_by not in (SPONSOR, question.settled_by):
                problems.append(
                    says(
                        "check.record.check_change",
                        "not_settler",
                        row=d.id,
                        by=d.set_by,
                        question=question.id,
                        settler=question.settled_by,
                    )
                )
            elif question.id in still:
                problems.append(
                    says("check.record.check_change", "still_open", question=question.id, row=d.id)
                )
    settled = {d.replaces for d in added}
    problems += [
        says("check.record.check_change", "question_removed", question=q)
        for q in asked
        if q not in still and q not in settled
    ]
    problems += [
        says("check.record.check_change", "bad_settler", question=q.id, settler=repr(q.settled_by))
        for q in after.open
        if q.id not in asked and q.settled_by not in SETTLERS
    ]
    if problems:
        refuse("check.record.check_change", kind=RecordRefused, problems="; ".join(problems))


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
    found = (
        [
            says(
                "check.record.check_change",
                "unidentified",
                row=d.id or "a new row",
                missing=", ".join(missing),
            )
        ]
        if missing
        else []
    )
    if not _ID.match(d.id) or not d.id.startswith("R"):
        found.append(says("check.record.check_change", "not_an_id", row=repr(d.id)))
    if d.set_by not in AUTHORS:
        found.append(says("check.record.check_change", "no_author", row=d.id, by=repr(d.set_by)))
    if d.status != BINDING:
        found.append(
            says("check.record.check_change", "not_binding", row=d.id, status=repr(d.status))
        )
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
        f"**The {'Goal' if after.on_goal else 'epic'}'s record changed**, by the {by}"
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
    """Change the record `epic` reads, check the change, write it, and say what changed.

    The record is wherever `locate` finds it: the epic's body, or its Goal's record,
    where a row this edit adds for the epic binds the epic unless it names others.
    `expected` is what held the record as the caller read it before deciding the
    change (the model may have thought for minutes): if it differs now, nothing is
    written. It is read once more just before writing, for the same reason. An edit
    that changes nothing writes nothing. `kind` tags the change comment, so a step can
    find its own changes without reading what they say. The comment goes on `epic`.
    """
    place, held, text = _locate(issues, repo, epic)
    if expected is not None and held != expected:
        raise RecordChanged(f"the record changed while it was decided: {repo}#{epic}")
    before = parse(text) if text else Record()
    after = change(before)
    if place.on_goal and place.goal != epic:
        after = after.bound(f"{issues.owner}/{repo}#{epic}")
    check_change(before, after)
    if after == before:
        return after
    if _locate(issues, repo, epic)[1] != held:
        raise RecordChanged(f"the record changed while it was edited: {repo}#{epic}")
    if place.comment is not None:
        issues.edit_comment(repo, place.comment, f"{GOAL_RECORD_MARKER}\n{render(after)}\n")
    else:
        head, _ = split(held)
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


def start_goal_record(
    issues: Any, sink: EventSink, *, repo: str, goal: int, by: str, record: Record, why: str = ""
) -> Record:
    """Write the Goal's first record, as its own comment, checked as any edit is.

    Once per Goal: a Goal that has one already is changed through `edit`.
    """
    if goal_record_comment(issues, repo, goal) is not None:
        raise RecordChanged(f"the Goal already has a record: {repo}#{goal}")
    after = replace(record, on_goal=True)
    empty = Record(on_goal=True)
    check_change(empty, after)
    issues.comment(repo, goal, f"{GOAL_RECORD_MARKER}\n{render(after)}\n")
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=goal,
        body=change_note(empty, after, by=by, why=why),
        by=by,
    )
    return after
