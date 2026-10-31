"""Each card in delivery-history says how it was filed (crew#974).

Provenance as W3C PROV has it: the agent that filed the card and the activity that
opened it, read from what the crew already writes, and unknown rather than guessed.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import get_args

import httpx
import pytest

from crew_org.packages import contract, gather, mock
from crew_org.packages import delivery_history as dh
from crew_org.tools.github_project import Card, ProjectClient

NOW = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
OPERATOR_SINCE = datetime(2026, 10, 9, 15, 55, 19, tzinfo=UTC)
FILERS = dh.Filers(
    crew=frozenset({"mqucifer-crew"}),
    sponsor="mquarters",
    operator="quarters-claude",
    operator_since=OPERATOR_SINCE,
)
CREW = "mqucifer-crew"
SRC = Path(__file__).resolve().parents[1] / "src" / "crew_org"


def card(
    *,
    author: str | None = CREW,
    created: datetime = NOW,
    parent: int | None = None,
    labels: tuple[str, ...] = (),
    title: str = "A card",
) -> Card:
    return Card(
        item_id="i",
        number=1,
        repo="crew-presentation",
        title=title,
        author=author,
        created=created,
        parent=parent,
        labels=frozenset(labels),
    )


def filed(body: str = "", filers: dh.Filers = FILERS, **kw) -> dh.Filed:
    return dh.how_filed(card(**kw), body, filers)


# --- who filed it ---------------------------------------------------------------------


def test_the_issues_author_says_who_filed_it():
    assert filed(author=CREW).by == "crew"
    assert filed(author="mquarters").by == "sponsor"
    assert filed(author="quarters-claude").by == "operator"


def test_someone_the_crew_doesnt_name_is_someone_else():
    assert filed(author="a-stranger").by == "other"


def test_github_naming_no_author_is_unknown():
    assert filed(author=None).by == "unknown"


def test_the_sponsors_login_before_the_operator_had_its_own_is_unknown():
    """Until crew#566 the operator worked as the Sponsor's login: never guessed."""
    before = OPERATOR_SINCE - timedelta(seconds=1)
    assert filed(author="mquarters", created=before).by == "unknown"
    assert filed(author="mquarters", created=OPERATOR_SINCE).by == "sponsor"


def test_a_card_that_doesnt_say_when_it_was_filed_cant_be_the_sponsors():
    undated = card(author="mquarters").model_copy(update={"created": None})
    assert dh.how_filed(undated, "", FILERS).by == "unknown"


def test_no_filers_named_leaves_every_card_unknown():
    assert filed(filers=dh.Filers()).by == "unknown"


# --- the activity that opened it -----------------------------------------------------

BUG = "<!-- crew:finding key=s223-bug-v0.2.0 -->\n\n**What happens:** it overflows"
RELEASE = "<!-- crew:task role=devops kind=release key=epic-211 -->\n\n**Role:** DevOps"
ESCAPED = (
    "<!-- crew:task role=qa kind=escaped-bug key=crew-presentation-b224 -->\n\n"
    "## The bug as it merged\n\n" + BUG
)


def test_a_finding_is_read_from_its_mark():
    assert filed(BUG) == dh.Filed(by="crew", activity="finding", kind=None, for_project=None)


def test_a_task_says_its_kind():
    found = filed(RELEASE, parent=211)
    assert (found.activity, found.kind) == ("task", "release")


def test_a_role_task_filed_from_a_finding_is_a_finding():
    body = "<!-- crew:task role=architect kind=finding key=s1-architect-abc -->\n\n**Role:** x"
    found = filed(body)
    assert (found.activity, found.kind) == ("finding", "finding")


def test_a_task_quoting_the_bug_it_tests_is_the_task():
    """QA's Task carries the bug's body whole, its finding mark too."""
    found = filed(ESCAPED, labels=("role:qa", "project:crew-presentation"))
    assert (found.activity, found.kind) == ("task", "escaped-bug")


def test_a_mark_quoted_lower_down_says_nothing_about_this_card():
    assert filed(f"As a reader, see the bug:\n\n{BUG}").activity is None


def test_a_task_kind_the_package_doesnt_name_stays_a_task_and_never_leaves():
    found = filed("<!-- crew:task role=qa kind=some-secret key=k -->")
    assert (found.activity, found.kind) == ("task", None)


def test_a_mark_only_counts_on_the_crews_own_card():
    """The repositories are public: anyone can write the mark into an issue."""
    for author in ("a-stranger", "quarters-claude", "mquarters"):
        assert filed(BUG, author=author).activity is None


