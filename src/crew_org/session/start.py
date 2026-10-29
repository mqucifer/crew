"""What a session starts with (crew#778, decision 4).

The story with the tests it declares; how the project works, from its record; a map
of the tree; the graph's picks as hints, paths only; and the rules for the tools and
`done`. Everything else the session reads for itself. Each part's size is measured:
the replay's starts were 14.7k to 16.4k characters, against about 50k for the
one-shot prompt.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from crew_org.calls import role_spec, who
from crew_org.profiles import profile_for
from crew_org.project import RECORD_PATH, ProjectRecordError, _uses, brief, read_record
from crew_org.session.tools import SKIPPED, VIEW_LINES
from crew_org.tools.ast_edit import definitions
from crew_org.tools.repo_context import _close_to, _is_test, select_files

HINTS = 15
_TEST_ID = re.compile(r"(tests/[\w/]+\.py)(?:::([\w\[\]-]+(?:::[\w\[\]-]+)?))?")
_TEST_NAME = re.compile(r"\b(test_\w+)\b")

RULES = """\
## How this session works

You work on the story by calling tools, one or more per turn, until you call `done` or
`blocked`. Nothing is shown to you that you didn't ask for, beyond this start.

- **See and find:** `view` shows part of a file with line numbers ({view_lines} lines \
at most, or one Python definition by name). `search` finds text, `.package/` included. \
`find` and `tests_for` are Python only.
- **Change:** `replace_text`, `replace_definition` and `write_file` apply at once to \
your branch; each says whether the file still parses and lints. `delete_file` removes a \
file. Change only what the story needs.
- **Check:** `run` runs the tests you name, the project's whole checks (`suite`), or \
lint, in the project's container, which has no network. It's feedback, not the verdict.
- **Tool output** stays in the conversation whole, unless it grows very long.
- **`done`** is for when the work is finished and you've seen the checks pass. Its \
description says what the crew checks on your branch after it.
- **`blocked`** is for when the work can't be finished from what's here: a file that \
isn't in the repository, a fact the story doesn't give, or a choice nobody has made. Say \
which, in your own words; it goes back to whoever wrote the story.
- **History:** `git` runs one git command line on your branch: `status`, `diff`, `log`, \
`show`, `blame` and `grep` read, and `restore --source=<ref> -- <paths>` is an edit. `base` \
is the commit your branch started from. The crew commits your work.
- There's no shell or network. The session ends if the same first error in the same test \
comes back on 3 runs with edits between, or after 3 hours of model time.
"""


def system_text(role: str = "developer", step: str | None = None) -> str:
    """Who the role is, without the answer forms' rules: the start tells the session's.
    A `step` of the role's says its own goal and backstory over the role's.

    The constitution's Definition of Done, audit and guidelines (10k characters) are
    left out: `done`'s own description says what the gate returns a branch for.
    """
    spec = role_spec(role, step)
    return f"{who(spec)}\n\nYour goal: {spec['goal'].strip()}"


def _size(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def project_map(root: Path) -> str:
    """The tree: each file's size, and a Python file's top-level names."""
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or SKIPPED & set(rel.parts) or rel.parts[0].startswith(".git"):
            continue
        size = path.stat().st_size
        names = ""
        if path.suffix == ".py" and not _is_test(rel):
            source = path.read_text(encoding="utf-8", errors="ignore")
            top = [n for n in definitions(source) if "." not in n]
            names = ": " + ", ".join(top[:12]) + (f", +{len(top) - 12}" if len(top) > 12 else "")
        elif path.suffix == ".py":
            tests = [n for n in definitions(path.read_text(errors="ignore")) if "test" in n]
            names = f": {len(tests)} tests"
        lines.append(f"{rel.as_posix()} ({_size(size)}){names}")
    return "\n".join(lines)


def declared_tests(root: Path, story: str) -> tuple[list[str], str]:
    """The tests the story names, and their sources where they exist at the base."""
    named: dict[str, set[str]] = {}
    for path, test in _TEST_ID.findall(story):
        named.setdefault(path, set())
        if test:
            named[path].add(test.split("::")[-1].split("[")[0])
    bare = set(_TEST_NAME.findall(story)) - {n for v in named.values() for n in v}
    tests = sorted(root.glob("tests/**/*.py"))
    for name in bare:
        for p in tests:
            rel = p.relative_to(root).as_posix()
            if re.search(
                rf"^\s*(async\s+)?def {re.escape(name)}\b", p.read_text(errors="ignore"), re.M
            ):
                named.setdefault(rel, set()).add(name)
    shown, ids = [], []
    for rel, names in sorted(named.items()):
        target = root / rel
        if not target.is_file():
            ids.append(f"{rel} (new: not in the repository yet)")
            continue
        source = target.read_text(encoding="utf-8", errors="ignore")
        defs = definitions(source)
        lines = source.splitlines()
        for name in sorted(names):
            node = defs.get(name) or next(
                (n for q, n in defs.items() if q.split(".")[-1] == name), None
            )
            if node is None:
                ids.append(f"{rel}::{name} (new: not defined yet)")
                continue
            ids.append(f"{rel}::{name}")
            start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
            body = "\n".join(lines[start - 1 : node.end_lineno])
            shown.append(f"`{rel}::{name}`, line {start}:\n```python\n{body}\n```")
        if not names:
            ids.append(f"{rel} (named; no test in it given)")
    return ids, "\n\n".join(shown)


def hints(root: Path, story: str, coverage: Any, *, named_in: str = "the story") -> list[str]:
    """The graph's picks: each path with why it was picked, in what `named_in` names:
    the story, or a refinement step's epic (crew#807)."""
    chosen, _ = select_files(root, story)
    why: dict[str, str] = {}
    for rel in chosen:
        name = Path(rel).name
        if rel in story or (name != "__init__.py" and name in story):
            why[rel] = f"named in {named_in}"
    for rel in chosen:
        if rel in why:
            continue
        paired = [c for c in chosen if profile_for(c).paired_test(c) == rel]
        if paired:
            why[rel] = f"the test file of `{paired[0]}`"
            continue
        if rel.endswith(".py"):
            words = set(re.findall(r"`([A-Za-z_]\w*)", story)) | set(
                re.findall(r"\b(\w{4,})\(", story)
            )
            names = [
                n
                for n in definitions((root / rel).read_text(errors="ignore"))
                if n.split(".")[0] in words
            ]
            if names:
                why[rel] = f"defines `{names[0]}`, which {named_in} names"
                continue
        why[rel] = f"named in {named_in}'s words"
    code = [c for c in chosen if c.endswith(".py") and not _is_test(Path(c))]
    for rel in _close_to(root, code, set(chosen), coverage):
        if rel in why:
            continue
        if "::" in rel:
            path = rel.partition("::")[0]
            if path not in why:
                why[path] = "a merged test that runs the named code"
        else:
            why[rel] = "imports the named code"
    return [f"- `{p}`: {w}" for p, w in list(why.items())[:HINTS]]


