"""A review that contradicts the story's own criteria goes to the Product Owner (#252).

On sprint-metrics#145 the Code Reviewer asked three times for the stdout
assertion to go, as the epic's design note said, and QA returned it twice
because the story's criterion required it. The reviewer had been shown the
note and never the criteria. It is now shown both, it names a clash as a
conflict, and a conflict sends the story back to refinement on the first
review, not the sixth delivery.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from crew_org.flows.review import render_review, review_open_pulls, story_criteria
from crew_org.tools.github_issues import IssueClient, Trust
from crew_org.tools.github_project import Card
from tests.test_review import BOT, REJECTION, FakeIssues, crew_pull

CRITERION = 'the exit code is 2, stderr contains "2024-01", and stdout is empty'
BODY = (
    "Serve the prior sprint.\n\n## Acceptance criteria\n\n"
    f"1. **Given** only 2024-02 **When** --prior-sprint 2024-01 **Then** {CRITERION}\n\n"
    "**Estimate** — 3 points\n\n---\n\nSplit from #59."
)

CONFLICT = ReviewVerdict(
    summary="The test asserts stdout is empty; the design note says not to.",
    approve=False,
    findings=[
        Finding(
            file="tests/test_prior_sprint.py",
            concern="asserts captured.out == '' against the design note",
            action="drop the stdout assertion so the later change is a pure addition",
            criterion=CRITERION,
            doing_it="breaks_it",
        )
    ],
)

# mqucifer/crew-presentation#276's review, word for word: a real bug, and a fix that
# protects the criterion it quotes. Filed as a conflict, it closed the pull request and
# queued a re-split that would have closed mqucifer/crew-presentation#271.
AC4 = (
    "Given a package that has analysis.sprints with a decided list containing entries with "
    'at, text, kind, and number\nWhen the sprint overview is rendered\nThen the "Decided by '
    'the crew" panel shows the entries from analysis with their sentence text, time, kind, '
    "and number as before (existing 2.x behaviour preserved)"
)
REVIEW_276 = ReviewVerdict(
    summary="The template change breaks AC4: analysis entries carry no `repo`.",
    approve=False,
    findings=[
        Finding(
            file="site/templates/sprint_overview.html",
            concern=(
                "The template references `d.repo` in the link for every decided entry, but AC4 "
                "specifies that analysis entries carry only `at, text, kind, and number`. When "
                "rendering analysis entries, `d.repo` is undefined, so the href becomes "
                "`https://github.com//issues/122`. This breaks AC4's 'as before (existing 2.x "
                "behaviour preserved)' requirement."
            ),
            action=(
                "Wrap the link in a conditional on `d.repo`: render the `<a>` link only when "
                "`d.repo` is present; otherwise fall back to the original `<code>` format."
            ),
            criterion=AC4,
            doing_it="keeps_it",
        ),
        Finding(
            file="tests/test_browser.py",
            concern="AC4 has no test. The diff adds tests for AC1, AC2, and AC3 but none for AC4.",
            action="Add a test that renders analysis `decided` entries with no `repo`.",
        ),
    ],
)


def story(status: str = "Reviewing") -> Card:
    return Card(
        item_id="S6",
        number=6,
        title="Show metric 6",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        parent=59,
    )


class Issues(FakeIssues):
    def __init__(self, pulls):
        super().__init__(pulls)
        self.posted, self.closed, self.labels = [], [], set()
        self.children, self.branch_names, self.edited_by = [6], [], {}
        self.said = {}

    owner = "mqucifer"

    def get(self, repo, number):
        return {"body": BODY}

    def comments(self, repo, number):
        return self.said.get(number, [])

    def sub_issues(self, repo, number):
        return [{"number": n} for n in self.children]

    def branches(self, repo):
        return [{"name": name} for name in self.branch_names]

    def people_who_edited(self, repo, number):
        return self.edited_by.get(number, set())

    def ensure_label(self, repo, name, *, color, description):
        self.labels.add(name)

    def add_labels(self, repo, number, labels):
        pass

    def comment(self, repo, number, body):
        self.posted.append((number, body))
        return {}

    def close_pull(self, repo, number):
        self.closed.append(number)


class Board:
    def __init__(self, cards):
        self._cards = cards
        self.moves, self.cleared = [], []

    def cards(self):
        return self._cards

    def repo_of(self, item_id):
        return None

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass

    def clear_field(self, item_id, name):
        self.cleared.append((item_id, name))


def sibling(number=7, status="Ready", state="OPEN") -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"Show metric {number}",
        status=status,
        state=state,
        work_type="Story",
        repo="sprint-metrics",
        parent=59,
    )


def review(verdict, monkeypatch, seen=None, others=(), arrange=None, sink=None):
    def judge(*args, **kwargs):
        if seen is not None:
            seen.update(kwargs)
        return verdict

    monkeypatch.setattr(review_flow, "review_diff", judge)
    card = story()
    cards = [card, *others]
    issues, board = Issues([crew_pull()]), Board(cards)
    issues.children = [c.number for c in cards]
    if arrange is not None:
        arrange(issues)
    result = review_open_pulls(
        issues,
        sink or EventSink(None),
        repo="sprint-metrics",
        bot_login=BOT,
        board=board,
        cards=cards,
    )
    return result, issues, board


def test_the_reviewer_is_shown_the_storys_criteria(monkeypatch):
    seen: dict = {}
    review(REJECTION, monkeypatch, seen)
    assert CRITERION in seen["acceptance_criteria"]
    assert "Estimate" not in seen["acceptance_criteria"]


def test_a_story_without_criteria_gives_the_reviewer_none():
    class NoBody(Issues):
        def get(self, repo, number):
            return {"body": "No criteria here."}

    assert story_criteria(NoBody([]), story(), "sprint-metrics") == ""
    assert story_criteria(Issues([]), None, "sprint-metrics") == ""


def test_a_conflict_sends_the_story_back_on_the_first_review(monkeypatch):
    result, issues, board = review(CONFLICT, monkeypatch)

    assert result.returned == [(6, 59)]
    assert ("S6", "Ready") in board.moves and ("S6", "In Progress") not in board.moves
    assert ("S6", "Sprint") in board.cleared
    assert NEEDS_REWORK in issues.labels
    (evidence,) = [body for number, body in issues.posted if number == 59]
    assert STORY_PROBLEM_MARKER in evidence and CRITERION in evidence
    assert issues.closed == [14], "no repair round on a pull request that can't settle it"


def test_an_ordinary_rejection_still_goes_back_to_the_author(monkeypatch):
    result, issues, board = review(REJECTION, monkeypatch)
    assert result.returned == []
    assert board.moves == [("S6", "In Progress")] and issues.closed == []


def test_the_author_reads_which_criterion_the_finding_conflicts_with():
    assert f"Conflicts with the story's criterion: {CRITERION}" in render_review(CONFLICT)


def test_a_finding_that_protects_a_criterion_goes_back_to_its_author(monkeypatch):
    """mqucifer/crew-presentation#276: a one-line fix closed the pull request and set
    the epic to be split again, with mqucifer/crew-presentation#271 waiting under it."""
    assert REVIEW_276.findings[0].criterion == AC4 and REVIEW_276.conflicts == []
    result, issues, board = review(REVIEW_276, monkeypatch, others=[sibling()])

    assert result.returned == []
    assert board.moves == [("S6", "In Progress")]
    assert issues.closed == [], "its pull request stays open"
    assert NEEDS_REWORK not in issues.labels
    assert [body for number, body in issues.posted if number == 59] == []