def test_the_operators_technical_epic_is_the_operators():
    """mqucifer/crew-presentation#211: technical, and no crew activity opened it."""
    found = filed("**The work:** adopt 2.0.0", author="quarters-claude", labels=("technical",))
    assert (found.by, found.activity, found.kind) == ("operator", None, None)


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("<!-- crew:main-red workflow=ci -->\n**The work:** fix", "main-red"),
        ("<!-- crew:release-check version=1.0.0 -->\n**The work:** fix", "release-check"),
        (
            "<!-- crew:adoption key=k -->\n\n**The work:** x\n\n<!-- crew:by Architect -->\n— *A*",
            "adoption",
        ),
        ("**The work:** split it\n\n<!-- crew:by Architect -->\n— *Architect*", "design"),
    ],
)
def test_a_technical_epic_says_its_cause(body, kind):
    found = filed(body, labels=("technical",))
    assert (found.activity, found.kind) == ("technical_epic", kind)


def test_a_technical_epic_with_no_cause_written_says_so():
    found = filed("**The work:** old", labels=("technical",))
    assert (found.activity, found.kind) == ("technical_epic", None)


def test_a_split_is_read_from_its_mark_where_nesting_failed():
    assert filed(f"{dh.SPLIT_MARK}\n\nAs a reader").activity == "split"


def test_an_older_split_is_read_from_its_parent():
    assert filed("As a reader", parent=211).activity == "split"


def test_an_older_crew_card_with_nothing_written_is_unknown_never_a_guess():
    found = filed("As a reader, I want it")
    assert (found.by, found.activity, found.kind) == ("crew", None, None)


@pytest.mark.parametrize(
    ("labels", "activity"),
    [
        (("proposed-goal",), "proposed_goal"),
        (("goal", "skeleton"), "skeleton_goal"),
        (("retro-finding",), "finding"),
        (("diagnosis",), "finding"),
        (("retro",), "retro"),
        (("standup",), "standup"),
    ],
)
def test_a_path_that_marks_by_label_is_read_from_it(labels, activity):
    assert filed("Body", labels=labels).activity == activity


def test_the_design_records_are_read_from_their_titles_and_label():
    from crew_org.flows.design import DESIGN, REVISIT
    from crew_org.flows.onboard import ONBOARDING

    for change, kind in ((ONBOARDING, "onboarding"), (DESIGN, "design"), (REVISIT, "revisit")):
        found = filed(change.issue_body, title=change.issue_title, labels=tuple(change.labels))
        assert (found.activity, found.kind) == ("record_change", kind)
    # A revisit that opened no pull request is recorded under its own title (`_record_look`).
    found = filed("Looked", title="Revisit declined", labels=("design:revisit",))
    assert (found.activity, found.kind) == ("record_change", "revisit")


def test_a_project_a_qa_task_is_for_is_said():
    found = filed(ESCAPED, labels=("role:qa", "project:crew-presentation"))
    assert found.for_project == "crew-presentation"
    assert filed(RELEASE).for_project is None
    # Only the crew's own labels are read.
    assert filed("x", author="a-stranger", labels=("project:other",)).for_project is None


# --- the flows are the source ---------------------------------------------------------


def test_the_marks_and_labels_are_the_flows_own():
    from crew_org.flows import (
        board_flow,
        diagnose,
        main_watch,
        proposed_goal,
        readiness,
        release_check,
        retro,
        role_tasks,
        standup,
    )
    from crew_org.flows.artifacts import BY_MARKER

    assert dh.SPLIT_MARK == board_flow.SPLIT_MARK
    assert dh._BY_MARK == BY_MARKER
    assert dh._FILING_LABELS == {
        proposed_goal.PROPOSED_LABEL: ("proposed_goal", None),
        readiness.SKELETON_LABEL: ("skeleton_goal", None),
        retro.FINDING_LABEL: ("finding", None),
        diagnose.DIAGNOSIS_LABEL: ("finding", None),
        retro.RETRO_LABEL: ("retro", None),
        standup.STANDUP_LABEL: ("standup", None),
        dh.REVISIT_LABEL: ("record_change", "revisit"),
    }
    assert dh.how_filed(card(), main_watch.RED_MARKER.format(workflow="ci"), FILERS).kind == (
        "main-red"
    )
    assert dh.how_filed(
        card(), release_check.RELEASE_MARKER.format(version="1.0.0"), FILERS
    ).kind == ("release-check")
    assert role_tasks.marker(role_tasks.QA, "story-test", "k").startswith("<!-- crew:task ")
    assert role_tasks._FINDING_BUG.startswith("<!-- crew:finding ")


