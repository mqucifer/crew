"""`crew diagnose`: the Senior Engineer reads the crew's code and says what's wrong (#9).

Diagnosis only, and that's enforced here rather than asked for:

- The role has no tools. It names files; this module reads them.
- Only files under `READABLE`, inside the crew's own checkout, and only ones
  the index offered, are read. A path that resolves anywhere else is refused.
- Nothing here writes to the crew's repository, runs git, or starts a process.
  The one write is the report, under `var/`, which git ignores.

A finding it gives must name a file it was shown and a line that file has. One
that doesn't is dropped and counted, not passed on: a finding that can't be
tied to code is a guess.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.crews.diagnosis_crew import MAX_FILES, Diagnosis, Finding

# What the role may read, relative to the crew's checkout.
READABLE = ("src/crew_org", "docs")
# The files it chose, shown whole with line numbers; past this they're named.
MAX_CHARS = 200_000
MAX_EVENTS = 40
DIAGNOSIS_LABEL = "diagnosis"


def crew_root() -> Path:
    """The crew's own checkout: where `src/crew_org` lives."""
    return Path(__file__).resolve().parents[3]


def readable(root: Path) -> list[str]:
    """Every file the role may ask for, repository-relative."""
    found: list[str] = []
    for top in READABLE:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            wanted = path.suffix in {".py", ".md", ".yaml", ".yml"}
            if wanted and path.is_file() and "__pycache__" not in path.parts:
                found.append(path.relative_to(root).as_posix())
    return found


def _outline(path: Path) -> str:
    """A Python file's top-level definitions, for the index."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return ""
    names = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    return ", ".join(names[:40]) + (" …" if len(names) > 40 else "")


def source_index(root: Path) -> str:
    """The map: each readable file, and for Python its top-level definitions."""
    lines = []
    for rel in readable(root):
        outline = _outline(root / rel) if rel.endswith(".py") else ""
        lines.append(f"- `{rel}`" + (f": {outline}" if outline else ""))
    return "\n".join(lines)


def resolve(root: Path, rel: str, offered: set[str]) -> Path | None:
    """The file to read, or None: only an offered file, inside a readable folder."""
    if rel not in offered:
        return None
    target = (root / rel).resolve()
    tops = [(root / top).resolve() for top in READABLE]
    if not any(target.is_relative_to(top) for top in tops) or not target.is_file():
        return None
    return target


@dataclass
class Read:
    text: str = ""
    shown: dict[str, int] = field(default_factory=dict)  # path -> line count
    refused: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)


def read_chosen(root: Path, chosen: list[str], offered: set[str]) -> Read:
    """The chosen files, whole, with line numbers, within the budget."""
    out = Read()
    parts: list[str] = []
    budget = MAX_CHARS
    for rel in list(dict.fromkeys(chosen))[:MAX_FILES]:
        target = resolve(root, rel, offered)
        if target is None:
            out.refused.append(rel)
            continue
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        numbered = "\n".join(f"{i:5} {line}" for i, line in enumerate(lines, 1))
        if len(numbered) > budget:
            out.left_out.append(rel)
            continue
        budget -= len(numbered)
        parts += [f"### `{rel}`", "", "```", numbered, "```", ""]
        out.shown[rel] = len(lines)
    if out.left_out:
        parts.append("Not shown, too large for what's left: " + ", ".join(out.left_out))
    out.text = "\n".join(parts)
    return out


def failure_of(events_dir: Path, issues: Any, repo: str, card: int) -> str:
    """What the crew recorded about the card: its events, and the crew's comments on it."""
    lines = [f"## {repo}#{card}"]
    recorded: list[dict[str, Any]] = []
    for path in sorted(events_dir.glob("*.jsonl")) if events_dir.is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("card") == card and event.get("kind") not in (
                "llm.started",
                "llm.finished",
                "task.started",
                "task.completed",
            ):
                recorded.append(event)
    recorded.sort(key=lambda e: e.get("at") or "")
    if recorded:
        lines += ["", "### What the event log recorded, latest last", ""]
        for event in recorded[-MAX_EVENTS:]:
            detail = json.dumps(event.get("detail") or {})[:400]
            lines.append(
                f"- {str(event.get('at'))[:19]} {event.get('kind')} {event.get('role') or ''}: "
                f"{event.get('summary') or ''} {detail}"
            )
    try:
        comments = issues.comments(repo, card)
    except Exception:  # noqa: BLE001
        comments = []
    crew = [c.get("body") or "" for c in comments if "<!-- crew:" in (c.get("body") or "")]
    if crew:
        lines += ["", "### The crew's latest comments on the card", ""]
        lines += [body[:2000] for body in crew[-4:]]
    return "\n".join(lines)


