"""Acceptance: verifying delivered work, and closing out what is finished.

QA judges behaviour against the acceptance criteria, which is a different
question from the Reviewer's. A story only leaves QAing when every
criterion is proven by a test that actually exercises it.

The columns say what a card is waiting for, not what is happening to it. A
card sits in QAing until QA has finished with it — QA does not pull it
into a lane of its own — and lands in Merging already verified, where
what it waits for is the Sponsor.

Parent completion is bookkeeping the crew should not make a human do: an epic
whose stories are all Done is done, and so is a goal whose epics are.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from crew_org import log, profiles
from crew_org.columns import DONE, IN_PROGRESS, MERGING, QAING
from crew_org.crews import asks as file_asks
from crew_org.crews.qa_crew import QAVerdict, verify_story
from crew_org.events import CrewEvent, EventKind, EventSink, attributed, held
from crew_org.flows import artifacts
from crew_org.flows.board_flow import NEEDS_REWORK
from crew_org.flows.conclusion import story_rows
from crew_org.flows.history import past_qa
from crew_org.flows.moves import move_card
from crew_org.git_ops import Workspace, branch_name
from crew_org.llm import reraise_if_down
from crew_org.profiles import profile_for
from crew_org.project import brief, read_record
from crew_org.rules import Rule
from crew_org.tools import workspace
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient, within
from crew_org.tools.repo_context import (
    READ_MIN_CHARS,
    READING_CHAR_CEILING,
    WHOLE_FILE_CHARS,
    Focus,
    project_files,
)
from crew_org.tools.repo_read import Reader
from crew_org.tools.sandbox import Sandbox
from crew_org.work_types import BUG, open_in

# QA reasons about the test code, so what it is shown decides its verdict. A
# 12,000-character slice in the prompt cut story #13's two new tests off the end
# of a 13,839-character file, and QA correctly reported that it could not find
# them. New tests are appended, so a head-slice lands on the evidence every
# time.
#
# Past this, QA refuses to judge rather than judging on part of the evidence.
# QAVerdict has no way to say "I could not see enough to tell" — `proven` is a
# bool — so incomplete evidence has to resolve to proven or unproven, and both
# are false. Asking the model in prose not to read an omission as an absence is
# worse still: it reads equally well as "assume it is covered", which turns a
# truncation into an acceptance in the gate that now merges without a person.
# 600,000 characters is roughly 150,000 tokens. The crew's own suite is already
# 189,997 and growing, so 200,000 was weeks from refusing every verdict — a
# guard that fires in ordinary work is a budget, and this one refuses outright.
QA_CONTEXT_CHAR_CEILING = 600_000
# The end of a test run is where the summary and the failures are. Keeping the
# front of it is the same mistake delivery already learned not to make.
QA_OUTPUT_CHAR_CEILING = 40_000

STORY_TYPE = "Story"
EPIC_TYPE = "Epic"
GOAL_TYPE = "Goal"

QA_MARKER = "<!-- crew:qa -->"


def qa_marker(revision: str) -> str:
    """The marker for a verdict on one revision.

    `has_comment_marked` matches *any* comment carrying the bare marker, so the
    rejection QA itself wrote permanently disqualified the card: the Developer
    repaired, the card came back to QAing, and QA skipped it in silence — for
    good, while the command printed "Nothing awaiting QA".

    Scoping the marker to the commit keeps the idempotency the guard was written
    for — a re-run on unchanged code posts nothing — and lets new commits be
    judged. Bare `QA_MARKER` stays in the body so older verdicts remain findable.
    """
    return f"<!-- crew:qa {revision[:12]} -->"


@dataclass
class QAOutcome:
    card: int
    accepted: bool
    unproven: int = 0
    reason: str | None = None


class EvidenceTooLarge(RuntimeError):
    """The tests did not fit, so there is no honest verdict to give."""


@dataclass
class AcceptanceResult:
    verified: list[QAOutcome] = field(default_factory=list)
    returned: list[QAOutcome] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    # Cards QA deliberately did not judge again. Reported, because a silent
    # `continue` is how a card sat in QAing for good while the run said there
    # was nothing to do.
    skipped: list[tuple[int, str]] = field(default_factory=list)
    parents_closed: list[int] = field(default_factory=list)


def awaiting_qa(cards: list[Card]) -> list[Card]:
    return open_in(cards, QAING)


def render_qa(verdict: QAVerdict, revision: str = "") -> str:
    lines = [
        # Both: the bare marker keeps every verdict findable, the revision one
        # says which commit this verdict is about.
        QA_MARKER,
        qa_marker(revision) if revision else "",
        f"## QA — {'accepted' if verdict.accepted else 'not accepted'}",
        "",
        verdict.summary,
        "",
        "### Criteria",
        "",
    ]
    for item in verdict.criteria:
        mark = "proven" if item.proven else "**not proven**"
        lines += [f"- {mark} — {item.criterion}", f"  - {item.evidence}"]
    return "\n".join(lines)


def collect_tests(worktree: Path) -> str:
    """The test code, which is the evidence QA reasons about.

    Every test file, whole, or EvidenceTooLarge. A cut that lands inside a test
    function shows QA half a test and no sign that there was more, and a
    verdict reached on part of the evidence is not a verdict.
    """
    parts: list[str] = []
    total = 0
    # The same files the Developer's context lists: what setup installed into the
    # checkout (`.crew-home`) holds other projects' tests (crew#690).
    for path in project_files(worktree):
        if not fnmatch(path.name, "test_*.py"):
            continue
        rel = path.relative_to(worktree)
        body = path.read_text(encoding="utf-8", errors="ignore")
        total += len(body)
        if total > QA_CONTEXT_CHAR_CEILING:
            raise EvidenceTooLarge(
                f"the tests are larger than {QA_CONTEXT_CHAR_CEILING:,} characters "
                f"(reached at {rel}), so no criterion can be judged on all of the "
                "evidence. Raise QA_CONTEXT_CHAR_CEILING or split the suite."
            )
        parts.append(f"# {rel}\n{body}")
    return "\n\n".join(parts)


# Above this, QA is shown the tests the work touches or names in full, and the
# rest by name (#231). sprint-metrics' suite passed 200,000 characters, and a QA
# call reached 78,528 prompt tokens for a docs story (2026-09-29): the band where
# answers came back empty (#312).
QA_FOCUS_ABOVE_CHARS = 60_000
# How often one verdict may ask to see more test files, as the Developer may.
QA_ASK_LIMIT = 2
# The tests a file defines, and tests named in prose, are the profile's (#404).


@dataclass
class QATests:
    """What QA is shown of the tests, and what it isn't."""

    text: str
    shown: list[str]
    focused: bool
    # Test name -> the file that defines it, for every test in the suite.
    defined: dict[str, str]
    # A large file shown in part: its path -> the names of the definitions shown.
    cut: dict[str, set[str]] = field(default_factory=dict)
    # What QA read with the tool, as `path` or `path::name`.
    read: list[str] = field(default_factory=list)

    def seen(self, name: str) -> bool:
        """Whether QA was shown the test `name`, or read it."""
        rel = self.defined.get(name)
        if rel is None:
            return False
        read = {key.split("::", 1)[1].rsplit(".", 1)[-1] for key in self.read if "::" in key}
        return (
            rel in self.shown
            or name in self.cut.get(rel, ())
            or rel in self.read
            or (name in read and any(key.startswith(f"{rel}::") for key in self.read))
        )


