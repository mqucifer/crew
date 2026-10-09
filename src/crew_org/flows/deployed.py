"""When a crew change went live: the first tick that ran code containing it (crew#449).

A fix was dated by when it merged. But a tick runs the checkout it was started
from, and the main checkout is updated only between ticks, so a failure after
the merge can still be from before the fix ran. mqucifer/crew#196 (lint F821)
closed on 2026-09-27, and the record couldn't say whether the four cards it
recurred on ran with it (discussion 552). Every record now carries its tick's
commit, so the first tick whose commit contains the fix is when it went live.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def commits_seen(events_dir: Path) -> list[tuple[str, str]]:
    """(first time seen, commit) for each commit the event log's records ran, oldest first."""
    first: dict[str, str] = {}
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            commit = (event.get("ctx") or {}).get("commit")
            at = str(event.get("at") or "")
            if commit and at and (commit not in first or at < first[commit]):
                first[commit] = at
    return sorted((at, commit) for commit, at in first.items())


def _contains(commit: str, change: str, repo: Path) -> bool:
    try:
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", change, commit],
                cwd=repo,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    except OSError:
        return False


def live_at(change: str, commits: Iterable[tuple[str, str]], repo: Path = REPO) -> str | None:
    """When the first tick that ran `change` began, or None if no recorded tick has."""
    for at, commit in commits:
        if _contains(commit, change, repo):
            return at
    return None
