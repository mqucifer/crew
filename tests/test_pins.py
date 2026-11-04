"""A pin pull request whose checks fail goes back to the crew as upgrade work (crew#1028).

The fixtures are the real cases: mqucifer/crew-presentation#303 failed `qa-suite / qa` on
a test hard-coding nine board columns, and mqucifer/crew-presentation#294 failed its own
`checks` on a contract test. Each waited for a person to file its work by hand.
"""

from __future__ import annotations

from crew_org.columns import BLOCKED, DONE, MERGING, READY
from crew_org.flows import pins, qa_tasks
from crew_org.flows.ci import CI_MARKER
from crew_org.flows.merge import ready_to_land
from crew_org.flows.role_tasks import marked
from crew_org.tools.github_project import Card
from crew_org.work_types import PIN_LABEL, SUITE, TASK
from tests.role_task_fakes import Board, Issues, events, sink

CONSUMER = "crew-presentation"
TAG = "delivery-history-2026-11-04"
BRANCH = f"chore/10-{TAG}"
HEAD = "4f1c" + "0" * 36
PULL = 303
QA_TEST = "crew-presentation/test_replay_board.py::test_replay_board_has_nine_columns_in_order"
OWN_TEST = "tests/test_contracts.py::test_build_check_exits_zero"

# The end of mqucifer/crew-presentation#303's `qa-suite / qa` job, as GitHub logged it.
QA_LOG = (
    "2026-11-04T12:31:40.0000000Z ##[group]Run uv run pytest crew-presentation\n"
    f"2026-11-04T12:31:56.2670293Z {QA_TEST} FAILED [ 66%]\n"
    f"2026-11-04T12:32:03.5762082Z FAILED {QA_TEST} - AssertionError: the package's last "
    "sprint ('Sprint 41') produces 10 board columns, but the test expects 9\n"
    "2026-11-04T12:32:03.5765017Z ======================== 1 failed, 23 passed in 10.46s\n"
    "2026-11-04T12:32:03.6408390Z ##[error]AssertionError: the package's last sprint "
    "('Sprint 41') produces 10 board columns\n"
)
# And of mqucifer/crew-presentation#294's `checks` job, from `run_checks.py`.
OWN_LOG = (
    "2026-11-02T07:00:20.8203332Z ##[group]python -m pytest tests/test_contracts.py -v\n"
    f"2026-11-02T07:01:10.0309594Z {OWN_TEST} FAILED [ 16%]\n"
    f"2026-11-02T07:01:54.4086113Z FAILED {OWN_TEST} - AssertionError: attempt legend "
    "section not found\n"
    "2026-11-02T07:01:54.4086994Z ======= 1 failed, 11 passed in 93.31s (0:01:33) =======\n"
    "2026-11-02T07:01:54.4797000Z ##[endgroup]\n"
    "2026-11-02T07:01:54.4797716Z failed (exit 1): python -m pytest tests/test_contracts.py -v\n"
    "2026-11-02T07:03:54.9358508Z ##[error]Process completed with exit code 1.\n"
)
PASSED = {"status": "completed", "conclusion": "success"}
JOB = "https://github.com/mqucifer/crew-presentation/actions/runs/1/job/{job}"


