"""What a repository looks like, for any agent that needs to know.

Delivery was the first to need it and for a while the only one, so it lived in
the delivery flow. Refinement needs it too: a Business Analyst that cannot see
the code writes acceptance criteria against a product it is imagining. crew#6
was split into three stories addressed to "the audit trail entry", with no way
to know that the board already had an Owner Agent field sitting empty.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Files worth showing an agent so its work fits in with what is there.
CONTEXT_FILES = ("pyproject.toml", "README.md")

# Every other text file is shown whole too (#140). A non-Python file is changed
# by quoting the text it replaces, and a quote can only be copied from text the
# Developer was shown: a CI workflow listed by name alone is one it would have to
# reconstruct from memory, and a reconstruction doesn't match.
TEXT_SUFFIXES = frozenset(
    {".toml", ".md", ".yml", ".yaml", ".cfg", ".ini", ".json", ".txt"}
    # Source in a language other than Python, shown whole for the same reason
    # (#404). A part in another language is edited by quoting its text, and on
    # the first live run the static-site fixture's page and Playwright test were
    # listed by name only: every answer asked to see them and changed nothing.
    | {".html", ".htm", ".css", ".scss", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}
    | {".vue", ".svelte", ".svg", ".xml", ".sh", ".sql", ".scad"}
    | {".go", ".rs", ".java", ".kt", ".rb", ".c", ".h", ".cpp", ".hpp", ".swift"}
)
# Text files named for what they are, with no suffix to say so. sprint-metrics#308
# asked to see its Dockerfile three times and was shown everything else: the
# story was to change it, and the pull request changed only a test (#335).
TEXT_NAMES = frozenset({"Dockerfile", "Containerfile", "Makefile", "Procfile", ".dockerignore"})


def is_text(path: Path) -> bool:
    """A file shown whole: by its suffix, or by a name like `Dockerfile` or `Dockerfile.dev`."""
    name = path.name
    return (
        path.suffix in TEXT_SUFFIXES
        or name in TEXT_NAMES
        or name.startswith(("Dockerfile.", "Containerfile."))
        or name.endswith((".dockerfile", ".containerfile"))
    )


# The whole repository, on every attempt. The window is 262,144 tokens and the
# pilot repo is 17,195 characters — under 2% of it. Showing a developer the
# names of three functions and asking it to honour behaviour it has never read
# is not a context budget, it is a blindfold, and every rule since #8 has been
# an attempt to describe in prose what one `cat` would have shown.
#
# The ceiling is a guard against a tree that genuinely does not fit, not a
# budget. It drops whole files and names them: a file cut mid-function is worse
# than a file left out, because nothing in it marks where it stopped.
#
# 200,000 was set when the only repository anyone read was the 22,000-character
# pilot. The crew's own repository is 244,159 characters, so the first time
# refinement read it the guard fired in ordinary work and dropped
# `github_project.py` — the file defining the `Owner Agent` field the epic
# being refined was about. A guard that fires in normal work is a budget.
#
# The arithmetic: 600,000 characters is roughly 150,000 tokens, leaving about
# 100,000 of the 262,144-token window for the story, the standing instructions,
# a failure report of up to MAX_FAILURE_REPORT_CHARS, and the generation.
CONTEXT_CHAR_CEILING = 600_000
# Tool droppings. They tell the Developer nothing and they are not free: the
# file listing is capped, so eleven cache entries are eleven real files the
# model never gets shown.
IGNORED_DIRS = frozenset(
    {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
    # A JavaScript part's installed packages and Playwright's reports (#404).
    | {"node_modules", "test-results", "playwright-report"}
)


def repository_context(worktree: Path, *, editing: bool = True, bodies: bool = True) -> str:
    """What the repository looks like, and what each file defines.

    Editing works by name, so the names are the context that matters. Listing
    them is a few hundred tokens that stay identical between attempts, where
    dumping file bodies was thousands that changed every time — which both
    poisoned the prefix cache and left the model guessing at targets it had
    never been shown. Both EDIT failures on story #8 were invented names.

    The names carry their signatures, because a name alone does not say that
    `is_completed` is a property or that `format_performance_table` takes
    `(cards, wip_limits)`. Story #9 changed both and broke thirteen tests, and
    it had never been shown either contract — it was refused for violating a
    rule nobody had told it. Signatures cost a few tokens more and are just as
    stable between attempts, so the cacheable prefix is unaffected.

    The bodies follow, in full, on every attempt. They used to appear only on a
    repair, on a prefix-cache argument: bodies churn between attempts where
    names do not. That optimised the wrong thing. Story #11 rewrote `main` —
    dropped its return type, its docstring and its stdin default — and was
    refused for breaking a contract that lives in a body it had never been
    shown. The signature line it did get, `main(argv: Sequence[str] | None =
    None) -> int`, carries none of that. Cache hits are cheaper than a story
    that never lands.

    `editing=False` leaves out the rules about how to address a definition and
    what may not be reshaped. Refinement reads this to decide what work should
    exist; it is not editing anything, and instructions for a job it is not
    doing are noise competing with the ones it must follow.
    """
    from crew_org.profiles import profile_for  # noqa: PLC0415

    paths = sorted(
        p for p in worktree.rglob("*") if p.is_file() and not (IGNORED_DIRS & set(p.parts))
    )

    lines = ["### Files and what they define", ""]
    for path in paths:
        rel = path.relative_to(worktree)
        if path.suffix == ".py":
            signatures = profile_for(rel.as_posix()).definitions(
                path.read_text(encoding="utf-8", errors="ignore")
            )
            defined = (
                ", ".join(f"{name}{sig}" for name, sig in sorted(signatures.items()))
                if signatures
                else "nothing at top level"
            )
            lines.append(f"- `{rel}` — {defined}")
        else:
            lines.append(f"- `{rel}`")

    if editing:
        lines += [
            "",
            "Target an existing definition by the names above. `Class.method` for a "
            "method. A file is not a definition: to change what a package exports, "
            "edit `__all__`, not `__init__`.",
            "",
            "The signatures above are what merged code already calls. Changing a "
            "public one is refused — not as a matter of taste, but because callers "
            "you are not editing would break. Everything else is yours: bodies, "
            "private helpers, module internals, and new definitions of whatever "
            "shape the story needs. Design it the way it should be designed.",
        ]

    for name in CONTEXT_FILES:
        target = worktree / name
        if target.exists():
            lines += ["", f"### {name}", "", "```", target.read_text().strip(), "```"]

    if not bodies:
        # The map alone, and the always-shown files: a focused context adds the
        # bodies the work names (#231).
        return "\n".join(lines)

    budget = CONTEXT_CHAR_CEILING
    omitted: list[str] = []
    for target in paths:
        rel = target.relative_to(worktree)
        if not is_text(target) or str(rel) in CONTEXT_FILES:
            continue
        body = target.read_text(encoding="utf-8", errors="ignore")
        if len(body) > budget:
            omitted.append(str(rel))
            continue
        budget -= len(body)
        lines += ["", f"### {rel}", "", "```", body.strip(), "```"]

    lines += ["", "### Current source", ""]
    tests_named_only = False
    for target in sorted(worktree.glob("src/**/*.py")) + sorted(worktree.glob("tests/**/*.py")):
        if IGNORED_DIRS & set(target.parts):
            continue
        rel = target.relative_to(worktree)
        # A role deciding what to build needs the code, and the tests only by
        # name, which the index above lists. On the Architect's design note for
        # sprint-metrics#59, test bodies were 123k of a 225k-character prompt
        # (#230). The Developer, which edits tests, still sees them whole.
        if not editing and _is_test(rel):
            tests_named_only = True
            continue
        body = target.read_text(encoding="utf-8", errors="ignore")
        if len(body) > budget:
            omitted.append(str(rel))
            continue
        budget -= len(body)
        lines += [f"`{rel}`", "", "```python", body.strip(), "```", ""]
    if tests_named_only:
        lines += [
            "Test files are shown above by the tests they define, not in full.",
            "",
        ]
    if omitted:
        lines += [
            "These files exist and are not shown, because the tree did not fit: "
            + ", ".join(f"`{name}`" for name in omitted)
            + ". Treat anything they define as code you cannot see.",
            "",
        ]

    return "\n".join(lines)


# Above this, the Developer is shown a focused context rather than everything
# (#231). Everything was right for the 17k-character pilot; at sprint-metrics'
# ~400k characters (#268: ~100k tokens, over half of it the whole test suite)
# it's mostly noise, and long prompts are where the model answers nothing
# (#312: none under 50k tokens in 99 calls, 62% empty over 90k served warm).
# ~40k tokens: small repositories keep the full view, whose reasons (#9, #11,
# #140) are cheapest to honour there.
FOCUS_ABOVE_CHARS = 160_000
_BACKTICKED = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`")
_CALLED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,})\(")
# The verb a definition's name starts with, dropped to read it as a noun phrase:
# `calculate_first_attempt_rate` is what a story calls "first-attempt rate".
_VERB_PREFIX = re.compile(r"^(calculate|compute|format|render|parse|load|get|build|make|to|as|is)_")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _phrase(text: str) -> str:
    """Lower case, every run of non-alphanumerics one space: hyphen, underscore and space alike."""
    return f" {_NOT_WORD.sub(' ', text.lower()).strip()} "


# How much of what the selection read is kept with `files.shown` (crew#449): the
# story and its verdicts fit; a whole failure report is cut.
SELECTION_TEXT_CHARS = 20_000


@dataclass
class Focus:
    """What a focused context showed, for the event log and for measuring #231."""

    focused: bool = False
    shown: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    chars: int = 0


