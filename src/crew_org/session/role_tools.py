"""The tools a role's question needs, beside the read tools (ADR 0035, crew#846).

Each refinement step reads the code with the same tools (`reading`). A role's own
question in the Goal's look needs more (crew#858): the Architect asks what's
expensive to reverse, so it reads what the project has published as a contract and
what changed in it since the last release; the UX Designer asks what the reader
meets, so it looks at the page. A step names the ones it's given (`Step.tools`), and
each is told only to that step.

| Tool | Given to | What it reads |
|---|---|---|
| `contracts` | the Architect | the published contracts, and their changes since the last release |
| `look` | the UX Designer | the published site, or a checkout's directory, at the reader's widths |

Neither runs the project's code on the host. `look` opens pages in the sandbox,
offline (`tools.look`).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.session.git import OUTPUT_CHARS
from crew_org.session.tools import S, _spec
from crew_org.tools.look import Fetched

# What a project publishes: anything under these, and these files anywhere.
CONTRACT_DIRS = ("contracts", "schemas")
SKIP = {".git", "node_modules", ".venv", "site-packages"}


@dataclass(frozen=True)
class Context:
    """What a role's tool reads: the step's checkout, its repository, and GitHub."""

    root: Path
    repo: str
    github: Any = None
    # What the session's `look`s fetched, until it ends (`ReadTools.stop`).
    looked: Fetched = field(default_factory=Fetched, compare=False)
    # Where a release review keeps each published page's screenshots (crew#830), and
    # which it took, by page and width: a correction is filed with its own.
    keep: Path | None = None
    shots: dict[tuple[str, int], Path] = field(default_factory=dict, compare=False)
    # Why a repository's published site can't be fetched, once found: it won't change
    # during the session, so it isn't fetched again.
    unpublished: dict[str, str] = field(default_factory=dict, compare=False)


class Refused(Exception):
    """A role tool's refusal, said as its reply: the session counts it as refused."""


@dataclass(frozen=True)
class RoleTool:
    spec: dict[str, Any]
    call: Callable[[Context, dict[str, Any]], str]
    # Whether a step over this project is given it at all.
    offered: Callable[[Context], bool] = lambda context: True


def _cut(text: str) -> str:
    return text if len(text) <= OUTPUT_CHARS else text[:OUTPUT_CHARS] + "\n… (cut)"


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


# --- contracts: what's published, and what changed since the release -------------------------


def _is_contract(rel: Path) -> bool:
    return (
        (len(rel.parts) > 1 and rel.parts[0] in CONTRACT_DIRS)
        or rel.name.endswith(".schema.json")
        or rel.name.startswith("openapi.")
    )


def _contract_files(root: Path) -> list[str]:
    """Walked with SKIP pruned: a recursive glob walked node_modules and .venv first."""
    found: list[str] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        under = Path(here).relative_to(root)
        found += [str(under / f) for f in files if _is_contract(under / f)]
    return sorted(found)


def contracts(context: Context, args: dict[str, Any]) -> str:
    """The published contracts, and each one's change since the last release tag."""
    root = context.root
    files = _contract_files(root)
    if not files:
        return (
            "No published contract found: no contracts/, schemas/, *.schema.json or openapi file."
        )
    tag = _git(root, "describe", "--tags", "--abbrev=0")
    if path := str(args.get("path") or "").strip():
        if path not in files:
            return f"{path} isn't one of the contracts: {', '.join(files)}"
        if not tag:
            return f"No release tag to compare {path} with."
        diff = _git(root, "diff", f"{tag}..HEAD", "--", path)
        return _cut(diff or f"{path} hasn't changed since {tag}.")
    lines = [f"Published contracts ({len(files)} files):", *[f"- {f}" for f in files]]
    for versions in (f for f in files if f.endswith("versions.json")):
        lines.append(f"\n{versions}:\n{(root / versions).read_text()[:2000]}")
    if tag:
        changed = _git(root, "diff", "--stat", f"{tag}..HEAD", "--", *files)
        log = _git(root, "log", "--oneline", f"{tag}..HEAD", "--", *files)
        lines.append(f"\nSince the last release, {tag}:")
        lines.append(changed or "nothing changed.")
        if log:
            lines.append(f"\nThe commits:\n{log}")
        lines.append("\nName a file as `path` to see its diff since the release.")
    else:
        lines.append("\nNo release tag yet, so nothing to compare with.")
    return _cut("\n".join(lines))


