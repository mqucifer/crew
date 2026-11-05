"""Stories write changelog fragments, and a release collects them (crew#1010).

towncrier's fragments, Keep a Changelog's file: each story adds `changes/<n>.<kind>.md`,
and only DevOps's release writes `CHANGELOG.md`, building its section from them. The
three things crew#1010 is done by, the first two shown here: a release cut while a
story is in flight leaves no conflict, and a story's changelog test passes on both
sides of a release. The third is the next real release of each project.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_org.flows import gates, role_tasks
from crew_org.session.tools import Tools
from crew_org.tools import changelog, regression, reproduction

ISSUE_FORMAT = "mqucifer/sprint-metrics#{issue}"
# The setup each project gets: towncrier's, in Keep a Changelog's sections and order.
TOWNCRIER = f"""
[tool.towncrier]
directory = "changes"
filename = "CHANGELOG.md"
start_string = "<!-- towncrier release notes start -->\\n"
title_format = "## [{{version}}] - {{project_date}}"
issue_format = "{ISSUE_FORMAT}"
underlines = ["", "", ""]
wrap = false
""" + "".join(
    f'\n[[tool.towncrier.type]]\ndirectory = "{kind}"\nname = "{name}"\nshowcontent = true\n'
    for kind, name in changelog.KINDS.items()
)
PYPROJECT = '[project]\nname = "demo"\nversion = "1.3.3"\n' + TOWNCRIER
CHANGELOG = (
    "# Changelog\n\n"
    "Unreleased changes wait as fragments in `changes/`; a release collects them here.\n\n"
    "<!-- towncrier release notes start -->\n\n"
    "## [1.3.3] - 2026-10-19\n\n### Fixed\n\n- A released entry (mqucifer/sprint-metrics#600)\n"
)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project set up for fragments, with one merged story's fragment waiting."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes/README.md").write_text("One file per change: `<issue>.<kind>.md`.\n")
    (tmp_path / "changes/661.added.md").write_text("The `/range` window verdict\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/metrics.py").write_text("CAP = 100\n")
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    return tmp_path


def towncrier(root: Path, command: str):
    return Tools(root, None, SimpleNamespace(verify=[])).call("towncrier", {"command": command})


# --- the build ---------------------------------------------------------------------------


def test_a_release_builds_its_section_from_the_fragments_and_removes_them(project):
    (project / "changes/663.fixed.md").write_text("The rate is no longer capped at 100%\n")
    (project / "changes/662.added.md").write_text("A `session` event\n")
    (project / "changes/+docs.added.md").write_text("A guide to the event intake\n")
    reply = towncrier(project, "build --version 1.4.0 --date 2026-11-04")
    assert reply.kind == "ok", reply.text
    text = (project / "CHANGELOG.md").read_text()
    assert text == (
        "# Changelog\n\n"
        "Unreleased changes wait as fragments in `changes/`; a release collects them here.\n\n"
        "<!-- towncrier release notes start -->\n\n"
        "## [1.4.0] - 2026-11-04\n\n"
        "### Added\n\n"
        "- The `/range` window verdict (mqucifer/sprint-metrics#661)\n"
        "- A `session` event (mqucifer/sprint-metrics#662)\n"
        "- A guide to the event intake\n\n"
        "### Fixed\n\n"
        "- The rate is no longer capped at 100% (mqucifer/sprint-metrics#663)\n\n"
        "## [1.3.3] - 2026-10-19\n\n### Fixed\n\n- A released entry (mqucifer/sprint-metrics#600)\n"
    )
    # The fragments it used are gone; the folder's README isn't one.
    assert sorted(p.name for p in (project / "changes").iterdir()) == ["README.md"]
    assert "1.4.0" in reply.text and "4 fragments" in reply.text


def test_the_build_is_towncriers(project):
    """The crew builds the section itself, so a project's container needs no towncrier,
    and checks it against towncrier's own on the project's setup."""
    (project / "changes/663.fixed.md").write_text("The rate is no longer capped at 100%\n")
    (project / "changes/662.added.md").write_text("A `session` event\n")
    (project / "changes/664.removed.md").write_text("The old `/sprints` answer\n")
    (project / "changes/+docs.changed.md").write_text("The intake's guide is rewritten\n")
    theirs = subprocess.run(
        [sys.executable, "-m", "towncrier", "build", "--draft", "--version", "1.4.0"]
        + ["--date", "2026-11-04"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    ours = changelog.build(
        CHANGELOG,
        changelog.fragments_in(project),
        "1.4.0",
        "2026-11-04",
        changelog.config_in(project),
    ).section
    drafted = theirs[theirs.index("## [1.4.0]") :]
    assert ours.split() == drafted.split()


def test_a_draft_changes_nothing(project):
    reply = towncrier(project, "towncrier build --draft --version 1.4.0 --date 2026-11-04")
    assert reply.kind == "ok", reply.text
    assert "## [1.4.0] - 2026-11-04" in reply.text
    assert (project / "CHANGELOG.md").read_text() == CHANGELOG
    assert (project / "changes/661.added.md").is_file()


@pytest.mark.parametrize(
    ("command", "says"),
    [
        ("build", "--version"),
        ("build --version 1.4 --date 2026-11-04", "1.4"),
        ("build --version 1.4.0 --date tomorrow", "tomorrow"),
        ("create 12.added.md", "build"),
        ("build --version 1.3.3 --date 2026-11-04", "already"),
    ],
)
def test_anything_but_a_build_of_a_new_version_is_refused(project, command, says):
    reply = towncrier(project, command)
    assert reply.kind == "refused" and says in reply.text, reply.text
    assert (project / "CHANGELOG.md").read_text() == CHANGELOG


def test_nothing_to_release_is_refused(project):
    (project / "changes/661.added.md").unlink()
    reply = towncrier(project, "build --version 1.4.0 --date 2026-11-04")
    assert reply.kind == "refused" and "nothing" in reply.text.lower(), reply.text


def test_only_devops_release_session_is_offered_it():
    def names(role, kind):
        return [s["function"]["name"] for s in role_tasks.done_specs(role, (), "sm", kind)]

    assert "towncrier" in names(role_tasks.DEVOPS, role_tasks.RELEASE)
    assert "towncrier" not in names(role_tasks.DEVOPS, role_tasks.CI_SERVICES)
    assert "towncrier" not in names(role_tasks.ARCHITECT, "")
    from crew_org.session.tools import SPECS

    assert "towncrier" not in [s["function"]["name"] for s in SPECS]


def test_a_release_may_change_the_fragments_it_removes():
    assert (
        role_tasks.outside_scope(
            role_tasks.DEVOPS, ["CHANGELOG.md", "changes/661.added.md", "pyproject.toml"]
        )
        == []
    )


# --- the switch-over loses nothing ---------------------------------------------------------

# crew-presentation on 2026-11-04: a fix merged the old way, under `[Unreleased]`, while
# its 0.4.1 waits; another branch in flight wrote its own heading.
OLD_WAY = """# Changelog