def _files(worktree: Path) -> list[Path]:
    return sorted(
        p for p in worktree.rglob("*") if p.is_file() and not (IGNORED_DIRS & set(p.parts))
    )


def _module_of(rel: Path) -> str:
    """`src/pkg/mod.py` -> `pkg.mod`; the dotted name imports use."""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def select_files(
    worktree: Path, about: str, extra: Iterable[str] = ()
) -> tuple[list[str], list[str]]:
    """The files a piece of work names (#231).

    `about` is what's written about this piece of work: the story and its
    criteria, earlier verdicts, the last failure. A file is chosen when it's
    named there by path, by file name (when that's unique), by module, or by a
    definition at most two files define, plus each chosen module's own test
    file. The epic's design note chooses nothing: it describes the whole epic,
    and on sprint-metrics#268 it named every source file. It's still shown, as
    text. Imports aren't followed; the map carries every signature, and the
    Developer asks for a body it needs. `extra` is what it asked for by path.
    Returns (chosen, unknown asks).
    """
    from crew_org.profiles import profile_for  # noqa: PLC0415

    rels = {str(p.relative_to(worktree)): p for p in _files(worktree)}
    by_name: dict[str, list[str]] = {}
    for rel in rels:
        by_name.setdefault(Path(rel).name, []).append(rel)

    def named_in(text: str) -> set[str]:
        found = {rel for rel in rels if rel in text}
        found |= {
            owners[0]
            for name, owners in by_name.items()
            if len(owners) == 1 and name != "__init__.py" and name in text
        }
        return found

    chosen = named_in(about)
    for rel in rels:
        module = _module_of(Path(rel)) if rel.endswith(".py") else ""
        if "." in module and module in about:
            chosen.add(rel)

    defined: dict[str, list[str]] = {}
    for rel, path in rels.items():
        if rel.endswith(".py") and not _is_test(Path(rel)):
            source = path.read_text(encoding="utf-8", errors="ignore")
            for name in profile_for(rel).definitions(source):
                defined.setdefault(name.split(".")[0], []).append(rel)
    for word in set(_BACKTICKED.findall(about)) | set(_CALLED.findall(about)):
        owners = sorted(set(defined.get(word.split(".")[0], [])))
        if 0 < len(owners) <= 2:
            chosen |= set(owners)
    # Named in prose: "first-attempt rate" is `calculate_first_attempt_rate`
    # (#231). sprint-metrics#281 documented three computations and was shown
    # only the doc, so it restated its criteria without seeing the code they
    # describe. Multi-word names only: a single word like `report` matches any
    # story.
    prose = _phrase(about)
    for name, owners in defined.items():
        words = _VERB_PREFIX.sub("", name.lstrip("_").lower()).split("_")
        if len(words) >= 2 and f" {' '.join(words)} " in prose and len(set(owners)) <= 2:
            chosen |= set(owners)

    unknown: list[str] = []
    for ask in extra:
        ask = ask.strip().removeprefix("./")
        if ask in rels:
            chosen.add(ask)
        else:
            unknown.append(ask)

    for rel in list(chosen):
        paired = profile_for(rel).paired_test(rel)
        if paired in rels:
            chosen.add(paired)
    return sorted(chosen), unknown


