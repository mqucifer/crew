"""QA verifies a story in a session, and its verdict cites what it ran (crew#895).

Its one call judged pasted tests it couldn't run, and a filter over its words overturned
proof it had: on mqucifer/sprint-metrics#763 and #770 a changelog criterion QA had
quoted correctly was held unproven, because the evidence also named the tests the story
removed. Now each criterion says what it rests on, and `done` holds each citation to
what the session ran and what the branch says.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews import qa_crew
from crew_org.crews.qa_crew import (
    CriterionVerdict,
    QAVerdict,
    Quote,
    Ran,
    Recorded,
    check_cited,
    is_artifact,
)
from crew_org.rulebook import Refusal
from tests.test_regression_merged import git
from tests.test_session import CHECKS, Fake, Proxy, said, seen  # noqa: F401
from tests.test_session import Ran as Done

RUN = "uv run pytest -q tests/test_service.py::test_sprint_is_gone"
CHANGELOG = "### Removed\n\n- GET /sprint: a range spanning one sprint answers it.\n"


def verdict(*criteria: CriterionVerdict) -> QAVerdict:
    return QAVerdict(
        summary="judged", accepted=all(c.proven for c in criteria), criteria=list(criteria)
    )


def by_run(command: str = RUN, code: int = 0, proven: bool = True) -> CriterionVerdict:
    return CriterionVerdict(
        criterion="GET /sprint answers 410",
        proven=proven,
        by="run",
        ran=[Ran(command=command, exit_code=code)],
        evidence="test_sprint_is_gone sends GET /sprint and asserts the 410",
    )


def by_read(path: str, text: str, criterion: str = "the changelog names the removal"):
    return CriterionVerdict(
        criterion=criterion,
        proven=True,
        by="read",
        quoted=[Quote(path=path, text=text)],
        evidence="the [Unreleased] section's Removed entry names it",
    )


def by_check(check: str = "docker", path: str = ".github/workflows/ci.yml", text: str = ""):
    return CriterionVerdict(
        criterion="the image builds",
        proven=True,
        by="check",
        check=check,
        quoted=[Quote(path=path, text=text or "run: docker build .")],
        evidence="the docker job builds the image from the Dockerfile",
    )


def refused(v: QAVerdict, root: Path, *, ran=(), base: str = "", checks=None) -> Refusal:
    with pytest.raises(Refusal) as found:
        check_cited(v, ran=list(ran), root=root, base=base, checks=checks or {})
    return found.value


@pytest.fixture
def branch(tmp_path: Path) -> tuple[Path, str]:
    """A story's branch, checked out: it removed a test and added a changelog entry. Returns
    the worktree and its base."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_service.py").write_text(
        "def test_sprint_answers_200():\n    pass\n\n\ndef test_range():\n    pass\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("def sprint():\n    return 410\n")
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text(
        "jobs:\n  docker:\n    - run: docker build .\n"
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.3.3"\n')
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    git(tmp_path, "checkout", "-qb", "feat/763")
    (tmp_path / "tests/test_service.py").write_text("def test_range():\n    pass\n")
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## [Unreleased]\n\n{CHANGELOG}")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "story")
    # QA reads a worktree of the clone, as delivery left the branch.
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "worktree", "add", "-q", str(tmp_path / "wt"), "feat/763")
    return (tmp_path / "wt").resolve(), base


# --- the form -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("by", "fields", "says"),
    [
        ("run", {}, "cites the run"),
        (
            "check",
            {"quoted": [{"path": ".github/workflows/ci.yml", "text": "x"}]},
            "names the check",
        ),
        ("read", {}, "quotes the file it's about"),
    ],
)
def test_a_proven_criterion_cites_what_it_rests_on(by, fields, says):
    with pytest.raises(ValidationError, match=says):
        CriterionVerdict(
            criterion="c", proven=True, by=by, evidence="what it rests on, said", **fields
        )


def test_an_unproven_criterion_needs_only_its_reason():
    c = CriterionVerdict(
        criterion="c", proven=False, by="run", evidence="no test sends GET /sprint"
    )
    assert verdict(c).unproven == [c]