def test_the_author_reads_which_criterion_the_fix_keeps():
    said = render_review(REVIEW_276)
    assert f"Keeps the story's criterion: {AC4}" in said
    assert "Conflicts with" not in said


@pytest.mark.parametrize(
    "fields",
    [{"criterion": CRITERION}, {"doing_it": "breaks_it"}, {"doing_it": "keeps_it"}],
)
def test_a_finding_says_both_which_criterion_and_what_doing_it_does_to_it(fields):
    with pytest.raises(ValidationError, match="what doing it does"):
        Finding(file="a.py", concern="c", action="consider renaming the helper", **fields)


def test_the_return_states_what_the_finding_says(monkeypatch):
    """mqucifer/crew-presentation#266's comment said the Reviewer asked for what the
    criterion "rules out", the reverse of what it had found."""
    _, issues, _ = review(CONFLICT, monkeypatch)
    (evidence,) = [body for number, body in issues.posted if number == 59]
    finding = CONFLICT.findings[0]
    assert finding.concern in evidence and finding.action in evidence
    assert f"The criterion it says this would break: {CRITERION}" in evidence
    closing = "".join(body for number, body in issues.posted if number == 14)
    assert "The Code Reviewer says" in evidence and "The Code Reviewer says" in closing
    assert "rule out" not in evidence + closing and "rules out" not in evidence + closing


