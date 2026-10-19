"""The replay's cases: stories that really failed, at the commit their first attempt
started from, with the story as it stood then (crew#778, Stage R)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
# Clones are fetched once, beside the crew's other experiments' (git ignores var/).
CLONES = Path("var/experiments/session-replay")


@dataclass(frozen=True)
class Case:
    repo: str
    card: int
    # The commit the first attempt started from: its pull request's first commit's
    # parent where it opened one, else the default branch's head before its first call.
    base: str
    first_attempt: str  # the first Developer call, from var/events (UTC)
    note: str = ""

    @property
    def name(self) -> str:
        return f"{self.repo}-{self.card}"


CASES = {
    c.name: c
    for c in (
        Case(
            "sprint-metrics",
            662,
            "aa78bf2",
            "2026-10-17T19:27:26Z",
            "no pull request opened; main unchanged since 2026-10-16",
        ),
        Case("sprint-metrics", 596, "da6ff26", "2026-10-12T15:15:35Z", "PR 615's parent"),
        Case(
            "sprint-metrics",
            567,
            "2cf938a",
            "2026-10-11T07:32:00Z",
            "main before its first call; PR 584 came from a later re-delivery",
        ),
        Case(
            "crew-presentation",
            124,
            "af2c04f",
            "2026-10-17T05:48:15Z",
            "PR 142's parent; criterion 4's '0.5 days' kept as written",
        ),
        Case(
            "crew-presentation",
            83,
            "44d3c1d",
            "2026-10-15T21:04:42Z",
            "PR 93's parent; its failure was Google Fonts with no network",
        ),
    )
}


def clone(repo: str) -> Path:
    """The repository, fetched once; every run works in its own worktree of it."""
    path = CLONES / repo
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["gh", "repo", "clone", f"mqucifer/{repo}", str(path), "--", "-q"], check=True
        )
    else:
        subprocess.run(["git", "-C", str(path), "fetch", "-q", "origin"], check=True)
    return path


def story(case: Case) -> str:
    """The issue's body as it stood at the first attempt: the latest edit before it.

    Cached beside the case's runs, so every run reads the same text.
    """
    cached = RUNS / case.name / "story.md"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    query = (
        f'{{repository(owner:"mqucifer",name:"{case.repo}"){{issue(number:{case.card}){{title body '
        "userContentEdits(first:50){nodes{editedAt diff}}}}}"
    )
    out = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"], capture_output=True, text=True, check=True
    )
    issue = json.loads(out.stdout)["data"]["repository"]["issue"]
    edits = sorted(
        (e for e in issue["userContentEdits"]["nodes"] if e.get("diff") is not None),
        key=lambda e: e["editedAt"],
    )
    before = [e for e in edits if e["editedAt"] <= case.first_attempt]
    body = before[-1]["diff"] if before else (edits[0]["diff"] if edits else issue["body"])
    text = f"# {issue['title']}\n\n{body}"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text
