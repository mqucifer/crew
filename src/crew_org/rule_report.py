"""How often each rule fired, and what the model's time went on (crew#756).

Reads the event log and counts each rule on `rule_list`'s list. An event names
its rule where it can: a `rules.Rule` as `rule`, a guard as its failure class, a
form's refusal as the form and the field it refused. Only where none of those is
there is the refusal's text matched against each rule's messages, and a count
made that way says so: most messages are f-strings, so a text match finds less
than happened.

Cards are counted as `repo#number`, never by number alone (crew#752). Events from
before the repository was recorded count under "repo unknown", not under a guess.

The model's time per role is the research track R3 of crew#755: every model call,
with its duration where the log has one, by role and by repository.

The report is for the Sponsor's cut, merge or keep. Its rows are keyed by the
rule's id, and `crew rules --since` reads an earlier snapshot and reports only what
changed, so earlier decisions carry over to the rows that didn't.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from crew_org import rule_list, rulebook
from crew_org.rule_list import Entry

UNKNOWN = "repo unknown"
# How much of a message's start a text must have to be read as that message: the log
# keeps a refusal to 400 or 600 characters, and older events have older wording.
PARTIAL = 20
BY_NAME = "name"
BY_NAME_AND_TEXT = "name, then text"
BY_TEXT = "text"

# "criteria_tests.0: Value error, the source for ..." in the crew's own call layer.
_ERROR = re.compile(r"(?:^|; )((?:[\w\[\]]+\.)*[\w\[\]]+|the answer): ")
# CrewAI's: "1 validation error for FirstAttempt\ncriteria_tests.0\n  Value error, ...".
_CREWAI_HEAD = re.compile(r"validation errors? for (\w+)")
_CREWAI_ERROR = re.compile(r"(?m)^([\w.]+)?\s*\n?\s+(Value error, )?(.+?) \[type=")


@dataclass
class Fired:
    """How often one rule fired, and on what."""

    events: int = 0
    cards: set[str] = field(default_factory=set)
    repos: Counter[str] = field(default_factory=Counter)
    roles: Counter[str] = field(default_factory=Counter)
    matched: Counter[str] = field(default_factory=Counter)
    # The model's time on the answers this rule refused.
    seconds: float = 0.0
    tokens: int = 0
    # The sprints it fired in.
    sprints: set[str] = field(default_factory=set)


@dataclass
class Time:
    """One role's model calls, in one repository or overall."""

    calls: int = 0
    timed: int = 0
    seconds: float = 0.0
    completion: int = 0
    reasoning: int = 0
    refused_calls: int = 0
    refused_seconds: float = 0.0


@dataclass
class Report:
    fired: dict[str, Fired]
    unmatched: Counter[str]
    time: dict[tuple[str, str], Time]
    first: str = ""
    last: str = ""
    events: int = 0
    # Every sprint the log names, in the order each first appears.
    sprints: list[str] = field(default_factory=list)


