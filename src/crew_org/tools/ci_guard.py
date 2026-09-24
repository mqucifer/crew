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
