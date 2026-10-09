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
# QA finding a criterion unproven is that story's, like a failed assertion.
UNPROVEN = "an acceptance criterion unproven"
CARDS_OWN = frozenset({CARD_OWN, UNPROVEN})

# A gate returning a story is an attempt too (#254), and the costlier kind:
# each is a full delivery, review and QA. The Developer's local retries never
# see them — sprint-metrics#145 was returned five times and counted first try.
REVIEW = "REVIEW"
QA = "QA"

# A parse failure is a defect in what the model is asked for (the escalation
# policy's own reading of a persistent SCHEMA failure), not bad luck.
PROMPT_CLASSES = frozenset({"SCHEMA"})

_QUOTED = re.compile(r"`[^`]*`|'[^']*'|\"[^\"]*\"")
_NUMBER = re.compile(r"\d+")
_LINT = re.compile(r"^([A-Z]{1,4}\d{3,4}) (.+)$", re.MULTILINE)
_PYTEST = re.compile(r"^(?:E\s+|FAILED .* - )(\w+(?:Error|Exception|Failure))\b", re.MULTILINE)
_ASSERT = re.compile(r"^(?:E\s+assert |FAILED .* - assert )", re.MULTILINE)
# pytest's short summary: "FAILED tests/test_x.py::test_y - AssertionError: assert 0 == 1".
_FAILED_TEST = re.compile(r"^FAILED (\S+)(?: - (.*))?$", re.MULTILINE)
# A failure's section in pytest's report: "___ TestX.test_y ___", then its traceback.
_SECTION = re.compile(r"^_{3,} (.+?) _{3,}$", re.MULTILINE)


def failing_tests(report: str) -> list[dict[str, str]]:
    """The tests that failed, by pytest id, each with its assertion line (crew#449).

    sprint-metrics#443 was blocked after the same two tests failed on three runs,
    and their names were nowhere: the record kept the report's first 600
    characters, which stop before pytest's summary.
    """
    found: dict[str, str] = {}
    for match in _FAILED_TEST.finditer(report):
        found.setdefault(match.group(1), (match.group(2) or "").strip()[:200])
    # Outside a terminal, pytest leaves the message off a summary line too long
    # for 80 columns, as sprint-metrics#529's schema tests were: read the first
    # `E` line of the test's own section instead.
    raised = _first_errors(report)
    for test, line in found.items():
        if not line:
            found[test] = raised.get(".".join(test.split("::")[1:]), "")[:200]
    return [{"id": test, "assertion": line} for test, line in list(found.items())[:50]]


def _first_errors(report: str) -> dict[str, str]:
    """Each failure section's first `E` line, by the name pytest heads it with."""
    headers = list(_SECTION.finditer(report))
    found: dict[str, str] = {}
    for here, after in zip(headers, [*headers[1:], None], strict=False):
        body = report[here.end() : after.start() if after else len(report)]
        line = next((x[1:].strip() for x in body.splitlines() if x.startswith("E ")), "")
        found.setdefault(here.group(1), line)
    return found


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


def _gate_return(event: dict[str, Any]) -> Attempt | None:
    """A review that requested changes, or QA returning a story, as an attempt."""
    detail = event.get("detail") or {}
    role = event.get("role") or ""
    at = event.get("at") or ""
    # The review's own event names the pull request, not the story; the move
    # it makes names the story.
    if (
        event.get("kind") == "card.moved"
        and role == "Code Reviewer"
        and detail.get("to") == "In Progress"
    ):
        finding = str(detail.get("finding") or "")
        cause = _mask(re.split(r"\. ", finding, maxsplit=1)[0]) if finding else ""
        return Attempt(
            card=int(event["card"]),
            role=role,
            failure_class=REVIEW,
            cause=cause or f"changes requested{UNRECORDED}",
            error=finding[:300],
            at=at,
        )
    if (
        event.get("kind") == "agent.finished"
        and role == "QA Engineer"
        and detail.get("accepted") is False
    ):
        unproven = detail.get("unproven") or []
        return Attempt(
            card=int(event["card"]),
            role=role,
            failure_class=QA,
            cause=UNPROVEN,
            error=str(unproven[0] if unproven else "")[:300],
            at=at,
        )
    return None


def read_attempts(events_dir: Path) -> list[Attempt]:
    """Every retry the event log recorded, and every gate return, oldest first."""
    found: list[Attempt] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("card") is None:
                continue
            gate = _gate_return(event)
            if gate is not None:
                found.append(gate)
                continue
            if event.get("kind") != "escalation.decided":
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
            and self.cause not in CARDS_OWN
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
                "superseded": bool(getattr(c, "superseded", False)),
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
    # A superseded story never landed, on any attempt: one never started would
    # otherwise count as landing first time (crew#558). Its failures still count.
    landed = [s for s in stories if not s.get("superseded")]
    first = sum(1 for s in landed if s["first_try"])
    lines = [f"{first} of {len(landed)} stories landed on their first attempt."]
    if len(landed) < len(stories):
        lines[0] += f" {len(stories) - len(landed)} more were superseded before they landed."
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
