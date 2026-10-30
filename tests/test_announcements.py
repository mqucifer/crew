"""A producer's announced new major opens an item in each project that uses it (crew#960)."""

from __future__ import annotations

import json

from crew_org.columns import NEEDS_REFINEMENT
from crew_org.flows import announcements as an
from crew_org.flows.role_tasks import ANNOUNCED, ARCHITECT, marked
from crew_org.project import RECORD_PATH
from tests.role_task_fakes import Board, Issues, events, sink

RECORD = """
intent:
  scope:
    purpose: Show how the crew works.
    serves:
    - {who: the Sponsor, needs: [a sprint overview]}
    uses:
    - repo: mqucifer/crew
      file: README.md
      section: 'Data packages: how projects use them'
      pin: delivery-history.tag
      contract:
      - contracts/delivery-history/schema.json
      - contracts/delivery-history/manifest.schema.json
    - repo: mqucifer/design-system
      file: README.md
      section: How projects use it
      pin: site/css/design-system.version
      contract:
      - styles.css
  release:
    deploys: true
    where: GitHub Pages
  done:
    bar: Its tests pass.
"""
TAG = "delivery-history-2026-10-29"
CHANGELOG = """\
2.0.0 carries sprint-metrics 4.2.1's answers.
- **What leaves:** `escalation_count`.
- **What changes shape:** `top_failure_causes` becomes a list."""
README = f"""# crew

## Data packages: how projects use them

How to use it.

### Announced: delivery-history 2.0.0

{CHANGELOG}

## The two gates
"""
ANNOUNCEMENT = {
    "2.0.0": {
        "replaces": "1.x",
        "on": "2026-11-06",
        "contract": "contracts/delivery-history/2.0.0/",
        "changelog": {"file": "README.md", "section": "Announced: delivery-history 2.0.0"},
    }
}
DIR = "contracts/delivery-history"


class Project(Issues):
    """crew-presentation: its record, and the release it pins."""

    def __init__(self, record: str = RECORD, pin: str = TAG) -> None:
        super().__init__()
        self.files = {RECORD_PATH: record, "delivery-history.tag": f"{pin}\n"}


def producer(announced=ANNOUNCEMENT, *, at_pin=None, versions=("1.10.0", "1.11.0")):
    """The crew's files as its main and the pinned tag have them; design-system announces none."""
    pinned = {f"{DIR}/versions.json": json.dumps(dict.fromkeys(versions, "f"))}
    if at_pin is not None:
        pinned[f"{DIR}/{an.ANNOUNCED_FILE}"] = json.dumps(at_pin)
    files = {
        ("mqucifer/crew", "HEAD"): {
            "README.md": README,
            **({f"{DIR}/{an.ANNOUNCED_FILE}": json.dumps(announced)} if announced else {}),
        },
        ("mqucifer/crew", TAG): pinned,
    }
    asked: list[tuple[str, str, str]] = []

    def read(repo: str, path: str, ref: str = "HEAD") -> str | None:
        asked.append((repo, path, ref))
        return files.get((repo, ref), {}).get(path)

    read.asked = asked  # type: ignore[attr-defined]
    return read


def run(issues, board, s, read, repos=frozenset({"crew-presentation"})):
    return an.announced_tasks(issues, board, s, repos=set(repos), cards=[], read=read)


