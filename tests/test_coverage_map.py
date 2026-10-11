"""The coverage map: which merged tests execute or read which code (crew#583, ADR 0024).

The report is written here in coverage.py's own JSON form, with contexts shown, so
these run without a sandbox or pytest-cov. The real run, on sprint-metrics in the
sandbox, is in the pull request.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from crew_org.tools import coverage_map as cm
from crew_org.tools.workspace import CommandResult

SCHEMA = """\
METRIC_KEYS = ["throughput", "cycle_time_days"]

SINGLE_SPRINT_SCHEMA = {"required": METRIC_KEYS}


def required():
    return list(SINGLE_SPRINT_SCHEMA["required"])


class Report:
    def keys(self):
        return METRIC_KEYS
"""

GEN = """\
from pkg.schema import SINGLE_SPRINT_SCHEMA


def generate():
    return dict(SINGLE_SPRINT_SCHEMA)
"""

TESTS = """\
from pkg.schema import METRIC_KEYS, required


def test_required():
    assert required() == METRIC_KEYS


def test_generate():
    from pkg.gen import generate

    assert generate()
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/__init__.py").write_text("")
    (tmp_path / "src/pkg/schema.py").write_text(SCHEMA)
    (tmp_path / "src/pkg/gen.py").write_text(GEN)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_schema.py").write_text(TESTS)
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)
    return tmp_path


REQUIRED = "tests/test_schema.py::test_required"
GENERATE = "tests/test_schema.py::test_generate"
REPORT = {
    "files": {
        "src/pkg/schema.py": {
            "contexts": {
                # Import time: no test is running.
                "1": [""],
                "3": [""],
                "6": [""],
                "7": [f"{REQUIRED}|run"],
                "12": [f"{GENERATE}[a]|run", f"{GENERATE}[b]|run"],
            }
        },
        "src/pkg/gen.py": {"contexts": {"4": [""], "5": [f"{GENERATE}|run"]}},
    }
}


def test_a_context_names_its_test_without_phase_or_parameters():
    assert cm.test_id("tests/test_x.py::test_y[a-1]|run") == "tests/test_x.py::test_y"
    assert cm.test_id("tests/test_x.py::TestA::test_y|setup") == "tests/test_x.py::TestA::test_y"
    assert cm.test_id("") is None


def test_each_covered_line_goes_under_the_innermost_definition(repo):
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert "src/pkg/schema.py::required" in found.executed_by(REQUIRED)
    assert "src/pkg/schema.py::Report.keys" in found.executed_by(GENERATE)


def test_lines_run_at_import_belong_to_no_test(repo):
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert set(found.tests) == {REQUIRED, GENERATE}


def test_a_definition_counts_the_constants_it_reads_in_its_own_file(repo):
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert "src/pkg/schema.py::SINGLE_SPRINT_SCHEMA" in found.executed_by(REQUIRED)
    assert "src/pkg/schema.py::METRIC_KEYS" in found.executed_by(GENERATE)


def test_a_constant_read_through_an_import_counts_where_it_is_defined(repo):
    """sprint-metrics' test_schema_gen reads its schema table through the generator."""
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert "src/pkg/schema.py::SINGLE_SPRINT_SCHEMA" in found.executed_by(GENERATE)


def test_what_a_test_reads_in_its_own_body_counts(repo):
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert "src/pkg/schema.py::METRIC_KEYS" in found.executed_by(REQUIRED)


def test_the_tests_covering_a_file_a_definition_or_a_constant(repo):
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert found.covering(files=["src/pkg/gen.py"]) == [GENERATE]
    assert found.covering(names=["src/pkg/schema.py::required"]) == [REQUIRED]
    assert found.covering(names=["src/pkg/schema.py::SINGLE_SPRINT_SCHEMA"]) == [
        GENERATE,
        REQUIRED,
    ]
    assert found.covering(names=["src/pkg/schema.py::Report"]) == [GENERATE]
    assert found.covering(names=["src/pkg/schema.py::Rep"]) == []


def test_the_map_survives_its_store(repo):
    found = cm.from_report("pkg", "abc", repo, REPORT)
    assert cm.CoverageMap.from_json(found.to_json()) == found


class Runs:
    """The sandbox, standing in: records each command and writes the report."""

    def __init__(self, *, report=REPORT, fail=None):
        self.commands = []
        self.report = report
        self.fail = fail

    def __call__(self, worktree, command, *, sandbox, network, timeout=0):
        self.commands.append((command, network))
        if self.fail and self.fail in command:
            return CommandResult(" ".join(command), 1, "no network")
        if command == cm.MEASURE and self.report is not None:
            (worktree / cm.WORK / "coverage.json").write_text(json.dumps(self.report))
        return CommandResult(" ".join(command), 0, "")


