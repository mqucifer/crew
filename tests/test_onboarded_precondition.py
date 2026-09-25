"""A repo in delivery.repos without a usable record isn't worked (#132).

A precondition, not a gate: the tick leaves the repo alone and the standup
says why, until the project is onboarded.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.flows.loop import not_onboarded
from crew_org.flows.standup import write_standup
from crew_org.project import RECORD_PATH
from tests.test_standup import AT, SPRINT, run

RECORD = """
version: 2
intent:
  scope:
    purpose: Report delivery metrics for the crew's own board.
  release:
    deploys: false
  done:
    bar: Its acceptance criteria are met and CI is green.
"""


class Clones:
    """A workspace whose clones are directories, one per repo."""

    def __init__(self, root: Path, broken: set[str] = frozenset()) -> None:
        self.root, self.broken, self.repo = root, broken, None

    def for_repo(self, repo):
        clone = Clones(self.root, self.broken)
        clone.repo = repo
        return clone

    def current(self) -> Path:
        if self.repo in self.broken:
            raise RuntimeError("git fetch failed: could not resolve host")
        path = self.root / self.repo
        path.mkdir(parents=True, exist_ok=True)
        return path


def onboard(root: Path, repo: str, record: str = RECORD) -> None:
    (root / repo / ".crew").mkdir(parents=True, exist_ok=True)
    (root / repo / RECORD_PATH).write_text(record)


# --- 1: not worked, and the standup says what's missing ------------------------------------


def test_a_repo_without_a_record_is_not_worked(tmp_path):
    gaps = not_onboarded(Clones(tmp_path), {"sprint-metrics"})
    assert "has no `.crew/project.yaml`" in gaps["sprint-metrics"]
    assert "crew onboard sprint-metrics" in gaps["sprint-metrics"]


def test_a_record_missing_a_required_answer_names_it(tmp_path):
    onboard(tmp_path, "sprint-metrics", RECORD.split("  done:")[0])
    why = not_onboarded(Clones(tmp_path), {"sprint-metrics"})["sprint-metrics"]
    assert "what must be true for a change to count as done" in why


def test_a_record_that_cannot_be_checked_says_so(tmp_path):
    why = not_onboarded(Clones(tmp_path, broken={"sprint-metrics"}), {"sprint-metrics"})
    assert "couldn't be checked: git fetch failed" in why["sprint-metrics"]


def test_the_standup_says_which_repo_isnt_worked_and_why():
    text = write_standup(
        run(),
        sprint=SPRINT,
        at=AT,
        waiting=[],
        aging=[],
        not_onboarded={"sprint-metrics": "it has no `.crew/project.yaml`"},
    ).text
    assert "**Not worked: not onboarded**\n- sprint-metrics: it has no `.crew/project.yaml`" in text


def test_a_repo_not_worked_does_not_make_a_quiet_tick_loud():
    """A quiet standup is posted once, then held back: the note isn't repeated every tick."""
    standup = write_standup(
        run(), sprint=SPRINT, at=AT, waiting=[], aging=[], not_onboarded={"r": "no record"}
    )
    assert standup.quiet


# --- 2: once onboarded, it is worked --------------------------------------------------------


def test_an_onboarded_repo_is_worked(tmp_path):
    onboard(tmp_path, "sprint-metrics")
    assert not_onboarded(Clones(tmp_path), {"sprint-metrics"}) == {}


def test_only_the_repos_without_a_record_are_left_alone(tmp_path):
    onboard(tmp_path, "sprint-metrics")
    gaps = not_onboarded(Clones(tmp_path), {"sprint-metrics", "new-thing"})
    assert set(gaps) == {"new-thing"}
