"""Replay mqucifer/crew-presentation#296's split and QA's criteria check on this branch
(crew#1037), before mqucifer/crew#1038 merges.

    uv run python experiments/criterion-rule-1037/replay.py
    uv run python experiments/criterion-rule-1037/replay.py --inputs   # no model calls

A fresh split, as a tick would make it today: the epic, its Goal
mqucifer/crew-presentation#214, the Goal's record as it stands, the board, and
crew-presentation's main, read through the same functions `refine_epics` calls. Then
QA's criteria check on the stories it made, one story's session at a time. No repair
and no re-check follow: the question is what the split and its first check do.

Real model calls, through LiteLLM on :4000 only, with the crew's aliases. The key is
read in this process from the main checkout's `.env`. GitHub is read with the
caller's `GH_TOKEN`, through clients that refuse every write. Everything goes to
`runs/<time>/`, which git ignores: `transcript.jsonl` (each call, its request and
answer), `events.jsonl`, `split.json`, `check.json` and `result.json`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from crew_org import calls, llm
from crew_org.config import load_env
from crew_org.crews.criteria_crew import check_criteria
from crew_org.crews.refinement_crew import split_epic
from crew_org.events import EventSink, attributed, forward_calls_to
from crew_org.flows import criteria_check, removed_tests
from crew_org.flows import record as record_flow
from crew_org.flows.board_flow import (
    KnownDelivered,
    RepoContext,
    _goal_body,
    _proposal_comment,
    planned_elsewhere,
)
from crew_org.flows.goal_look import diagram_of
from crew_org.flows.project_log import read_log
from crew_org.tools.coverage_map import stored
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import ProjectClient

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
CREW = Path("/Users/quarters/Documents/git/crew")  # the main checkout: its .env, read only
REPO, EPIC = "crew-presentation", 296

# What the replay may call on GitHub: reads only.
ISSUE_READS = frozenset(
    [
        "readable",
        "exists",
        "labelled",
        "closed_since",
        "updated_since",
        "open_issues",
        "get",
        "last_push",
        "comments",
        "repository",
        "branches",
        "pull_files",
        "open_pulls",
        "closed_pulls",
        "check_runs",
        "default_branch",
        "head_sha",
        "tag_exists",
        "release_for_tag",
        "releases",
        "list_dir",
        "tree",
        "file_at",
        "pull_diff",
        "compare_diff",
        "pull_reviews",
        "review_comments",
        "all_pulls",
        "people_who_edited",
        "pull",
        "search",
        "sub_issues",
        "permission",
        "has_comment_marked",
        "pages",
        "pages_live_since",
    ]
)
BOARD_READS = frozenset(
    ["schema", "cards", "bodies", "status_history", "counts", "sprints", "repo_of"]
)


class ReadOnly:
    """A client that answers its reads and refuses everything else."""

    def __init__(self, client: Any, reads: frozenset[str]) -> None:
        self._client, self._reads = client, reads
        self.owner = client.owner

    def __getattr__(self, name: str) -> Any:
        if name not in self._reads:
            raise PermissionError(f"the replay writes nothing to GitHub: {name}")
        return getattr(self._client, name)


class Checkout:
    """The workspace a tick's RepoContext reads: here, one clone of main."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def for_repo(self, repo: str) -> Checkout:
        return self

    def at_main(self) -> Path:
        return self.root

    def current(self) -> Path:
        return self.root


def recorded(path: Path) -> Any:
    """`calls.post`, each call written down with its time: the transcript."""
    sent = calls.post
    lock = threading.Lock()

    def post(body: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        began = time.monotonic()
        answer = sent(body, *args, **kwargs)
        with lock, path.open("a", encoding="utf-8") as out:
            out.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "seconds": round(time.monotonic() - began, 1),
                        "req": body,
                        "resp": answer,
                    }
                )
                + "\n"
            )
        return answer

    return post


def clone(into: Path) -> tuple[Path, str]:
    root = into / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", f"https://github.com/mqucifer/{REPO}", str(root)], check=True
    )
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return root, head


def coverage_from_the_crew(head: str) -> None:
    """The tick's map for this commit, if it has measured one: read, never written. Without
    it, RepoContext measures it in the sandbox, into this worktree's own `var/`."""
    theirs = stored(REPO, head, store=CREW / "var" / "coverage")
    mine = stored(REPO, head)
    if theirs.exists() and not mine.exists():
        mine.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(theirs, mine)


def serial(check: Any) -> Any:
    """QA's checks one at a time: the Spark is near its session limit."""
    lock = threading.Lock()

    def one(**shown: Any) -> Any:
        with lock:
            return check(**shown)

    return one


def criteria(proposal: Any) -> list[dict[str, Any]]:
    return [
        {
            "title": s.title,
            "points": s.points,
            "follows": s.follows,
            "criteria": [ac.model_dump() for ac in s.acceptance_criteria],
        }
        for s in proposal.stories
    ]