def _open_task_kinds() -> set[str]:
    """Every kind a Task can be opened with, from the calls themselves."""
    from crew_org.flows import announcements, qa_tasks, role_tasks

    names = {
        "ALERT": role_tasks.ALERT,
        "CI_SERVICES": role_tasks.CI_SERVICES,
        "DEPLOY": role_tasks.DEPLOY,
        "DIAGRAM": role_tasks.DIAGRAM,
        "DISPUTE": role_tasks.DISPUTE,
        "FINDING": role_tasks.FINDING,
        "FIRST_DIAGRAM": role_tasks.FIRST_DIAGRAM,
        "FIRST_WORKSPACE": role_tasks.FIRST_WORKSPACE,
        "NEED": role_tasks.NEED,
        "RELEASE": role_tasks.RELEASE,
        "REVIEW": role_tasks.REVIEW,
    }
    found: set[str] = {qa_tasks.STORY_TEST, qa_tasks.ESCAPED, announcements.RULING}
    for path in (SRC / "flows").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in ("open_task", "render", "marker"):
                continue
            for kw in node.keywords:
                if kw.arg != "kind":
                    continue
                value = kw.value
                const = value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", "")
                if const in names:
                    found.add(names[const])
                elif const not in ("kind", "RULING", "STORY_TEST", "ESCAPED"):
                    raise AssertionError(f"{path.name}: a Task kind this test can't read: {const}")
    return found


def test_every_kind_of_task_the_crew_opens_is_one_the_package_names():
    assert _open_task_kinds() <= set(get_args(dh.FiledKind))


# Each place the crew files an issue, and what the package reads for it. A new one fails
# here until the package can say how its cards were filed (crew#974).
FILING_PATHS = {
    ("board_flow.py", "create_epic_cards"),
    ("board_flow.py", "refine_epics"),
    ("diagnose.py", "file_finding"),
    ("main_watch.py", "file_technical_epic"),
    ("proposed_goal.py", "file_in_inbox"),
    ("record_pr.py", "propose"),
    ("retro.py", "_file_recurring"),
    ("retro.py", "record_retro"),
    ("revisit.py", "_record_look"),
    ("revisit.py", "file_technical_epic"),
    ("role_tasks.py", "open_task"),
    ("role_tasks.py", "_file_bug"),
    ("standup.py", "record_standup"),
}


def _issue_creators() -> set[tuple[str, str]]:
    """(file, function) of every `issues.create(...)` in the flows."""
    found = set()
    for path in sorted((SRC / "flows").glob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "issues"
                ):
                    found.add((path.name, fn.name))
    return found


def test_every_place_the_crew_files_an_issue_is_one_the_package_reads():
    creators = _issue_creators()
    assert creators - FILING_PATHS == set(), "a new filing path: give it a mark and name it here"
    assert FILING_PATHS - creators == set(), "a filing path that's gone: take it off"


def test_a_real_role_task_and_bug_read_back_as_filed():
    from crew_org.flows import role_tasks as rt

    body = rt.render(rt.DEVOPS, kind=rt.RELEASE, key="k", why="w", work="x", done_when=["d"])
    assert (filed(body).activity, filed(body).kind) == ("task", "release")


def test_a_real_design_epic_reads_back_with_its_cause():
    from crew_org.events import EventSink
    from crew_org.flows import revisit
    from tests.role_task_fakes import Board, Issues

    issues = Issues()
    revisit.file_technical_epic(
        issues,
        Board(),
        EventSink(None),
        repo="sm",
        title="Split it",
        body="**The work:** split it",
        summary="technical epic",
    )
    created = max(issues.issues.values(), key=lambda i: i["number"])
    found = filed(created["body"], labels=("technical",))
    assert (found.activity, found.kind) == ("technical_epic", "design")


# --- in the package ------------------------------------------------------------------


def _inputs(cards: list[Card], bodies: dict[tuple[str, int], str]) -> dh.Inputs:
    return dh.Inputs(
        events=[],
        cards=cards,
        sprints=[],
        pulls={},
        crew_repository="crew",
        repositories=["crew-presentation"],
        timezone="America/Chicago",
        date=date(2026, 10, 30),
        generated=NOW,
        bodies=bodies,
        filers=FILERS,
    )


def test_each_card_in_the_package_says_how_it_was_filed():
    history, _, _ = dh.build(
        _inputs(
            [card()],
            {("crew-presentation", 1): BUG},
        )
    )
    assert history.cards[0].filed == dh.Filed(
        by="crew", activity="finding", kind=None, for_project=None
    )


