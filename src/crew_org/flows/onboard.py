"""`crew onboard`: a project's record, from an interview with the Sponsor (#130).

The interview and the take-away file are two ways to give the same answers.
Either can stop and hand over to the other at any point, and the record's
loader (#129) is what tells both of them what is still missing. Nothing here
decides the interview is over except that loader and the Sponsor's yes.

The record arrives as a pull request to the project's repository, never as a
push: the project owns its answers, and changes to them are reviewed there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crew_org.flows.record_pr import RecordChange, propose
from crew_org.project import (
    FORMAT_VERSION,
    RECORD_PATH,
    REQUIRED,
    WHERE,
    Done,
    ProjectRecord,
    ProjectRecordError,
    Release,
    Scope,
    gaps,
    load_raw,
    lookup,
    render,
    validate,
)

Path_ = tuple[str, ...]

# The question for each required answer, used when the Product Owner did not
# ask one of its own, and carried into a take-away file for each one unanswered.
QUESTIONS: dict[Path_, str] = {
    ("intent", "scope", "purpose"): "What is this project for, and who is it for?",
    ("intent", "release", "deploys"): (
        "When work merges, is anything deployed, or is the merge itself the release?"
    ),
    ("intent", "done", "bar"): (
        "What must be true for a change to count as done here? In words: the tools "
        "that check it are the Architect's to choose."
    ),
    WHERE: "Where is it deployed?",
}
assert set(REQUIRED) | {WHERE} == set(QUESTIONS)

# Replies that end the interview early and leave a take-away file.
LATER = frozenset({"later", "/later", "stop", "/stop", "quit", "/quit"})
YES = frozenset({"y", "yes"})
# The Sponsor's way past the Product Owner's questions, to the record itself.
DONE = frozenset({"done", "/done", "/review"})

# The take-away file's layout, in the record's own order.
SECTIONS: dict[str, type] = {"scope": Scope, "release": Release, "done": Done}


def key(path: Path_) -> str:
    return ".".join(path)


def merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """`update` laid over `base`. Mappings merge; anything else replaces; None is no answer."""
    out = dict(base)
    for name, value in update.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(out.get(name), dict):
            out[name] = merge(out[name], value)
        else:
            out[name] = value
    return out


def problems(raw: dict[str, Any]) -> list[str]:
    """Why a record with every required answer still would not load. Empty if it would."""
    try:
        validate(raw)
    except ProjectRecordError as exc:
        return exc.problems
    return []


@dataclass
class Interview:
    """Where an interview ended."""

    raw: dict[str, Any]
    # The question for each required answer still missing, for a take-away file.
    questions: dict[Path_, str] = field(default_factory=dict)
    # Every required answer settled, the record valid, and the Sponsor said yes.
    settled: bool = False
    # Why it stopped short, when it was not the Sponsor's choice.
    interrupted: str | None = None
    # Everything said, in order, by the Product Owner and the Sponsor.
    transcript: list[str] = field(default_factory=list)
    # What the Product Owner proposed and the Sponsor has not confirmed (#148).
    # Never part of `raw`: a proposal is a suggestion for a take-away file.
    proposed: dict[str, Any] = field(default_factory=dict)


Turn = Callable[..., Any]
Ask = Callable[[str], "str | None"]
Tell = Callable[[str], None]

ANSWER = "Your answer ('done' to review the record, 'later' to finish offline)"
CONFIRM = "Open the pull request with this record? yes, or say what to change"


def interview(
    raw: dict[str, Any],
    *,
    repository: str,
    turn: Turn,
    ask: Ask,
    tell: Tell,
    intent: str = "",
    show: Callable[..., None] | None = None,
) -> Interview:
    """Interview the Sponsor until the record is settled, or they stop.

    `turn` is the Product Owner (`interview_turn`); `ask` returns the Sponsor's
    reply, or None when they end the session; `tell` shows them something.

    It continues while a required answer is missing or the Product Owner is
    still asking: a filled-in field is not the same as a clear one, and that
    judgement is the Product Owner's. The Sponsor has two ways to disagree:
    say so, and the Product Owner hears it, or reply `done` to go straight to
    the record. `done` cannot skip a required answer. A record that is
    already complete goes straight to the Sponsor's yes.

    `show`, if given, is told the record and the open questions whenever they
    change, for a front end that shows them as more than lines of text.
    """
    show = show or (lambda **_: None)
    transcript: list[str] = []
    questions: dict[Path_, str] = {}
    asking: list[str] = []
    # Project guidelines that would relax a crew-wide one (§19). They hold the
    # interview open like a missing answer: a project can add, never relax.
    conflicts: list[str] = []

    def said(who: str, text: str) -> None:
        transcript.append(f"**{who}:** {text}")

    def unsettled() -> tuple[dict[Path_, str], list[str]]:
        wanted = gaps(raw)
        return wanted, ([] if wanted else problems(raw))

    def stop(**how: Any) -> Interview:
        return Interview(
            raw,
            _questions(gaps(raw), questions),
            transcript=transcript,
            proposed=proposed,
            **how,
        )

    proposed: dict[str, Any] = {}
    wanted, wrong = unsettled()
    needs_turn = bool(wanted or wrong)
    show(raw=raw, asking=asking, proposed=proposed)
    while True:
        if needs_turn:
            try:
                result = turn(
                    repository=repository,
                    intent=intent,
                    draft=yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
                    if raw
                    else "(nothing yet)",
                    proposed=yaml.safe_dump(proposed, sort_keys=False, allow_unicode=True)
                    if proposed
                    else "",
                    missing={key(p): words for p, words in wanted.items()},
                    problems=wrong,
                    conversation="\n\n".join(transcript),
                )
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                return stop(
                    interrupted="stopped" if isinstance(exc, KeyboardInterrupt) else str(exc)
                )
            raw = merge(raw, {"intent": result.answers.model_dump(exclude_none=True)})
            # Only answers reach the record. A proposal stays a proposal until the
            # Sponsor confirms it and the Product Owner records it as an answer.
            proposed = unanswered(
                merge(proposed, {"intent": result.proposals.model_dump(exclude_none=True)}),
                raw,
            )
            wanted, wrong = unsettled()
            by_key = {q.about: q.question for q in result.questions}
            questions = {p: by_key.get(key(p)) or QUESTIONS[p] for p in wanted}
            # Its own questions, then a standing one for any missing answer it
            # did not ask about: a required answer is never left unasked.
            asking = [q.question for q in result.questions] + [
                q for p, q in questions.items() if key(p) not in by_key
            ]
            conflicts = [
                f"“{c.guideline}” would relax {c.crew_rule}: {c.why}" for c in result.conflicts
            ]
            message = result.say.strip()
            if conflicts:
                message += (
                    "\n\nThese would relax a crew-wide guideline, which a project can "
                    "add to but never relax:\n" + "\n".join(f"- {c}" for c in conflicts)
                )
            if wrong:
                message += "\n\nThese don't fit the record yet:\n" + "\n".join(
                    f"- {p}" for p in wrong
                )
            if asking:
                message += "\n\n" + "\n".join(f"- {q}" for q in asking)
            tell(message)
            said("Product Owner", message)
            show(raw=raw, asking=asking, proposed=proposed)

        if wanted or wrong or asking or conflicts:
            reply = ask(ANSWER)
            if reply is None:
                return stop()
            said("Sponsor", reply)
            if reply.strip().lower() in LATER:
                return stop()
            if reply.strip().lower() in DONE:
                if wanted or wrong or conflicts:
                    left = list(wanted.values()) + wrong + conflicts
                    note = "Not yet. The record can't be written without:\n" + "\n".join(
                        f"- {w}" for w in left
                    )
                    tell(note)
                    said("crew", note)
                    needs_turn = False
                    continue
                asking, needs_turn = [], False
                show(raw=raw, asking=asking, proposed=proposed)
                continue
            needs_turn = True
            continue

        tell(render(validate(raw)))
        reply = ask(CONFIRM)
        if reply is None:
            return stop()
        said("Sponsor", reply)
        if reply.strip().lower() in LATER:
            return stop()
        if reply.strip().lower() in YES:
            return Interview(raw, settled=True, transcript=transcript, proposed=proposed)
        needs_turn = True


def _questions(wanted: dict[Path_, str], asked: dict[Path_, str]) -> dict[Path_, str]:
    return {p: asked.get(p) or QUESTIONS[p] for p in wanted}


def unanswered(proposed: dict[str, Any], raw: dict[str, Any], at: Path_ = ()) -> dict[str, Any]:
    """The proposals the record does not answer yet. An answered one is spent."""
    out: dict[str, Any] = {}
    for name, value in proposed.items():
        where = (*at, name)
        if isinstance(value, dict):
            rest = unanswered(value, raw, where)
            if rest:
                out[name] = rest
        elif lookup(raw, where) in (None, "", []) and value not in (None, "", []):
            out[name] = value
    return out


def takeaway(
    raw: dict[str, Any],
    questions: dict[Path_, str],
    *,
    repo: str,
    path: Path,
    proposed: dict[str, Any] | None = None,
) -> str:
    """The answers so far as a file to finish in any editor.

    Every answer given is written as YAML. Every required one still missing is
    a commented-out line under the question the Product Owner would have asked,
    and the optional ones are commented out under what they mean, so the file
    explains itself to someone who has never seen the interview.
    """
    wanted = {**gaps(raw), **{p: "" for p in questions}}
    lines = [
        f"# {repo}'s onboarding record, not finished yet (#130).",
        "# Answer what is commented out, in any editor: remove the `# ` before a",
        "# field and give it a value. Then carry on from where this left off with:",
        f"#   crew onboard {repo} --from {path}",
        f"version: {raw.get('version', FORMAT_VERSION)}",
        "intent:",
    ]
    for section, model in SECTIONS.items():
        lines.append(f"  {section}:")
        for name, info in model.model_fields.items():
            where = ("intent", section, name)
            value = lookup(raw, where)
            if value is not None:
                lines += _indent(yaml.safe_dump({name: value}, allow_unicode=True), 4)
                continue
            if where in wanted:
                lines.append(f"    # Required. {questions.get(where) or QUESTIONS[where]}")
            else:
                lines.append(f"    # Optional. {info.description or name.replace('_', ' ')}")
            suggestion = lookup(proposed or {}, where)
            if suggestion is not None:
                # Commented out, like the field: a proposal is only an answer once
                # the Sponsor gives it (#148).
                shown = yaml.safe_dump(suggestion, default_flow_style=True, allow_unicode=True)
                lines.append(
                    f"    # Proposed, not confirmed: {shown.strip().removesuffix('...').strip()}"
                )
            placeholder = "[]" if "list" in str(info.annotation) else ""
            lines.append(f"    # {name}: {placeholder}".rstrip())
    guidelines = lookup(raw, ("intent", "guidelines"))
    if guidelines:
        lines += _indent(yaml.safe_dump({"guidelines": guidelines}, allow_unicode=True), 2)
    else:
        lines += [
            "  # Optional. This project's own rules, on top of the crew-wide ones",
            "  # (constitution §19). They can add to those, never relax one.",
            "  # guidelines: []",
        ]
    priority = lookup(raw, ("intent", "priority"))
    if priority is not None:
        lines.append(f"  priority: {priority}")
    else:
        lines += ["  # Optional. Against other projects; 1 is first", "  # priority:"]
    # The Architect's and the crew's sections are carried through untouched:
    # the take-away is the Sponsor's to finish, not theirs.
    for owned in ("design", "learned"):
        if raw.get(owned):
            lines += _indent(yaml.safe_dump({owned: raw[owned]}, allow_unicode=True), 0)
    return "\n".join(lines) + "\n"


def _indent(text: str, by: int) -> list[str]:
    return [" " * by + line for line in text.rstrip("\n").splitlines()]


# --- the project, as the Product Owner reads it --------------------------------


@dataclass
class Project:
    """What the Product Owner is shown, and what the interview starts from."""

    repository: str = ""
    intent: str = ""
    default_branch: str | None = None
    existing: dict[str, Any] | None = None


def describe_intent(open_issues: list[dict[str, Any]]) -> str:
    """The project's open issues, goals first: what it has been asked for so far.

    An answer can only be tested against intent the Product Owner is shown. In
    sprint-metrics' first onboarding it wrote down "current-sprint" metrics as
    the purpose while goal #43 asked for sprints past, because it had only
    ever seen the code.
    """

    def labels(issue: dict[str, Any]) -> list[str]:
        return [label["name"] for label in issue.get("labels") or []]

    ordered = sorted(open_issues, key=lambda i: ("goal" not in labels(i), i["number"]))
    blocks = []
    for issue in ordered:
        tags = f" [{', '.join(labels(issue))}]" if labels(issue) else ""
        body = (issue.get("body") or "").strip() or "(no description)"
        blocks.append(f"### #{issue['number']} {issue['title']}{tags}\n\n{body}")
    return "\n\n".join(blocks)


def describe(path: Path, *, branch: str, protection: dict[str, Any] | None) -> str:
    """The project for the Product Owner: its code, its CI, and how its branch is protected.

    Empty when the repository holds no files at all, which is what tells the
    Product Owner to ask rather than propose.
    """
    from crew_org.tools.repo_context import IGNORED_DIRS, repository_context  # noqa: PLC0415

    files = [p for p in path.rglob("*") if p.is_file() and not (IGNORED_DIRS & set(p.parts))]
    if not files:
        return ""
    lines = [repository_context(path, editing=False)]
    # CI is where a project's checks already live, so it is most of the answer
    # to what done means there. The general context lists workflows by name only.
    for workflow in sorted((path / ".github" / "workflows").glob("*.y*ml")):
        rel = workflow.relative_to(path)
        lines += ["", f"### {rel}", "", "```yaml", workflow.read_text().strip(), "```"]
    lines += ["", f"### Branch protection on `{branch}`", ""]
    if protection is None:
        lines.append("Not protected.")
    else:
        checks = (protection.get("required_status_checks") or {}).get("contexts") or []
        lines.append(
            "Protected. Required checks: " + (", ".join(checks) if checks else "none named") + "."
        )
    return "\n".join(lines)


def read_project(ws: Any, issues: Any, repo: str) -> Project:
    """The project as it stands on its default branch. Empty if it has no commits yet."""
    intent = describe_intent(issues.open_issues(repo))
    if not issues.branches(repo):
        return Project(intent=intent)
    path = ws.current()
    branch = issues.repository(repo)["default_branch"]
    existing_file = path / RECORD_PATH
    existing = load_raw(existing_file.read_text()) if existing_file.exists() else None
    return Project(
        repository=describe(path, branch=branch, protection=issues.branch_protection(repo, branch)),
        intent=intent,
        default_branch=branch,
        existing=existing,
    )


# --- the pull request ----------------------------------------------------------

ISSUE_TITLE = "Onboard this project: its record for the crew"
ISSUE_BODY = (
    "The crew reads what this project is for, how it is released, and what done "
    f"means here from `{RECORD_PATH}`. It is written with the Sponsor through "
    "`crew onboard`, and arrives as a pull request so the project reviews its own "
    "answers.\n\nSee mqucifer/crew#111."
)


def release_line(record: ProjectRecord) -> str:
    """How the project is released, as a sentence of its own, not an answer pasted into one."""
    release = record.intent.release
    if not release.deploys:
        return "the merge. Nothing is deployed."
    line = f"a deployment. Where: {release.where}"
    how = record.design.release_how if record.design else None
    return line + (f" How: {how}" if how else "")


ONBOARDING = RecordChange(
    issue_title=ISSUE_TITLE,
    issue_body=ISSUE_BODY,
    branch_summary="project-record",
    commit_message=(
        "chore(onboarding): record what the project is for, how it is released, and what done means"
    ),
    pr_title="chore: this project's onboarding record",
    updated_by="`crew onboard`",
    by="Product Owner",
)


def open_record_pr(
    ws: Any,
    issues: Any,
    repo: str,
    record: ProjectRecord,
    *,
    base: str,
    transcript: list[str] | None = None,
) -> str:
    """Propose the Sponsor's record to the project as a pull request. Returns its URL."""
    conversation = interview_record(transcript or [])
    intent = record.intent

    def body(number: int) -> str:
        return (
            f"Closes #{number}\n\n"
            f"Adds `{RECORD_PATH}`, this project's onboarding record, from an interview "
            "with the Sponsor through `crew onboard`.\n\n"
            f"- **Purpose:** {intent.scope.purpose}\n"
            f"- **Release:** {release_line(record)}\n"
            f"- **Done:** {intent.done.bar}\n\n"
            "## Verification\n\n"
            "The record was loaded by the crew's own record loader (mqucifer/crew#129) "
            "before it was written, and the Sponsor confirmed it in the interview." + conversation
        )

    return propose(
        ws, issues, repo, record, ONBOARDING, base=base, body=body, update_note=conversation
    )


