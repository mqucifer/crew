"""QA's session replayed on a delivered story, beside the verdict its one call gave (crew#895).

On a clone of sprint-metrics, never the real repository: the clone's push URL is
disabled first, and nothing here pushes, comments or moves a card. The story's branch
is checked out at the commit QA's one call judged, as a worktree of the clone, with the
default branch put back where the branch left it, so `base` and the services are as QA
saw them. The session runs in the story's held workspace and calls QA's model through
LiteLLM with the builders' key, read inside this process. GitHub is only read: the
story, its rows and diagram, and the checks on that commit. QA's earlier verdicts are
left out, so the session judges the story cold, as the one call's first verdict did.

    uv run python experiments/qa-session-895/replay.py <clone> <story> <revision> <base>

It writes the events, the verdict and the one call's posted verdict to
`var/sessions/qa-895-<story>/`, and prints each criterion's two verdicts side by side.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from crew_org import profiles
from crew_org.config import load_env
from crew_org.crews import qa_crew
from crew_org.events import EventSink, forward_calls_to
from crew_org.flows import acceptance
from crew_org.flows.conclusion import story_diagram, story_rows
from crew_org.project import read_record
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"
OUT = Path("var/sessions")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def posted(issues: IssueClient, number: int, revision: str) -> str:
    """The one call's verdict on this commit, as it was posted."""
    marker = acceptance.qa_marker(revision)
    found = [c["body"] for c in issues.comments(REPO, number) if marker in (c.get("body") or "")]
    return found[-1] if found else ""


def one_call_marks(body: str) -> list[bool]:
    """Each criterion's proven or not, from a posted verdict."""
    marks = re.findall(r"^- (proven|\*\*not proven\*\*)", body, re.M)
    return [not m.startswith("**not") for m in marks]


def parent_of(number: int) -> int | None:
    """The story's epic, from GitHub's sub-issues API, read with `gh`."""
    found = subprocess.run(
        ["gh", "api", f"repos/mqucifer/{REPO}/issues/{number}/parent", "--jq", ".number"],
        capture_output=True,
        text=True,
    )
    return int(found.stdout) if found.returncode == 0 and found.stdout.strip() else None


def main(clone: Path, number: int, revision: str, base: str) -> None:
    clone = clone.resolve()
    if git(clone, "remote", "get-url", "--push", "origin").strip() != "DISABLED":
        sys.exit("disable the clone's push URL: git remote set-url --push origin DISABLED")
    key = load_env(Path.home() / ".config/crew/builders.env").get("CREW_LLM_API_KEY")
    if not key:
        sys.exit("no CREW_LLM_API_KEY in builders.env")
    os.environ["CREW_LLM_API_KEY"] = key

    revision = git(clone, "rev-parse", revision).strip()
    base = git(clone, "rev-parse", base).strip()
    # The default branch where the story's branch left it: `base`, the services and
    # `merged_base` all read origin/HEAD.
    subprocess.run(["git", "symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=clone)
    git(clone, "update-ref", "refs/remotes/origin/HEAD", base)
    worktree = clone.parent / f"qa-895-{number}"
    if worktree.exists():
        git(clone, "worktree", "remove", "--force", str(worktree))
    git(clone, "worktree", "add", "-q", "--detach", str(worktree), revision)

    out = OUT / f"qa-895-{number}"
    out.mkdir(parents=True, exist_ok=True)
    log = out / "events.jsonl"
    log.unlink(missing_ok=True)
    sink = EventSink(log)
    forward_calls_to(sink, card=number)
    profiles.set_project(read_record(worktree))

    issues = IssueClient(os.environ["GH_TOKEN"], "mqucifer")
    story = issues.get(REPO, number)
    parent = parent_of(number)
    card = Card(
        item_id=f"S{number}",
        number=number,
        title=story["title"],
        repo=REPO,
        status="QAing",
        state="OPEN",
        work_type="Story",
        parent=parent,
    )
    changed = acceptance.changed_paths(worktree)
    held = acceptance.hold(worktree, f"{REPO}-{number}-qa-895")
    try:
        verdict = qa_crew.verify_story(
            f"{story['title']}\n\n{story.get('body') or ''}",
            worktree=worktree,
            repo=REPO,
            github=issues,
            workspace=held,
            base=base,
            checks=acceptance.ci_checks(issues, REPO, revision),
            decisions=story_rows(issues, card, REPO),
            diagram=story_diagram(issues, card, REPO),
            changed=changed,
            unguarded=acceptance.unguarded(changed),
        )
    finally:
        held.stop()
        git(clone, "worktree", "remove", "--force", str(worktree))

    before = posted(issues, number, revision)
    (out / "verdict.json").write_text(verdict.model_dump_json(indent=2))
    (out / "session-verdict.md").write_text(acceptance.render_qa(verdict, revision))
    (out / "one-call-verdict.md").write_text(before)

    events = [json.loads(line) for line in log.read_text().splitlines()]
    tools = Counter(e["detail"].get("tool") for e in events if e["kind"] == "tool.finished")
    calls = sum(e["kind"] == "llm.finished" for e in events)
    refused = [
        e["detail"].get("rule")
        for e in events
        if e["kind"] == "tool.finished" and e["detail"].get("tool") == "done"
    ]
    print(f"#{number} at {revision[:12]} (base {base[:12]}): model calls {calls}")
    print(f"tools: {dict(tools)}")
    print(f"`done` replies: {refused}")
    print(f"ran: {[r.command for r in held.ran]}")
    print(
        f"session: {'accepted' if verdict.accepted else 'not accepted'}; one call: "
        f"{'accepted' if '## QA — accepted' in before else 'not accepted'}"
    )
    marks = one_call_marks(before)
    for n, c in enumerate(verdict.criteria, start=1):
        was = marks[n - 1] if n <= len(marks) else None
        print(
            f"  {n}. session {'proven' if c.proven else 'NOT'} by {c.by}; one call "
            f"{'proven' if was else 'NOT' if was is not None else '?'}"
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4])
