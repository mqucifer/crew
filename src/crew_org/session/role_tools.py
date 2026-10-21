"""The tools a role's question needs, beside the read tools (ADR 0035, crew#846).

Each refinement step reads the code with the same tools (`reading`). A role's own
question needs more: QA asks how we'll know it worked, so it reads what CI really
runs; DevOps asks whether it can run, so it reads CI's logs; the Architect asks
what's expensive to reverse, so it reads what the project has published as a
contract and what changed in it since the last release. A step names the ones it's
given (`Step.tools`), and each is told only to that step.

| Tool | Given to | What it reads |
|---|---|---|
| `ci_steps` | QA, DevOps | each workflow's jobs and the commands they run, and the devcontainer's |
| `ci_log` | DevOps | the default branch's latest CI runs, and a job's log |
| `contracts` | the Architect | the published contracts, and their changes since the last release |
| `data` | the Product Owner | the newest delivery-history package, with sprint-metrics' answers |
| `look` | the UX Designer | the published site, or a checkout's directory, at the reader's widths |

None runs the project's code on the host. `look` opens pages in the sandbox, offline
(`tools.look`), and QA's `run` is the Developer's, in its workspace container
(`reading.ReadTools`).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from crew_org.session.git import OUTPUT_CHARS
from crew_org.session.tools import S, _spec

# A job's log is long; its end says how it finished.
LOG_LINES = 200
CONTRACT_GLOBS = ("contracts/**/*", "**/*.schema.json", "**/openapi.*", "schemas/**/*")
SKIP = {".git", "node_modules", ".venv", "site-packages"}
# Where `crew package build` and a release write a package, from the crew's directory.
PACKAGES = Path("var") / "packages"
_PACKAGE = re.compile(r"delivery-history-(\d{4}-\d{2}-\d{2})(?:-(\d+))?$")


@dataclass(frozen=True)
class Context:
    """What a role's tool reads: the step's checkout, its repository, and GitHub."""

    root: Path
    repo: str
    github: Any = None


@dataclass(frozen=True)
class RoleTool:
    spec: dict[str, Any]
    call: Callable[[Context, dict[str, Any]], str]


def _cut(text: str) -> str:
    return text if len(text) <= OUTPUT_CHARS else text[:OUTPUT_CHARS] + "\n… (cut)"


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


# --- ci_steps: what CI really runs --------------------------------------------------------------


def ci_steps(context: Context, args: dict[str, Any]) -> str:
    """Each workflow's triggers, jobs, images and the commands each step runs."""
    workflows = sorted((context.root / ".github" / "workflows").glob("*.y*ml"))
    wanted = str(args.get("workflow") or "").strip()
    if wanted:
        workflows = [w for w in workflows if wanted in (w.name, f".github/workflows/{w.name}")]
    out: list[str] = []
    for path in workflows:
        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            out.append(f"## .github/workflows/{path.name}\n\nDoesn't parse: {exc}")
            continue
        # YAML reads the key `on` as True.
        triggers = doc.get("on", doc.get(True))
        on = ", ".join(triggers) if isinstance(triggers, dict | list) else str(triggers)
        lines = [f"## .github/workflows/{path.name}: {doc.get('name', '')}", f"On: {on}"]
        for name, job in (doc.get("jobs") or {}).items():
            job = job or {}
            lines.append(f"\n### Job `{name}` on {job.get('runs-on', '?')}")
            if image := (job.get("container") or {}):
                image = image if isinstance(image, str) else image.get("image")
                lines.append(f"- container: {image}")
            for service, spec in (job.get("services") or {}).items():
                lines.append(f"- service `{service}`: {(spec or {}).get('image', '?')}")
            for step in job.get("steps") or []:
                step = step or {}
                said = step.get("name") or ""
                if "run" in step:
                    command = "\n".join(
                        f"  {line}" for line in str(step["run"]).strip().splitlines()
                    )
                    lines.append(f"- {said or 'run'}:\n  ```\n{command}\n  ```")
                elif "uses" in step:
                    lines.append(f"- {said or 'uses'}: uses {step['uses']}")
        out.append("\n".join(lines))
    devcontainer = context.root / ".devcontainer" / "devcontainer.json"
    if devcontainer.is_file() and not wanted:
        try:
            spec = json.loads(devcontainer.read_text())
        except json.JSONDecodeError:
            spec = {}
        commands = {
            k: v for k, v in spec.items() if k.endswith("Command") or k in ("image", "build")
        }
        out.append("## .devcontainer/devcontainer.json\n\n" + json.dumps(commands, indent=2))
    if not out:
        return "No workflow found" + (f" named {wanted}" if wanted else "") + "."
    return _cut("\n\n".join(out))


# --- contracts: what's published, and what changed since the release -------------------------


def _contract_files(root: Path) -> list[str]:
    found: set[str] = set()
    for pattern in CONTRACT_GLOBS:
        for path in root.glob(pattern):
            rel = path.relative_to(root)
            if path.is_file() and not SKIP & set(rel.parts):
                found.add(str(rel))
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


# --- ci_log: how CI ran on the default branch ---------------------------------------------------


def _branch(root: Path) -> str:
    head = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return head.removeprefix("origin/") or "main"