def qa_tests(worktree: Path, *, about: str = "", extra: Sequence[str] = ()) -> QATests:
    """The tests, for QA: all of them when small, else the ones the work touches or names.

    Chosen: the test files the branch changed, those defining a test `about`
    names (the story, the Developer's proof, the criteria), and those QA asked
    for. A chosen file is shown whole unless it's large: then it's shown in
    part, its imports and the tests the branch changed or the work names, with
    the fixtures those use (lean-prompts plan, L4). Every other test file is
    listed with the tests it defines, so QA can read one by name.

    `extra` holds what QA asked for: a path is shown whole, and `path::name`
    adds that test to its file's part.
    """
    files = [p for p in project_files(worktree) if fnmatch(p.name, "test_*.py")]
    bodies = {
        str(p.relative_to(worktree)): p.read_text(encoding="utf-8", errors="ignore") for p in files
    }
    defined = {
        name: rel for rel, body in bodies.items() for name in profile_for(rel).tests_defined(body)
    }
    if sum(len(b) for b in bodies.values()) <= QA_FOCUS_ABOVE_CHARS:
        return QATests(collect_tests(worktree), list(bodies), False, defined)
    asked = [str(Path(e.strip().removeprefix("./"))) for e in extra if e.strip()]
    wanted = {e for e in asked if "::" not in e}
    named = set(profile_for().test_names_in(about)) | {
        e.split("::", 1)[1].rsplit(".", 1)[-1] for e in asked if "::" in e
    }
    changed = set(changed_paths(worktree))
    lines = changed_lines(worktree)
    chosen = [
        rel
        for rel in bodies
        if rel in changed
        or rel in wanted
        or rel in about
        or any(defined.get(name) == rel for name in named)
    ]
    parts: list[str] = []
    shown: list[str] = []
    cut: dict[str, set[str]] = {}
    for rel in chosen:
        body = bodies[rel]
        part = (
            in_part(body, lines.get(rel, set()), named)
            if len(body) > WHOLE_FILE_CHARS and rel not in wanted
            else None
        )
        if part is None:
            parts.append(f"# {rel}\n{body}")
            shown.append(rel)
        else:
            parts.append(f"# {rel} (in part: read any other definition by name)\n{part[0]}")
            cut[rel] = part[1]
    others = [
        f"- `{rel}`: " + (", ".join(profile_for(rel).tests_defined(body)) or "no tests")
        for rel, body in bodies.items()
        if rel not in chosen
    ]
    if others:
        parts.append(
            "# Every other test file, and the tests it defines (not shown in full)\n"
            + "\n".join(others)
        )
    return QATests("\n\n".join(parts), shown, True, defined, cut)


