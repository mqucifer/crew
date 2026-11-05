"""A project's changelog: towncrier's fragments, which stories write, and Keep a
Changelog's `CHANGELOG.md`, which only a release writes (crew#1010).

**The standards:** towncrier (https://towncrier.readthedocs.io) for the fragments and
the build, Keep a Changelog 1.1.0 for the file. A story or a bug adds one file,
`changes/<issue>.<kind>.md` (`+<name>.<kind>.md` with no issue), whose text is its
entry, `<kind>` a Keep a Changelog section in lower case. A release builds its version's
section from them, as `towncrier build` does, and removes them. Stories used to edit the
one `## [Unreleased]` section, so it conflicted in more rebuilds than any other file, and
a release rewrote it while other stories were still in flight.

**The switch-over loses nothing:** a branch from before it wrote its entry under
`## [Unreleased]`. A release collects those beside the fragments, by their section, and
the heading goes, so the first release after the switch carries both.

Each project declares the same in its `[tool.towncrier]`, which this reads where it says
otherwise: the folder, the file, the line a release writes after, and how an issue is
referenced. The crew builds the section itself, so no project's container needs
towncrier, and the old `[Unreleased]` entries are collected too.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# Keep a Changelog's sections, in its order, by the fragment kind that names each.
KINDS = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "fixed": "Fixed",
    "security": "Security",
}
DIRECTORY = "changes"
FILENAME = "CHANGELOG.md"
# towncrier's own default: the release's section goes right after this line.
START = "<!-- towncrier release notes start -->"

_FRAGMENT = re.compile(
    r"^(?P<issue>\+?[^./]+)\.(?P<kind>" + "|".join(KINDS) + r")(?:\.(?P<n>\d+))?(?:\.md)?$"
)
_HEADING = re.compile(r"^##\s+(.*?)\s*$")
_SECTION = re.compile(r"^###\s+(.+?)\s*$")
_UNRELEASED = re.compile(r"^##\s+\[?unreleased\]?", re.IGNORECASE)


@dataclass(frozen=True)
class Config:
    """What a project's `[tool.towncrier]` says, with towncrier's defaults for the rest."""

    directory: str = DIRECTORY
    filename: str = FILENAME
    start: str = START
    # towncrier's `issue_format`, `{issue}` its number: `mqucifer/sprint-metrics#{issue}`.
    issue_format: str = ""


def config(pyproject: str | None) -> Config:
    """A project's towncrier settings, from its `pyproject.toml`'s text."""
    try:
        said = tomllib.loads(pyproject or "").get("tool", {}).get("towncrier", {})
    except tomllib.TOMLDecodeError:
        said = {}
    return Config(
        directory=str(said.get("directory") or DIRECTORY).strip("/"),
        filename=str(said.get("filename") or FILENAME),
        start=str(said.get("start_string") or START).strip(),
        issue_format=str(said.get("issue_format") or ""),
    )


def config_in(root: Path) -> Config:
    path = root / "pyproject.toml"
    return config(path.read_text(encoding="utf-8") if path.is_file() else None)


@dataclass(frozen=True)
class Fragment:
    path: str
    # Its issue, or "" for one named `+<name>`.
    issue: str
    kind: str
    text: str = ""


def parse(path: str, directory: str = DIRECTORY) -> Fragment | None:
    """The fragment a path names, or None: a file in `directory` itself (a README or a
    `.gitkeep` there isn't one) whose name gives a kind."""
    pure = PurePosixPath(path)
    if str(pure.parent) != directory:
        return None
    found = _FRAGMENT.match(pure.name)
    if found is None:
        return None
    issue = found.group("issue")
    return Fragment(path, "" if issue.startswith("+") else issue, found.group("kind"))


def fragments_in(root: Path, directory: str = DIRECTORY) -> list[Fragment]:
    """The fragments in a worktree, each with its text."""
    folder = root / directory
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        named = parse(f"{directory}/{path.name}", directory) if path.is_file() else None
        if named is not None:
            text = path.read_text(encoding="utf-8", errors="replace")
            found.append(Fragment(named.path, named.issue, named.kind, text))
    return found


def unreleased(text: str | None) -> list[tuple[str, str]]:
    """Each entry under a `## [Unreleased]` heading, however many merges left, as
    (its section, the line)."""
    found: list[tuple[str, str]] = []
    inside, section = False, ""
    for line in (text or "").splitlines():
        if _HEADING.match(line):
            inside, section = bool(_UNRELEASED.match(line)), ""
            continue
        sub = _SECTION.match(line)
        if sub:
            section = sub.group(1)
            continue
        if inside and line.strip().startswith(("- ", "* ")):
            found.append((section, line.strip()))
    return found


def entry(fragment: Fragment, issue_format: str = "") -> str:
    """A fragment's line in the release's section, as towncrier writes it: its text, and
    its issue where the project says how to name one."""
    lines = [ln.rstrip() for ln in fragment.text.strip().splitlines()]
    first = (lines[0] if lines else "").removeprefix("- ").removeprefix("* ")
    body = "\n".join([first, *(f"  {ln.strip()}" if ln.strip() else "" for ln in lines[1:])])
    if fragment.issue and issue_format:
        body += f" ({issue_format.format(issue=fragment.issue)})"
    return f"- {body}"


def _order(fragment: Fragment) -> tuple[int, int, str]:
    # towncrier's: by issue number, then other issues, then those with none.
    if not fragment.issue:
        return (2, 0, fragment.path)
    if fragment.issue.isdigit():
        return (0, int(fragment.issue), fragment.path)
    return (1, 0, fragment.issue)


@dataclass
class Built:
    text: str
    # The fragments the section was built from, which the release removes.
    used: list[Fragment] = field(default_factory=list)
    # How many `[Unreleased]` entries it collected beside them.
    carried: int = 0
    section: str = ""


def build(
    text: str,
    fragments: list[Fragment],
    version: str,
    date: str,
    cfg: Config | None = None,
) -> Built:
    """`text` with the version's section built from `fragments` and every `[Unreleased]`
    entry, those headings gone. Entries keep Keep a Changelog's section order; one under
    a subheading it doesn't name keeps its own, after them. Written after the project's
    start line, or where `[Unreleased]` was, or above the newest version."""
    cfg = cfg or Config()
    by_section: dict[str, list[str]] = {}
    carried = unreleased(text)
    for section, line in carried:
        name = KINDS.get(section.strip().lower(), section.strip() or "Changed")
        by_section.setdefault(name, []).append(line)
    for fragment in sorted(fragments, key=_order):
        by_section.setdefault(KINDS[fragment.kind], []).append(entry(fragment, cfg.issue_format))
    names = [n for n in KINDS.values() if n in by_section]
    names += [n for n in by_section if n not in names]
    section = f"## [{version}] - {date}\n\n" + "".join(
        f"### {name}\n\n" + "\n".join(by_section[name]) + "\n\n" for name in names
    )

    kept: list[str] = []
    at: int | None = None
    dropping = False
    for line in text.splitlines():
        if _HEADING.match(line):
            dropping = bool(_UNRELEASED.match(line))
            if dropping and at is None:
                at = len(kept)
        if not dropping:
            kept.append(line)
    start = next((i + 1 for i, ln in enumerate(kept) if ln.strip() == cfg.start), None)
    if start is not None:
        at = start
    elif at is None:
        at = next((i for i, ln in enumerate(kept) if ln.startswith("## [")), len(kept))
    head = "\n".join(kept[:at]).rstrip("\n")
    tail = "\n".join(kept[at:]).strip("\n")
    joined = (head + "\n\n" if head else "") + section + (tail + "\n" if tail else "")
    return Built(joined.rstrip("\n") + "\n", list(fragments), len(carried), section)