def read(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def where(event: dict[str, Any]) -> tuple[str, str]:
    """The event's repository and card, as `repo#number`; a missing repo stays unknown."""
    ctx = event.get("ctx") or {}
    detail = event.get("detail") or {}
    repo = ctx.get("repo") or detail.get("repo") or UNKNOWN
    card = event.get("card") or ctx.get("card") or detail.get("card")
    return repo, f"{repo}#{card}" if card else ""


class Matcher:
    """Finds the rules an event records, by name first and by text only after."""

    def __init__(self, entries: Iterable[Entry]) -> None:
        self.entries = {e.id: e for e in entries}
        self.forms = {form.__name__: form for form in rule_list.answered()}
        self.patterns = {
            e.id: [p for m in e.messages if (p := rule_list.pattern(m)) is not None]
            for e in self.entries.values()
        }
        self.by_class: dict[str, list[Entry]] = defaultdict(list)
        for e in self.entries.values():
            if e.failure_class:
                self.by_class[e.failure_class].append(e)

    def text(self, message: str, among: Iterable[Entry] | None = None) -> str | None:
        """The rule whose message the text reads as: a whole match before a partial one,
        then the most fixed text. A partial match is the start of a message, at least
        `PARTIAL` characters of it."""
        best: tuple[bool, int, str] | None = None
        for entry in among if among is not None else self.entries.values():
            for p in self.patterns.get(entry.id, []):
                whole, fixed = p.search(message)
                if not whole and fixed < PARTIAL:
                    continue
                if best is None or (whole, fixed) > best[:2]:
                    best = (whole, fixed, entry.id)
        return best[2] if best else None

    def _field(self, form: str, loc: str) -> tuple[str, str]:
        """The form and field a refusal's location names, through nested forms.

        A location that ends on a nested form, `criteria_tests.0`, is that form's own
        rule, a model validator: the field is "".
        """
        model = self.forms.get(form)
        if model is None:
            return form, ""
        parts = [p for p in re.split(r"[.\[\]]", loc) if p and not p.isdigit()]
        for part in (p for p in parts if p != "the answer"):
            info = model.model_fields.get(part)
            if info is None:
                return model.__name__, ""
            nested = [
                a
                for a in _flat(info.annotation)
                if isinstance(a, type) and issubclass(a, BaseModel)
            ]
            if not nested:
                return rule_list._owner(model, part).__name__, part
            model = nested[0]
        return model.__name__, ""

    def refusal(self, form: str, loc: str, message: str) -> tuple[str | None, str]:
        """A form's refusal: the validators on the form and field it names, then text."""
        if not message.startswith("Value error, "):
            return "call.schema", BY_NAME
        message = message.removeprefix("Value error, ")
        owner, name = self._field(form, loc)
        model = self.forms.get(owner)
        bases = {c.__name__ for c in model.__mro__} if model is not None else {owner}
        candidates = [
            e
            for e in self.entries.values()
            if e.group == "form"
            and e.form in bases
            and (e.mode == "field" and name in e.fields if name else e.mode == "model")
        ]
        if len(candidates) == 1:
            return candidates[0].id, BY_NAME
        if candidates:
            found = self.text(message, candidates)
            if found is not None:
                return found, BY_NAME_AND_TEXT
        return self.text(message), BY_TEXT

    def reason(self, form: str, reason: str) -> list[tuple[str | None, str]]:
        """Each rule a refusal's reason names: one per error it lists."""
        starts = list(_ERROR.finditer(reason))
        if not starts or starts[0].start() != 0:
            return [(self.text(reason), BY_TEXT)]
        out = []
        for here, after in zip(starts, [*starts[1:], None], strict=False):
            message = reason[here.end() : after.start() if after else len(reason)]
            out.append(self.refusal(form, here.group(1), message.strip()))
        return out

    def crewai(self, error: str) -> list[tuple[str | None, str]]:
        """A refusal as CrewAI wrote it, before the crew made its own calls (ADR 0025)."""
        head = _CREWAI_HEAD.search(error)
        if head is None:
            return [(self.text(error), BY_TEXT)]
        out = []
        for found in _CREWAI_ERROR.finditer(error[head.end() :]):
            loc, valued, message = found.groups()
            out.append(self.refusal(head.group(1), loc or "", (valued or "") + message))
        return out or [(self.text(error), BY_TEXT)]

    def match(self, event: dict[str, Any]) -> list[tuple[str | None, str]]:
        """The rules an event records, each with how it was found."""
        kind = event.get("kind")
        detail = event.get("detail") or {}
        found: list[tuple[str | None, str]] = []
        rule = detail.get("rule")
        if rule and rule in self.entries:
            found.append((rule, BY_NAME))
        if kind != "llm.refused" and detail.get("rules"):
            # A step's own refusal, named by the rule that made it (ADR 0031).
            found += [(r if r in self.entries else None, BY_NAME) for r in detail["rules"]]
        if kind == "llm.refused" and detail.get("rules"):
            # Named by the refusal itself, since the rulebook (ADR 0031).
            found += [
                (r, BY_NAME) if r in self.entries else (None, BY_NAME) for r in detail["rules"]
            ]
        elif kind == "llm.refused":
            form = str(detail.get("output_schema") or "").partition(":")[0]
            reason = str(detail.get("reason") or "")
            if detail.get("empty"):
                found.append((self._empty(), BY_NAME))
            else:
                found += self.reason(form, reason)
        elif kind == "llm.empty":
            found.append((self._empty(), BY_NAME))
        elif kind == "escalation.decided":
            klass = str(detail.get("failure_class") or "")
            guards = self.by_class.get(klass, [])
            said = " ".join(
                str(detail.get(k) or "") for k in ("reason", "error", "detail", "reasons")
            )
            if klass in ("SCHEMA", "EDIT") and detail.get("error"):
                error = str(detail["error"])
                crewai = "validation error" in error
                found += self.crewai(error) if crewai else self.reason("", error)
            elif len(guards) == 1:
                found.append((guards[0].id, BY_NAME))
            elif guards:
                found.append((self.text(said, guards) or guards[0].id, BY_NAME_AND_TEXT))
        return found

    def _empty(self) -> str | None:
        return next((i for i in self.entries if i.startswith("call.the_answer_was_empty")), None)


def _flat(annotation: Any) -> Iterator[Any]:
    yield annotation
    for arg in get_args(annotation):
        yield from _flat(arg)


def count(events: Iterable[dict[str, Any]], entries: Iterable[Entry] | None = None) -> Report:
    matcher = Matcher(entries if entries is not None else rule_list.entries())
    fired: dict[str, Fired] = defaultdict(Fired)
    unmatched: Counter[str] = Counter()
    time: dict[tuple[str, str], Time] = defaultdict(Time)
    report = Report(fired, unmatched, time)
    for event in events:
        report.events += 1
        at = str(event.get("at") or "")
        if at:
            report.first = min(report.first or at, at)
            report.last = max(report.last, at)
        repo, card = where(event)
        sprint = str(
            (event.get("ctx") or {}).get("sprint")
            or (event.get("detail") or {}).get("sprint")
            or ""
        )
        if sprint and sprint not in report.sprints:
            report.sprints.append(sprint)
        role = str(event.get("role") or "")
        detail = event.get("detail") or {}
        if event.get("kind") == "llm.finished":
            for key in ((role, repo), (role, "")):
                t = time[key]
                t.calls += 1
                if isinstance(detail.get("duration_s"), int | float):
                    t.timed += 1
                    t.seconds += float(detail["duration_s"])
                t.completion += int(detail.get("completion_tokens") or 0)
                t.reasoning += int(detail.get("reasoning_tokens") or 0)
        if event.get("kind") == "llm.refused":
            for key in ((role, repo), (role, "")):
                time[key].refused_calls += 1
                time[key].refused_seconds += float(detail.get("duration_s") or 0)
        # A rule once per event: a refusal can name it for several fields, and its
        # time was spent once.
        found: dict[str | None, str] = {}
        for rule, how in matcher.match(event):
            found.setdefault(rule, how)
        for rule, how in found.items():
            if rule is None:
                unmatched[_shape(event)] += 1
                continue
            f = fired[rule]
            f.events += 1
            if sprint:
                f.sprints.add(sprint)
            if card:
                f.cards.add(card)
            f.repos[repo] += 1
            if role:
                f.roles[role] += 1
            f.matched[how] += 1
            if event.get("kind") == "llm.refused":
                f.seconds += float(detail.get("duration_s") or 0)
                f.tokens += int(detail.get("completion_tokens") or 0)
        # The holds a tick decided without an event of their own, on its end (crew#756).
        for hold in detail.get("held") or () if event.get("kind") == "tick.finished" else ():
            if not isinstance(hold, dict) or hold.get("rule") not in matcher.entries:
                continue
            f = fired[hold["rule"]]
            f.events += 1
            if sprint:
                f.sprints.add(sprint)
            held_repo = str(hold.get("repo") or repo)
            if hold.get("card"):
                f.cards.add(f"{held_repo}#{hold['card']}")
            f.repos[held_repo] += 1
            f.matched[BY_NAME] += 1
    return report


def _shape(event: dict[str, Any]) -> str:
    """An unmatched refusal, with its specifics masked, so alike ones count together."""
    detail = event.get("detail") or {}
    text = str(detail.get("reason") or detail.get("error") or event.get("summary") or "")
    text = re.sub(r"'[^']*'|`[^`]*`|\"[^\"]*\"|\d+", "…", text)
    return f"{event.get('kind')}: {text[:140]}"


# --- the report -------------------------------------------------------------------------


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}"


