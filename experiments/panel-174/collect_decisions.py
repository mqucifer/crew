"""The Sponsor's recorded decisions for Goal #174, collected by a written rule.

    uv run python experiments/panel-174/collect_decisions.py

Writes `inputs/decisions.md` (what the panel is shown as item 4) and
`inputs/decisions-sources.json` (what the rule found, and from where).

**The rule.** Everything the Sponsor wrote before CUTOFF that is either on the
Goal's own cards or names the Goal:

1. On sprint-metrics Goal #174 and every sub-issue under it: the Sponsor's
   comments, and any section of a card's body whose heading names a
   "Sponsor decision".
2. In the crew and sprint-metrics repositories: any issue the Sponsor opened
   whose body, or one of the Sponsor's comments on it, names the Goal
   (`sprint-metrics#174`, `Goal #174`, or its URL), with the Sponsor's
   comments on it.
3. Any other comment by the Sponsor in those repositories that names the Goal.

Bodies are taken as they stood at CUTOFF, from their edit history.

**Why not "names the repository".** 133 crew issues by the Sponsor name
sprint-metrics, nearly all about the crew's own mechanics with sprint-metrics
as evidence. That is not a context any role could be given; a production rule
has to be per Goal.

**Why the cutoff is the start of 2026-10-01**, not the Goal's revision at
13:35Z: the review of these epics ran that day, and the decisions it produced
were recorded during it (crew#280's comment at 13:30Z names #184's in-memory
history, finding 1). The panel would be reading the answer.

**One revision after the first run.** Rule 2 first looked at the body only,
and found crew#280's one-line comment ("The service Goal is
sprint-metrics#174") but not crew#280 itself, whose body says "#174's API".
A comment that links an issue to the Goal makes the issue part of it, so
rule 2 now counts the Sponsor's comments too. This was changed after seeing
the output, so it is named here: crew#280 is one of the sources the design
expected. Nothing else was changed to reach the others; crew#283 (OTLP) and
crew#389 (the counting rule) never name the Goal, and the rule doesn't find
them.

**The Sponsor's login** is the account the operator's `gh` also runs as, so an
issue the operator filed counts as the Sponsor's. That is how the crew would
see it too: the crew's own posts come from its App.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
OWNER = "mqucifer"
REPOS = ["crew", "sprint-metrics"]
GOAL_REPO, GOAL = "sprint-metrics", 174
SPONSOR = "mquarters"
CUTOFF = "2026-10-01T00:00:00Z"
NAMES_GOAL = re.compile(r"sprint-metrics\s*#174\b|Goal\s*#174\b|sprint-metrics/issues/174\b", re.I)
DECISION_HEADING = re.compile(r"^(#{1,6})\s+.*Sponsor decision", re.I | re.M)

ISSUE_FIELDS = """
  number title url createdAt author { login }
  body lastEditedAt
  userContentEdits(first: 50) { nodes { editedAt diff } }
  comments(first: 100) { nodes { author { login } createdAt url body } }
"""


def gql(query: str, **variables) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return json.loads(out)["data"]


def issue(repo: str, number: int) -> dict:
    q = f"""query($o:String!,$r:String!,$n:Int!){{repository(owner:$o,name:$r){{issue(number:$n){{
      {ISSUE_FIELDS} subIssues(first: 50) {{ nodes {{ number }} }} }}}}}}"""
    return gql(q, o=OWNER, r=repo, n=number)["repository"]["issue"]


def body_at_cutoff(item: dict) -> str | None:
    """The body as it stood at CUTOFF, or None if it didn't exist yet."""
    if item["createdAt"] >= CUTOFF:
        return None
    if not item.get("lastEditedAt") or item["lastEditedAt"] < CUTOFF:
        return item["body"]
    before = [e for e in item["userContentEdits"]["nodes"] if e["editedAt"] < CUTOFF]
    if not before:
        return None
    return max(before, key=lambda e: e["editedAt"])["diff"]


