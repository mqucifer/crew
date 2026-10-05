"""The Sponsor's recorded decisions for a Goal, collected by a written rule (crew#440).

Epics are proposed from a Goal early and split much later, and the Sponsor
decides things in between. On 2026-10-01 the split of sprint-metrics Goal 174
contradicted four of those decisions because nothing put them in front of it.
The panel and the split are shown what this returns.

**The rule.** Everything the Sponsor has written that is either on the Goal's
own cards or names the Goal:

1. On the Goal and every sub-issue under it: the Sponsor's comments, and any
   section of a card's body whose heading names the Sponsor (the Goal's
   decision log, or `(Sponsor, 2026-09-29)` as crew#280 writes it).
2. In the searched repositories: any issue the Sponsor opened whose body, or
   one of the Sponsor's comments on it, names the Goal. From it, the
   Sponsor's comments and the body sections headed as above, and nothing else
   of the body. A comment that links an issue to the Goal makes the issue part
   of it: crew#280's one-line comment was the only link to its decision on
   storage.
3. Any other comment by the Sponsor in those repositories that names the Goal.

**Why per Goal, and not "names the repository".** 133 crew issues by the
Sponsor name sprint-metrics, nearly all about the crew's own mechanics with
sprint-metrics as evidence. That is not a context any role could be given.

**Why not a whole body in rule 2.** The Sponsor's login also opens the crew's
defect reports, which cite the Goal as evidence ("found in the Sprint 8 tick,
decomposing sprint-metrics#171"). Counted whole, they were about 15 KB of the
36 KB for Goal 174, and four of five other Goals pulled in the same kind. A
decision in an issue body has to sit under a heading that names the Sponsor.

**Who is the Sponsor** is `org.yaml`'s `trust.sponsor`, as everywhere else
(crew#399): only that login's words are direction. `IssueClient.comments`
already drops everyone else's.

The rule was found and checked in `experiments/panel-174/collect_decisions.py`
against a fixed cutoff; this is the same rule as of now.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from crew_org.tools.github_issues import IssueClient, IssueError, author

DECISION_HEADING = re.compile(r"^(#{1,6})\s+.*\bSponsor\b", re.I | re.M)


@dataclass(frozen=True)
class Decision:
    rule: int
    # `owner/repo#number`: the explicit form, so it can be quoted anywhere (crew#456).
    source: str
    url: str
    kind: str
    text: str
    title: str = ""


@dataclass(frozen=True)
class Collected:
    decisions: list[Decision]
    # Repositories the token can't search (the App isn't installed on a private one).
    # Said aloud, because a silently skipped repository is a decision nobody saw.
    unsearched: list[str]


def names_goal(repo: str, number: int) -> re.Pattern[str]:
    """How the Sponsor's words name a Goal: `repo#n`, `Goal #n` or its URL."""
    return re.compile(
        rf"{re.escape(repo)}\s*#{number}\b|Goal\s*#{number}\b|{re.escape(repo)}/issues/{number}\b",
        re.I,
    )


def decision_sections(body: str) -> list[str]:
    """Each section whose heading names the Sponsor, to the next heading as high."""
    sections = []
    for m in DECISION_HEADING.finditer(body):
        level = len(m.group(1))
        rest = body[m.end() :]
        end = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
        sections.append((m.group(0) + (rest[: end.start()] if end else rest)).strip())
    return sections


def _comments(issues: IssueClient, repo: str, number: int, sponsor: str) -> list[dict[str, Any]]:
    return [c for c in issues.comments(repo, number) if author(c) == sponsor]


def _comment(rule: int, source: str, c: dict[str, Any], title: str = "") -> Decision:
    return Decision(
        rule=rule,
        source=source,
        url=c.get("html_url", ""),
        kind=f"comment {c.get('created_at', '')}",
        text=(c.get("body") or "").strip(),
        title=title,
    )


def collect_decisions(
    issues: IssueClient, *, goal_repo: str, goal: int, repos: Iterable[str]
) -> Collected:
    """The Sponsor's decisions for Goal `goal` in `goal_repo`, searching `repos` for the rest."""
    sponsor = issues.trust.sponsor
    pattern = names_goal(goal_repo, goal)
    found: list[Decision] = []
    unsearched: list[str] = []

    # 1. The Goal's own cards.
    tree = [goal] + [c["number"] for c in issues.sub_issues(goal_repo, goal)]
    for n in tree:
        card = issues.get(goal_repo, n)
        source = f"{issues.owner}/{goal_repo}#{n}"
        for section in decision_sections(card.get("body") or ""):
            found.append(Decision(1, source, card["html_url"], "body section", section))
        found += [_comment(1, source, c) for c in _comments(issues, goal_repo, n, sponsor)]

    # 2 and 3. Elsewhere, anything that names the Goal.
    for repo in dict.fromkeys(repos):
        try:
            numbers = issues.search(repo, str(goal))
        except IssueError:
            unsearched.append(repo)
            continue
        for n in numbers:
            if repo == goal_repo and n in tree:
                continue
            item = issues.get(repo, n)
            source = f"{issues.owner}/{repo}#{n}"
            title = item.get("title", "")
            body = (item.get("body") or "").strip()
            comments = _comments(issues, repo, n, sponsor)
            authored = author(item) == sponsor
            linked = any(pattern.search(c.get("body") or "") for c in comments)
            if authored and body and (pattern.search(body) or linked):
                found += [
                    Decision(2, source, item["html_url"], "body section", s, title)
                    for s in decision_sections(body)
                ]
                found += [_comment(2, source, c, title) for c in comments]
                continue
            found += [
                _comment(3, source, c, title)
                for c in comments
                if pattern.search(c.get("body") or "")
            ]
    return Collected(found, unsearched)


def render(collected: Collected, *, goal: str) -> str:
    """The decisions as the markdown a role is shown. `goal` is `owner/repo#n`."""
    parts = [
        "# The Sponsor's recorded decisions\n",
        f"What the Sponsor has written on {goal}'s cards, or naming the Goal elsewhere.\n",
    ]
    for d in collected.decisions:
        heading = f"{d.source}{': ' + d.title if d.title else ''} ({d.kind})"
        parts.append(f"## {heading}\n\n{d.text}\n")
    if collected.unsearched:
        parts.append("Not searched, no access: " + ", ".join(collected.unsearched) + "\n")
    return "\n".join(parts)