def snapshot(report: Report, entries: Iterable[Entry] | None = None) -> dict[str, Any]:
    """The rules and their counts, keyed by rule id: what a later report is diffed against."""
    rows = {}
    for e in entries if entries is not None else rule_list.entries():
        f = report.fired.get(e.id, Fired())
        rows[e.id] = {
            "group": e.group,
            "where": e.where,
            "says": e.says,
            "repairs": e.repairs,
            "category": e.category,
            "roles": sorted(e.roles),
            "told": sorted(e.told),
            "checked": sorted(e.checked),
            "origin": sorted(e.origin),
            "messages": e.messages,
            "fired": f.events,
            "cards": len(f.cards),
            "repos": dict(sorted(f.repos.items())),
            "matched": dict(sorted(f.matched.items())),
            "refused_hours": round(f.seconds / 3600, 2),
        }
    return {
        "events": report.events,
        "from": report.first,
        "to": report.last,
        "rows": rows,
    }


def _cell(values: Iterable[str]) -> str:
    return "<br>".join(sorted(values)).replace("|", "\\|") or "—"


def render(report: Report, *, source: str = "", built_from: str = "") -> str:
    entries = rule_list.entries()
    total = Counter[str]()
    for f in report.fired.values():
        total.update(f.matched)
    lines = [
        "# The rules list: how often each fired, and the model's time",
        "",
        f"From {source or 'the event log'}: {report.events:,} events, "
        f"{report.first[:10]} to {report.last[:10]}."
        + (f" The list is from the crew at commit {built_from}." if built_from else ""),
        "",
        "Each rule's row in `docs/reference/rules.md` says where it lives, who's told it and",
        "what checks it. This adds how often it fired, on how many cards, in which",
        "repositories, and the model's time on the answers it refused.",
        "",
        "## How the events were matched",
        "",
        "| Matched by | Events |",
        "|---|---|",
        f"| The rule's name, as the event records it | {total[BY_NAME]:,} |",
        f"| Its name narrowed to a few, then the text | {total[BY_NAME_AND_TEXT]:,} |",
        f"| The text alone (a floor, not an exact count) | {total[BY_TEXT]:,} |",
        f"| Not matched to any rule | {sum(report.unmatched.values()):,} |",
        "",
        "Cards are counted as `repo#number`. Events written before the repository was",
        f'recorded count under "{UNKNOWN}".',
        "",
        "## The model's time, by role (R3)",
        "",
        "Every model call the log records, by the role that made it. Hours are for the",
        "calls that recorded a duration; calls before the crew's own call layer didn't.",
        "",
        "| Role | Calls | With a duration | Hours | Completion tokens | Reasoning tokens "
        "| Refused calls | Hours refused |",
        "|---|---|---|---|---|---|---|---|",
    ]
    overall = sorted(
        ((role, t) for (role, repo), t in report.time.items() if not repo),
        key=lambda x: -x[1].seconds,
    )
    for role, t in overall:
        lines.append(
            f"| {role or '(no role)'} | {t.calls:,} | {t.timed:,} | {_hours(t.seconds)} "
            f"| {t.completion:,} | {t.reasoning:,} | {t.refused_calls:,} "
            f"| {_hours(t.refused_seconds)} |"
        )
    lines += [
        "",
        "### By repository",
        "",
        "| Role | Repository | Calls | Hours | Hours refused |",
        "|---|---|---|---|---|",
    ]
    for (role, repo), t in sorted(report.time.items()):
        if repo:
            lines.append(
                f"| {role or '(no role)'} | {repo} | {t.calls:,} | {_hours(t.seconds)} "
                f"| {_hours(t.refused_seconds)} |"
            )
    lines += categories(report, entries)
    lines += retirement(report, entries)
    lines += guidance()
    for group, title in rule_list.GROUPS.items():
        rows = [e for e in entries if e.group == group]
        if group == "told":
            # A line a role is told fires nothing: what it costs is in its prompt.
            continue
        lines += [
            "",
            f"## {title}",
            "",
            "| Rule | Fired | Cards | Repositories | Roles seen | Matched by | Hours refused |",
            "|---|---|---|---|---|---|---|",
        ]
        for e in sorted(rows, key=lambda e: (-report.fired.get(e.id, Fired()).events, e.id)):
            f = report.fired.get(e.id, Fired())
            lines.append(
                f"| `{e.id}` | {f.events:,} | {len(f.cards):,} "
                f"| {_cell(f'{r} {n}' for r, n in f.repos.items())} "
                f"| {_cell(f'{r} {n}' for r, n in f.roles.items()) if f.roles else _cell(e.roles)} "
                f"| {_cell(f'{m} {n}' for m, n in f.matched.items())} "
                f"| {_hours(f.seconds) if f.seconds else '—'} |"
            )
    lines += [
        "",
        "## Refusals no rule matched",
        "",
        "Alike ones counted together, their specifics masked. Each is a rule the list",
        "doesn't find yet, or a message too computed to match by text.",
        "",
        "| Refusal | Events |",
        "|---|---|",
        *(f"| {_show(shape)} | {n} |" for shape, n in report.unmatched.most_common(40)),
        "",
    ]
    return "\n".join(lines)