def sponsor_comments(item: dict) -> list[dict]:
    return [
        c
        for c in item["comments"]["nodes"]
        if (c["author"] or {}).get("login") == SPONSOR and c["createdAt"] < CUTOFF
    ]


def decision_sections(body: str) -> list[str]:
    sections = []
    for m in DECISION_HEADING.finditer(body):
        level = len(m.group(1))
        rest = body[m.end() :]
        end = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
        sections.append((m.group(0) + (rest[: end.start()] if end else rest)).strip())
    return sections


def search(repo: str) -> list[int]:
    """Issues in `repo` mentioning 174 anywhere; filtered by the rule afterwards."""
    out = subprocess.run(
        [
            "gh",
            "search",
            "issues",
            "174",
            "--repo",
            f"{OWNER}/{repo}",
            "--limit",
            "200",
            "--json",
            "number",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({i["number"] for i in json.loads(out)})


def main() -> None:
    found: list[dict] = []

    # 1. The Goal's own cards.
    goal = issue(GOAL_REPO, GOAL)
    tree = [GOAL] + [n["number"] for n in goal["subIssues"]["nodes"]]
    for n in tree:
        card = goal if n == GOAL else issue(GOAL_REPO, n)
        body = body_at_cutoff(card) or ""
        for s in decision_sections(body):
            found.append(
                {
                    "rule": 1,
                    "source": f"{GOAL_REPO}#{n}",
                    "url": card["url"],
                    "kind": "body section",
                    "text": s,
                }
            )
        for c in sponsor_comments(card):
            found.append(
                {
                    "rule": 1,
                    "source": f"{GOAL_REPO}#{n}",
                    "url": c["url"],
                    "kind": f"comment {c['createdAt']}",
                    "text": c["body"].strip(),
                }
            )

    # 2 and 3. Elsewhere, anything that names the Goal.
    for repo in REPOS:
        for n in search(repo):
            if repo == GOAL_REPO and n in tree:
                continue
            item = issue(repo, n)
            body = body_at_cutoff(item)
            authored = (item["author"] or {}).get("login") == SPONSOR
            comments = sponsor_comments(item)
            linked = any(NAMES_GOAL.search(c["body"]) for c in comments)
            if authored and body and (NAMES_GOAL.search(body) or linked):
                found.append(
                    {
                        "rule": 2,
                        "source": f"{repo}#{n}",
                        "url": item["url"],
                        "kind": "issue body",
                        "title": item["title"],
                        "text": body.strip(),
                    }
                )
                for c in comments:
                    found.append(
                        {
                            "rule": 2,
                            "source": f"{repo}#{n}",
                            "url": c["url"],
                            "kind": f"comment {c['createdAt']}",
                            "text": c["body"].strip(),
                        }
                    )
                continue
            for c in sponsor_comments(item):
                if NAMES_GOAL.search(c["body"]):
                    found.append(
                        {
                            "rule": 3,
                            "source": f"{repo}#{n}",
                            "url": c["url"],
                            "kind": f"comment {c['createdAt']}",
                            "title": item["title"],
                            "text": c["body"].strip(),
                        }
                    )

    out = HERE / "inputs"
    out.mkdir(exist_ok=True)
    (out / "decisions-sources.json").write_text(
        json.dumps({"sponsor": SPONSOR, "cutoff": CUTOFF, "found": found}, indent=2) + "\n"
    )
    parts = [
        "# The Sponsor's recorded decisions\n",
        f"What the Sponsor wrote before {CUTOFF[:10]} on Goal #{GOAL}'s cards, "
        "or naming the Goal elsewhere.\n",
    ]
    for f in found:
        heading = f["source"] + (f": {f['title']}" if f.get("title") else "") + f" ({f['kind']})"
        parts.append(f"## {heading}\n\n{f['text']}\n")
    (out / "decisions.md").write_text("\n".join(parts))
    for f in found:
        print(f["rule"], f["source"], f["kind"], len(f["text"]))


if __name__ == "__main__":
    main()