def test_the_tests_run_offline_after_a_setup_with_the_network(repo, tmp_path_factory):
    runs = Runs()
    store = tmp_path_factory.mktemp("store")
    cm.build(repo, "pkg", store=store, run=runs)
    assert [network for _, network in runs.commands] == [True, True, False]
    assert runs.commands[-1][0] == cm.MEASURE


def test_a_map_is_stored_per_commit_and_not_measured_twice(repo, tmp_path_factory):
    store = tmp_path_factory.mktemp("store")
    first = cm.build(repo, "pkg", store=store, run=Runs())
    again = Runs()
    assert cm.build(repo, "pkg", store=store, run=again) == first
    assert again.commands == []
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert (store / "pkg" / f"{head}.json").exists()


def test_nothing_is_left_in_the_clone(repo, tmp_path_factory):
    cm.build(repo, "pkg", store=tmp_path_factory.mktemp("store"), run=Runs())
    assert not (repo / cm.WORK).exists()


def test_a_setup_that_fails_says_why(repo, tmp_path_factory):
    with pytest.raises(cm.CoverageUnavailable, match="no network"):
        cm.build(repo, "pkg", store=tmp_path_factory.mktemp("store"), run=Runs(fail="pip"))
    assert not (repo / cm.WORK).exists()


def test_no_report_says_why(repo, tmp_path_factory):
    with pytest.raises(cm.CoverageUnavailable, match="no coverage report"):
        cm.build(repo, "pkg", store=tmp_path_factory.mktemp("store"), run=Runs(report=None))


def test_a_project_with_its_own_image_has_no_map_yet(repo, tmp_path_factory):
    class Own:
        image = "node@sha256:abc"
        problem = None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cm.workspace, "toolchain", lambda _: Own())
        with pytest.raises(cm.CoverageUnavailable, match="Python only"):
            cm.build(repo, "pkg", store=tmp_path_factory.mktemp("store"), run=Runs())


def test_a_constant_built_from_another_reads_it_too(repo):
    """`SINGLE_SPRINT_SCHEMA` is built from `METRIC_KEYS`: a schema test pins the keys (C2)."""
    only_generate = {"files": {"src/pkg/gen.py": {"contexts": {"5": [f"{GENERATE}|run"]}}}}
    found = cm.from_report("pkg", "abc", repo, only_generate)
    assert "src/pkg/schema.py::SINGLE_SPRINT_SCHEMA" in found.executed_by(GENERATE)
    assert "src/pkg/schema.py::METRIC_KEYS" in found.executed_by(GENERATE)


SERVICE_TESTS = """\
import pytest

from pkg.gen import generate
from pkg.schema import METRIC_KEYS


@pytest.mark.skipif(True, reason="needs a database")
def test_the_service_returns_the_schema():
    assert generate() == {"required": ["throughput", "cycle_time_days"]}


def test_two_keys():
    assert len(METRIC_KEYS) == 2
"""
SKIPPED = "tests/test_service.py::test_the_service_returns_the_schema"
COUNTED = "tests/test_service.py::test_two_keys"


def test_a_test_the_sandbox_skips_still_names_what_it_calls(repo):
    """sprint-metrics' service tests need Postgres and never ran for the map (crew#612)."""
    (repo / "tests/test_service.py").write_text(SERVICE_TESTS)
    found = cm.with_static(cm.from_report("pkg", "abc", repo, REPORT), repo)
    assert "src/pkg/gen.py::generate" in found.executed_by(SKIPPED)
    assert SKIPPED in found.covering(files=["src/pkg/gen.py"])


def test_a_test_that_only_counts_a_constant_is_in_the_map(repo):
    """`len(ALL_METRICS) == 12` runs no line under the test; it reads the table (crew#612)."""
    (repo / "tests/test_service.py").write_text(SERVICE_TESTS)
    found = cm.with_static(cm.from_report("pkg", "abc", repo, REPORT), repo)
    assert found.covering(names=["src/pkg/schema.py::METRIC_KEYS"]).count(COUNTED) == 1


def test_the_static_half_adds_to_what_a_test_ran(repo):
    found = cm.with_static(cm.from_report("pkg", "abc", repo, REPORT), repo)
    assert "src/pkg/schema.py::required" in found.executed_by(REQUIRED)
    assert "src/pkg/gen.py::generate" in found.executed_by(GENERATE)


def test_a_stored_map_gets_the_static_half_without_a_new_run(repo, tmp_path_factory):
    store = tmp_path_factory.mktemp("store")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    path = cm.stored("pkg", head, store=store)
    path.parent.mkdir(parents=True)
    path.write_text(cm.from_report("pkg", head, repo, REPORT).to_json())
    (repo / "tests/test_service.py").write_text(SERVICE_TESTS)

    def never(*_a, **_k):
        raise AssertionError("measured again")

    found = cm.build(repo, "pkg", store=store, run=never)
    assert SKIPPED in found.tests