class World(Issues):
    """GitHub across the consumer and qa-suite: their numbers are kept apart by the tests."""

    def __init__(self, *failing: str) -> None:
        super().__init__()
        self.files["crew-presentation/README.md"] = "**The surface:** the built site."
        self.files["delivery-history.tag"] = "delivery-history-2026-11-02\n"
        self.pulls = {
            PULL: {"number": PULL, "state": "open", "head": {"ref": BRANCH, "sha": HEAD}},
            # Not a pin: the crew's follow leaves it alone.
            40: {"number": 40, "state": "open", "head": {"ref": "feat/40-x", "sha": "e" * 40}},
        }
        self.runs = {
            HEAD: [
                {"name": "checks", **PASSED, "details_url": JOB.format(job=1)},
                {"name": "qa-suite / suite", **PASSED, "details_url": JOB.format(job=2)},
                {"name": "qa-suite / qa", **PASSED, "details_url": JOB.format(job=3)},
            ]
        }
        self.logs = {1: OWN_LOG, 3: QA_LOG}
        for name in failing:
            for run in self.runs[HEAD]:
                if run["name"] == name:
                    run["conclusion"] = "failure"

    def open_pulls(self, repo: str) -> list[dict]:
        return [p for p in self.pulls.values() if p["state"] == "open"] if repo == CONSUMER else []

    def pull(self, repo: str, number: int) -> dict:
        return self.pulls[number]

    def check_runs(self, repo: str, sha: str) -> list[dict]:
        return list(self.runs.get(sha, []))

    def job_log(self, repo: str, job: int) -> str:
        return self.logs[job]

    def labels_of(self, number: int) -> set[str]:
        return {lab["name"] for lab in self.issues[number]["labels"]}

    def with_label(self, label: str) -> list[int]:
        return [n for n in self.issues if label in self.labels_of(n)]


class Ws:
    def __init__(self) -> None:
        self.reran: list[str] = []

    def for_repo(self, repo):
        return self

    def open(self, branch, resume=False):
        self.reran.append(branch)
        return "path"

    def commit(self, message, allow_empty=False):
        assert allow_empty
        return True

    def push(self):
        pass

    def close(self, path):
        pass


def _follow(issues, board, s, cards=(), ws=None, stop=3):
    return pins.follow(
        issues, board, s, ws or Ws(), cards=list(cards), repos={CONSUMER, SUITE}, stop=stop
    )


def _pin_card(number: int = 901, status: str = MERGING, *labels: str) -> Card:
    return Card(
        item_id=f"item-N{number}",
        number=number,
        title=f"Upgrade to {TAG}",
        repo=CONSUMER,
        state="OPEN",
        status=status,
        work_type=TASK,
        labels=frozenset({PIN_LABEL, *labels}),
    )


def _qa_card(number: int, status: str) -> Card:
    return Card(
        item_id=f"item-N{number}",
        number=number,
        title="QA",
        repo=SUITE,
        state="OPEN",
        status=status,
        work_type=TASK,
        labels=frozenset({"role:qa", f"project:{CONSUMER}"}),
    )


def _returned(tmp_path, *failing: str):
    """The pin returned on its failing head: its card waits on its upgrade work."""
    issues, board, s = World(*failing), Board(), sink(tmp_path)
    _follow(issues, board, s)
    return issues, board, s


# --- the card -------------------------------------------------------------------------


def test_an_open_pin_pull_request_gets_its_card_once(tmp_path):
    issues, board, s = World(), Board(), sink(tmp_path)
    result = _follow(issues, board, s)
    assert result.opened == [(CONSUMER, 901)]
    card = issues.issues[901]
    assert card["title"] == f"Upgrade to {TAG}"
    assert issues.labels_of(901) == {PIN_LABEL}
    assert pins.pull_of(card) == PULL and f"mqucifer/{CONSUMER}#{PULL}" in card["body"]
    assert ("select", "item-N901", "Work Type", TASK) in board.done
    assert ("status", "item-N901", MERGING) in board.done
    again = _follow(issues, Board(), s, cards=[_pin_card()])
    assert again.opened == [] and len(issues.issues) == 1


def test_a_green_pin_waits_for_the_sponsor_and_the_crew_never_lands_it(tmp_path):
    issues, board, s = World(), Board(), sink(tmp_path)
    _follow(issues, board, s)
    board = Board()
    result = _follow(issues, board, s, cards=[_pin_card()])
    assert result.sponsor == [(CONSUMER, PULL)] and not board.done
    assert not issues.comments(CONSUMER, PULL)
    assert ready_to_land([_pin_card()]) == []


def test_a_pin_whose_checks_are_still_running_waits(tmp_path):
    issues, board, s = World(), Board(), sink(tmp_path)
    issues.runs[HEAD][2] = {"name": "qa-suite / qa", "status": "in_progress", "conclusion": None}
    result = _follow(issues, board, s, cards=[_pin_card()])
    assert result.waiting and result.sponsor == [] and not board.done


