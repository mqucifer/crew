"""The Product Owner's project-wide decisions reach the project's log (crew#468).

A row the settle step marks as deciding something for every epic is written into the
project's `docs/decisions/` by an ordinary pull request from a clone, which the crew
merges itself, as it does a design revision. Run every pass, it never writes an entry
twice and never has two log pull requests open at once.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from crew_org.crews.settle_crew import Conclusion, Row
from crew_org.events import EventSink
from crew_org.flows import decision_log as log
from crew_org.flows.merge import Landed, Landing
from crew_org.flows.settle import TO_LOG_LABEL, render

REPO = "sprint-metrics"
TODAY = date(2026, 10, 8)
APPROVED = "**Outcome** — trends across sprints."


def conclusion_body(*rows: Row) -> str:
    return f"{APPROVED}\n\n{render(Conclusion(rows=list(rows)), owner='mqucifer', repo=REPO)}\n"


def row(context="Counting", *, wide=True, own=False, settles=(1,)) -> Row:
    return Row(
        context=context,
        decision="A story counts in the sprint it merges | not where it started",
        consequences="Every metric reads the merge date",
        source="crew#389" if not own else "the Goal says per sprint",
        own_call=own,
        goal_wording="per sprint" if own else "",
        settles=list(settles),
        project_wide=wide,
    )


class Github:
    """One delivery repository: labelled epics, its log on main, pulls and labels."""

    def __init__(self, epics=None, log=None, open_pulls=None):
        self.epics = epics or {}
        self.log = dict(log or {})
        self.pulls = list(open_pulls or [])
        self.created: list[dict] = []
        self.removed: list[tuple[int, str]] = []

    def open_pulls(self, repo):
        return self.pulls

    def labelled(self, repo, label):
        assert label == TO_LOG_LABEL
        return [{"number": n, **e} for n, e in self.epics.items()]

    def repository(self, repo):
        return {"default_branch": "main"}

    def list_dir(self, repo, path, ref):
        return sorted(self.log)

    def file_at(self, repo, path, ref):
        return self.log.get(Path(path).name)

    def create_pull(self, repo, *, title, head, base, body):
        self.created.append({"title": title, "head": head, "base": base, "body": body})
        return {"html_url": f"https://github.com/mqucifer/{repo}/pull/500"}

    def add_labels(self, repo, number, labels):
        pass

    def remove_label(self, repo, number, name):
        self.removed.append((number, name))


class Reviewer:
    def __init__(self):
        self.reviews: list[int] = []

    def pull_reviews(self, repo, number):
        return []

    def create_review(self, repo, number, *, event, body):
        self.reviews.append(number)


class Clone:
    """A workspace's clone: the files written, and what was committed and pushed."""

    def __init__(self, root: Path):
        self.root, self.branch, self.commits, self.pushed = root, None, [], False

    def for_repo(self, repo):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def open(self, branch):
        self.branch = branch
        return self.root

    def commit(self, message):
        self.commits.append(message)

    def push(self, force=False):
        self.pushed = True


def write(gh, tmp_path, monkeypatch, *, merged=True):
    monkeypatch.setattr(
        log,
        "land",
        lambda issues, repo, number: Landed(Landing.MERGED if merged else Landing.QUEUED, ""),
    )
    clone, reviewer = Clone(tmp_path), Reviewer()
    result = log.write_logs(gh, reviewer, EventSink(None), clone, repos={REPO}, today=TODAY)
    return result, clone, reviewer


def entries(tmp_path):
    return sorted((tmp_path / "docs" / "decisions").glob("*.md"))


def test_marked_rows_become_entries_numbered_after_the_log_in_its_own_form(tmp_path, monkeypatch):
    body = conclusion_body(row("Local", wide=False), row("Sprint counting"))
    gh = Github(
        epics={406: {"title": "Run as a service", "body": body}}, log={"0008-runtime.md": ""}
    )

    result, clone, _ = write(gh, tmp_path, monkeypatch)

    [only] = entries(tmp_path)
    assert only.name == "0009-sprint-counting.md", (
        "after the log's last entry, named by its context"
    )
    text = only.read_text()
    assert text.startswith("# 9. Sprint counting\n\n- **Date:** 2026-10-08\n- **Status:** Accepted")
    assert "## Decision\n\nA story counts in the sprint it merges | not where it started" in text
    assert "epic #406 (Run as a service), row R2" in text
    assert "<!-- crew:from epic 406 R2 -->" in text
    assert result.opened == [(REPO, "https://github.com/mqucifer/sprint-metrics/pull/500")]
    assert clone.pushed and "Refs #406" in clone.commits[0]
    assert log.LOG_PR in gh.created[0]["body"] and gh.created[0]["base"] == "main"


def test_an_own_call_says_so_in_its_entry(tmp_path, monkeypatch):
    gh = Github(epics={407: {"title": "Trends", "body": conclusion_body(row(own=True))}})
    write(gh, tmp_path, monkeypatch)
    assert "the Product Owner's own call, within the Goal" in entries(tmp_path)[0].read_text()


def test_a_row_already_in_the_log_is_not_written_again_and_its_label_comes_off(
    tmp_path, monkeypatch
):
    body = conclusion_body(row())
    logged = {"0009-counting.md": "# 9. Counting\n\n<!-- crew:from epic 406 R1 -->\n"}
    gh = Github(epics={406: {"title": "Run as a service", "body": body}}, log=logged)

    result, _, _ = write(gh, tmp_path, monkeypatch)

    assert entries(tmp_path) == [] and gh.created == [] and result.opened == []
    assert gh.removed == [(406, TO_LOG_LABEL)]


def test_an_open_log_pull_request_is_merged_before_another_is_opened(tmp_path, monkeypatch):
    waiting = {"number": 499, "body": f"{log.LOG_PR}\nentries"}
    gh = Github(epics={406: {"title": "x", "body": conclusion_body(row())}}, open_pulls=[waiting])

    result, _, reviewer = write(gh, tmp_path, monkeypatch, merged=False)

    assert reviewer.reviews == [499], "approved by the crew's reviewing identity"
    assert result.merged == [] and gh.created == [], "one at a time: it waits"

    result, _, _ = write(gh, tmp_path, monkeypatch, merged=True)
    assert result.merged == [(REPO, 499)] and len(gh.created) == 1


def test_nothing_labelled_writes_nothing(tmp_path, monkeypatch):
    result, clone, _ = write(Github(), tmp_path, monkeypatch)
    assert not result.moved and clone.commits == []


def test_a_conclusion_with_no_marked_rows_gives_none():
    assert log.to_log(406, "x", conclusion_body(row(wide=False))) == []
    assert log.to_log(406, "x", APPROVED) == []


def test_a_failure_in_one_project_is_reported_not_raised(tmp_path, monkeypatch):
    class Broken(Github):
        def open_pulls(self, repo):
            raise RuntimeError("GitHub said no")

    result, _, _ = write(Broken(), tmp_path, monkeypatch)
    assert result.failed and "GitHub said no" in result.failed[0][1]