# --- look: the page as its reader meets it ----------------------------------------------------


def look(context: Context, args: dict[str, Any]) -> str:
    """A page of the published site, or of a directory in the checkout, at each width."""
    from crew_org.tools import look as looking  # noqa: PLC0415

    source = str(args.get("source") or "published").strip().strip("/")
    page = str(args.get("page") or "index.html").strip().lstrip("/")
    widths = tuple(int(w) for w in args.get("widths") or ()) or looking.WIDTHS
    base = "/"
    try:
        if source == "published":
            if context.github is None:
                return "The published site can't be fetched here: this step has no GitHub client."
            if context.repo in context.unpublished:
                raise Refused(context.unpublished[context.repo])
            try:
                files, base = context.looked.site(context.github, context.repo)
            except looking.NotLooked as exc:
                # No page, width or URL form changes it: the UX review's proof tried 30
                # (crew#830).
                context.unpublished[context.repo] = (
                    f"The published site can't be looked at in this session: {exc}. That "
                    "won't change while it runs: no page, width or URL form works, so don't "
                    "call `look` with `source` `published` again. Look at a directory of the "
                    "repository instead, or say in your answer that you couldn't see the "
                    "published pages."
                )
                raise Refused(context.unpublished[context.repo]) from exc
        else:
            files = (context.root / source).resolve()
            if not files.is_dir() or context.root.resolve() not in files.parents:
                return f"`{source}` isn't a directory in the repository."
        # The host reads the page for its links, so it's the site's, as `Tools._path` keeps
        # a read in the worktree: `../` or a link mustn't reach the host's own files.
        if not (files / page).resolve().is_relative_to(files.resolve()):
            return f"`{page}` isn't a page of `{source}`."
        if not (files / page).is_file():
            pages = sorted(str(p.relative_to(files)) for p in files.rglob("*.html"))[:40]
            return f"`{page}` isn't in `{source}`. Its pages: {', '.join(pages)}"
        # Only the published site's: a mock isn't what a reader meets.
        keep = None
        if context.keep is not None and source == "published":
            keep = context.keep / re.sub(r"[^A-Za-z0-9]+", "-", page).strip("-")
        seen = looking.look(files, page, widths[:4], base=base, fetched=context.looked, keep=keep)
        context.shots.update({(page, w): shot for w, shot in seen.shots.items()})
        return seen.text()
    except looking.NotLooked as exc:
        return f"Couldn't look: {exc}"


TOOLS: dict[str, RoleTool] = {
    "contracts": RoleTool(
        _spec(
            "contracts",
            "What this project publishes for others to build on (schemas, contracts and "
            "their versions), and what changed in them since the last release tag. Give "
            "`path` for one file's diff since the release.",
            {"path": S},
            [],
        ),
        contracts,
    ),
    "look": RoleTool(
        _spec(
            "look",
            "See a page as its reader meets it, in a browser at each width: its headings "
            "and text in reading order, whether it scrolls sideways and which elements cause "
            "it, what didn't load, and images without alt text. `source` is `published` (the "
            "published site, as readers are served it) or a directory of the repository that "
            "holds pages, such as a design mock. `widths` default to 375, 768 and 1280: use "
            "the ones the Goal names.",
            {"page": S, "source": S, "widths": {"type": "array", "items": {"type": "integer"}}},
            [],
        ),
        look,
    ),
}