# --- a failed check, routed -----------------------------------------------------------


def test_a_pin_failing_on_the_consumers_own_test_goes_to_the_developer_with_the_log(tmp_path):
    (
        issues,
        board,
        s,
    ) = World("checks"), Board(), sink(tmp_path)
    result = _follow(issues, board, s)
    assert result.returned == [(CONSUMER, PULL)]
    said = issues.comments(CONSUMER, PULL)[-1]["body"]
    assert said.startswith(CI_MARKER.format(head=HEAD)) and OWN_TEST in said
    (bug,) = issues.with_label("bug")
    body = issues.issues[bug]["body"]
    assert OWN_TEST in body and "attempt legend section not found" in body
    assert f"mqucifer/{CONSUMER}#{PULL}" in body and TAG in body
    assert ("status", f"item-N{bug}", "Needs Refinement") in board.done
    assert issues.with_label("role:qa") == []
    assert ("status", "item-N901", BLOCKED) in board.done
    assert ("label", 901, pins.WAITS_LABEL) in issues.done
    assert (
        pins.WAITS.format(head=HEAD, on=f"{CONSUMER}#{bug}")
        in (issues.comments(CONSUMER, 901)[-1]["body"])
    )


def test_a_pin_failing_on_a_qa_suite_test_opens_qa_s_task_in_qa_suite(tmp_path):
    issues, board, s = World("qa-suite / qa"), Board(), sink(tmp_path)
    result = _follow(issues, board, s)
    assert result.returned == [(CONSUMER, PULL)]
    (task,) = issues.with_label("role:qa")
    body = issues.issues[task]["body"]
    found = marked(body)
    assert found.kind == qa_tasks.DISPUTE
    assert issues.labels_of(task) == {"role:qa", f"project:{CONSUMER}"}
    assert QA_TEST in body and "10 board columns" in body and TAG in body
    # Shaped as a dispute: qa-suite runs QA's fix against the pin's head, and lands it
    # straight after the pin (crew#945).
    assert qa_tasks.DISPUTED.format(repo=CONSUMER, story=901, pull=PULL, head=HEAD) in body
    assert qa_tasks.reference_ref(body) == HEAD
    assert ("status", f"item-N{task}", READY) in board.done
    assert issues.with_label("bug") == []
    assert ("status", "item-N901", BLOCKED) in board.done
    assert (
        pins.WAITS.format(head=HEAD, on=f"{SUITE}#{task}")
        in (issues.comments(CONSUMER, 901)[-1]["body"])
    )
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert opened and opened[0]["detail"]["rule"] == "decision.pins.upgrade_work"


def test_the_same_failure_returned_stop_times_blocks_the_pin_for_a_person(tmp_path):
    issues, board, s = World("qa-suite / qa"), Board(), sink(tmp_path)
    issues.comments_on[PULL] = [
        {"body": f"{CI_MARKER.format(head=f'old{n}')}\n```\nFAILED {QA_TEST} - no\n```"}
        for n in range(2)
    ]
    _follow(issues, board, s, cards=[_pin_card()], stop=3)
    assert ("status", "item-N901", BLOCKED) in board.done
    assert ("label", 901, "blocked", "needs:human") in issues.done
    assert issues.with_label("role:qa") == [] and issues.with_label("bug") == []


# --- its upgrade work, settled --------------------------------------------------------


def test_the_pin_waits_while_its_upgrade_work_is_open(tmp_path):
    issues, _, s = _returned(tmp_path, "qa-suite / qa")
    (task,) = issues.with_label("role:qa")
    board, ws = Board(), Ws()
    waiting = _pin_card(901, BLOCKED, pins.WAITS_LABEL)
    result = _follow(issues, board, s, cards=[waiting, _qa_card(task, READY)], ws=ws)
    assert result.waiting and ws.reran == [] and not board.done


