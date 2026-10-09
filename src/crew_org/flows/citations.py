"""Which crew issue is about which cards, recorded as it's seen (crew#449, discussion 552).

The only link from a run's trouble to the issue it led to was a `#N` in the
issue's prose. A bare number names a card in some repository, and numbers
collide: card 15 is in both sprint-metrics and crew-presentation. The crew's own
findings now name their cards qualified (`defect.filed`), but most crew issues
are filed by Claude, the Sponsor or a diagnosis, which no event recorded.

Each pass reads the crew issues opened or edited since the last one and records
the cards each cites, qualified, as `issue.cites`. The guard hook makes Claude's
`gh` bodies say `owner/repo#N`, so the references are reliable from now on; an
older bare `#N` isn't guessed at. Only a change in what an issue cites is
recorded again.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crew_org.events import CrewEvent, EventKind, EventSink

STATE_FILE = "citations-seen.json"
# Where the first scan starts: before the crew's first sprint, so its history is read.
HISTORY_FROM = "2026-09-01T00:00:00Z"

_QUALIFIED = re.compile(r"(?<![\w/.-])(?:([\w.-]+)/)?([\w.-]+)#(\d+)\b")
_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)")


def cited(text: str, *, owner: str, repos: set[str]) -> list[str]:
    """The cards `text` names in one of `repos`, as `repo#n`, in order, once each."""
    found: dict[str, None] = {}
    for match in _URL.finditer(text):
        if match.group(1) == owner and match.group(2) in repos:
            found[f"{match.group(2)}#{match.group(3)}"] = None
    for match in _QUALIFIED.finditer(text):
        who, repo, number = match.groups()
        if (who is None or who == owner) and repo in repos:
            found[f"{repo}#{number}"] = None
    return list(found)


def _load(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def record_citations(
    issues: Any,
    sink: EventSink,
    *,
    crew_repo: str,
    repos: set[str],
    events_dir: Path,
    now: datetime | None = None,
) -> int:
    """Record what each crew issue changed since the last pass cites. Returns how many."""
    path = events_dir / STATE_FILE
    state = _load(path)
    since = str(state.get("since") or HISTORY_FROM)
    known: dict[str, list[str]] = state.get("cites") or {}
    started = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recorded = 0
    for issue in issues.updated_since(crew_repo, since):
        number = int(issue["number"])
        cites = cited(issue.get("body") or "", owner=issues.owner, repos=repos)
        if not cites or known.get(str(number)) == cites:
            continue
        known[str(number)] = cites
        sink.emit(
            CrewEvent(
                kind=EventKind.ISSUE_CITES,
                card=number,
                summary=f"{crew_repo}#{number} cites {', '.join(cites)}"[:120],
                detail={
                    "repo": crew_repo,
                    "cites": cites,
                    "labels": sorted(label["name"] for label in issue.get("labels") or []),
                    "state": issue.get("state"),
                    "state_reason": issue.get("state_reason"),
                    "opened": issue.get("created_at"),
                    "closed": issue.get("closed_at"),
                },
            )
        )
        recorded += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"since": started, "cites": known}), encoding="utf-8")
    return recorded


def record_dropped(issues: Any, sink: EventSink, *, reported: set[tuple[Any, ...]]) -> None:
    """Record the references left unlinked and the comments ignored since the last pass.

    `IssueClient` kept both and wrote neither (crew#456). `reported` is what was
    recorded already, so each is recorded once.
    """
    for repo, number in sorted(set(issues.unresolved) - reported):
        reported.add((repo, number))
        sink.emit(
            CrewEvent(
                kind=EventKind.REFERENCE_UNRESOLVED,
                summary=f"#{number} left unlinked in {repo}: no such issue",
                detail={"repo": repo, "number": number},
            )
        )
    for repo, number, login in sorted(set(issues.ignored) - reported):
        reported.add((repo, number, login))
        sink.emit(
            CrewEvent(
                kind=EventKind.COMMENT_IGNORED,
                card=number,
                summary=f"a comment on {repo}#{number} from {login}, outside trust, ignored",
                detail={"repo": repo, "login": login},
            )
        )