def _untold(entry: Entry) -> bool:
    """Checked and never told, against the second policy (crew#756): a check is told in
    the form it checks. Exempt are the call layer's, since the model doesn't choose an
    empty answer or a cut field, and the crew's own decisions and holds, which check no
    form."""
    return entry.group not in ("told", "call", "decides", "skip") and not entry.told


def _unchecked(entry: Entry) -> bool:
    """Told and never checked by anything the list links by name."""
    return entry.group == "told" and not any(not c.startswith("maybe ") for c in entry.checked)


def categories(report: Report, entries: Iterable[Entry]) -> list[str]:
    """Each category's summary, for the Sponsor's review by category, and what's unplaced."""
    entries = list(entries)
    lines = [
        "",
        "## By category",
        "",
        "What each category holds, and its two gaps: rules checked and never told (the",
        "call layer's and the crew's own decisions are exempt: they check no form a role",
        "fills), and lines told and never checked. Fired and refused hours are from the",
        "log above.",
        "",
        "| Category | Rules | By kind | Checked, never told | Told, never checked "
        "| Fired | Cards | Hours refused | Roles |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for category, title in [*rule_list.CATEGORIES.items(), ("", "Unplaced")]:
        rows = [e for e in entries if e.category == category]
        fired = [report.fired[e.id] for e in rows if e.id in report.fired]
        kinds = Counter(e.group for e in rows)
        roles = Counter(role for e in rows for role in e.roles)
        lines.append(
            f"| {title}" + (f" (`{category}`)" if category else "") + f" | {len(rows)} "
            f"| {', '.join(f'{k} {n}' for k, n in kinds.most_common())} "
            f"| {sum(1 for e in rows if _untold(e))} "
            f"| {sum(1 for e in rows if _unchecked(e))} "
            f"| {sum(f.events for f in fired):,} "
            f"| {len(set().union(*(f.cards for f in fired))) if fired else 0:,} "
            f"| {_hours(sum(f.seconds for f in fired))} "
            f"| {', '.join(f'{r} {n}' for r, n in roles.most_common(4)) or '—'} |"
        )
    unplaced = [e for e in entries if not e.category]
    lines += [
        "",
        "### Unplaced",
        "",
        f"{len(unplaced)} rules no line of the placement table holds for. Each is listed, not",
        "guessed: placing one is a change to the table in `rule_list.PLACEMENT`.",
        "",
        "| Rule | Says | Where |",
        "|---|---|---|",
        *(
            f"| `{e.id}` | {_show(e.says)} | `{e.where}` |"
            for e in sorted(unplaced, key=lambda e: e.where)
        ),
    ]
    return lines


# Sprints with no fire before a rule is a candidate to retire (crew#756, the operator,
# 2026-10-17). Flagged at the retro, never retired by itself.
RETIRE_AFTER = 10
# How many of the most-firing rules the retro is shown, as "a missing tool?".
MOST_FIRING = 5


def guidance() -> list[str]:
    """Each role's guidance, the lines it's told and nothing checks, against its budget."""
    budget = rulebook.budget()
    return [
        "",
        "## Guidance against each role's budget",
        "",
        "Lines a role is told and nothing checks, labelled guidance (the first policy).",
        "A role over its budget fails the tests; the budget only goes down.",
        "",
        "| Role | Guidance | Budget |",
        "|---|---|---|",
        *(
            f"| {role} | {n} | {budget.get(role, 0)} |"
            for role, n in rule_list.guidance_counts().items()
        ),
    ]


def retirement(report: Report, entries: Iterable[Entry], *, after: int = RETIRE_AFTER) -> list[str]:
    """Rules that haven't fired in the last `after` sprints, and those that fire most."""
    recent = report.sprints[-after:]
    if len(recent) < after:
        return [
            "",
            "## Candidates to retire",
            "",
            f"The log names {len(report.sprints)} sprints, fewer than the {after} a rule must",
            "go without firing before it's a candidate.",
        ]
    firing = [e for e in entries if e.group not in ("told",)]
    quiet = [e for e in firing if not report.fired.get(e.id, Fired()).sprints & set(recent)]
    loud = sorted(
        (e for e in firing if e.id in report.fired),
        key=lambda e: (
            -sum(1 for _ in report.fired[e.id].sprints & set(recent)),
            -report.fired[e.id].events,
            e.id,
        ),
    )[:MOST_FIRING]
    return [
        "",
        "## Candidates to retire",
        "",
        f"{len(quiet)} rules that can fire didn't, in the last {after} sprints "
        f"({recent[0]} to {recent[-1]}). Each is for the retro to keep or cut; nothing",
        "is retired by itself.",
        "",
        "| Rule | Category | Fired ever |",
        "|---|---|---|",
        *(
            f"| `{e.id}` | {e.category or '—'} | {report.fired.get(e.id, Fired()).events} |"
            for e in sorted(quiet, key=lambda e: (e.category, e.id))
        ),
        "",
        "### Firing most: a missing tool?",
        "",
        "| Rule | Category | Sprints fired, of the last " + str(after) + " | Fired |",
        "|---|---|---|---|",
        *(
            f"| `{e.id}` | {e.category or '—'} "
            f"| {len(report.fired[e.id].sprints & set(recent))} | {report.fired[e.id].events} |"
            for e in loud
        ),
    ]


_COMPARED = (
    "group",
    "category",
    "where",
    "says",
    "roles",
    "told",
    "checked",
    "origin",
    "messages",
)


def changes(before: dict[str, Any], after: dict[str, Any]) -> str:
    """What changed between two snapshots: rows added, removed, and changed, and how."""
    old, new = before.get("rows", {}), after.get("rows", {})
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = {
        rule: [k for k in _COMPARED if old[rule].get(k) != new[rule].get(k)]
        for rule in sorted(set(old) & set(new))
    }
    changed = {rule: keys for rule, keys in changed.items() if keys}
    lines = [
        "# The rules list: what changed",
        "",
        f"{len(added)} added, {len(removed)} removed, {len(changed)} changed, "
        f"{len(set(old) & set(new)) - len(changed)} unchanged. A decision made on an",
        "unchanged row carries over.",
        "",
    ]
    if added:
        lines += ["## Added", "", "| Rule | Kind | Says | Where |", "|---|---|---|---|"]
        lines += [
            f"| `{r}` | {new[r]['group']} | {new[r]['says']} | `{new[r]['where']}` |" for r in added
        ]
        lines.append("")
    if removed:
        lines += ["## Removed", "", "| Rule | Kind | Said |", "|---|---|---|"]
        lines += [f"| `{r}` | {old[r]['group']} | {old[r]['says']} |" for r in removed]
        lines.append("")
    if changed:
        lines += ["## Changed", "", "| Rule | What | Before | After |", "|---|---|---|---|"]
        for rule, keys in changed.items():
            for key in keys:
                lines.append(
                    f"| `{rule}` | {key} | {_show(old[rule].get(key))} "
                    f"| {_show(new[rule].get(key))} |"
                )
        lines.append("")
    return "\n".join(lines).replace("\n\n\n", "\n\n")


def _show(value: Any) -> str:
    if isinstance(value, list):
        return _cell(str(v) for v in value)
    return str(value).replace("|", "\\|")


def write(
    events: Path, out: Path, *, since: Path | None = None, built_from: str = ""
) -> list[Path]:
    """The report and its snapshot, and what changed since an earlier snapshot if given."""
    report = count(read(events))
    out.mkdir(parents=True, exist_ok=True)
    page = out / "rules-report.md"
    page.write_text(render(report, source=str(events), built_from=built_from), encoding="utf-8")
    shot = out / "rules.json"
    current = snapshot(report)
    current["built_from"] = built_from
    written = [page]
    if since is not None:
        diff = out / "rules-changes.md"
        diff.write_text(changes(json.loads(since.read_text(encoding="utf-8")), current))
        written.append(diff)
    shot.write_text(json.dumps(current, indent=1, sort_keys=True), encoding="utf-8")
    written.append(shot)
    return written
