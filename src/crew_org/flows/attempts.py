"""Why work doesn't land first time: the crew's retries, counted (#157).

Every retry is an `escalation.decided` event carrying its failure class and what
went wrong. A pull request's body kept only the total, and nothing added them
up. This reads the events back, turns each failure into a cause that can be
counted (the same mistake reads the same whatever file or name it hit), and
reports a sprint's stories: which landed first time, what the others took, and
the causes that recur.

The data is the crew's, because only the crew has it. The metric is
sprint-metrics' (Goal sprint-metrics#87), which reads what `crew export` writes.
The diagnosis is the retro's, which is shown the causes and files the ones that
recur.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A cause seen on this many of a sprint's cards is the crew's, not the card's.
RECURRING_CARDS = 2

# Causes that are counted but never filed as recurring. Two unrelated failures
# that both lost their detail are not one cause; and a test's assertion failing
# means that card's code gave the wrong answer, which is the card's, however
# many cards it happens on. An ImportError or NameError on several is systemic.
UNRECORDED = "; its cause wasn't recorded"
CARD_OWN = "a test's assertion failed"

# A parse failure is a defect in what the model is asked for (the escalation
# policy's own reading of a persistent SCHEMA failure), not bad luck.
PROMPT_CLASSES = frozenset({"SCHEMA"})

_QUOTED = re.compile(r"`[^`]*`|'[^']*'|\"[^\"]*\"")
_NUMBER = re.compile(r"\d+")
_LINT = re.compile(r"^([A-Z]{1,4}\d{3,4}) (.+)$", re.MULTILINE)
_PYTEST = re.compile(r"^(?:E\s+|FAILED .* - )(\w+(?:Error|Exception|Failure))\b", re.MULTILINE)
_ASSERT = re.compile(r"^(?:E\s+assert |FAILED .* - assert )", re.MULTILINE)


@dataclass(frozen=True)
class Attempt:
    card: int
    role: str
    failure_class: str
    cause: str
    error: str
    at: str = ""


def _mask(text: str) -> str:
    """The same mistake reads the same whatever names, paths or numbers it hit."""
    return _NUMBER.sub("N", _QUOTED.sub("…", text)).strip()


def _first(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def cause_of(failure_class: str, detail: dict[str, Any]) -> tuple[str, str]:
    """(the countable cause, the first line of what actually went wrong)."""
    if failure_class == "SCHEMA":
        error = str(detail.get("error") or "")
        found = re.search(r"Value error, (.+?)(?: \[type=|$)", error, re.DOTALL)
        message = found.group(1) if found else _first(error)
        # The rule that was broken, not the rest of the sentence explaining it.
        first_clause = re.split(r"\. ", message.strip(), maxsplit=1)[0]
        return _mask(first_clause), first_clause
    if failure_class == "VERIFY":
        # The first error line from the full output, recorded since #157. The
        # stored output is cut at 600 characters, before pytest's summary.
        output = "\n".join(str(detail.get(k) or "") for k in ("first_error", "output"))
        lint = _LINT.search(output)
        if lint:
            # The rule's code is the cause; only its message is masked.
            return f"lint {lint.group(1)}: {_mask(lint.group(2))}", lint.group(0)
        test = _PYTEST.search(output)
        if test and test.group(1) != "AssertionError":
            return f"a test failed: {test.group(1)}", _line_at(output, test.start())
        asserted = _ASSERT.search(output) or test
        if asserted:
            return CARD_OWN, _line_at(output, asserted.start())
        failing = ", ".join(detail.get("failing_commands") or []) or "a check"
        return f"{failing} failed{UNRECORDED}", _first(output.split("(exit", 1)[-1])
    if failure_class == "REGRESSION":
        contracts = detail.get("contracts") or {}
        return "changed a contract merged code depends on", ", ".join(sorted(contracts))[:200]
    error = str(detail.get("error") or detail.get("reason") or detail.get("reasons") or "")
    first = _first(error)
    return _mask(first) or failure_class.lower(), first


def read_attempts(events_dir: Path) -> list[Attempt]:
    """Every retry the event log recorded, oldest first."""
    found: list[Attempt] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("kind") != "escalation.decided" or event.get("card") is None:
                continue
            detail = event.get("detail") or {}
            failure_class = str(
                detail.get("failure_class") or (event.get("summary") or "").split(" ")[0]
            )
            cause, error = cause_of(failure_class, detail)
            found.append(
                Attempt(
                    card=int(event["card"]),
                    role=event.get("role") or "",
                    failure_class=failure_class,
                    cause=cause,
                    error=error[:300],
                    at=event.get("at") or "",
                )
            )
    return sorted(found, key=lambda a: a.at)


@dataclass
class Cause:
    failure_class: str
    cause: str
    count: int = 0
    cards: list[int] = field(default_factory=list)
    example: str = ""
    # (when, card) for each occurrence: a fix merged mid-sprint is judged
    # against when the failures happened, not the sprint as a whole (#199).
    occurrences: list[tuple[str, int]] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable across sprints, so a cause already filed is recognised."""
        return hashlib.sha1(f"{self.failure_class}:{self.cause}".encode()).hexdigest()[:12]

    @property
    def recurring(self) -> bool:
        return (
            len(self.cards) >= RECURRING_CARDS
            and not self.cause.endswith(UNRECORDED)
            and self.cause != CARD_OWN
        )

    @property
    def prompt_defect(self) -> bool:
        return self.failure_class in PROMPT_CLASSES