def in_part(source: str, lines: set[int], names: set[str]) -> tuple[str, set[str]] | None:
    """A large test file as QA needs it, and the names it shows; None if it doesn't parse.

    Its imports, the functions the branch added or changed (`lines`, as the file
    now numbers them) or `names` names, and the fixtures those take from this
    file. Every other definition is listed by name, to read with the tool.
    """
    import ast  # noqa: PLC0415

    from crew_org.tools.ast_edit import definitions  # noqa: PLC0415

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    found = definitions(source)
    functions = {
        k: n for k, n in found.items() if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    def span(node: ast.stmt) -> tuple[int, int]:
        start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        return start, node.end_lineno or node.lineno

    picked = [
        k
        for k, n in functions.items()
        if k.rsplit(".", 1)[-1] in names or any(span(n)[0] <= x <= span(n)[1] for x in lines)
    ]
    # A test is read with its fixtures: a test's arguments name them.
    taken = {a.arg for k in picked for a in functions[k].args.args}
    picked += [k for k in functions if k not in picked and k in taken]
    picked.sort(key=lambda k: span(functions[k])[0])
    text = source.splitlines()
    imports = [
        "\n".join(text[n.lineno - 1 : n.end_lineno or n.lineno])
        for n in tree.body
        if isinstance(n, ast.Import | ast.ImportFrom)
    ]
    shown = [
        (f"# in class {k.rsplit('.', 1)[0]}\n" if "." in k else "")
        + "\n".join(text[span(functions[k])[0] - 1 : span(functions[k])[1]])
        for k in picked
    ]
    rest = [k for k, n in found.items() if k not in picked and not isinstance(n, ast.ClassDef)]
    listing = [f"# Not shown, read by name: {', '.join(rest)}"] if rest else []
    return (
        "\n\n".join(["\n".join(imports), *shown, *listing]).strip(),
        {k.rsplit(".", 1)[-1] for k in picked},
    )


def unread(verdict, tests: QATests) -> dict[str, str]:
    """Tests any criterion cites that QA wasn't shown and didn't read: name -> its file.

    An unproven criterion counts too: mqucifer/sprint-metrics#569's criterion 6 was
    held unproven because its test's file wasn't shown in full, though the test
    proved it (crew#645).
    """
    cited = {name for c in verdict.criteria for name in profile_for().test_names_in(c.evidence)}
    return {n: tests.defined[n] for n in cited if n in tests.defined and not tests.seen(n)}


def unseen_citations(verdict, tests: QATests) -> dict[str, str | None]:
    """Tests a proven criterion cites that QA wasn't shown: name -> its file, or None if none."""
    cited = {
        name
        for c in verdict.criteria
        if c.proven
        for name in profile_for().test_names_in(c.evidence)
    }
    return {n: tests.defined.get(n) for n in cited if not tests.seen(n)}


def held_to_what_it_read(verdict: QAVerdict, tests: QATests) -> QAVerdict:
    """A proven criterion citing a test QA never read, or one that doesn't exist, isn't proven.

    The counterpart of refusing a Developer's named test that doesn't exist
    (#221): acceptance rests on evidence QA saw.
    """
    unseen = unseen_citations(verdict, tests)
    if not unseen:
        return verdict
    criteria = []
    for c in verdict.criteria:
        # A name the criterion itself quotes is its data, not a test QA cites: the
        # failure code `test_assertion_failed` held a proven criterion unproven (crew#768).
        missing = (
            [
                n
                for n in profile_for().test_names_in(c.evidence)
                if n in unseen and not (unseen[n] is None and n in c.criterion)
            ]
            if c.proven
            else []
        )
        if not missing:
            criteria.append(c)
            continue
        why = "; ".join(
            f"cites `{n}`, which "
            + ("no test file defines" if unseen[n] is None else "QA wasn't shown")
            for n in missing
        )
        criteria.append(c.model_copy(update={"proven": False, "evidence": f"{c.evidence} ({why})"}))
    accepted = verdict.accepted and all(c.proven for c in criteria)
    return verdict.model_copy(update={"criteria": criteria, "accepted": accepted})


def _judge(
    story: str, *, worktree: Path, sink: EventSink, number: int, repo: str, **evidence
) -> QAVerdict:
    """QA's verdict, shown the tests the work touches or names, asking for more if it must (#231).

    QA reads what else it needs with the tool, as the Developer does (L4). A
    test a criterion cites that QA neither was shown nor read is added and the
    story judged again, as if QA had asked for it. Past the limit, what it
    cites and didn't read doesn't count.
    """
    asked: list[str] = []
    # Set when an ask brings nothing new to show: the next try must judge.
    judge_now = False
    for ask in range(QA_ASK_LIMIT + 1):
        tests = qa_tests(worktree, about=story, extra=asked)
        sink.note(
            EventKind.NOTE,
            f"#{number} QA context: {len(tests.text):,} chars of tests"
            + (
                f", {len(tests.shown)} files in full, {len(tests.cut)} in part"
                if tests.focused
                else ", the whole suite"
            ),
            card=number,
            context_chars=len(tests.text),
            focused=tests.focused,
            shown=tests.shown,
            cut=sorted(tests.cut),
            asked=asked,
        )
        can_ask = tests.focused and ask < QA_ASK_LIMIT and not judge_now
        # Offered only when there's something to ask for: the whole suite shown
        # leaves nothing listed by name.
        ask_for = {"can_ask": True} if can_ask else {}
        # What it reads shares one ceiling with what it was shown, as the Developer's does.
        room = (
            READING_CHAR_CEILING
            - len(story)
            - len(tests.text)
            - sum(len(str(v)) for v in evidence.values())
        )
        reader = (
            Reader(
                worktree,
                budget=room,
                shown=tests.shown,
                # What QA was shown, in the selection's terms, for each read's note.
                focus=Focus(focused=tests.focused, shown=tests.shown, partial=list(tests.cut)),
            )
            if tests.focused and room >= READ_MIN_CHARS
            else None
        )
        try:
            verdict = attributed(verify_story, card=number, repo=repo)(
                story,
                test_code=tests.text,
                **({"read": reader.tool()} if reader is not None else {}),
                **ask_for,
                **evidence,
            )
        finally:
            if reader is not None and reader.read:
                tests.read = list(reader.read)
                sink.note(
                    EventKind.NOTE,
                    f"#{number} QA read {', '.join(reader.read)[:200]}",
                    card=number,
                    read=reader.read,
                )
        if verdict.criteria:
            wanted = [
                f"{rel}::{name}"
                for name, rel in unread(verdict, tests).items()
                if f"{rel}::{name}" not in asked
            ]
            if not wanted or not can_ask:
                break
        else:
            wanted = [f for f in file_asks.paths(verdict.need_files) if f not in asked]
            judge_now = not wanted
        asked += wanted
    if not verdict.criteria:
        raise ValueError(
            f"QA asked to see {', '.join(file_asks.paths(verdict.need_files))} past its limit"
        )
    return held_to_what_it_read(verdict, tests)


# Generous, not a budget (§16): docs a story edits are read whole or named.
QA_DOCS_CHAR_CEILING = 60_000


def changed_paths(worktree: Path) -> list[str]:
    """Every file the branch changed since it left the default branch."""
    import subprocess  # noqa: PLC0415

    from crew_org.tools.regression import merged_base  # noqa: PLC0415

    merged = merged_base(worktree)
    if merged is None:
        return []
    diff = subprocess.run(
        ["git", "diff", "--name-only", merged.sha],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return diff.stdout.split()


_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def changed_lines(worktree: Path) -> dict[str, set[int]]:
    """The lines the branch added or changed in each file, numbered as the file now is."""
    import subprocess  # noqa: PLC0415

    from crew_org.tools.regression import merged_base  # noqa: PLC0415

    merged = merged_base(worktree)
    if merged is None:
        return {}
    diff = subprocess.run(
        ["git", "diff", "-U0", merged.sha],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    found: dict[str, set[int]] = {}
    rel = ""
    for line in diff.stdout.splitlines():
        if line.startswith("+++ "):
            rel = line[6:] if line.startswith("+++ b/") else ""
            continue
        hunk = _HUNK.match(line)
        if hunk and rel:
            start = int(hunk.group(1))
            count = int(hunk.group(2)) if hunk.group(2) is not None else 1
            found.setdefault(rel, set()).update(range(start, start + count))
    return found


def _with_unguarded(checks: str, worktree: Path) -> str:
    """QA's checks, plus the changed files no regression guard read (#404)."""
    bare = [p for p in changed_paths(worktree) if not profile_for(p).guarded]
    if not bare:
        return checks
    notice = (
        "**No regression guard ran on:** "
        + ", ".join(f"`{p}`" for p in bare)
        + ". The crew's guard reads Python only: judge these criteria by their tests, "
        "and name anything existing the change seems to have removed."
    )
    return "\n\n".join(part for part in (checks, notice) if part)


def ci_only(worktree: Path) -> bool:
    """The branch changes CI workflows, and at most the docs that describe them (crew#333).

    A deploy workflow comes with a README note on how the site deploys
    (crew-presentation#74); the Code Reviewer reads both.
    """
    from crew_org.crews.delivery_crew import is_doc  # noqa: PLC0415
    from crew_org.tools.ci_guard import is_workflow  # noqa: PLC0415

    changed = changed_paths(worktree)
    return (
        bool(changed)
        and all(is_workflow(p) or is_doc(p) for p in changed)
        and any(is_workflow(p) for p in changed)
    )


def collect_docs(worktree: Path) -> str:
    """The docs this change edits, as they now read (§7.1, crew#324).

    A criterion about what a doc says is proven by the doc, not by a test, so
    QA reads it. Only documentation files the branch changed since it left the
    default branch; a doc that doesn't fit is named rather than cut.
    """
    from crew_org.crews.delivery_crew import is_doc  # noqa: PLC0415

    changed = changed_paths(worktree)
    parts: list[str] = []
    left_out: list[str] = []
    budget = QA_DOCS_CHAR_CEILING
    for rel in sorted(p for p in changed if is_doc(p)):
        path = worktree / rel
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        if len(body) > budget:
            left_out.append(rel)
            continue
        budget -= len(body)
        parts.append(f"# {rel}\n{body}")
    if left_out:
        parts.append("Also edited, and not shown because they did not fit: " + ", ".join(left_out))
    return "\n\n".join(parts)


def ci_checks(issues: IssueClient, repo: str, sha: str) -> str:
    """The CI checks on the commit QA judges (#325), or "" where none can be read.

    What only CI runs (an image build, a workflow) has no test in the sandbox:
    its passing check is the evidence.
    """
    from crew_org.tools.review_evidence import checks_section  # noqa: PLC0415

    try:
        runs = issues.check_runs(repo, sha)
    except Exception:  # noqa: BLE001
        return ""
    return checks_section(runs, "") if runs else ""


def collect_output(results) -> str:
    """What running the suite produced, keeping the end rather than the front."""
    joined = "\n\n".join(f"$ {r.command}\n{r.output}" for r in results)
    if len(joined) <= QA_OUTPUT_CHAR_CEILING:
        return joined
    return "…earlier output trimmed…\n" + joined[-QA_OUTPUT_CHAR_CEILING:]


# A story the Developer answered is already done (#221): no pull request, the
# evidence on the story. QA judges it like any story; accepted, it closes.
ALREADY_DONE_MARKER = "<!-- crew:already-done -->"
# Criteria a delivery says the existing tests prove (#217): QA runs and cites them.
EXISTING_PROOF_MARKER = "<!-- crew:existing-proof -->"


def _existing_proof(issues: IssueClient, repo: str, number: int) -> str:
    """The criteria the latest delivery named existing tests for, as it wrote them."""
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, number)]
    except Exception:  # noqa: BLE001
        return ""
    latest = [b for b in bodies if b.startswith("Implemented in #")]
    if not latest or EXISTING_PROOF_MARKER not in latest[-1]:
        return ""
    block = latest[-1].split(EXISTING_PROOF_MARKER, 1)[1]
    return block.split("<!-- crew:by")[0].strip()


def _already_done(issues: IssueClient, repo: str, number: int) -> str:
    """The Developer's already-done evidence, if the story's latest delivery was that."""
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, number)]
    except Exception:  # noqa: BLE001
        return ""
    latest = [b for b in bodies if ALREADY_DONE_MARKER in b or b.startswith("Implemented in #")]
    if not latest or ALREADY_DONE_MARKER not in latest[-1]:
        return ""
    return latest[-1].replace(ALREADY_DONE_MARKER, "").split("<!-- crew:by")[0].strip()


