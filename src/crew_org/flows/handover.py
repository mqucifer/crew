"""Whether the crew can take over a repository it didn't build: the handover check (crew#757).

Claude builds a new project's alpha, and the crew takes it over at readiness
level 2 with no skeleton Goal (ADR 0030). So the handover is the only point
where the alpha is checked against what the crew's tools can work with. Most of
crew-presentation's onboarding faults were layout, the sandbox and missing facts,
not code (crew#711 to crew#714, crew#718, crew#725, crew#727).

The checklist is the M6 research's (R1: 28 items in 7 groups). Readiness covers
the settings, the record, CI and protection; this covers the rest, each with
who fixes it:

- **the record and design:** `crew design`'s rules on a design it may not have
  written, its `structure` matching the files on the default branch, the docs it
  names, each `uses` the roles can follow, and what the alpha leaves behind;
- **layout:** Python kept in named definitions, every other language in a
  declared part, and no placeholder marker left;
- **CI:** the template's files unchanged, and workflows the crew may edit later;
- **tests:** each part's test files there;
- **setup** re-locking and leaving the tree clean, when asked to run it.

Sizes are reported, never refused (the Sponsor, ADR 0030): a repository grows
whatever size it starts at, and the crew's focused views handle growth. What only
a person can judge is named as not checked.

It is the one Definition of Done for Claude and the crew. `crew ready` runs it;
a tick doesn't, so it costs the tick nothing.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crew_org import profiles
from crew_org.flows.design import image_problems, needs_an_image, shelled, unlocked
from crew_org.flows.project_log import DECISIONS_DIR
from crew_org.flows.readiness import CREW, SPONSOR, Missing
from crew_org.flows.skeleton import PLACEHOLDER, marked
from crew_org.flows.template import wanted
from crew_org.project import CREW_FILES, ProjectRecord, is_protected
from crew_org.tools import ci_guard, devcontainer
from crew_org.tools.repo_context import (
    FOCUS_ABOVE_CHARS,
    WHOLE_FILE_CHARS,
    is_text,
    project_files,
)

# Who fixes the alpha's own code, before the takeover (ADR 0030). Once the
# repository is in `delivery.repos`, the crew does.
CLAUDE = "Claude, the alpha's builder"

# What only a person can judge, said rather than skipped (R1).
BY_HAND = (
    "every behaviour the project promises has a test, in a file named for its concern",
    "packages that drive a program in the image match the image's version (crew#709)",
    "`ci_checks` lists what the sandbox can't run, and CI proves each one",
    "a producer's README says how projects use it, with its contract in each release",
)

# Source in a language other than Python, which only a declared part edits with
# the right tools (§14). Markup, styles and config are edited as text anyway.
CODE_SUFFIXES = frozenset(
    {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".scad"}
    | {".go", ".rs", ".java", ".kt", ".rb", ".c", ".h", ".cpp", ".hpp", ".swift"}
)

# A path named in the design's `structure`, by its folder or its suffix. Prose
# like "play/pause" is neither.
_NAMED = re.compile(r"(?<![\w./-])\.?/?([\w.-]+(?:/[\w.-]+)*(?:/|\.[a-z]{1,8}))(?![\w/-])")
_SUFFIXES = frozenset({"py", "lock", "tag", "version", "toml", "cfg", "ini", "txt", "sh"}) | {
    s.lstrip(".") for s in CODE_SUFFIXES | {".md", ".json", ".yml", ".yaml", ".html", ".css"}
}


@dataclass
class Handover:
    missing: list[Missing] = field(default_factory=list)
    # Sizes past where the crew's views stop showing everything: never refused.
    reported: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)


def check(
    issues: Any,
    repo: str,
    root: Path,
    record: ProjectRecord,
    *,
    alpha: bool,
    template: dict[str, str],
    sandbox: Any = None,
) -> Handover:
    """What keeps the crew from taking `repo`, checked out at `root`, over.

    `alpha` is a repository the crew doesn't work yet, whose code Claude fixes.
    `template` is `mqucifer/project-template`'s files. Setup runs only with a
    `sandbox`: it needs the network and Docker, and `crew ready` otherwise only reads.
    """
    builder = CLAUDE if alpha else CREW
    files = [p.relative_to(root).as_posix() for p in project_files(root)]
    found = Handover(unchecked=list(BY_HAND))
    defined = devcontainer.read(root)
    found.missing += design_rules(repo, record, image=defined.image if defined else None)
    found.missing += workspace_file(repo, record, defined, builder)
    found.missing += structure(repo, root, record, files)
    found.missing += documented(root, record, builder)
    found.missing += followed(root, record, builder)
    found.missing += left_behind(issues, repo, root, alpha=alpha)
    found.missing += layout(root, record, files, builder)
    found.missing += ci(root, record, files, template, builder)
    found.missing += tested(record, files, builder)
    found.reported += sizes(root, files)
    if sandbox is None:
        found.unchecked.append(
            f"setup re-locks and leaves the tree clean: `crew ready {repo} --setup` runs it "
            "once in the sandbox"
        )
    else:
        found.missing += setup_leaves(root, record, sandbox, builder)
    return found


# --- the record and design ------------------------------------------------------------


def design_rules(repo: str, record: ProjectRecord, *, image: str | None = None) -> list[Missing]:
    """`crew design`'s rules, on a design it may not have written (R1).

    A design written by hand otherwise met only the lockfile's presence at level 2
    and the shell refusal at run time. `image` is the one the project's
    devcontainer.json names, which stands for the design's (`workspace_file`).
    """
    design = record.design
    if design is None:
        return []

    def choice(value: str | None) -> Any:
        return SimpleNamespace(value=value) if value else None

    own = design.sandbox_image or image
    proposal = SimpleNamespace(
        lockfile=choice(design.lockfile or (None if own else "uv.lock")),
        setup=choice(design.setup),
        checks=[choice(c) for c in design.checks],
        autofix=[choice(c) for c in design.autofix],
        sandbox_image=choice(own),
        parts=design.parts,
    )
    reasons = [*unlocked(proposal), *shelled(proposal), *needs_an_image(proposal)]
    reasons += image_problems(choice(design.sandbox_image))
    if design.lockfile is None and not own:
        # Assumed to be uv's, and checked as that, until the design says so (ADR 0030).
        reasons.append("the design doesn't name its lockfile in `lockfile`")
    return [Missing(f"design: {r}; `crew design {repo} --reason`", CREW) for r in reasons]


def workspace_file(
    repo: str, record: ProjectRecord, defined: devcontainer.Definition | None, builder: str
) -> list[Missing]:
    """The project's devcontainer.json, as the crew reads it (crew#778).

    Without one, the design's image and setup are the workspace's, and there's
    nothing to check here. With one: what stops it being used; each field the crew
    doesn't honour, since a file asking for what it doesn't get misleads whoever
    reads it; the image rule; and one source, so the design names neither the
    image nor the setup (the Sponsor, 2026-10-18).
    """
    if defined is None:
        return []
    path = defined.path
    found = [Missing(problem, builder) for problem in defined.problems]
    found += [
        Missing(f"`{path}`: `{name}` isn't honoured ({why}); remove it", builder)
        for name, why in defined.not_honoured.items()
    ]
    found += [
        Missing(f"`{path}`: `{name}` {why}", builder) for name, why in defined.unknown.items()
    ]
    if defined.image:
        found += [
            Missing(f"`{path}`: {reason}", builder)
            for reason in image_problems(SimpleNamespace(value=defined.image))
        ]
    design = record.design
    twice = [
        what
        for what, given in (
            ("sandbox image", design.sandbox_image if design else None),
            ("setup", design.setup if design else None),
        )
        if given
    ]
    if twice:
        found.append(
            Missing(
                f"design: it names its {' and '.join(twice)}, and `{path}` is their one source; "
                f"`crew design {repo} --reason`",
                CREW,
            )
        )
    return found


def structure(repo: str, root: Path, record: ProjectRecord, files: list[str]) -> list[Missing]:
    """The design's `structure` names what is there, and nothing that isn't (R1).

    crew-presentation's design declared changes to files that weren't there
    (crew-presentation#37, #39). Paths it names that git ignores, such as a build's
    output, are made by running the code.
    """
    design = record.design
    if design is None or not design.structure:
        return [Missing(f"design: it has no `structure`; `crew design {repo} --reason`", CREW)]
    folders = {"/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)}
    named = [n for n in dict.fromkeys(_NAMED.findall(design.structure)) if _is_path(n)]

    def there(name: str) -> bool:
        if name.endswith("/"):
            stem = name.rstrip("/")
            return any(d == stem or d.endswith("/" + stem) for d in folders)
        return any(f == name or f.endswith("/" + name) for f in files)

    absent = [n for n in named if not there(n) and not _ignored(root, n)]

    def covered(path: str) -> bool:
        return any(
            path == n or path.endswith("/" + n) or (n.endswith("/") and f"/{n}" in f"/{path}")
            for n in named
        )

    unnamed = [f for f in _code(record, files) if not covered(f)]
    found = []
    if absent:
        found.append(
            Missing(
                "design: `structure` names what isn't on the default branch: "
                + ", ".join(f"`{n}`" for n in absent)
                + f"; `crew design {repo} --reason`",
                CREW,
            )
        )
    if unnamed:
        found.append(
            Missing(
                "design: `structure` doesn't name "
                + ", ".join(f"`{f}`" for f in unnamed)
                + f"; `crew design {repo} --reason`",
                CREW,
            )
        )
    return found


def _is_path(name: str) -> bool:
    return name.endswith("/") or name.rsplit(".", 1)[-1] in _SUFFIXES


def _ignored(root: Path, name: str) -> bool:
    found = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", name],
        cwd=root,
        capture_output=True,
    )
    return found.returncode == 0


def _code(record: ProjectRecord, files: list[str]) -> list[str]:
    """The project's code that isn't a test: Python, and any declared part's files.

    Not the crew's own files from the template, which no design describes, nor a
    package's `__init__.py`, which its package's modules stand for.
    """
    profiles.set_project(record)
    try:
        return [
            f
            for f in files
            if (f.endswith(".py") or Path(f).suffix in CODE_SUFFIXES)
            and not is_protected(f, list(CREW_FILES))
            and Path(f).name != "__init__.py"
            and not profiles.profile_for(f).is_test_path(f)
            and not profiles.PYTHON.is_test_path(f)
        ]
    finally:
        profiles.clear()


def documented(root: Path, record: ProjectRecord, builder: str) -> list[Missing]:
    """Each file a user reads to use the project is there (R1, §16)."""
    absent = [d for d in record.user_docs if not (root / d).is_file()]
    if not absent:
        return []
    return [
        Missing(
            "the docs the design names aren't there: " + ", ".join(f"`{d}`" for d in absent),
            builder,
        )
    ]


def followed(root: Path, record: ProjectRecord, builder: str) -> list[Missing]:
    """Each project this one uses, as the roles will be shown it (ADR 0021; crew#713).

    Without the producer's notes and the release's contract, the roles guessed
    a package's layout.
    """
    from crew_org.usage import unreadable  # noqa: PLC0415

    found = []
    for use in record.intent.scope.uses:
        if use.pin and not (root / use.pin).is_file():
            gap = f"`{use.pin}`, naming the {use.repo} release, isn't there"
            found.append(Missing(gap, builder))
            continue
        found += [
            Missing(f"`uses` {use.repo}: {gap}; fix the record's `uses`", SPONSOR)
            for gap in unreadable(use, root)
        ]
    return found


def left_behind(issues: Any, repo: str, root: Path, *, alpha: bool) -> list[Missing]:
    """What the alpha leaves the crew: its decisions, and what it delivered (ADR 0030).

    The panel, the settle step and the split read the project's ADRs for every
    epic; later splits see a closed Goal's work as delivered, and bugs are judged
    against its criteria. A project the crew built has its epics' records instead
    of an alpha's ADRs.
    """
    found = []
    folder = root / DECISIONS_DIR
    if alpha and not any(folder.glob("[0-9][0-9][0-9][0-9]-*.md")):
        found.append(
            Missing(
                f"no decisions in `{DECISIONS_DIR}/`: write the ones later work must keep as ADRs",
                CLAUDE,
            )
        )
    if not any(i.get("state") == "closed" for i in issues.labelled(repo, "goal")):
        found.append(
            Missing(
                "no closed Goal: file what it delivered as one, its epic and stories Done "
                "with their criteria",
                CLAUDE if alpha else CREW,
            )
        )
    return found


# --- layout the crew's view and edit tools handle -------------------------------------


def layout(root: Path, record: ProjectRecord, files: list[str], builder: str) -> list[Missing]:
    """Python in named definitions, other languages in parts, and no marker left (R1)."""
    found = []
    for path in files:
        if not path.endswith(".py"):
            continue
        try:
            lines = top_level_code((root / path).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            found.append(Missing(f"`{path}` doesn't parse as Python: {exc}", builder))
            continue
        if lines:
            found.append(
                Missing(
                    f"`{path}` runs code at its top level (line {', '.join(map(str, lines))}): "
                    "the crew edits Python by definition, so move it into a function",
                    builder,
                )
            )
    profiles.set_project(record)
    try:
        loose = [
            f
            for f in files
            if Path(f).suffix in CODE_SUFFIXES and profiles.profile_for(f).name == "python"
        ]
    finally:
        profiles.clear()
    if loose:
        found.append(
            Missing(
                "code that isn't Python sits in no part the design declares: "
                + ", ".join(f"`{f}`" for f in loose)
                + "; the design names its part, language and tests",
                CREW,
            )
        )
    left = [
        f
        for f in files
        if (f.endswith(".py") or is_text(Path(f)))
        and marked((root / f).read_text(encoding="utf-8", errors="replace"))
    ]
    if left:
        found.append(
            Missing(
                f"`{PLACEHOLDER}` still starts a line in "
                + ", ".join(f"`{f}`" for f in left)
                + ": replace the placeholder, or stop quoting the marker at a line's start",
                builder,
            )
        )
    return found


def top_level_code(source: str) -> list[int]:
    """The lines of statements outside any definition that do more than define.

    The top level holds imports, constants, definitions and a `__main__` block.
    Text edits reach only lines outside definitions, and imports can't be appended:
    crew-presentation's `build.py` failed with `NameError` three times (crew#714).
    """
    return [node.lineno for node in ast.parse(source).body if not _defines(node)]


def _defines(node: ast.stmt) -> bool:
    if isinstance(
        node,
        ast.Import
        | ast.ImportFrom
        | ast.FunctionDef
        | ast.AsyncFunctionDef
        | ast.ClassDef
        | ast.Assign
        | ast.AnnAssign
        | ast.AugAssign
        | ast.Pass,
    ):
        return True
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    if isinstance(node, ast.If):
        if "__name__" in ast.unparse(node.test):
            return True
        return all(_defines(n) for n in [*node.body, *node.orelse])
    if isinstance(node, ast.Try):
        handled = [n for h in node.handlers for n in h.body]
        return all(_defines(n) for n in [*node.body, *handled, *node.orelse, *node.finalbody])
    return False


# --- CI and checks that run in the crew's sandbox -------------------------------------


def ci(
    root: Path, record: ProjectRecord, files: list[str], template: dict[str, str], builder: str
) -> list[Missing]:
    """The template's CI unchanged, and workflows the crew may edit later (R1, §19.2).

    The template's `checks.yml` runs the design's setup and checks the way the
    sandbox does. A workflow outside what the crew may write is one it can never
    change (crew#279, crew#727).
    """
    found = []
    differs = sorted(wanted(template, root))
    if differs:
        found.append(
            Missing(
                "it isn't what the template gives every project: "
                + ", ".join(
                    f"`{p}` {'differs' if (root / p).exists() else 'is missing'}" for p in differs
                )
                + "; copy them from `mqucifer/project-template`",
                builder,
            )
        )
    design = record.design
    writes = ci_guard.RELEASE_WRITES | (
        ci_guard.PAGES_WRITES if record.intent.release.to_pages else frozenset()
    )
    secrets = set(design.secrets if design else [])
    for path in files:
        if not ci_guard.is_workflow(path) or is_protected(path, list(CREW_FILES)):
            continue
        text = (root / path).read_text(encoding="utf-8")
        found += [
            Missing(f"{reason} Otherwise the crew can't edit it", builder)
            for reason in ci_guard.unsafe(path, text, secrets, writes)
        ]
    return found


def setup_leaves(root: Path, record: ProjectRecord, sandbox: Any, builder: str) -> list[Missing]:
    """Setup writes the lockfile itself, and leaves nothing git would commit (R1).

    A story that changes a dependency can't write a lock: crew-presentation#66
    blocked twice (crew#712). What setup installs or fetches outside ignored paths
    reached every view, and a 1 MB view broke the model's context (crew#711).
    Run on a copy, without the lockfile, so setup has to write it. Run as a
    session's workspace (crew#778): its container starts with no network, setup
    runs with one, and then a command runs in the container.
    """
    from crew_org.tools.container import Held  # noqa: PLC0415
    from crew_org.tools.workspace import toolchain  # noqa: PLC0415

    design = record.design
    copy = Path(tempfile.mkdtemp(prefix="handover-", dir=root.parent))
    held = None
    try:
        subprocess.run(["git", "clone", "--quiet", str(root), str(copy)], check=True)
        subprocess.run(
            ["git", "checkout", "--quiet", _head(root)], cwd=copy, check=True, capture_output=True
        )
        tools = toolchain(copy)
        if tools.problem or not tools.setup:
            return [Missing(f"setup can't run: {tools.problem or 'the design names none'}", CREW)]
        lockfile = (design.lockfile if design else None) or (None if tools.image else "uv.lock")
        if lockfile:
            (copy / lockfile).unlink(missing_ok=True)
        held = Held(copy, copy.name, sandbox=sandbox)
        started = held.start()
        if not started.ok:
            return [Missing(f"the workspace doesn't start: {started.output[-500:]}", builder)]
        result = held.setup()
        if result is None or not result.ok:
            said = result.output[-500:] if result else "nothing ran"
            return [Missing(f"setup fails: {said}", builder)]
        answered = held.exec(["true"])
        if not answered.ok:
            return [Missing(f"the workspace runs nothing after setup: {answered.output}", builder)]
        found = []
        if lockfile and not (copy / lockfile).is_file():
            found.append(
                Missing(
                    f"setup doesn't write `{lockfile}`, so a story that changes a dependency "
                    "can't re-lock: setup should resolve before it installs",
                    builder,
                )
            )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=copy,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        left = sorted({line[3:] for line in status} - {lockfile})
        if left:
            shown = ", ".join(f"`{p}`" for p in left[:10]) + (" …" if len(left) > 10 else "")
            found.append(
                Missing(
                    f"setup leaves {len(left)} files git would commit ({shown}); "
                    "ignore where it installs and fetches",
                    builder,
                )
            )
        return found
    finally:
        if held is not None:
            held.stop()
        shutil.rmtree(copy, ignore_errors=True)


def _head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


# --- tests that pin behaviour ---------------------------------------------------------


def tested(record: ProjectRecord, files: list[str], builder: str) -> list[Missing]:
    """Each part's test files are there: a part that isn't Python has no other guard (§14)."""
    design = record.design
    found = []
    for part in design.parts if design else []:
        profile = profiles.GenericProfile(part.language, part.tests)
        if not part.tests:
            found.append(Missing(f"the part `{part.path or '(all)'}` names no tests", CREW))
        elif not any(profile.is_test_file(f) for f in files):
            found.append(
                Missing(
                    f"no file matches the part `{part.path or '(all)'}`'s tests "
                    + ", ".join(f"`{t}`" for t in part.tests),
                    builder,
                )
            )
    return found


# --- size and shape -------------------------------------------------------------------


def sizes(root: Path, files: list[str]) -> list[str]:
    """Where the crew's views stop showing everything: reported, never refused (ADR 0030)."""

    def chars(path: str) -> int:
        return len((root / path).read_text(encoding="utf-8", errors="replace"))

    text = {f: chars(f) for f in files if f.endswith(".py") or is_text(Path(f))}
    found = []
    total = sum(text.values())
    if total > FOCUS_ABOVE_CHARS:
        other = [f for f in files if Path(f).suffix in CODE_SUFFIXES]
        found.append(
            f"its text is {total:,} characters, above {FOCUS_ABOVE_CHARS:,}: the Developer "
            "sees a focused view"
            + (
                f", where the map lists what isn't Python by name only ({len(other)} files)"
                if other
                else ""
            )
        )
    big = sorted(
        (f for f in text if f.endswith(".py") and text[f] > WHOLE_FILE_CHARS),
        key=lambda f: -text[f],
    )
    if big:
        found.append(
            f"Python files above {WHOLE_FILE_CHARS:,} characters are shown by definition, "
            "not whole: " + ", ".join(f"`{f}` ({text[f]:,})" for f in big)
        )
    return found
