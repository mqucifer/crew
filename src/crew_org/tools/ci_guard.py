"""A workflow change may not weaken what CI enforces (#131, from #140).

The crew may change a project's CI. It may not change it so that a check the
Architect's design names (`design.checks`) stops running, or keeps running but
can no longer fail: `|| true` on the line, or `continue-on-error` on its step
or job. That is the constitution's §19 rule 5, made a check, because a model
told the suite must pass has two ways to get there and only one of them is
fixing the code.

It compares before and after. A check that CI didn't run before isn't this
change's doing; one it ran and can still fail on afterwards is fine wherever
it moved to.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

WORKFLOWS = ".github/workflows/"

# Ways a shell line keeps going after the command on it fails.
SWALLOWED = re.compile(r"\|\|\s*(true|:|exit\s+0)\b|;\s*(true|exit\s+0)\s*$", re.MULTILINE)


def is_workflow(path: str) -> bool:
    return path.startswith(WORKFLOWS) and path.endswith((".yml", ".yaml"))


def _enforcing_runs(text: str) -> list[str]:
    """Every `run:` that fails its job when it fails, in one workflow file."""
    try:
        doc: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    runs: list[str] = []
    for job in (jobs or {}).values():
        if not isinstance(job, dict) or job.get("continue-on-error") is True:
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict) or step.get("continue-on-error") is True:
                continue
            run = step.get("run")
            if isinstance(run, str):
                runs.append(run)
    return runs


def enforced(command: str, workflows: dict[str, str]) -> bool:
    """Does some workflow run `command` on a line whose failure fails the job?"""
    wanted = " ".join(command.split())
    for text in workflows.values():
        for run in _enforcing_runs(text):
            for line in run.splitlines():
                if wanted in " ".join(line.split()) and not SWALLOWED.search(line):
                    return True
    return False


def weakened(checks: list[str], before: dict[str, str], after: dict[str, str]) -> list[str]:
    """The checks CI enforced before this change and no longer does after it.

    `before` and `after` map each workflow path to its text; a path missing
    from `after` was deleted.
    """
    return [c for c in checks if enforced(c, before) and not enforced(c, after)]


# --- what a workflow the crew writes may not do (#279) ----------------------------------

# Write scopes a release plausibly needs: a tag and release (contents), an image
# (packages), trusted publishing (id-token), provenance (attestations). Any
# other write, or write-all, is refused.
RELEASE_WRITES = frozenset({"contents", "packages", "id-token", "attestations"})
_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _triggers(doc: dict) -> set[str]:
    # YAML reads a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(t) for t in on}
    if isinstance(on, dict):
        return {str(t) for t in on}
    return set()


def _write_scopes(permissions: Any) -> list[str]:
    if permissions in ("write-all",):
        return ["write-all"]
    if isinstance(permissions, dict):
        return [str(k) for k, v in permissions.items() if str(v) == "write"]
    return []


def unsafe(path: str, text: str, allowed_secrets: set[str]) -> list[str]:
    """Why this workflow may not be written by the crew, one line per reason."""
    try:
        doc: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        return [f"`{path}` isn't valid YAML"]
    if not isinstance(doc, dict):
        return []
    reasons = []
    if "pull_request_target" in _triggers(doc):
        reasons.append(
            f"`{path}` runs on `pull_request_target`, which gives a pull request's own "
            "code the repository's secrets. Use `pull_request`."
        )
    scopes = [("the workflow", doc.get("permissions"))]
    scopes += [
        (f"job `{name}`", job.get("permissions"))
        for name, job in (doc.get("jobs") or {}).items()
        if isinstance(job, dict)
    ]
    for where, permissions in scopes:
        broad = [w for w in _write_scopes(permissions) if w not in RELEASE_WRITES]
        if broad:
            reasons.append(
                f"`{path}`: {where} asks to write {', '.join(broad)}. A crew workflow may "
                f"write only {', '.join(sorted(RELEASE_WRITES))}, and only where it needs to."
            )
    allowed = allowed_secrets | {"GITHUB_TOKEN"}
    unnamed = sorted({s for s in _SECRET.findall(text) if s not in allowed})
    if unnamed:
        reasons.append(
            f"`{path}` reads secrets the project's design doesn't name: {', '.join(unnamed)}. "
            "Only GITHUB_TOKEN and the secrets listed in the record's `design.secrets` may be used."
        )
    return reasons