def test_the_body_never_leaves_only_the_name_read_from_it():
    history, _, _ = dh.build(_inputs([card()], {("crew-presentation", 1): BUG}))
    assert "overflows" not in history.model_dump_json()


def test_a_card_with_no_body_read_says_only_who_filed_it():
    history, _, _ = dh.build(_inputs([card()], {}))
    assert history.cards[0].filed.by == "crew"
    assert history.cards[0].filed.activity is None


# 2.0.0's fields on a card, as a reader of 2.0.0 reads them.
CARD_AT_2_0_0 = {
    "repo",
    "number",
    "type",
    "title",
    "url",
    "parent",
    "points",
    "sprint",
    "status",
    "status_set",
    "created",
    "closed",
    "technical",
    "closed_as",
    "superseded_because",
}


def test_a_reader_of_2_0_0_still_finds_everything_it_read():
    schema = json.loads((contract.CONTRACTS / "schema.json").read_text())
    defined = schema["$defs"]["Card"]
    assert set(defined["properties"]) >= CARD_AT_2_0_0
    assert set(defined["required"]) >= CARD_AT_2_0_0
    index = json.loads(mock.files()["delivery-history.json"])
    assert index["schema_version"].split(".")[0] == "2"
    for found in index["cards"]:
        assert set(found) >= CARD_AT_2_0_0


def test_the_version_is_a_minor_after_2_0_0():
    assert dh.SCHEMA_VERSION == "2.1.0"
    assert "2.0.0" in contract.read_versions()


def test_the_mock_shows_every_way_a_consumer_meets_a_card_filed():
    index = json.loads(mock.files()["delivery-history.json"])
    filings = [c["filed"] for c in index["cards"]]
    assert {f["by"] for f in filings} >= {"crew", "sponsor", "operator", "unknown"}
    assert {f["activity"] for f in filings} >= {"finding", "task", "technical_epic", "split", None}
    assert any(f["for_project"] for f in filings)


# --- gathering ------------------------------------------------------------------------


def test_a_gathered_package_reads_the_boards_bodies_and_the_orgs_filers(monkeypatch, tmp_path):
    class Board:
        def cards(self):
            return [card()]

        def bodies(self):
            return {("crew-presentation", 1): BUG}

        def sprints(self):
            return []

    class Issues:
        def all_pulls(self, repo):
            return []

    monkeypatch.setattr(gather, "sprint_today", lambda org: date(2026, 10, 30))
    org = {
        "sprint": {"timezone": "America/Chicago"},
        "trust": {"sponsor": "mquarters", "crew": ["mqucifer-crew[bot]"]},
    }
    history, _, _ = gather.history_now(
        Board(), Issues(), org=org, crew_repo="crew", events_dir=tmp_path
    )
    assert history.cards[0].filed.activity == "finding"


def test_filers_come_from_org_yaml_as_graphql_names_them():
    """GraphQL says `mqucifer-crew`, where REST and `trust` say `mqucifer-crew[bot]`."""
    filers = gather.filers(
        {
            "trust": {"sponsor": "s", "crew": ["c[bot]"]},
            "filers": {"operator": "o", "operator_since": "2026-10-09T15:55:19Z"},
        }
    )
    assert filers == dh.Filers(
        crew=frozenset({"c"}), sponsor="s", operator="o", operator_since=OPERATOR_SINCE
    )


def test_the_real_org_names_the_crew_sponsor_and_operator():
    from crew_org.config import load_org

    filers = gather.filers(load_org())
    assert filers.sponsor == "mquarters"
    assert "mqucifer-crew" in filers.crew
    assert filers.operator == "quarters-claude"
    assert filers.operator_since == OPERATOR_SINCE


def test_no_filers_section_leaves_them_unnamed():
    assert gather.filers({}) == dh.Filers()


def test_the_board_gives_each_issues_body_across_pages():
    pages = [
        {
            "data": {
                "organization": {
                    "projectV2": {
                        "items": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            "nodes": [
                                {
                                    "content": {
                                        "number": 1,
                                        "body": "one",
                                        "repository": {"name": "sm"},
                                    }
                                },
                                {"content": None},
                            ],
                        }
                    }
                }
            }
        },
        {
            "data": {
                "organization": {
                    "projectV2": {
                        "items": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "content": {
                                        "number": 1,
                                        "body": None,
                                        "repository": {"name": "qa-suite"},
                                    }
                                }
                            ],
                        }
                    }
                }
            }
        },
    ]
    queue = list(pages)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=queue.pop(0))

    board = ProjectClient(
        "tok", "mqucifer", 1, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert board.bodies() == {("sm", 1): "one", ("qa-suite", 1): ""}