def interview_record(transcript: list[str]) -> str:
    """The interview, folded, for a pull request: the evidence the record came from."""
    if not transcript:
        return ""
    return (
        "\n\n<details><summary>The interview</summary>\n\n"
        + "\n\n".join(transcript)
        + "\n\n</details>"
    )


# --- the Sponsor's side of the terminal ------------------------------------------

# Bold, in the markers readline needs around anything that takes no columns on
# screen. Without them it counts the escape codes as characters and loses track
# of the cursor once an answer wraps.
_BOLD, _PLAIN = "\001\033[1m\002", "\001\033[0m\002"


def terminal_ask(prompt: str) -> str | None:
    """One answer from the Sponsor, with ordinary line editing. None when they end it.

    Line editing is `readline`'s, and Python's `input()` only uses it once the
    module is imported. The prompt is passed to `input()` rather than printed
    first, so readline knows where the answer starts: printed separately, as
    Rich does, backspace cannot reach back past a wrapped line (#135).
    """
    import contextlib  # noqa: PLC0415

    with contextlib.suppress(ImportError):  # not on every platform; input() still works
        import readline  # noqa: F401, PLC0415

    try:
        return input(f"\n{_BOLD}{prompt}{_PLAIN} › ")
    except (EOFError, KeyboardInterrupt):
        return None