def run_qa(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Workspace,
    sandbox: Sandbox,
    *,
    cards: list[Card],
    repo: str,
    repos: set[str] | None = None,
) -> AcceptanceResult:
    """Verify everything sitting in QAing.

    `repo` is a fallback for a card that names none, never the answer. QA read
    the story, checked its own marker and posted its verdict against this
    argument whatever card it was judging — so a card outside the pilot got its
    body read from the wrong repository and its verdict posted there. Delivery
    has always used `card.repo or repo`; QA was the one flow that did not.
    """
    result = AcceptanceResult()

    for card in awaiting_qa(within(cards, repos)):
        # One QA run per card (crew#449).
        with log.run("QA Engineer", card=card.number, repo=card.repo or repo):
            number = card.number or 0
            card_repo = card.repo or repo
            branch = branch_name(number, card.title)
            # The worktree has to come from the card's own repository too, or QA
            # verifies a branch of the same name in a different codebase.
            card_ws = ws.for_repo(card_repo)

            try:
                worktree = card_ws.open_existing(branch)
                revision = card_ws.head()
            except Exception as exc:  # noqa: BLE001
                result.failed.append((number, f"{type(exc).__name__}: {exc}"))
                continue

            if issues.has_comment_marked(card_repo, number, qa_marker(revision)):
                held("skip.acceptance.already_judged_at", repo=card.repo or repo, card=number)
                result.skipped.append((number, f"already judged at {revision[:7]}"))
                continue

            # Only CI workflows: QA can observe nothing of what
            # they do until they run on `main`. The Code Reviewer judged the
            # workflow; its run is the proof (§7.1, crew#333).
            if ci_only(worktree):
                card_ws.close()
                artifacts.comment(
                    issues,
                    sink,
                    repo=card_repo,
                    number=number,
                    body=f"{QA_MARKER}\n{qa_marker(revision)}\n"
                    "## QA — not applicable: a CI-only change\n\n"
                    "This pull request changes CI workflows, and at most the docs that "
                    "describe them, which the Code Reviewer read. What a workflow "
                    "does can only be observed when it runs, mostly on `main`, so it is judged "
                    "by the Code Reviewer and proven by its own run (constitution §7.1). "
                    "A check that fails on the pull request or in the merge queue still sends "
                    "it back with the log.",
                    by="QA Engineer",
                )
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=MERGING,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="CI-only change: judged by review, proven by its run",
                    rule="decision.acceptance.ci_only",
                )
                result.verified.append(QAOutcome(card=number, accepted=True))
                continue

            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_STARTED,
                    role="QA Engineer",
                    card=number,
                    summary=f"verify {card.title[:46]}",
                )
            )
            try:
                check = workspace.check(worktree, sandbox=sandbox)
                done = _already_done(issues, card_repo, number)
                proof = _existing_proof(issues, card_repo, number)
                if proof:
                    done_or_proof = (
                        "\n\n## Criteria the Developer says the existing tests prove\n\n"
                        f"{proof}\n\nThese have no new test. Judge each from the named tests' "
                        "results in the test run, and cite them as the evidence."
                    )
                else:
                    done_or_proof = ""
                answered = (
                    "\n\n## The Developer answered that this is already done\n\n"
                    f"{done}\n\nNothing was changed. Judge each criterion against the code "
                    "and the tests named, as for any story."
                    if done
                    else done_or_proof
                )
                # Read first: it sets this project's parts, which the notice below needs (#404).
                project = _project_brief(worktree)
                # A bug's body is its report, then the criteria that reproduce it
                # (crew#407): judged the same, said so the report isn't read as scope.
                bug = (
                    "This is a bug fix. The report above the criteria says what was wrong; "
                    "judge the criteria, which reproduce it.\n\n"
                    if card.work_type == BUG
                    else ""
                )
                body = issues.get(card_repo, number).get("body") or ""
                verdict = _judge(
                    f"{bug}{card.title}\n\n{body}{answered}",
                    worktree=worktree,
                    sink=sink,
                    number=number,
                    repo=card_repo,
                    test_output=collect_output(check.results),
                    docs=collect_docs(worktree),
                    checks=_with_unguarded(ci_checks(issues, card_repo, revision), worktree),
                    prior_verdicts=past_qa(issues, card_repo, number, marker=QA_MARKER),
                    project=project,
                    decisions=story_rows(issues, card, card_repo),
                )
            except Exception as exc:  # noqa: BLE001
                reraise_if_down(exc)
                result.failed.append((number, f"{type(exc).__name__}: {exc}"))
                sink.emit(
                    CrewEvent(
                        kind=EventKind.AGENT_FAILED,
                        role="QA Engineer",
                        card=number,
                        summary=str(exc)[:80],
                    )
                )
                continue
            finally:
                card_ws.close()

            artifacts.comment(
                issues,
                sink,
                repo=card_repo,
                number=number,
                body=render_qa(verdict, revision),
                by="QA Engineer",
            )

            if verdict.accepted and done:
                # Nothing to merge (#221): the story closes, proven against main.
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=DONE,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="already done — every criterion proven, no pull request",
                    rule="gate.qa_unproven",
                )
                issues.close(card_repo, number)
                result.verified.append(QAOutcome(card=number, accepted=True))
            elif verdict.accepted:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=MERGING,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="every criterion proven",
                    rule="gate.qa_unproven",
                )
                result.verified.append(QAOutcome(card=number, accepted=True))
            else:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=IN_PROGRESS,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary=f"returned — {len(verdict.unproven)} unproven",
                    rule=Rule.QA_UNPROVEN,
                )
                outcome = QAOutcome(
                    card=number,
                    accepted=False,
                    unproven=len(verdict.unproven),
                    reason=verdict.unproven[0].evidence if verdict.unproven else None,
                )
                result.returned.append(outcome)

            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FINISHED,
                    role="QA Engineer",
                    card=number,
                    summary=f"{'accepted' if verdict.accepted else 'returned'} — "
                    f"{len(verdict.unproven)} unproven",
                    detail={
                        "accepted": verdict.accepted,
                        "unproven": [c.criterion for c in verdict.unproven],
                        # Which criteria, by their number on the card (crew#449).
                        "unproven_numbers": [
                            n for n, c in enumerate(verdict.criteria, start=1) if not c.proven
                        ],
                    },
                )
            )

    return result