def focused_context(
    worktree: Path,
    *,
    about: str,
    extra: Iterable[str] = (),
    above: int | None = None,
    editing: bool = True,
) -> tuple[str, Focus]:
    """The repository for a piece of work: all of it when small, the named part when not (#231).

    `editing` is False for a role that decides rather than edits (refinement):
    the rules for editing by name are noise to it.
    """
    full = repository_context(worktree, editing=editing)
    if len(full) <= (FOCUS_ABOVE_CHARS if above is None else above):
        return full, Focus(focused=False, chars=len(full))

    chosen, unknown = select_files(worktree, about, extra)
    index = repository_context(worktree, editing=editing, bodies=False)
    lines = [index, "", "### The files this work names, in full", ""]
    shown: list[str] = []
    unshown: list[str] = []
    for rel in chosen:
        path = worktree / rel
        if path.suffix == ".py":
            fence = "python"
        elif is_text(path):
            fence = ""
        else:
            # Said, not skipped: a file asked for and silently left out reads
            # as one that was shown (#335).
            unshown.append(rel)
            continue
        if rel in CONTEXT_FILES:
            shown.append(rel)
            continue  # already shown in full above
        body = path.read_text(encoding="utf-8", errors="ignore").strip()
        lines += [f"`{rel}`", "", f"```{fence}", body, "```", ""]
        shown.append(rel)
    if unshown:
        lines += [
            "Not shown, because they aren't text: "
            + ", ".join(f"`{rel}`" for rel in unshown)
            + ".",
            "",
        ]
    lines += [
        "Every other file is listed above by name and what it defines, and not shown in "
        "full. If the work needs one you can't see, don't guess at it: name it in "
        "`need_files` and you'll be asked again with it shown.",
        "",
    ]
    text = "\n".join(lines)
    asked = [a.strip().removeprefix("./") for a in extra if a.strip().removeprefix("./") in chosen]
    return text, Focus(focused=True, shown=shown, asked=asked, unknown=unknown, chars=len(text))


def _is_test(rel: Path) -> bool:
    """A test module: under tests/, or named like one."""
    from crew_org.profiles import profile_for  # noqa: PLC0415

    return profile_for(rel.as_posix()).is_test_path(rel.as_posix())