def test_a_conflict_with_only_unstarted_unchanged_stories_later_still_reaches_the_product_owner(
    monkeypatch,
):
    """ADR 0037's re-split stands: nobody has touched what it would replace."""
    result, issues, board = review(CONFLICT, monkeypatch, others=[sibling()])
    assert result.returned == [(6, 59)]
    assert NEEDS_REWORK in issues.labels and issues.closed == [14]


def test_a_conflict_past_merged_siblings_still_reaches_the_product_owner(monkeypatch):
    """A closed story isn't replaced by a re-split, so it's not at risk."""
    merged = sibling(5, status="Done", state="CLOSED")
    result, issues, _ = review(CONFLICT, monkeypatch, others=[merged])
    assert result.returned == [(6, 59)] and NEEDS_REWORK in issues.labels


def assert_held(result, issues, board, names):
    assert result.returned == []
    assert board.moves == [("S6", "Blocked")], "nothing else moves"
    assert board.cleared == [], "nothing leaves its sprint"
    assert issues.closed == [], "its pull request stays open"
    assert NEEDS_REWORK not in issues.labels
    assert [body for number, body in issues.posted if number == 59] == []
    (said,) = [body for number, body in issues.posted if number == 6]
    assert STORY_PROBLEM_MARKER not in said
    assert names in said and CONFLICT.findings[0].concern in said
    assert "`In Progress`" in said and "`needs:rework`" in said, "says how out of it"


def test_a_conflict_is_held_for_a_person_when_a_later_story_has_a_persons_edit(monkeypatch):
    """mqucifer/crew-presentation#271 sat in Ready, unstarted, with criteria edited at the
    Sponsor's word: the edit is all that records it."""

    def edited(issues):
        issues.edited_by = {7: {"quarters-claude"}}

    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    result, issues, board = review(
        CONFLICT, monkeypatch, others=[sibling()], arrange=edited, sink=sink
    )
    assert_held(result, issues, board, "#7")
    (note,) = [e for e in seen if e.detail.get("rule") == "story.return_held" and e.kind == "note"]
    assert note.card == 6 and note.detail["at_risk"] == [7]
    (blocked,) = [e for e in seen if e.kind == "card.blocked"]
    assert blocked.detail["rule"] == "story.return_held"


def test_a_conflict_is_held_when_the_sponsor_has_spoken_on_a_later_story(monkeypatch):
    def spoke(issues):
        issues.said = {7: [{"body": "These criteria are right."}]}

    result, issues, board = review(CONFLICT, monkeypatch, others=[sibling()], arrange=spoke)
    assert_held(result, issues, board, "#7")


def test_a_conflict_is_held_when_a_later_story_has_started(monkeypatch):
    def branched(issues):
        issues.branch_names = ["feat/7-show-metric-7"]

    started = sibling(status="In Progress")
    result, issues, board = review(CONFLICT, monkeypatch, others=[started], arrange=branched)
    assert_held(result, issues, board, "#7")


def test_a_conflict_is_held_when_a_person_edited_the_story_itself(monkeypatch):
    def edited(issues):
        issues.edited_by = {6: {"mquarters"}}

    result, issues, board = review(CONFLICT, monkeypatch, arrange=edited)
    assert_held(result, issues, board, "#6")