def test_qa_s_approved_fix_runs_the_pins_checks_again(tmp_path):
    issues, _, s = _returned(tmp_path, "qa-suite / qa")
    (task,) = issues.with_label("role:qa")
    board, ws = Board(), Ws()
    waiting = _pin_card(901, BLOCKED, pins.WAITS_LABEL)
    result = _follow(issues, board, s, cards=[waiting, _qa_card(task, MERGING)], ws=ws)
    assert result.rerun == [(CONSUMER, PULL)] and ws.reran == [BRANCH]
    assert ("status", "item-N901", MERGING) in board.done
    assert ("unlabel", 901, pins.WAITS_LABEL) in issues.done
    assert f"mqucifer/{SUITE}#{task}" in issues.comments(CONSUMER, PULL)[-1]["body"]


def test_qa_saying_the_test_stands_sends_the_site_to_the_developer(tmp_path):
    issues, _, s = _returned(tmp_path, "qa-suite / qa")
    (task,) = issues.with_label("role:qa")
    issues.issues[task]["state"] = "closed"
    issues.comments_on[task] = [
        {"body": f"{qa_tasks.JUSTIFIED_MARKER}\nA sprint's board has nine columns."}
    ]
    board, ws = Board(), Ws()
    waiting = _pin_card(901, BLOCKED, pins.WAITS_LABEL)
    result = _follow(issues, board, s, cards=[waiting], ws=ws)
    assert ws.reran == [] and result.waiting
    (bug,) = issues.with_label("bug")
    body = issues.issues[bug]["body"]
    assert "A sprint's board has nine columns." in body and QA_TEST in body
    assert (
        pins.WAITS.format(head=HEAD, on=f"{CONSUMER}#{bug}")
        in (issues.comments(CONSUMER, 901)[-1]["body"])
    )


def test_the_developers_fix_merged_runs_the_pins_checks_again(tmp_path):
    issues, _, s = _returned(tmp_path, "checks")
    (bug,) = issues.with_label("bug")
    issues.issues[bug].update(state="closed", state_reason="completed")
    board, ws = Board(), Ws()
    waiting = _pin_card(901, BLOCKED, pins.WAITS_LABEL)
    result = _follow(issues, board, s, cards=[waiting], ws=ws)
    assert result.rerun == [(CONSUMER, PULL)] and ws.reran == [BRANCH]
    assert ("status", "item-N901", MERGING) in board.done


def test_upgrade_work_closed_without_a_fix_blocks_the_pin_for_a_person(tmp_path):
    issues, _, s = _returned(tmp_path, "checks")
    (bug,) = issues.with_label("bug")
    issues.issues[bug].update(state="closed", state_reason="not_planned")
    board, ws = Board(), Ws()
    waiting = _pin_card(901, BLOCKED, pins.WAITS_LABEL)
    _follow(issues, board, s, cards=[waiting], ws=ws)
    assert ws.reran == []
    assert ("label", 901, "blocked", "needs:human") in issues.done
    assert ("unlabel", 901, pins.WAITS_LABEL) in issues.done
    said = issues.comments(CONSUMER, 901)[-1]["body"]
    assert f"mqucifer/{CONSUMER}#{bug}" in said and "without a fix" in said


def test_a_waiting_pin_isnt_a_persons_to_unblock():
    from crew_org.flows.standup import waiting_on_a_person

    waiting = _pin_card(901, BLOCKED, pins.WAITS_LABEL)
    stuck = _pin_card(902, BLOCKED, "needs:human")
    assert waiting_on_a_person([waiting, stuck]) == ["#902"]


# --- merged ---------------------------------------------------------------------------


def test_a_merged_pin_closes_its_card(tmp_path):
    issues, board, s = World(), Board(), sink(tmp_path)
    _follow(issues, board, s)
    issues.pulls[PULL].update(state="closed", merged=True)
    board = Board()
    result = _follow(issues, board, s, cards=[_pin_card()])
    assert result.closed == [(CONSUMER, 901)]
    assert issues.issues[901]["state"] == "closed"
    assert ("status", "item-N901", DONE) in board.done