def usage(path: Path) -> dict[str, Any]:
    """Model time and tokens, from the transcript, by which step made the call."""
    by: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        call = json.loads(line)
        system = call["req"]["messages"][0]["content"]
        step = "criteria check" if "QA Engineer" in system[:200] else "split"
        used = call["resp"].get("usage") or {}
        into = by.setdefault(step, {"calls": 0, "seconds": 0.0, "prompt": 0, "completion": 0})
        into["calls"] += 1
        into["seconds"] += call["seconds"]
        into["prompt"] += used.get("prompt_tokens") or 0
        into["completion"] += used.get("completion_tokens") or 0
        into["reasoning"] = into.get("reasoning", 0) + (
            (used.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        )
    return by


def main() -> None:
    run = RUNS / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run.mkdir(parents=True)
    env = load_env(CREW / ".env")
    # The proxy's key, in this process only: never printed, never on a command line.
    if env.get("CREW_LLM_API_KEY"):
        os.environ["CREW_LLM_API_KEY"] = env["CREW_LLM_API_KEY"]
    owner, project = env["GITHUB_OWNER"], int(env["GITHUB_PROJECT_NUMBER"])
    del env
    if not llm.base_url().startswith("http://localhost:4000"):
        sys.exit(f"model traffic goes through LiteLLM on :4000 only, not {llm.base_url()}")
    ok, said = llm.health()
    if not ok:
        sys.exit(said)

    token = os.environ["GH_TOKEN"]
    issues = ReadOnly(IssueClient(token, owner), ISSUE_READS)
    board = ReadOnly(ProjectClient(token, owner, project), BOARD_READS)
    sink = EventSink(run / "events.jsonl")
    forward_calls_to(sink, card=EPIC)
    calls.post = recorded(run / "transcript.jsonl")

    root, head = clone(run)
    coverage_from_the_crew(head)
    context = RepoContext(Checkout(root), sink, issues)  # type: ignore[arg-type]
    cards = board.cards()
    epic = next(c for c in cards if c.number == EPIC and (c.repo or REPO) == REPO)
    goal = epic.parent
    known = KnownDelivered(issues, sink)  # type: ignore[arg-type]
    known.cards = cards
    done = known.for_repo(REPO)
    began = time.monotonic()

    # What `refine_epics` gives the split, for a fresh one: no feedback, nothing kept.
    body = record_flow.split(_goal_body(issues, REPO, EPIC))[0]  # type: ignore[arg-type]
    conclusion = record_flow.text_for(issues, REPO, EPIC)
    rows = record_flow.parse(conclusion).binding_ids()
    declared = record_flow.parse(conclusion).declared_tests()
    diagram = diagram_of(issues, REPO, goal) if goal else ""  # type: ignore[arg-type]
    goal_text = _goal_body(issues, REPO, goal) if goal else ""  # type: ignore[arg-type]
    project_log = read_log(issues, REPO)
    planned = planned_elsewhere(cards, [], repo=REPO, epic=EPIC)
    reading = context.reading_for(REPO)
    if reading is None:
        sys.exit("no checkout to read: see events.jsonl")
    inputs = {
        "head": head,
        "epic": epic.title,
        "goal": goal,
        "rows": rows,
        "declared": declared,
        "planned": planned,
        "delivered": len(done.stories),
        "coverage": reading.coverage is not None,
        "conclusion_chars": len(conclusion),
    }
    (run / "inputs.json").write_text(json.dumps(inputs, indent=2))
    print(f"inputs: {json.dumps(inputs)[:600]}", flush=True)
    if "--inputs" in sys.argv:
        return

    split_began = time.monotonic()
    proposal = attributed(split_epic, card=EPIC, repo=REPO)(
        epic.title,
        body,
        epic_ref=f"{owner}/{REPO}#{EPIC}",
        superseded=None,
        kept="",
        replaced="",
        feedback="",
        delivered=done.render(),
        delivered_numbers=done.numbers,
        planned="\n".join(f"- #{n} {title} (epic #{e})" for n, title, e in planned),
        planned_numbers={n for n, _t, _e in planned},
        conclusion=conclusion,
        project_log=project_log,
        diagram=diagram,
        provided=context.provided_for(REPO),
        rows=rows,
        declared=declared,
        reading=reading,
    )
    split_s = time.monotonic() - split_began
    (run / "split.json").write_text(proposal.model_dump_json(indent=2))
    print(f"split: {len(proposal.stories)} stories in {split_s / 60:.1f} min", flush=True)

    tests = removed_tests.Index.of(reading.root)
    check_began = time.monotonic()
    checked = criteria_check.check_each(
        serial(attributed(check_criteria, card=EPIC, repo=REPO)),
        proposal,
        precheck=partial(removed_tests.undeclared, tests),
        planned_in={n: e for n, _t, e in planned if e != EPIC},
        planned=criteria_check.planned_criteria(
            issues, REPO, planned, cards=cards, goal=goal, epic=EPIC
        ),
        order=criteria_check.goal_order(
            _proposal_comment(issues, REPO, goal),  # type: ignore[arg-type]
            cards,
            goal=goal,
            epic=EPIC,
            conclusion=conclusion,
        )
        if goal
        else "",
        conclusion=conclusion,
        goal=goal_text,
        project_log=project_log,
        reading=reading,
    )
    check_s = time.monotonic() - check_began
    (run / "check.json").write_text(checked.model_dump_json(indent=2))
    result = {
        "inputs": inputs,
        "stories": criteria(proposal),
        "conflicts": [c.model_dump() for c in checked.conflicts],
        "flagged": [s.title for s in criteria_check.flagged(proposal, checked)],
        "wall_minutes": {
            "split": round(split_s / 60, 1),
            "check": round(check_s / 60, 1),
            "all": round((time.monotonic() - began) / 60, 1),
        },
        "model": usage(run / "transcript.jsonl"),
    }
    (run / "result.json").write_text(json.dumps(result, indent=2))
    print(f"check: {len(checked.conflicts)} conflicts in {check_s / 60:.1f} min", flush=True)
    print(f"wrote {run}", flush=True)


if __name__ == "__main__":
    main()
