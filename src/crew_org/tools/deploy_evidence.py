"""What the DevOps Engineer is shown to judge something that runs or deploys (#335).

sprint-metrics#269 added a Dockerfile whose image couldn't be reached: the
server it runs binds 127.0.0.1, so `-p 8080:8080` reaches nothing. The Code
Reviewer judged the diff and QA the criteria, correctly, and neither was shown
the code the image runs. A diff that adds a Dockerfile doesn't carry that code,
so it's found here: the project's console scripts, and every file that binds
or listens.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from crew_org.tools.review_evidence import candidate_paths

# Generous, not a budget (§16): a small project's runnable files and entry code
# fit whole. A file that doesn't is named rather than cut.
MAX_DEPLOY_EVIDENCE_CHARS = 80_000

_FILE = re.compile(r"^(?:\+\+\+ b|--- a)/(\S+)$", re.MULTILINE)
# Code that accepts connections: where a host, a port and a user are decided.
_LISTENS = re.compile(
    r"\b(?:HTTPServer|ThreadingHTTPServer|TCPServer|UDPServer|serve_forever|"
    r"socket\.socket|create_server|start_server|uvicorn\.run|run_simple|web\.run_app)\b"
    r"|\.bind\(|\.listen\(|\bapp\.run\("
)


def is_runnable(path: str) -> bool:
    """A file that decides how something runs or ships: an image, a stack, a workflow."""
    p = PurePosixPath(path)
    name = p.name.lower()
    if name.startswith(("dockerfile", "containerfile")) or name.endswith(".dockerfile"):
        return True
    if name in {".dockerignore"}:
        return True
    if re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", name):
        return True
    return p.parts[:2] == (".github", "workflows") and p.suffix in {".yml", ".yaml"}


def changed_files(diff: str) -> list[str]:
    return [p for p in dict.fromkeys(_FILE.findall(diff)) if p != "/dev/null"]


def touches_runnable(diff: str) -> bool:
    """Does this change add, alter or remove anything that runs or deploys?"""
    return any(is_runnable(p) for p in changed_files(diff))


def runnable_paths(root: Path | None, diff: str) -> list[str]:
    """Every runnable file: those in the repository, and those this change adds."""
    found = set(p for p in changed_files(diff) if is_runnable(p))
    if root is not None:
        for path in root.rglob("*"):
            rel = path.relative_to(root).as_posix()
            if path.is_file() and not rel.startswith(".git/") and is_runnable(rel):
                found.add(rel)
    return sorted(found)


def entry_modules(pyproject: str | None) -> list[str]:
    """The modules the project's console scripts run: `pkg.cli:main` gives `pkg.cli`."""
    if not pyproject:
        return []
    try:
        scripts = tomllib.loads(pyproject).get("project", {}).get("scripts", {})
    except tomllib.TOMLDecodeError:
        return []
    return list(dict.fromkeys(str(target).split(":")[0].strip() for target in scripts.values()))


def listening_files(root: Path | None) -> list[str]:
    """The project's own Python files that accept connections. Tests are left out."""
    if root is None:
        return []
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".git/", ".venv/", "tests/")) or "/tests/" in rel:
            continue
        try:
            if _LISTENS.search(path.read_text(errors="replace")):
                found.append(rel)
        except OSError:
            continue
    return found


def deploy_evidence(root: Path | None, diff: str, read_head: Callable[[str], str | None]) -> str:
    """The runnable files and the code they run, as at the pull request's head.

    `root` is the repository at its base branch, for finding files; each is
    read at the head with `read_head(path)`, which returns None for a file the
    change removes.
    """
    code = [
        path
        for module in entry_modules(read_head("pyproject.toml"))
        for path in candidate_paths(module)[:2]
    ] + listening_files(root)
    sections: list[tuple[str, list[str]]] = [
        ("What runs or deploys, as this change leaves it", runnable_paths(root, diff)),
        ("The code it runs: the console scripts, and anything that listens", code),
    ]
    budget = MAX_DEPLOY_EVIDENCE_CHARS
    out: list[str] = []
    omitted: list[str] = []
    seen: set[str] = set()
    for heading, paths in sections:
        shown: list[str] = []
        for path in paths:
            if path in seen:
                continue
            text = read_head(path)
            if text is None:
                continue
            seen.add(path)
            if len(text) > budget:
                omitted.append(path)
                continue
            budget -= len(text)
            shown += [f"`{path}`", "", "```", text.rstrip(), "```", ""]
        if shown:
            out += [f"## {heading}", "", *shown]
    if omitted:
        out.append("Not shown, too large to include whole: " + ", ".join(f"`{p}`" for p in omitted))
    return "\n".join(out).strip()
