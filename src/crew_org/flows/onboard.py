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

from crew_org.project import (
    FORMAT_VERSION,
    RECORD_PATH,
    REQUIRED,
    WHERE,
    Build,
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
    ("intent", "done", "checks"): (
        "Which commands must pass for a change to count as done, e.g. its test suite?"
    ),
    WHERE: "Where is it deployed?",
}
assert set(REQUIRED) | {WHERE} == set(QUESTIONS)

# Replies that end the interview early and leave a take-away file.
LATER = frozenset({"later", "/later", "stop", "/stop", "quit", "/quit"})
YES = frozenset({"y", "yes"})

# The take-away file's layout, in the record's own order.
SECTIONS: dict[str, type] = {"scope": Scope, "release": Release, "done": Done, "build": Build}


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


Turn = Callable[..., Any]
Ask = Callable[[str], "str | None"]
Tell = Callable[[str], None]


def interview(
    raw: dict[str, Any], *, repository: str, turn: Turn, ask: Ask, tell: Tell
) -> Interview:
    """Interview the Sponsor until the record is settled, or they stop.

    `turn` is the Product Owner (`interview_turn`); `ask` returns the Sponsor's
    reply, or None when they end the session; `tell` shows them something.
    A record that is already complete goes straight to the Sponsor's yes.
    """
    conversation: list[str] = []
    questions: dict[Path_, str] = {}
    reply: str | None = None

    def unsettled() -> tuple[dict[Path_, str], list[str]]:
        wanted = gaps(raw)
        return wanted, ([] if wanted else problems(raw))

    wanted, wrong = unsettled()
    while True:
        if wanted or wrong or reply is not None:
            try:
                result = turn(
                    repository=repository,
                    draft=yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
                    if raw
                    else "(nothing yet)",
                    missing={key(p): words for p, words in wanted.items()},
                    problems=wrong,
                    conversation="\n\n".join(conversation),
                )
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
                why = "stopped" if isinstance(exc, KeyboardInterrupt) else str(exc)
                return Interview(raw, _questions(gaps(raw), questions), interrupted=why)
            raw = merge(raw, {"intent": result.answers.model_dump(exclude_none=True)})
            wanted, wrong = unsettled()
            asked = {q.about: q.question for q in result.questions}
            questions = {p: asked.get(key(p)) or QUESTIONS[p] for p in wanted}
            said = result.say.strip()
            if wrong:
                said += "\n\nThese don't fit the record yet:\n" + "\n".join(f"- {p}" for p in wrong)
            if questions:
                said += "\n\n" + "\n".join(f"- {q}" for q in questions.values())
            tell(said)
            conversation.append(f"Product Owner: {said}")

        if not wanted and not wrong:
            tell(render(validate(raw)))
            reply = ask("Open the pull request with this record? yes, or say what to change")
            if reply is None or reply.strip().lower() in LATER:
                return Interview(raw)
            if reply.strip().lower() in YES:
                return Interview(raw, settled=True)
        else:
            reply = ask("Your answer (or 'later' to finish offline)")
            if reply is None or reply.strip().lower() in LATER:
                return Interview(raw, questions)
        conversation.append(f"Sponsor: {reply}")


def _questions(wanted: dict[Path_, str], asked: dict[Path_, str]) -> dict[Path_, str]:
    return {p: asked.get(p) or QUESTIONS[p] for p in wanted}


def takeaway(raw: dict[str, Any], questions: dict[Path_, str], *, repo: str, path: Path) -> str:
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
            placeholder = "[]" if "list" in str(info.annotation) else ""
            lines.append(f"    # {name}: {placeholder}".rstrip())
    priority = lookup(raw, ("intent", "priority"))
    if priority is not None:
        lines.append(f"  priority: {priority}")
    else:
        lines += ["  # Optional. Against other projects; 1 is first", "  # priority:"]
    if raw.get("learned"):
        lines += _indent(yaml.safe_dump({"learned": raw["learned"]}, allow_unicode=True), 0)
    return "\n".join(lines) + "\n"


def _indent(text: str, by: int) -> list[str]:
    return [" " * by + line for line in text.rstrip("\n").splitlines()]


# --- the project, as the Product Owner reads it --------------------------------


@dataclass
class Project:
    """What the Product Owner is shown, and what the interview starts from."""

    repository: str = ""
    default_branch: str | None = None
    existing: dict[str, Any] | None = None


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
    if not issues.branches(repo):
        return Project()
    path = ws.current()
    branch = issues.repository(repo)["default_branch"]
    existing_file = path / RECORD_PATH
    existing = load_raw(existing_file.read_text()) if existing_file.exists() else None
    return Project(
        repository=describe(path, branch=branch, protection=issues.branch_protection(repo, branch)),
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


def open_record_pr(ws: Any, issues: Any, repo: str, record: ProjectRecord, *, base: str) -> str:
    """Propose the record to the project as a pull request. Returns its URL.

    Run again, it updates the same branch and pull request rather than opening a
    second: the issue it closes is found by title, and the branch is named for it.
    """
    from crew_org.git_ops import branch_name  # noqa: PLC0415

    open_issue = next((i for i in issues.open_issues(repo) if i["title"] == ISSUE_TITLE), None)
    number = (open_issue or issues.create(repo, ISSUE_TITLE, ISSUE_BODY))["number"]
    branch = branch_name(number, "project-record", kind="chore")

    ws = ws.for_repo(repo)
    with ws:
        path = ws.open(branch)
        target = path / RECORD_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(record))
        ws.commit(
            "chore(onboarding): record what the project is for, how it is released, "
            f"and what done means\n\nRefs #{number}"
        )
        ws.push(force=True)

    existing = next((p for p in issues.open_pulls(repo) if p["head"]["ref"] == branch), None)
    if existing:
        return existing["html_url"]
    intent = record.intent
    body = (
        f"Closes #{number}\n\n"
        f"Adds `{RECORD_PATH}`, this project's onboarding record, from an interview "
        "with the Sponsor through `crew onboard`.\n\n"
        f"- **Purpose:** {intent.scope.purpose}\n"
        f"- **Release:** {record.release_is}"
        + (f", to {intent.release.where}" if intent.release.where else "")
        + "\n"
        f"- **Done:** {', '.join(f'`{c}`' for c in intent.done.checks)} must pass\n\n"
        "## Verification\n\n"
        "The record was loaded by the crew's own record loader (mqucifer/crew#129) "
        "before it was written, and the Sponsor confirmed it in the interview."
    )
    pull = issues.create_pull(
        repo, title="chore: this project's onboarding record", head=branch, base=base, body=body
    )
    return pull["html_url"]
