"""What waits under `[Unreleased]` with no epic to release it gets a release (crew#961)."""

from __future__ import annotations

from datetime import UTC, datetime

from crew_org.columns import IN_PROGRESS, READY
from crew_org.flows import role_tasks, unreleased
from crew_org.flows.role_tasks import DEVOPS, marked, render
from crew_org.tools.github_project import Card
from crew_org.work_types import TASK
from tests.role_task_fakes import Board, Issues, events, sink

# crew-presentation's own shape on 2026-10-30: each merged fix added its own heading.
FIXES = """# Changelog

## [Unreleased]

### Fixed

- The stats row renders an em dash for a missing value (mqucifer/cp#176)

## [Unreleased]

### Fixed

- Story pages load the site stylesheet (mqucifer/cp#176)

## [0.1.2] - 2026-10-17

### Added

- Each row of the attempt chart links to its story (mqucifer/cp#130)

## [0.1.1] - 2026-10-12
"""


def _run(issues, board, s, cards=()):
    return unreleased.unreleased_releases(issues, board, s, repos={"cp"}, cards=list(cards))


def _epic(number: int = 50, state: str = "OPEN") -> Card:
    return Card(item_id=f"E{number}", number=number, repo="cp", work_type="Epic", state=state)


def _story(number: int, parent: int = 50) -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        repo="cp",
        work_type="Story",
        state="OPEN",
        status=IN_PROGRESS,
        parent=parent,
        created=datetime(2026, 10, 29, tzinfo=UTC),
    )


def _release_task(issues: Issues, number: int, key: str, state: str = "open") -> Card:
    issues.add(
        number,
        render(DEVOPS, kind=role_tasks.RELEASE, key=key, why="w", work="w", done_when=["d"]),
        (DEVOPS.label,),
        state=state,
    )
    return Card(
        item_id=f"T{number}",
        number=number,
        repo="cp",
        work_type=TASK,
        labels=[DEVOPS.label],
        state=state.upper(),
        parent=50,
    )


def test_fixes_waiting_with_no_epic_get_devops_s_release_task_as_a_patch(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    found = _run(issues, board, s)
    assert found.opened == [("cp", 901)]
    task = issues.issues[901]
    mark = marked(task["body"])
    assert (mark.role, mark.kind, mark.key) == (DEVOPS, role_tasks.RELEASE, "unreleased-0.1.2")
    # The same path as an epic's release: the Sponsor approves its merge.
    assert mark.kind in role_tasks.SPONSOR_APPROVES
    assert task["title"] == "Release 0.1.3 of mqucifer/cp's unreleased changes"
    assert "PATCH" in task["body"]
    assert "0.1.3" in task["body"]
    # Every entry it ships, from each `[Unreleased]` heading.
    assert "em dash" in task["body"]
    assert "stylesheet" in task["body"]
    assert ("number", "item-N901", "Points", role_tasks.RELEASE_POINTS) in board.done
    assert ("status", "item-N901", READY) in board.done
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert opened[0]["detail"]["rule"] == unreleased.RULE
    assert opened[0]["detail"]["bump"] == "PATCH"


def test_it_is_opened_once_for_what_waits_after_a_version(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    _run(issues, board, s)
    issues.issues[901]["state"] = "closed"
    again = _run(issues, board, s)
    assert again.opened == []
    assert [d for d in issues.done if d[0] == "create"] == [
        ("create", 901, issues.issues[901]["title"])
    ]


def test_once_released_the_next_fixes_open_the_next_release(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    _run(issues, board, s)
    issues.issues[901]["state"] = "closed"
    issues.files["CHANGELOG.md"] = (
        "## [Unreleased]\n\n### Fixed\n\n- A later fix\n\n## [0.1.3] - 2026-10-31\n\n- x\n"
    )
    found = _run(issues, board, s)
    assert found.opened == [("cp", 902)]
    assert marked(issues.issues[902]["body"]).key == "unreleased-0.1.3"
    assert issues.issues[902]["title"] == "Release 0.1.4 of mqucifer/cp's unreleased changes"


def test_an_added_entry_makes_it_a_minor(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES.replace(
        "## [0.1.2]", "## [Unreleased]\n\n### Added\n\n- A journey strip\n\n## [0.1.2]", 1
    )
    _run(issues, board, s)
    task = issues.issues[901]
    assert task["title"] == "Release 0.2.0 of mqucifer/cp's unreleased changes"
    assert "MINOR" in task["body"]


def test_a_removed_entry_leaves_the_major_to_the_product_owner(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = (
        "## [Unreleased]\n\n### Removed\n\n- The old page\n\n## [1.4.0] - 2026-10-01\n"
    )
    _run(issues, board, s)
    task = issues.issues[901]
    assert task["title"] == "Release of mqucifer/cp's unreleased changes"
    assert "MAJOR" in task["body"]


def test_an_open_release_task_will_ship_them_so_none_is_opened(tmp_path):
    """An epic's release ships what `[Unreleased]` holds, its fixes included (ADR 0019):
    fixes landing while it's in flight wait for it, not a release of their own."""
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    task = _release_task(issues, 70, "epic-50")
    found = _run(issues, board, s, cards=[_epic(), _story(51), task])
    assert found.opened == []
    assert found.waiting == [("cp", "waits for DevOps's release Task mqucifer/cp#70")]


def test_an_epic_split_whose_release_task_isnt_open_yet_holds_them_too(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    found = _run(issues, board, s, cards=[_epic(), _story(51)])
    assert found.opened == []
    assert found.waiting == [("cp", "waits for epic mqucifer/cp#50's release")]


def test_a_released_epic_or_an_unsplit_one_holds_nothing(tmp_path):
    """An approved epic not yet split may wait weeks behind another Goal's: the fixes
    don't wait for it. An epic whose release Task closed has released."""
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    done = _release_task(issues, 70, "epic-50", state="closed")
    cards = [_epic(), _story(51), done, _epic(60)]
    assert _run(issues, board, s, cards=cards).opened == [("cp", 901)]


def test_nothing_waiting_opens_nothing(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = "## [Unreleased]\n\n## [0.1.2] - 2026-10-17\n\n- x\n"
    assert _run(issues, board, s).opened == []
    del issues.files["CHANGELOG.md"]
    assert _run(issues, board, s).opened == []


def test_a_project_never_released_waits_for_its_first_epic(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = "## [Unreleased]\n\n### Fixed\n\n- A fix\n"
    found = _run(issues, board, s)
    assert found.opened == []
    assert found.waiting == [("cp", "no version released yet: its first epic's release cuts one")]


def test_github_refusing_is_held_and_tried_again(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    issues.fail = "create"
    found = _run(issues, board, s)
    assert found.opened == []
    assert found.failed and found.failed[0][0] == "cp"
    issues.fail = None
    assert _run(issues, board, s).opened == [("cp", 901)]


def test_its_pull_request_names_the_version_its_changelog_moved_to(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from crew_org.flows import session_delivery

    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.0] - 2026-10-30\n")
    monkeypatch.setattr(session_delivery, "_branch_paths", lambda _w: ["CHANGELOG.md"])
    card = Card(item_id="T", number=901, title="Release 0.1.3 of mqucifer/cp's unreleased changes")
    mark = role_tasks.Marked(DEVOPS, role_tasks.RELEASE, "unreleased-0.1.2")
    got = session_delivery.pr_title(card, mark, SimpleNamespace(owner="mqucifer"), "cp", tmp_path)
    assert got == "Release 0.2.0 of mqucifer/cp's unreleased changes"
