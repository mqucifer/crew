"""How often a refinement panel member repeats another member's note on the same epic.

Reads every panel comment (the `crew:panel-data` block the panel posts on an epic) in
the delivery repositories since a date, and counts, per panel:
- notes, and words per note (problem, source and settle);
- notes that overlap a different member's note on the same epic;
- each member's "nothing to add", and who each note says settles it.

Two notes overlap when their problems share at least a fifth of their content words
(Jaccard 0.2), or they cite at least two of the same files. That's a rough measure: it
was checked by reading sampled pairs (README), not scored note by note as panel-174 was.

Run from the repository root, with `gh` signed in:

    python3 experiments/panel-overlap-828/analyse.py [since]
"""

from __future__ import annotations

import collections
import itertools
import json
import re
import statistics
import subprocess
import sys

REPOS = ("mqucifer/sprint-metrics", "mqucifer/crew-presentation")
SESSIONS_FROM = "2026-10-19"  # the panel became sessions over the code (crew#807)
STOP = frozenset(
    {
        "the", "a", "an", "of", "to", "in", "and", "or", "is", "it", "its", "for", "on",
        "that", "this", "with", "as", "be", "by", "not", "are", "at", "from", "what",
        "which", "when", "each", "epic", "story", "stories", "goal",
    }
)  # fmt: skip
DATA = re.compile(r"crew:panel-data (\{.*\}) -->", re.S)
FILE_REF = re.compile(r"[\w/.-]+\.(?:py|json|md|yaml|yml|ts|js)(?::\d+)?")


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_][a-z0-9_]{2,}", text.lower()) if w not in STOP}


def panels(since: str) -> list[dict]:
    found = []
    for repo in REPOS:
        out = subprocess.run(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{repo}/issues/comments?since={since}&per_page=100",
                "-q",
                '.[]|select(.body|contains("crew:panel-data"))|{issue:(.issue_url|split("/")|last),at:.created_at,body:.body}',
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for line in out.splitlines():
            comment = json.loads(line)
            match = DATA.search(comment["body"])
            if match:
                found.append(
                    {
                        "repo": repo,
                        "issue": comment["issue"],
                        "at": comment["at"],
                        "answers": json.loads(match.group(1))["answers"],
                    }
                )
    return found


def overlapping(notes: list[tuple[str, dict]]) -> set[int]:
    hit: set[int] = set()
    for (i, (role_a, a)), (j, (role_b, b)) in itertools.combinations(enumerate(notes), 2):
        if role_a == role_b:
            continue
        wa, wb = words(a["problem"]), words(b["problem"])
        jaccard = len(wa & wb) / max(1, len(wa | wb))
        shared = FILE_REF.findall(a["source"] + a["problem"])
        shared = set(shared) & set(FILE_REF.findall(b["source"] + b["problem"]))
        if jaccard >= 0.2 or len(shared) >= 2:
            hit |= {i, j}
    return hit


def main(since: str = "2026-10-01T00:00:00Z") -> None:
    eras: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0, 0])
    asked: collections.Counter = collections.Counter()
    nothing: collections.Counter = collections.Counter()
    settled_by: collections.Counter = collections.Counter()
    sizes: list[int] = []
    for panel in panels(since):
        notes = [(role, n) for role, a in panel["answers"].items() for n in a.get("notes", [])]
        for role, answer in panel["answers"].items():
            asked[role] += 1
            nothing[role] += bool(answer.get("nothing_to_add"))
        for _, n in notes:
            sizes.append(len((n["problem"] + n["source"] + n["settle"]).split()))
            settled_by[n["settled_by"]] += 1
        era = "sessions" if panel["at"] >= SESSIONS_FROM else "one call"
        row = eras[era]
        row[0] += 1
        row[1] += len(notes)
        row[2] += len(overlapping(notes))
        row[3] += sum(len((n["problem"] + n["source"] + n["settle"]).split()) for _, n in notes)
    for era, (count, notes, overlap, total) in eras.items():
        print(
            f"{era}: {count} panels, {notes} notes ({notes / count:.1f} a panel), "
            f"{overlap} overlapping ({100 * overlap // max(1, notes)}%), "
            f"{total // count} words a panel"
        )
    print("median words a note:", statistics.median(sizes))
    print("nothing to add:", {r: f"{nothing[r]}/{asked[r]}" for r in asked})
    print("settled by:", dict(settled_by))


if __name__ == "__main__":
    main(*sys.argv[1:])
