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
