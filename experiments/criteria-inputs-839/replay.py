"""The start sprint-metrics#752's re-check would get now, from its real records (crew#839).

Read-only, and no model calls. It reads the sessions' records the operator kept (the
model call each session ended with, its start included), the sprint-metrics checkout
those sessions read (head 9602a12), and, with `gh`, the stories the split then created
and the board's statuses now. It renders the re-check's start with today's code and
compares it, part by part, with the start the re-check had. It also runs the pre-check
on sprint-metrics#753's /range story.

    uv run python experiments/criteria-inputs-839/replay.py <records dir> <checkout>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from crew_org.crews.criteria_crew import CriteriaConflict, opening
from crew_org.crews.refinement_crew import AcceptanceCriterion, Story
from crew_org.flows import criteria_check, removed_tests
from crew_org.flows.board_flow import builds_on
from crew_org.flows.conclusion import parse_follows
from crew_org.session import steps

REPO = "mqucifer/sprint-metrics"
HEAD = "9602a1213ab123604b7a7f0f3741783b080aa168"
FIRST = "52b52bde04074ba8a9584066f77f7882"  # 752's first check of the story: the conflict
RECHECK = "c4910606276b44e2b87e0f915b535965"  # its re-check after the repair: 33.0 minutes
RANGE = "b0e1838115f04e808a8c14d6f4b6aa79"  # 753's /range story
GOAL, EPIC = 749, 752
# The parts of a recorded start, by the heading each begins with.
HEADINGS = {
    "step": "## Your step:",
    "goal": "## The Goal, set by the Sponsor",
    "project_log": "## The project's decision log",
    "conclusion": "## The epic's record, decided before the split",
    "siblings": "## The other stories in this split",
    "planned": "## Stories other epics already plan",
    "stories": "## The story under check",
    "project": "## The project\n",
    "map": "## The map:",
    "hints": "## Likely relevant",
    "rules": "## How this session works",
}
# The tests QA named on sprint-metrics#753: in the /range story's session, and in the brief.
QA_NAMED = {
    "tests/test_report.py": "the /range story's session",
    "tests/test_scrape.py": "the brief",
    "tests/test_prior_sprint.py": "the brief",
    "tests/test_schema_gen.py": "the brief",
}


def record(records: Path, name: str) -> dict:
    return json.loads((records / f"{name}.json").read_text())


def start_of(call: dict) -> str:
    return call["req"]["messages"][1]["content"]


def parts_of(start: str) -> dict[str, str]:
    at = sorted((start.find(h), key) for key, h in HEADINGS.items() if h in start)
    return {
        key: start[pos : (at[i + 1][0] if i + 1 < len(at) else len(start))].strip()
        for i, (pos, key) in enumerate(at)
    }


def body(part: str) -> str:
    return part.split("\n\n", 1)[1] if "\n\n" in part else ""


_CRITERION = re.compile(r"^\d+\. Given (.*?) / When (.*?) / Then (.*)$")


def stories_in(text: str) -> list[Story]:
    found = []
    for block in re.split(r"(?m)^(?=### )", text):
        lines = block.strip().splitlines()
        if not lines or not lines[0].startswith("### "):
            continue
        criteria = [
            AcceptanceCriterion(given=m[1], when=m[2], then=m[3])
            for ln in lines
            if (m := _CRITERION.match(ln))
        ]
        pinned = next((ln[16:] for ln in lines if ln.startswith("Existing tests: ")), "")
        found.append(
            Story(
                title=lines[0][4:],
                as_a="-",
                i_want="-",
                so_that="-",
                acceptance_criteria=criteria,
                points=3,
                pinned_behaviour=pinned,
            )
        )
    return found


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def issue(number: int) -> dict:
    return json.loads(
        gh(
            "issue",
            "view",
            str(number),
            "-R",
            REPO,
            "--json",
            "number,title,body,state,projectItems",
        )
    )


def card(found: dict, parent: int | None, work_type: str) -> SimpleNamespace:
    status = next(
        (p["status"]["name"] for p in found["projectItems"] if p["title"] == "Crew Delivery"),
        "not on the board",
    )
    if found["state"] == "CLOSED":
        status = f"{status} (closed)"
    return SimpleNamespace(
        number=found["number"],
        title=found["title"],
        status=status,
        parent=parent,
        work_type=work_type,
    )


def parent_of(number: int) -> int | None:
    try:
        return json.loads(gh("api", f"repos/{REPO}/issues/{number}/parent"))["number"]
    except subprocess.CalledProcessError:
        return None


def conflicts_of(call: dict) -> list[CriteriaConflict]:
    calls = call["resp"]["choices"][0]["message"].get("tool_calls") or []
    said = json.loads(calls[-1]["function"]["arguments"])
    return [CriteriaConflict(**c) for c in said["conflicts"]]


def with_order(stories: list[Story], created: dict[str, str]) -> list[Story]:
    """Each story with the rows it follows and what it builds on, from its issue."""
    return [
        s.model_copy(
            update={
                "follows": parse_follows(created.get(s.title, "")),
                "builds_on": sorted(builds_on(created.get(s.title, ""))),
            }
        )
        for s in stories
    ]


def main(records: Path, checkout: Path) -> None:
    first, recheck = record(records, FIRST), record(records, RECHECK)
    had = parts_of(start_of(recheck))
    # The split's stories as GitHub holds them: their Follows and Builds on lines.
    subs = json.loads(gh("api", f"repos/{REPO}/issues/{EPIC}/sub_issues"))
    created = {s["title"]: s["body"] or "" for s in subs}
    [story] = with_order(stories_in(body(had["stories"])), created)
    siblings = with_order(stories_in(body(had["siblings"])), created)
    [was] = stories_in(body(parts_of(start_of(first))["stories"]))
    before = criteria_check.earlier([was], SimpleNamespace(conflicts=conflicts_of(first)))

    # The other epics' planned stories it was shown, with their epics, as the board has them.
    shown = re.findall(r"^### #(\d+) (.*) \(epic #(\d+)\)$", had["planned"], re.M)
    planned = [(int(n), t, int(e)) for n, t, e in shown]
    epics = {e for _n, _t, e in planned} | {751, 752, 753}
    cards = [card(issue(n), e, "Story") for n, _t, e in planned]
    cards += [card(issue(e), parent_of(e), "Epic") for e in sorted(epics)]

    class Issues:
        def get(self, repo: str, number: int) -> dict:
            return issue(number)

    conclusion = body(had["conclusion"])
    proposal = next(
        (
            c["body"]
            for c in reversed(
                json.loads(gh("issue", "view", str(GOAL), "-R", REPO, "--json", "comments"))[
                    "comments"
                ]
            )
            if "<!-- crew:epic-proposal -->" in c["body"]
        ),
        "",
    )
    index = removed_tests.Index.of(checkout)
    facts = removed_tests.described(removed_tests.undeclared(index, story))
    parts = opening(
        stories=criteria_check.render_stories([story]),
        siblings=criteria_check.render_stories(siblings),
        planned=criteria_check.planned_criteria(
            Issues(), "sprint-metrics", planned, cards=cards, goal=GOAL, epic=EPIC
        ),
        conclusion=criteria_check.record_for_story(conclusion, story),
        project_log=had.get("project_log", ""),
        order=criteria_check.goal_order(
            proposal, cards, goal=GOAL, epic=EPIC, conclusion=conclusion
        ),
        recheck=criteria_check.repaired(before[criteria_check._key(was.title)], story),
        facts=facts,
    )
    # The Goal's part is as it was, rendered from the Goal's words.
    parts = {k: (had["goal"] if k == "goal" else v) for k, v in parts.items()}
    now = steps.start(
        parts,
        checkout,
        repo="sprint-metrics",
        head=HEAD,
        about=parts["stories"],
        named_in="the story",
    )
    text = now.text
    print("## The 752 re-check's start, then and now\n")
    print("| Part | Then | Now |\n|---|---:|---:|")
    for key in dict.fromkeys([*had, *now.sizes]):
        if key == "total":
            continue
        print(f"| {key} | {len(had.get(key, '')):,} | {now.sizes.get(key, 0):,} |")
    print(f"| **total** | {len(start_of(recheck)):,} | {now.sizes['total']:,} |\n")
    conflict = conflicts_of(first)[0]
    print("Carries the conflict it found:", conflict.criterion[:60] in text)
    print("Carries its earlier verdict (the why):", conflict.why[:60] in text)
    print("Says it's a re-check:", "## This is a re-check" in text)
    print("Follows:", story.follows, " Builds on:", story.builds_on)
    # The planned part's size by where its stories stand: whole criteria grow it.
    shown_planned = parts["planned"]
    others = [b for b in re.split(r"(?m)^(?=### )", shown_planned) if "Goal #" in b.split("\n")[0]]
    print(f"Planned, another Goal's epics: {sum(len(b) for b in others):,} characters")
    live = [p for p in planned if "closed" not in next(c.status for c in cards if c.number == p[0])]
    open_now = criteria_check.planned_criteria(
        Issues(), "sprint-metrics", live, cards=cards, goal=GOAL, epic=EPIC
    )
    print(
        "Planned, the stories open on the board now (as `planned_elsewhere` gives them): "
        f"{len(live)} of {len(planned)}, {len(open_now):,} characters"
    )
    print("\n### The re-check part\n")
    print(parts["recheck"])
    print("\n### The order part\n")
    print(parts["order"])
    print("\n### The planned part's epic headings\n")
    print("\n".join(ln for ln in parts["planned"].splitlines() if ln.startswith("###")))
    print("\n### The record, its one-line rows\n")
    print(parts["conclusion"].rpartition("The other rows, one line each:")[2].strip())
    print("\n### The pre-check on the story under check\n")
    print(facts or "(none)")

    [ranged] = stories_in(body(parts_of(start_of(record(records, RANGE)))["stories"]))
    print("\n## The pre-check on 753's /range story\n")
    print("Names its criteria remove:", removed_tests.removed_names(ranged))
    found = removed_tests.undeclared(index, ranged)
    files = sorted({k.split("::")[0] for k in found})
    print(f"Undeclared merged tests: {len(found)}, in {len(files)} files")
    for f in files:
        print(f"- {f}: {sum(1 for k in found if k.split('::')[0] == f)}")
    print("\nAgainst the files QA named:")
    for f, where in QA_NAMED.items():
        print(f"- {f} ({where}): {'found' if f in files else 'not found'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