## [Unreleased]

### Fixed

- The first-attempt rate prints its '%' unit (mqucifer/crew-presentation#305)

## [Unreleased]

### Added

- The story page names its epic (mqucifer/crew-presentation#310)

## [0.4.0] - 2026-11-01

### Added

- A "What got done" section (mqucifer/crew-presentation#279)
"""


def test_the_first_release_after_the_switch_carries_both(tmp_path):
    """The entries left under `[Unreleased]` and the fragments go in one section, by
    kind, and no `[Unreleased]` heading is left to write to."""
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes/312.fixed.md").write_text("The replay's legend reads in dark mode\n")
    (tmp_path / "CHANGELOG.md").write_text(OLD_WAY)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.towncrier]\nissue_format = "mqucifer/crew-presentation#{issue}"\n'
    )
    reply = towncrier(tmp_path, "build --version 0.5.0 --date 2026-11-04")
    assert reply.kind == "ok", reply.text
    assert "2 entries" in reply.text
    assert (tmp_path / "CHANGELOG.md").read_text() == (
        "# Changelog\n\n"
        "## [0.5.0] - 2026-11-04\n\n"
        "### Added\n\n"
        "- The story page names its epic (mqucifer/crew-presentation#310)\n\n"
        "### Fixed\n\n"
        "- The first-attempt rate prints its '%' unit (mqucifer/crew-presentation#305)\n"
        "- The replay's legend reads in dark mode (mqucifer/crew-presentation#312)\n\n"
        "## [0.4.0] - 2026-11-01\n\n"
        "### Added\n\n"
        '- A "What got done" section (mqucifer/crew-presentation#279)\n'
    )
    assert changelog.unreleased((tmp_path / "CHANGELOG.md").read_text()) == []


def test_a_release_with_only_old_entries_is_built_from_them(tmp_path):
    """The 0.4.1 crew-presentation may have open as this lands: nothing in `changes/`."""
    (tmp_path / "CHANGELOG.md").write_text(OLD_WAY)
    reply = towncrier(tmp_path, "build --version 0.4.1 --date 2026-11-04")
    assert reply.kind == "ok", reply.text
    text = (tmp_path / "CHANGELOG.md").read_text()
    assert text.startswith("# Changelog\n\n## [0.4.1] - 2026-11-04\n\n### Added\n\n")
    assert "(mqucifer/crew-presentation#305)" in text and "[Unreleased]" not in text


# --- done when: a release cut while a story is in flight leaves no conflict -----------------


def _story(root: Path, branch: str, number: int, line: str) -> None:
    git(root, "checkout", "-qb", branch, "main")
    (root / "src/metrics.py").write_text(f"CAP = None  # {number}\n")
    (root / f"changes/{number}.changed.md").write_text(f"{line}\n")
    commit(root, f"story {number}")


def _release(root: Path, version: str) -> None:
    git(root, "checkout", "-qb", f"release/{version}", "main")
    reply = towncrier(root, f"build --version {version} --date 2026-11-04")
    assert reply.kind == "ok", reply.text
    commit(root, f"release {version}")


def test_a_release_cut_while_a_story_is_in_flight_leaves_no_conflict(project):
    _story(project, "feat/663-uncap", 663, "The rate is no longer capped")
    _release(project, "1.4.0")
    git(project, "checkout", "-q", "main")
    git(project, "merge", "-q", "--no-ff", "release/1.4.0")
    # The story, brought up to date after the release merged: nothing to resolve.
    git(project, "checkout", "-q", "feat/663-uncap")
    merged = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "main"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert merged.returncode == 0, merged.stdout + merged.stderr
    git(project, "checkout", "-q", "main")
    git(project, "merge", "-q", "--no-ff", "feat/663-uncap")
    # The next release ships it, once.
    _release(project, "1.5.0")
    text = (project / "CHANGELOG.md").read_text()
    assert text.count("The rate is no longer capped") == 1
    assert text.index("## [1.5.0]") < text.index("no longer capped") < text.index("## [1.4.0]")
    assert text.count("The `/range` window verdict") == 1


def test_the_old_way_conflicted_on_the_same_cut(tmp_path):
    """The same two branches in sprint-metrics' old shape, each editing `[Unreleased]`:
    the story either conflicts or, merged clean, lands its line in the notes of a release
    that didn't ship it, as mqucifer/sprint-metrics#937's did. What the test above shows
    is the fragments' doing, not the scenario's."""
    subheadings = "### Added\n\n### Changed\n\n"
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- One merged\n\n### Changed\n\n"
        "## [1.3.3] - 2026-10-19\n"
    )
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "merged")
    git(tmp_path, "checkout", "-qb", "story")
    text = (tmp_path / "CHANGELOG.md").read_text()
    (tmp_path / "CHANGELOG.md").write_text(text.replace("- One merged\n", "- One merged\n- Mine\n"))
    commit(tmp_path, "story")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n{subheadings}## [1.4.0] - 2026-11-04\n\n"
        "### Added\n\n- One merged\n\n### Changed\n\n## [1.3.3] - 2026-10-19\n"
    )
    commit(tmp_path, "release")
    git(tmp_path, "checkout", "-q", "story")
    merged = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "main"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    if merged.returncode != 0:
        assert "CONFLICT" in merged.stdout + merged.stderr
    else:
        text = (tmp_path / "CHANGELOG.md").read_text()
        assert "- Mine" not in [line for _, line in changelog.unreleased(text)]
        assert text.index("## [1.4.0]") < text.index("- Mine") < text.index("## [1.3.3]")


# --- done when: a story's changelog test passes before and after a release cut --------------

# The shape the gate lets through: the entry is found in its fragment while it waits,
# and in the released section after, and the test still fails if it's in neither.
EVERYWHERE = '''from pathlib import Path

ROOT = Path(__file__).parent.parent


def changelog_entries():
    """Every changelog entry: the fragments waiting in changes/, and CHANGELOG.md."""
    waiting = [p.read_text() for p in sorted((ROOT / "changes").glob("*.md"))]
    return "\\n".join(waiting + [(ROOT / "CHANGELOG.md").read_text()])


def test_the_changelog_names_the_uncap():
    assert "no longer capped" in changelog_entries()
'''
# One that reads its own fragment alone: the release deletes it.
FRAGMENT_ONLY = """from pathlib import Path

FRAGMENT = Path(__file__).parent.parent / "changes" / "663.changed.md"


def test_the_fragment_names_the_uncap():
    assert "no longer capped" in FRAGMENT.read_text()
"""


def _passes(root: Path, test: str) -> bool:
    ran = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return ran.returncode == 0


def _pinned(root: Path) -> list[gates.Finding]:
    merged = regression.merged_base(root)
    assert merged is not None
    return gates.reads_only_unreleased(root, merged, reproduction._changed(root, merged.sha))


def test_a_storys_changelog_test_passes_before_and_after_a_release_cut(project):
    _story(project, "feat/663-uncap", 663, "The rate is no longer capped")
    (project / "tests").mkdir()
    (project / "tests/test_changelog.py").write_text(EVERYWHERE)
    (project / "tests/test_fragment.py").write_text(FRAGMENT_ONLY)
    # The gate lets the first through and sends the second back, by name.
    (finding,) = _pinned(project)
    assert finding.rule == "check.gates.reads_only_unreleased"
    assert "`tests/test_fragment.py::test_the_fragment_names_the_uncap`" in finding.says
    assert "test_the_changelog_names_the_uncap" not in finding.says
    commit(project, "tests")
    assert _passes(project, "tests/test_changelog.py")
    assert _passes(project, "tests/test_fragment.py")
    # The story merges; a release is cut.
    git(project, "checkout", "-q", "main")
    git(project, "merge", "-q", "--no-ff", "feat/663-uncap")
    _release(project, "1.4.0")
    assert _passes(project, "tests/test_changelog.py")
    assert not _passes(project, "tests/test_fragment.py")
    # And it still fails when the entry is in neither.
    text = (project / "CHANGELOG.md").read_text()
    (project / "CHANGELOG.md").write_text(text.replace("no longer capped", "uncapped"))
    assert not _passes(project, "tests/test_changelog.py")