NOTES = "@notes/"


def how_it_works(record: Any, root: Path) -> tuple[str, dict[str, str]]:
    """The record as agents are shown it, with each producer's notes as a file to view.

    A producer's usage notes and what its pinned release provides are long (29k
    characters for crew-presentation) and not every story needs them: the start names
    them, and `view` reads them.
    """
    if record is None:
        return "## The project\n\n(no record)", {}
    uses = record.intent.scope.uses
    scope = record.intent.scope.model_copy(update={"uses": []})
    bare = record.model_copy(update={"intent": record.intent.model_copy(update={"scope": scope})})
    notes = {f"{NOTES}{u.repo.replace('/', '-')}.md": "\n".join(_uses([u], root)) for u in uses}
    # How it's released and what CI runs aren't how to work on a story here.
    text = "\n".join(
        line
        for line in brief(bare).splitlines()
        if not line.startswith(("- release how:", "- ci checks:"))
    )
    text += f"\n\nHow it's released, and what its CI runs: `{RECORD_PATH}`."
    if notes:
        text += "\n\n**What the producers this project uses say, and what they provide:** "
        text += "; ".join(f"`{p}` ({_size(len(t))})" for p, t in notes.items())
        text += ". View them as files."
    return text, notes


def start(
    root: Path,
    story: str,
    coverage: Any,
    instead_of_hints: str | None = None,
    work: str = "story",
) -> tuple[str, dict[str, int], dict[str, str]]:
    """The start, each part's size in characters, and the notes it names. `work` is
    what the session works, a story or a role's Task: a Task's session read "## The
    story" for QA's own test (crew#944).

    `instead_of_hints` goes where the hints would, as it's written: for a session
    that starts from a branch with work on it, such as review's findings and the last
    `done` (P2) or the checkpoint's message (P3), in place of the graph's picks.
    """
    try:
        record = read_record(root)
    except ProjectRecordError:
        record = None
    project, notes = how_it_works(record, root)
    ids, sources = declared_tests(root, story)
    parts = {
        "story": f"## The {work}\n\n" + story,
        "declared_tests": f"## The tests the {work} declares\n\n"
        + ("\n".join(f"- `{i}`" for i in ids) or "- (none named)")
        + (f"\n\nAt the base commit:\n\n{sources}" if sources else ""),
        "project": project,
        "map": "## The map: every file, its size, and a Python file's top-level names\n\n"
        + project_map(root),
        **(
            {"instead_of_hints": instead_of_hints}
            if instead_of_hints is not None
            else {
                "hints": "## Likely relevant, as hints (paths only; view what you need)\n\n"
                + ("\n".join(hints(root, story, coverage)) or "- (none)")
            }
        ),
        "rules": RULES.format(view_lines=VIEW_LINES),
    }
    sizes = {k: len(v) for k, v in parts.items()}
    sizes["total"] = sum(sizes.values())
    return "\n\n".join(parts.values()), sizes, notes