def test_an_announced_major_opens_one_architect_task_in_needs_refinement(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    done = run(issues, board, s, producer())
    assert done.opened == [("crew-presentation", 901)]
    task = issues.issues[901]
    assert task["title"] == "delivery-history 2.0.0 replaces 1.x on 2026-11-06"
    found = marked(task["body"])
    assert (found.role, found.kind) == (ARCHITECT, ANNOUNCED)
    assert found.key == "mqucifer/crew:delivery-history@2.0.0"
    assert [lab["name"] for lab in task["labels"]] == ["role:architect"]
    # The changelog whole, the date, where the new contract is, and what it pins now.
    assert CHANGELOG in task["body"]
    assert "**2026-11-06**" in task["body"]
    assert (
        "https://github.com/mqucifer/crew/tree/HEAD/contracts/delivery-history/2.0.0/"
        in task["body"]
    )
    assert f"`{TAG}`" in task["body"] and "1.11.0" in task["body"]
    # Triaged as any finding: no points, in Needs Refinement.
    assert ("status", "item-N901", NEEDS_REFINEMENT) in board.done
    assert not any(d[0] == "number" for d in board.done)
    [event] = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert event["detail"]["event"] == "producer.announced"
    assert event["detail"]["rule"] == "decision.announcements.announced_tasks"
    assert event["detail"]["version"] == "2.0.0"


def test_a_second_pass_files_nothing_again_even_once_closed(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    run(issues, board, s, producer())
    assert run(issues, board, s, producer()).opened == []
    issues.issues[901]["state"] = "closed"
    assert run(issues, board, s, producer()).opened == []
    assert len(issues.issues) == 1


def test_a_producer_that_publishes_no_announcements_files_nothing_and_says_so(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    done = run(issues, board, s, producer(announced=None))
    assert done.opened == [] and issues.issues == {}
    assert set(done.unannounced) == {
        ("crew-presentation", "mqucifer/crew"),
        ("crew-presentation", "mqucifer/design-system"),
    }
    said = [e["summary"] for e in events(tmp_path) if e["kind"] == "note"]
    assert "crew-presentation: mqucifer/design-system publishes no announcements" in said


def test_a_project_that_pins_the_announced_major_already_gets_nothing(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    done = run(issues, board, s, producer(versions=("1.11.0", "2.0.0")))
    assert done.opened == [] and issues.issues == {}


def test_the_pinned_version_leaves_out_what_was_only_announced_at_its_tag(tmp_path):
    # A release cut during the week records 2.0.0 beside 1.x and still follows 1.x.
    issues, board, s = Project(), Board(), sink(tmp_path)
    read = producer(versions=("1.11.0", "2.0.0"), at_pin=ANNOUNCEMENT)
    assert run(issues, board, s, read).opened == [("crew-presentation", 901)]
    assert "follows 1.11.0" in issues.issues[901]["body"]


def test_a_major_that_replaces_another_than_the_pinned_one_files_nothing(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    later = {"3.0.0": {**ANNOUNCEMENT["2.0.0"], "replaces": "2.x"}}
    assert run(issues, board, s, producer(announced=later)).opened == []


def test_nothing_is_filed_in_the_producers_own_repository(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    done = run(issues, board, s, producer(), repos={"crew"})
    assert done.opened == [] and issues.issues == {}


def test_a_project_with_no_record_is_passed_over(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    done = run(issues, board, s, producer())
    assert done.opened == [] and done.failed == [] and done.unannounced == []


def test_an_unreadable_announcement_or_pin_is_held_and_files_nothing(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)

    def broken(repo, path, ref="HEAD"):
        if path.endswith(an.ANNOUNCED_FILE) and ref == "HEAD":
            return "{not json"
        return None

    done = run(issues, board, s, broken)
    assert issues.issues == {}
    assert done.failed and done.failed[0][0] == "crew-presentation"
    assert "mqucifer/crew" in done.failed[0][1]

    unpinned = run(Project(pin=""), Board(), s, producer())
    assert unpinned.opened == []
    assert "names no release" in unpinned.failed[0][1]


def test_a_task_github_refuses_is_held_and_tried_next_pass(tmp_path):
    issues, board, s = Project(), Board(), sink(tmp_path)
    issues.fail = "create"
    done = run(issues, board, s, producer())
    assert done.opened == [] and "not opened" in done.failed[0][1]
    issues.fail = None
    assert run(issues, board, s, producer()).opened == [("crew-presentation", 901)]


def test_the_dry_run_renders_what_a_pass_would_file_and_opens_nothing(tmp_path):
    issues = Project()
    plan = an.plan(issues, "crew-presentation", read=producer())
    [item] = plan.items
    assert item.title == "delivery-history 2.0.0 replaces 1.x on 2026-11-06"
    assert item.filed is None
    assert issues.done == [] and issues.issues == {}
    text = an.render_plan(plan)
    assert "Would file in crew-presentation: delivery-history 2.0.0 replaces 1.x" in text
    assert CHANGELOG in text
    assert "mqucifer/design-system publishes no announcements" in text

    run(issues, Board(), sink(tmp_path), producer())
    [again] = an.plan(issues, "crew-presentation", read=producer()).items
    assert again.filed == 901
    assert "Already filed as crew-presentation#901" in an.render_plan(
        an.plan(issues, "crew-presentation", read=producer())
    )


def test_the_dry_run_reads_the_producer_at_the_ref_it_is_given():
    read = producer()
    an.plan(Project(), "crew-presentation", read=read, ref="feat/960")
    assert ("mqucifer/crew", f"{DIR}/{an.ANNOUNCED_FILE}", "feat/960") in read.asked