# --- each run cited is one the session ran ----------------------------------------------------


def test_a_proven_run_that_the_session_ran_and_passed_is_taken(tmp_path):
    check_cited(
        verdict(by_run()), ran=[Done(RUN, 0, "1 passed in 0.2s")], root=tmp_path, base="", checks={}
    )


def test_a_run_the_session_didnt_make_is_refused(tmp_path):
    found = refused(verdict(by_run()), tmp_path, ran=[Done("uv run pytest -q", 0, "9 passed")])
    assert found.rule == "check.acceptance.ran_as_cited"
    assert "isn't a command this session ran" in str(found)


def test_a_run_cited_with_another_exit_code_is_refused(tmp_path):
    found = refused(verdict(by_run(code=0)), tmp_path, ran=[Done(RUN, 1, "1 failed")])
    assert "exited 1, not 0" in str(found)


def test_a_failed_run_proves_nothing_but_can_be_cited_for_an_unproven_criterion(tmp_path):
    ran = [Done(RUN, 1, "1 failed")]
    found = refused(verdict(by_run(code=1)), tmp_path, ran=ran)
    assert "A run that failed proves nothing" in str(found)
    check_cited(verdict(by_run(code=1, proven=False)), ran=ran, root=tmp_path, base="", checks={})


def test_a_run_whose_tests_were_skipped_proves_nothing(tmp_path):
    """mqucifer/sprint-metrics#667: the database tests were skipped where it was proven."""
    found = refused(verdict(by_run()), tmp_path, ran=[Done(RUN, 0, "1 skipped in 0.1s")])
    assert "passed no test" in str(found)
    found = refused(verdict(by_run()), tmp_path, ran=[Done(RUN, 0, "no tests ran in 0.1s")])
    assert "passed no test" in str(found)


def test_a_command_run_twice_is_cited_by_the_run_that_ended_as_cited(tmp_path):
    ran = [Done(RUN, 1, "1 failed"), Done(RUN, 0, "1 passed")]
    check_cited(verdict(by_run()), ran=ran, root=tmp_path, base="", checks={})


# --- each quote is what the branch says --------------------------------------------------------


def test_the_changelog_quoted_as_it_reads_proves_a_criterion_about_the_pull_request(branch):
    """mqucifer/sprint-metrics#763 and #770: what a test can't exercise is proven by reading."""
    root, base = branch
    v = verdict(
        by_read("CHANGELOG.md", "- GET /sprint: a range spanning one sprint answers it."),
        by_read(
            "pyproject.toml",
            'version = "1.3.3"',
            criterion="pyproject.toml's version stays 1.3.3",
        ),
    )
    check_cited(v, ran=[], root=root, base=base, checks={})


def test_a_removed_test_is_quoted_from_what_the_diff_removed(branch):
    root, base = branch
    removed = by_read(
        "tests/test_service.py",
        "def test_sprint_answers_200():",
        criterion="the tests in tests/test_service.py that pinned the 200 are removed",
    )
    check_cited(verdict(removed), ran=[], root=root, base=base, checks={})


def test_a_quote_the_file_doesnt_have_is_refused(branch):
    root, base = branch
    found = refused(verdict(by_read("CHANGELOG.md", "- GET /trend is removed")), root, base=base)
    assert found.rule == "check.acceptance.quoted_as_it_reads"
    assert "isn't in `CHANGELOG.md`" in str(found)


def test_a_quote_from_a_file_that_isnt_there_is_refused(branch):
    root, base = branch
    found = refused(verdict(by_read("docs/service.md", "GET /sprint")), root, base=base)
    assert "there's no file `docs/service.md`" in str(found)


# --- reading proves a pull request's artifacts, never what the code does --------------------


def test_quoting_source_doesnt_prove_what_the_code_does(branch):
    """The operator's guard (crew#895): source or tests quoted in place of running them."""
    root, base = branch
    found = refused(verdict(by_read("src/app.py", "return 410", "GET /sprint answers 410")), root)
    assert found.rule == "check.acceptance.read_proves_the_artifact"
    assert "proven by `run` or `check`, not by quoting it" in str(found)