def test_a_started_story_the_re_split_keeps_does_not_hold_it(monkeypatch):
    """ADR 0037: an earlier story isn't replaced, started or not."""

    def branched(issues):
        issues.branch_names = ["feat/5-show-metric-5"]

    earlier = sibling(5, status="In Progress")
    result, issues, _ = review(CONFLICT, monkeypatch, others=[earlier], arrange=branched)
    assert result.returned == [(6, 59)] and NEEDS_REWORK in issues.labels


def test_a_conflict_is_held_when_who_touched_the_stories_cannot_be_read(monkeypatch):
    def unreadable(issues):
        def fails(repo, number):
            raise OSError("502")

        issues.people_who_edited = fails

    result, issues, board = review(CONFLICT, monkeypatch, others=[sibling()], arrange=unreadable)
    assert_held(result, issues, board, "couldn't read")


def test_a_conflict_is_held_when_the_epics_stories_cannot_be_read(monkeypatch):
    def unreadable(issues):
        def fails(repo, number):
            raise OSError("502")

        issues.sub_issues = fails

    result, issues, board = review(CONFLICT, monkeypatch, arrange=unreadable)
    assert_held(result, issues, board, "couldn't read")


def test_a_conflict_that_does_not_block_is_only_a_note():
    verdict = ReviewVerdict(
        summary="Fine.",
        approve=True,
        findings=[
            Finding(
                file="a.py",
                concern="c",
                action="consider renaming the helper",
                blocking=False,
                criterion=CRITERION,
                doing_it="breaks_it",
            )
        ],
    )
    assert verdict.conflicts == []


def test_an_approval_still_cannot_carry_a_blocking_conflict():
    with pytest.raises(ValidationError):
        ReviewVerdict(summary="s", approve=True, findings=CONFLICT.findings)


def test_a_review_is_recorded_under_the_card_it_closes_with_its_pull_request(monkeypatch):
    """Keyed by the pull request's number, a review had to be matched to its card
    through the delivery before it (crew#449, discussion 552)."""
    from crew_org.events import EventKind

    monkeypatch.setattr(review_flow, "review_diff", lambda *a, **k: REJECTION)
    card = story()
    issues, board = Issues([crew_pull()]), Board([card])
    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    review_open_pulls(issues, sink, repo="sprint-metrics", bot_login=BOT, board=board, cards=[card])
    work = [e for e in seen if e.kind in (EventKind.AGENT_STARTED, EventKind.AGENT_FINISHED)]
    assert work and all(e.card == card.number for e in work)
    assert all(e.detail["pr"] == crew_pull()["number"] for e in work)
    assert len({e.ctx["run"] for e in work}) == 1
    [finished] = [e for e in work if e.kind == EventKind.AGENT_FINISHED]
    findings = finished.detail["findings"]
    assert findings and all({"file", "statement", "blocking"} == set(f) for f in findings)
    assert findings[0]["statement"] == " ".join(REJECTION.findings[0].concern.split())[:300]


def edits_client(*editors):
    answer = {
        "data": {
            "repository": {
                "issue": {
                    "userContentEdits": {
                        "nodes": [
                            {"editor": {"login": login} if login else None} for login in editors
                        ]
                    }
                }
            }
        }
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=answer))
    return IssueClient(
        "token",
        "mqucifer",
        client=httpx.Client(transport=transport),
        trust=Trust(
            sponsor="mquarters",
            crew=frozenset({"mqucifer-crew[bot]"}),
            tools=frozenset({"dependabot[bot]"}),
        ),
    )


def test_an_edit_outside_the_crew_is_read_and_the_crews_own_is_not():
    """GitHub's GraphQL spells an App's login without `[bot]`: `mqucifer/crew-presentation#271`
    came back as `mqucifer-crew` and `quarters-claude`."""
    edits = edits_client("quarters-claude", "quarters-claude", "mqucifer-crew")
    assert edits.people_who_edited("crew-presentation", 271) == {"quarters-claude"}
    assert edits_client("mqucifer-crew").people_who_edited("crew-presentation", 271) == set()
    assert edits_client().people_who_edited("crew-presentation", 271) == set()


def test_an_edit_by_a_deleted_account_still_counts():
    assert edits_client(None).people_who_edited("crew-presentation", 271) == {"ghost"}
