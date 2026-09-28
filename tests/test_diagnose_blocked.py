"""The Senior Engineer on each card blocked for a person, once (#9).

On 2026-09-27 it never ran, because only `crew diagnose` called it, and every
crew defect that day was found by hand. Run by hand on sprint-metrics#200, it
found a sharper cause than the hand diagnosis had.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.events import EventSink
from crew_org.flows import diagnose as flow
from crew_org.tools.github_project import Card


def card(number, status="Blocked", labels=("blocked", "needs:human"), repo="sprint-metrics"):
    return Card(
        item_id=f"C{number}",
        number=number,
        title=f"story {number}",
        status=status,
        state="OPEN",
        work_type="Story",
        repo=repo,
        labels=frozenset(labels),
    )


class Issues:
    def __init__(self, diagnosed=()):
        self.diagnosed = set(diagnosed)
        self.posted: list[tuple[int, str]] = []

    def has_comment_marked(self, repo, number, marker):
        return number in self.diagnosed

    def comment(self, repo, number, body):
        self.posted.append((number, body))


class Found:
    findings = [object(), object()]


def stub(monkeypatch, fail_on=()):
    def run(root, failure, *, choose, diagnose):
        if f"#{fail_on}" in failure:
            raise RuntimeError("model down")
        return Found()

    monkeypatch.setattr(flow, "failure_of", lambda d, i, repo, n: f"## {repo}#{n}")
    monkeypatch.setattr(flow, "run_diagnosis", run)
    monkeypatch.setattr(flow, "render", lambda report, *, repo, card: f"# Diagnosis of #{card}")


def run(issues, cards, tmp_path: Path, seen=None, **kw):
    sink = EventSink(None)
    if seen is not None:
        sink.subscribe(seen.append)
    return flow.diagnose_blocked(
        issues,
        sink,
        cards=cards,
        repos={"sprint-metrics"},
        events_dir=tmp_path,
        out_dir=tmp_path / "diagnoses",
        choose=None,
        diagnose=None,
        **kw,
    )


def test_a_card_blocked_for_a_person_gets_the_diagnosis_on_it(monkeypatch, tmp_path):
    stub(monkeypatch)
    issues = Issues()
    assert run(issues, [card(200)], tmp_path) == [("sprint-metrics", 200)]
    ((number, body),) = issues.posted
    assert number == 200 and flow.DIAGNOSED_MARKER in body and "# Diagnosis of #200" in body
    assert "nothing was changed or filed" in body, "a person decides what becomes work"
    assert (tmp_path / "diagnoses" / "sprint-metrics-200.md").exists()


def test_each_block_is_diagnosed_once(monkeypatch, tmp_path):
    stub(monkeypatch)
    issues = Issues(diagnosed={200})
    assert run(issues, [card(200)], tmp_path) == [] and issues.posted == []


def test_only_cards_blocked_for_a_person(monkeypatch, tmp_path):
    """#238 sat in Inbox with needs:human: a hold, not a failure."""
    stub(monkeypatch)
    issues = Issues()
    cards = [
        card(238, status="Inbox (Goals)"),
        card(7, labels=("blocked",)),
        card(9, repo="elsewhere"),
    ]
    assert run(issues, cards, tmp_path) == [] and issues.posted == []


def test_a_tick_diagnoses_a_bounded_number(monkeypatch, tmp_path):
    stub(monkeypatch)
    issues = Issues()
    done = run(issues, [card(n) for n in (1, 2, 3)], tmp_path)
    assert done == [("sprint-metrics", 1), ("sprint-metrics", 2)] and len(issues.posted) == 2


def test_a_failed_diagnosis_is_noted_and_the_rest_go_on(monkeypatch, tmp_path):
    stub(monkeypatch, fail_on=1)
    issues, seen = Issues(), []
    done = run(issues, [card(1), card(2)], tmp_path, seen)
    assert done == [("sprint-metrics", 2)]
    assert any("diagnosis failed" in (e.summary or "") for e in seen)