def test_quoting_a_test_doesnt_prove_it_passes(branch):
    root, base = branch
    found = refused(verdict(by_read("tests/test_service.py", "def test_range():")), root)
    assert found.rule == "check.acceptance.read_proves_the_artifact"


@pytest.mark.parametrize(
    ("path", "criterion", "artifact"),
    [
        ("CHANGELOG.md", "an entry under [Unreleased]", True),
        ("docs/service.md", "the docs say it", True),
        ("README.md", "the readme says it", True),
        ("pyproject.toml", "the version is unchanged", True),
        ("schemas/event.schema.json", "the schema has the field", True),
        ("contracts/v1/range.json", "the contract has the field", True),
        ("tests/test_service.py", "tests/test_service.py drops the 200 tests", True),
        ("src/app.py", "GET /sprint answers 410", False),
        ("tests/test_service.py", "GET /sprint answers 410", False),
        ("tests/fixtures/expected.md", "the report matches", False),
    ],
)
def test_what_reading_can_prove(path, criterion, artifact):
    assert is_artifact(path, criterion) is artifact


# --- a check proves only what its steps run ----------------------------------------------------


def test_a_passing_check_whose_workflow_step_is_quoted_is_taken(branch):
    root, base = branch
    check_cited(verdict(by_check()), ran=[], root=root, base=base, checks={"docker": "success"})


def test_a_check_not_on_the_commit_is_refused(branch):
    root, base = branch
    found = refused(verdict(by_check("image")), root, checks={"docker": "success"})
    assert found.rule == "check.acceptance.proven_by_a_check_that_runs_it"
    assert "`image` isn't a check on this commit. Its checks are `docker`" in str(found)


def test_a_check_that_failed_proves_nothing(branch):
    root, base = branch
    found = refused(verdict(by_check()), root, checks={"docker": "failure"})
    assert "`docker` is failure on this commit" in str(found)


def test_a_check_proven_without_its_workflow_step_is_refused(branch):
    """crew#814: the criteria passed on a `tests` check whose steps don't run them."""
    root, base = branch
    c = by_check(path="CHANGELOG.md", text="## [Unreleased]")
    found = refused(verdict(c), root, checks={"docker": "success"})
    assert "quote the step of `docker`'s workflow" in str(found)


# --- the session ------------------------------------------------------------------------------


def test_qa_runs_the_test_in_the_held_workspace_and_its_verdict_is_held_to_it(branch, seen):  # noqa: F811
    root, base = branch
    held = Fake({RUN: (0, "1 passed in 0.3s")})
    held.tools = CHECKS
    recorded = Recorded(held)
    invented = {
        "summary": "proven",
        "accepted": True,
        "criteria": [
            {**by_run("uv run pytest -q").model_dump(), "criterion": "GET /sprint answers 410"}
        ],
    }
    honest = verdict(
        by_run(),
        by_read("CHANGELOG.md", "- GET /sprint: a range spanning one sprint answers it."),
    ).model_dump()
    proxy = Proxy(
        said(("git", {"command": "git diff base --stat"})),
        said(("run", {"what": "tests", "tests": ["tests/test_service.py::test_sprint_is_gone"]})),
        said(("done", invented)),
        said(("done", honest)),
    )
    found = qa_crew.verify_story(
        "Answer GET /sprint with 410",
        worktree=root,
        repo="sprint-metrics",
        workspace=recorded,
        base=base,
        checks={"tests": "success"},
        send=proxy,
        changed=["CHANGELOG.md", "tests/test_service.py"],
    )
    assert found.accepted and [c.by for c in found.criteria] == ["run", "read"]
    assert [r.command for r in recorded.ran] == [RUN]
    diff = proxy.sent[1]["messages"][-1]["content"]
    assert "CHANGELOG.md" in diff and "tests/test_service.py" in diff
    refusal = proxy.sent[3]["messages"][-1]["content"]
    assert "`uv run pytest -q` isn't a command this session ran" in refusal
    start = proxy.sent[0]["messages"][1]["content"]
    assert start.startswith("## Your step: verify this story against its acceptance criteria")
    assert "- `tests`: success" in start and "- `CHANGELOG.md`" in start
    assert "at the story's branch" in start