def ci_log(context: Context, args: dict[str, Any]) -> str:
    """The default branch's latest run of each workflow, and its jobs; or a job's log."""
    github = context.github
    if github is None:
        return "CI's logs can't be read here: this step has no GitHub client."
    if job := args.get("job"):
        log = github.job_log(context.repo, int(job))
        if not log:
            return f"Job {job}'s log can't be read: expired, or no access."
        tail = log.splitlines()[-LOG_LINES:]
        return _cut(f"The last {len(tail)} lines of job {job}'s log:\n\n" + "\n".join(tail))
    branch = _branch(context.root)
    runs = github.latest_runs(context.repo, branch)
    if not runs:
        return f"No finished push run on {branch}."
    lines = [f"The latest finished push run of each workflow on {branch}:"]
    for path, run in runs.items():
        lines.append(
            f"\n## {path}: {run.get('conclusion')} at {run.get('updated_at')} "
            f"({str(run.get('head_sha', ''))[:7]})"
        )
        for each in github.run_jobs(context.repo, int(run["id"])):
            lines.append(f"- job {each.get('id')} `{each.get('name')}`: {each.get('conclusion')}")
    lines.append("\nName a job's number as `job` to read the end of its log.")
    return _cut("\n".join(lines))


# --- data: what the reader would be shown ----------------------------------------------------


def newest_package(packages: Path = PACKAGES) -> Path | None:
    """The newest package directory, by its date and that day's number."""
    found = [
        (m.group(1), int(m.group(2) or 1), path)
        for path in packages.glob("delivery-history-*")
        if path.is_dir() and (m := _PACKAGE.match(path.name))
    ]
    return max(found)[2] if found else None


def _size(value: Any) -> str:
    if isinstance(value, dict):
        return f"{len(value)} keys: {', '.join(list(value)[:12])}"
    if isinstance(value, list):
        return f"{len(value)} items"
    return json.dumps(value)


def data(context: Context, args: dict[str, Any], packages: Path = PACKAGES) -> str:
    """The newest package's parts, or one part by its dotted path."""
    found = newest_package(packages)
    if found is None:
        return f"No delivery-history package has been built here ({packages})."
    package = json.loads((found / "delivery-history.json").read_text())
    part = str(args.get("part") or "").strip()
    if not part:
        lines = [f"{found.name}, schema {package.get('schema_version')}. Its parts:"]
        lines += [f"- `{k}`: {_size(v)}" for k, v in package.items()]
        lines.append("\nName one as `part`, with dots for deeper, such as `analysis.sprints`.")
        return "\n".join(lines)
    value: Any = package
    for key in part.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return f"`{part}` isn't in {found.name}: `{key}` isn't there."
    return _cut(f"{found.name}, `{part}`:\n\n" + json.dumps(value, indent=1))


# --- look: the page as its reader meets it ----------------------------------------------------


def look(context: Context, args: dict[str, Any]) -> str:
    """A page of the published site, or of a directory in the checkout, at each width."""
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from crew_org.tools import look as looking  # noqa: PLC0415

    source = str(args.get("source") or "published").strip().strip("/")
    page = str(args.get("page") or "index.html").strip().lstrip("/")
    widths = tuple(int(w) for w in args.get("widths") or ()) or looking.WIDTHS
    fetched: Path | None = None
    base = "/"
    try:
        if source == "published":
            if context.github is None:
                return "The published site can't be fetched here: this step has no GitHub client."
            fetched = Path(tempfile.mkdtemp(prefix="look-site-"))
            files = looking.published(context.github, context.repo, fetched)
            base = looking.base_path(context.github, context.repo)
        else:
            files = (context.root / source).resolve()
            if not files.is_dir() or context.root.resolve() not in files.parents:
                return f"`{source}` isn't a directory in the repository."
        if not (files / page).is_file():
            pages = sorted(str(p.relative_to(files)) for p in files.rglob("*.html"))[:40]
            return f"`{page}` isn't in `{source}`. Its pages: {', '.join(pages)}"
        return looking.look(files, page, widths[:4], base=base).text()
    except looking.NotLooked as exc:
        return f"Couldn't look: {exc}"
    finally:
        if fetched is not None:
            shutil.rmtree(fetched, ignore_errors=True)


TOOLS: dict[str, RoleTool] = {
    "ci_steps": RoleTool(
        _spec(
            "ci_steps",
            "What CI really runs: each workflow's triggers, jobs, container and service "
            "images, and the commands its steps run, with the devcontainer's commands. Give "
            "`workflow` (its file name) for one.",
            {"workflow": S},
            [],
        ),
        ci_steps,
    ),
    "ci_log": RoleTool(
        _spec(
            "ci_log",
            "How CI ran on the default branch: each workflow's latest finished run and its "
            f"jobs. Give `job` (a job's number) for the last {LOG_LINES} lines of its log.",
            {"job": {"type": "integer"}},
            [],
        ),
        ci_log,
    ),
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
    "data": RoleTool(
        _spec(
            "data",
            "The real data the crew's site shows its readers: the newest delivery-history "
            "package built here, with sprint-metrics' answers in it. Without `part`, its parts "
            "and their sizes; give `part` (a dotted path, such as `analysis.sprints` or "
            "`sprint_metrics.answers`) for that part.",
            {"part": S},
            [],
        ),
        data,
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
