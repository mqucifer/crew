"""QA sees the tests the work touches or names, asks for more, and cites what it read (#231).

A QA call for a docs story reached 78,528 prompt tokens on 2026-09-29: every
test file in sprint-metrics, whole, about 200,000 characters of it unrelated
to the change. That's the band where answers came back empty (#312).
"""

from __future__ import annotations

import pytest

from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict
from crew_org.events import EventSink
from crew_org.flows import acceptance as flow
from crew_org.flows.acceptance import held_to_what_it_read, qa_tests

SUITE = {
    "tests/test_report.py": "def test_api_version():\n    assert True\n",
    "tests/test_metrics.py": "def test_cycle_time():\n    assert True\n",
    "tests/test_scrape.py": (
        "def test_scrape_port():\n    pass\n\n\ndef test_scrape_host():\n    pass\n"
    ),
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for rel, body in SUITE.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    monkeypatch.setattr(flow, "QA_FOCUS_ABOVE_CHARS", 10)  # every suite here is "large"
    monkeypatch.setattr(flow, "changed_paths", lambda w: ["tests/test_metrics.py", "docs/x.md"])
    return tmp_path


def verdict(evidence, proven=True, need=()):
    if need:
        return QAVerdict(summary="asking", accepted=False, need_files=list(need))
    c = CriterionVerdict(criterion="it works as documented", proven=proven, evidence=evidence)
    return QAVerdict(summary="judged", accepted=proven, criteria=[c])


# --- what it's shown -------------------------------------------------------------------------


def test_a_small_suite_is_shown_whole(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    tests = qa_tests(tmp_path)
    assert not tests.focused and "def test_a" in tests.text


def test_a_large_suite_shows_what_the_change_touches_and_the_story_names(repo):
    tests = qa_tests(repo, about="Proven by `tests/test_report.py::test_api_version`.")
    assert tests.focused
    assert set(tests.shown) == {"tests/test_metrics.py", "tests/test_report.py"}
    assert "def test_cycle_time" in tests.text and "def test_api_version" in tests.text
    assert "def test_scrape_port" not in tests.text, "not shown in full"
    assert "`tests/test_scrape.py`: test_scrape_port, test_scrape_host" in tests.text


def test_a_file_asked_for_is_shown(repo):
    tests = qa_tests(repo, extra=["./tests/test_scrape.py"])
    assert "def test_scrape_port" in tests.text


# --- held to what it read --------------------------------------------------------------------


def test_citing_a_test_it_read_stands(repo):
    tests = qa_tests(repo)
    v = verdict("tests/test_metrics.py::test_cycle_time checks the rounding")
    assert held_to_what_it_read(v, tests) == v


def test_citing_a_test_that_doesnt_exist_is_not_proof(repo):
    tests = qa_tests(repo)
    held = held_to_what_it_read(verdict("test_nowhere proves it"), tests)
    assert not held.accepted and "no test file defines" in held.criteria[0].evidence


def test_citing_a_test_it_wasnt_shown_is_not_proof(repo):
    tests = qa_tests(repo)
    held = held_to_what_it_read(verdict("test_scrape_port proves it"), tests)
    assert not held.accepted and "wasn't shown" in held.criteria[0].evidence


def test_a_name_the_criterion_quotes_is_its_data_not_a_citation(repo):
    # crew#768: a failure code that reads like a test name, quoted from the Given.
    tests = qa_tests(repo)
    c = CriterionVerdict(
        criterion='Given top_failure_causes contains {"reason": "test_assertion_failed"}',
        proven=True,
        evidence="tests/test_metrics.py::test_cycle_time builds it with test_assertion_failed",
    )
    v = QAVerdict(summary="judged", accepted=True, criteria=[c])
    assert held_to_what_it_read(v, tests) == v


def test_a_missing_test_is_still_refused_beside_quoted_data(repo):
    tests = qa_tests(repo)
    c = CriterionVerdict(
        criterion='Given {"reason": "test_assertion_failed"}',
        proven=True,
        evidence="test_nowhere proves it, with test_assertion_failed",
    )
    held = held_to_what_it_read(QAVerdict(summary="judged", accepted=True, criteria=[c]), tests)
    assert not held.accepted
    assert "`test_nowhere`" in held.criteria[0].evidence
    assert "`test_assertion_failed`" not in held.criteria[0].evidence


def test_a_file_name_isnt_taken_for_a_test(repo):
    tests = qa_tests(repo)
    v = verdict("tests/test_metrics.py::test_cycle_time, in test_metrics.py")
    assert held_to_what_it_read(v, tests).accepted


# --- asking ----------------------------------------------------------------------------------


def judge(repo, answers):
    calls = []

    def fake(story, *, test_code, can_ask=False, **evidence):
        calls.append((test_code, can_ask))
        return answers[len(calls) - 1]

    flow.verify_story, original = fake, flow.verify_story
    try:
        result = flow._judge(
            "the story", worktree=repo, sink=EventSink(None), number=1, repo="sm", test_output=""
        )
    finally:
        flow.verify_story = original
    return result, calls


def test_it_asks_and_is_asked_again_with_the_file_shown(repo):
    result, calls = judge(
        repo,
        [verdict("", need=["tests/test_scrape.py"]), verdict("test_scrape_port covers it")],
    )
    assert len(calls) == 2 and "def test_scrape_port" in calls[1][0]
    assert result.accepted


def test_a_test_cited_unseen_is_shown_and_judged_again(repo):
    result, calls = judge(
        repo, [verdict("test_scrape_host proves it"), verdict("test_scrape_host proves it")]
    )
    assert len(calls) == 2 and "def test_scrape_host" in calls[1][0]
    assert result.accepted


def test_on_its_last_try_it_isnt_offered_an_ask(repo):
    answers = [verdict("", need=["tests/test_scrape.py"])] * 2 + [
        verdict("test_cycle_time checks the rounding")
    ]
    result, calls = judge(repo, answers)
    assert [ask for _, ask in calls] == [True, True, False]
    assert result.accepted


# --- a large file in part, and the read tool (lean-prompts plan, L4) -------------------------

SERVICE = """import pytest

from app import serve


@pytest.fixture
def client():
    return serve()


def test_sprint_label(client):
    assert client.get("/sprint")


def test_range_one_sprint(client):
    assert client.get("/range")


def test_trend(client):
    assert client.get("/trend")
"""


@pytest.fixture
def large(repo, monkeypatch):
    (repo / "tests" / "test_service.py").write_text(SERVICE)
    monkeypatch.setattr(flow, "WHOLE_FILE_CHARS", 50)
    monkeypatch.setattr(flow, "changed_paths", lambda w: ["tests/test_service.py"])
    # test_range_one_sprint's assert line is what the branch changed.
    line = SERVICE.splitlines().index('    assert client.get("/range")') + 1
    monkeypatch.setattr(flow, "changed_lines", lambda w: {"tests/test_service.py": {line}})
    return repo


def test_a_large_file_is_shown_as_what_the_change_touches_and_its_fixtures(large):
    tests = qa_tests(large)
    assert "tests/test_service.py" not in tests.shown
    assert tests.cut["tests/test_service.py"] == {"test_range_one_sprint", "client"}
    assert "from app import serve" in tests.text and "def client" in tests.text
    assert "def test_range_one_sprint" in tests.text
    assert "def test_trend" not in tests.text
    assert "read by name: test_sprint_label, test_trend" in tests.text


def test_a_test_the_story_names_is_shown_from_a_large_file(large):
    tests = qa_tests(large, about="Proven by test_trend.")
    assert "def test_trend" in tests.text and tests.seen("test_trend")
    assert not tests.seen("test_sprint_label")


def test_a_test_it_read_is_seen(large):
    tests = qa_tests(large)
    tests.read = ["tests/test_service.py::test_sprint_label"]
    v = verdict("test_sprint_label covers it")
    assert held_to_what_it_read(v, tests) == v


def test_qa_is_offered_the_read_tool_when_the_tests_are_cut(large):
    offered = []

    def fake(story, *, test_code, read=None, **evidence):
        offered.append(read)
        return verdict("test_range_one_sprint covers it")

    flow.verify_story, original = fake, flow.verify_story
    try:
        flow._judge("the story", worktree=large, sink=EventSink(None), number=1, repo="sm")
    finally:
        flow.verify_story = original
    assert offered[0] is not None and offered[0].name == "read_file"


def test_a_criterion_unproven_for_a_test_it_wasnt_shown_is_judged_again_with_it(large):
    """crew#645: sm#569's criterion 6 was held unproven for a test QA wasn't shown."""
    unshown = verdict("test_trend is named, but its file wasn't shown in full", proven=False)
    result, calls = judge(large, [unshown, verdict("test_trend checks the trend")])
    assert len(calls) == 2 and "def test_trend" in calls[1][0]
    assert result.accepted