def sprint_report(
    sprint: str, stories: Iterable[Any], attempts: list[Attempt], escalations: int
) -> dict[str, Any]:
    """A sprint's stories, their attempts, and the causes across them."""
    stories = sorted(stories, key=lambda c: c.number or 0)
    numbers = {c.number for c in stories}
    ours = [a for a in attempts if a.card in numbers]
    causes: dict[tuple[str, str], Cause] = {}
    for a in ours:
        entry = causes.setdefault(
            (a.failure_class, a.cause), Cause(a.failure_class, a.cause, example=a.error)
        )
        entry.count += 1
        entry.occurrences.append((a.at, a.card))
        if a.card not in entry.cards:
            entry.cards.append(a.card)
    return {
        "sprint": sprint,
        "escalations": escalations,
        "stories": [
            {
                "number": c.number,
                "title": c.title,
                "repo": c.repo,
                "status": c.status,
                "first_try": not any(a.card == c.number for a in ours),
                "attempts": [
                    {
                        "class": a.failure_class,
                        "role": a.role,
                        "cause": a.cause,
                        "error": a.error,
                        "at": a.at,
                    }
                    for a in ours
                    if a.card == c.number
                ],
            }
            for c in stories
        ],
        "causes": [
            {
                "class": c.failure_class,
                "cause": c.cause,
                "count": c.count,
                "cards": c.cards,
                "example": c.example,
                "recurring": c.recurring,
                "key": c.key,
                "occurrences": c.occurrences,
            }
            for c in sorted(causes.values(), key=lambda c: (-len(c.cards), -c.count, c.cause))
        ],
    }


def causes_of(report: dict[str, Any]) -> list[Cause]:
    return [
        Cause(
            c["class"],
            c["cause"],
            c["count"],
            list(c["cards"]),
            c["example"],
            [tuple(o) for o in c.get("occurrences", [])],
        )
        for c in report["causes"]
    ]


def retries_text(report: dict[str, Any]) -> str:
    """What the retro is shown: the first-try rate and the causes, most widespread first."""
    stories = report["stories"]
    if not stories:
        return "No stories this sprint."
    first = sum(1 for s in stories if s["first_try"])
    lines = [f"{first} of {len(stories)} stories landed on their first attempt."]
    for c in report["causes"]:
        cards = ", ".join(f"#{n}" for n in c["cards"])
        times = "once" if c["count"] == 1 else f"{c['count']} times"
        # The key is how a fix names the cause it fixes (#199).
        key = f"; cause {c['key']}" if c.get("key") else ""
        lines.append(f"- {c['class']}: {c['cause']} ({times}, on {cards}{key})")
    return "\n".join(lines)


def _line_at(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start : end if end != -1 else len(text)].strip()


def first_error(report: str) -> str:
    """The first line of a failure report that says what actually failed."""
    for pattern in (_LINT, _PYTEST, _ASSERT):
        found = pattern.search(report)
        if found:
            return _line_at(report, found.start())
    return ""