def close_finished_parents(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    cards: list[Card],
    *,
    repo: str,
    repos: set[str] | None = None,
) -> list[int]:
    """Close an epic when its stories are Done, and a goal when its epics are.

    Bookkeeping a human should not have to do. Done means every child is Done —
    a parent with one story still open is not finished, however close it looks.

    A child closed as not planned was superseded, not delivered: GitHub moves it
    to Done all the same. It doesn't count either way, and a parent with nothing
    delivered isn't finished. sprint-metrics#184, #185 and #187 were closed as
    completed when their superseded stories were, and Goal #174 would have
    followed. A parent being planned again (`needs:rework`) never closes here.

    Closing means both: the card moves to Done and the issue closes. Moving the
    card alone left finished epics open in the repository, and a parent already
    in Done with its issue open is closed here rather than skipped.
    """
    closed: list[int] = []
    # By repository and number: the board spans repositories, and crew#33 is
    # not sprint-metrics#33. Now that this closes issues, confusing them would
    # close one whose children are not done.
    by_key = {(c.repo, c.number): c for c in cards}

    for parent_type in (EPIC_TYPE, GOAL_TYPE):
        # Parents only in the crew's repositories; their children are read
        # from the whole board.
        for card in within(cards, repos):
            if card.work_type != parent_type or card.state == "CLOSED":
                continue
            if NEEDS_REWORK in card.labels:
                continue
            # "Not finished" is settled without a fetch (#55). The children on
            # the board say it first: one that isn't Done. GitHub's sub-issue
            # count is only the fallback for a parent with none on the board,
            # because it can be wrong: it read 1 of 2 for sprint-metrics#304 with
            # both children closed and Done, and the epic, and everything held
            # on it, stayed open (crew#381). 100% either way only earns the
            # per-child check below; neither closes anything alone.
            on_board = [c for c in cards if c.parent == card.number and c.repo == card.repo]
            if on_board:
                if any(c.status != DONE for c in on_board):
                    continue
            else:
                total, closed_count = card.sub_issues_total, card.sub_issues_closed
                if total is not None and (total == 0 or (closed_count or 0) < total):
                    continue
            parent_repo = card.repo or repo
            try:
                children = issues.sub_issues(parent_repo, card.number or 0)
            except Exception:  # noqa: BLE001
                continue
            if not children:
                continue

            statuses = []
            for child in children:
                if child.get("state_reason") == "not_planned":
                    continue
                child_repo = child.get("repository_url", parent_repo).rsplit("/", 1)[-1]
                on_board = by_key.get((child_repo, child["number"]))
                if on_board:
                    statuses.append(on_board.status)
                else:
                    # A child off the board has no status to read; its issue
                    # closing is the only sign it is finished.
                    statuses.append(DONE if child.get("state") == "closed" else None)
            if not statuses or any(status != DONE for status in statuses):
                continue

            # Bookkeeping, not judgement: no role decided this, so the parent
            # keeps whichever role last worked on it.
            if card.status != DONE:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=DONE,
                    by=None,
                    card=card.number,
                    summary=f"all {len(statuses)} children done — closing {parent_type.lower()}",
                    rule="decision.acceptance.close_finished_parents",
                )
            issues.close(parent_repo, card.number or 0)
            closed.append(card.number or 0)
    return closed


def _project_brief(worktree) -> str:
    """The project's record for QA (#131), or nothing if it has none.

    Read from the branch under test, where it can't differ from main's:
    delivery refuses any change to the record (`bounds`). A record that can't
    be read raises, which fails this card's verification with the reason
    rather than judging it against rules nobody can see.
    """
    record = read_record(worktree)
    # QA reads this project's tests by its parts' profiles (#404).
    profiles.set_project(record)
    return brief(record) if record else ""