@dataclass
class Report:
    diagnosis: Diagnosis | None = None
    read: Read = field(default_factory=Read)
    why: str = ""
    findings: list[Finding] = field(default_factory=list)
    dropped: list[Finding] = field(default_factory=list)


def run_diagnosis(root: Path, failure: str, *, choose, diagnose) -> Report:
    """Map, then read, then diagnose. `choose` and `diagnose` are the role's two calls."""
    offered = set(readable(root))
    request = choose(failure=failure, index=source_index(root))
    report = Report(why=request.why)
    report.read = read_chosen(root, request.files, offered)
    if not report.read.shown:
        return report
    report.diagnosis = diagnose(failure=failure, files=report.read.text, why=request.why)
    for finding in report.diagnosis.findings:
        count = report.read.shown.get(finding.file)
        if count is not None and 1 <= finding.line <= count:
            report.findings.append(finding)
        else:
            report.dropped.append(finding)
    return report


def render(report: Report, *, repo: str, card: int) -> str:
    """The report, for the terminal and the file under `var/diagnoses`."""
    lines = [f"# Diagnosis of {repo}#{card}", ""]
    shown = ", ".join(f"`{p}`" for p in report.read.shown) or "nothing"
    lines += [f"Read: {shown}. Chosen because: {report.why}", ""]
    if report.read.refused:
        lines += [
            "Refused (not offered, or outside what it may read): " + ", ".join(report.read.refused),
            "",
        ]
    if report.diagnosis is None:
        return "\n".join([*lines, "No diagnosis: none of the files it asked for could be read."])
    lines += [report.diagnosis.summary, ""]
    for n, f in enumerate(report.findings, 1):
        lines += [
            f"## {n}. `{f.file}:{f.line}`",
            "",
            f"**Wrong:** {f.wrong}",
            "",
            f"**Change:** {f.change}",
            "",
            f"**Evidence:** {f.evidence}",
            "",
        ]
    if not report.findings:
        lines += ["No finding tied to a line it was shown.", ""]
    if report.dropped:
        lines += [
            f"{len(report.dropped)} finding(s) named a file or line it wasn't shown, and were "
            "left out.",
            "",
        ]
    if report.diagnosis.unsure:
        lines += [f"**To be sure, it would need:** {report.diagnosis.unsure}"]
    return "\n".join(lines).rstrip() + "\n"


def file_finding(
    issues: Any, crew_repo: str, finding: Finding, *, repo: str, card: int
) -> dict[str, Any]:
    """An accepted finding, as a crew issue carrying it as its body (#9, criterion 4)."""
    issues.ensure_label(
        crew_repo, DIAGNOSIS_LABEL, color="5319e7", description="Found by crew diagnose"
    )
    body = (
        f"Found by `crew diagnose` on {repo}#{card}, by the Senior Engineer, and accepted by "
        "the Sponsor.\n\n"
        f"**Where:** `{finding.file}:{finding.line}`\n\n"
        f"**What's wrong:** {finding.wrong}\n\n"
        f"**Change:** {finding.change}\n\n"
        f"**Evidence:** {finding.evidence}\n\n"
        "Operator work in the crew repo, outside `delivery.repos`."
    )
    title = finding.wrong.split(". ")[0].rstrip(".")[:120]
    return issues.create(crew_repo, title, body, labels=[DIAGNOSIS_LABEL])
