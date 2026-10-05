"""The Sponsor's decisions for a Goal, collected by rule (crew#440).

A tiny GitHub stands behind a real `IssueClient`, so the trust check
(crew#399) runs too: a stranger's comment on the Goal never gets in.
"""

from __future__ import annotations

import httpx

from crew_org.flows.decisions import (
    collect_decisions,
    decision_sections,
    names_goal,
    render,
)
from crew_org.tools.github_issues import IssueClient, Trust

TRUST = Trust(sponsor="mquarters", crew=frozenset({"mqucifer-crew[bot]"}), tools=frozenset())


def issue(number, *, by="mquarters", title="", body="", comments=(), subs=()):
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": body,
        "user": {"login": by},
        "html_url": f"https://github.com/mqucifer/r/issues/{number}",
        "comments": [
            {
                "user": {"login": login},
                "body": text,
                "created_at": f"2026-09-2{i}T10:00:00Z",
                "html_url": f"https://github.com/mqucifer/r/issues/{number}#c{i}",
            }
            for i, (login, text) in enumerate(comments)
        ],
        "subs": list(subs),
    }


def github(**repos):
    """`repos` maps a repository to its issues, by number."""
    searched: list[tuple[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/issues":
            q = request.url.params["q"]
            repo = q.split("repo:mqucifer/")[1].split()[0]
            searched.append((repo, q.split()[0]))
            return httpx.Response(200, json={"items": [{"number": n} for n in repos[repo]]})
        _, _, _, repo, _, n, *rest = path.split("/")
        found = repos[repo][int(n)]
        if rest == ["comments"]:
            return httpx.Response(200, json=found["comments"])
        if rest == ["sub_issues"]:
            return httpx.Response(200, json=[{"number": s} for s in found["subs"]])
        return httpx.Response(200, json=found)

    client = IssueClient(
        "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(answer)), trust=TRUST
    )
    client.searched = searched  # type: ignore[attr-defined]
    return client


def collect(issues, repos=("crew", "sprint-metrics")):
    return collect_decisions(issues, goal_repo="sprint-metrics", goal=174, repos=repos).decisions


def by_source(found):
    return {(d.rule, d.source) for d in found}


# --- rule 1: the Goal's own cards ---------------------------------------------------------


def test_the_sponsors_comments_on_the_goal_and_its_sub_issues_are_decisions():
    issues = github(
        **{
            "sprint-metrics": {
                174: issue(174, subs=[184], comments=[("mquarters", "Storage is Postgres.")]),
                184: issue(184, comments=[("mquarters", "Counted where it merges.")]),
            },
            "crew": {},
        }
    )
    found = collect(issues)
    assert [(d.rule, d.source, d.text) for d in found] == [
        (1, "mqucifer/sprint-metrics#174", "Storage is Postgres."),
        (1, "mqucifer/sprint-metrics#184", "Counted where it merges."),
    ]


def test_a_strangers_comment_on_the_goal_is_not_a_decision():
    issues = github(
        **{
            "sprint-metrics": {
                174: issue(174, comments=[("stranger", "Use SQLite."), ("mquarters", "Postgres.")])
            },
            "crew": {},
        }
    )
    assert [d.text for d in collect(issues)] == ["Postgres."]


def test_the_goals_decision_log_is_a_decision_but_other_sections_are_not():
    body = "## Why\n\nBecause.\n\n## Sponsor decisions\n\n| D1 | Postgres |\n\n## Scope\n\nMore."
    issues = github(**{"sprint-metrics": {174: issue(174, body=body)}, "crew": {}})
    [only] = collect(issues)
    assert only.kind == "body section"
    assert "D1" in only.text and "Because" not in only.text and "More" not in only.text


def test_a_section_ends_at_the_next_heading_as_high():
    body = "### Sponsor decision\n\nA\n\n#### Detail\n\nB\n\n### Next\n\nC"
    assert decision_sections(body) == ["### Sponsor decision\n\nA\n\n#### Detail\n\nB"]


# --- rule 2: the Sponsor's issues that name the Goal --------------------------------------


def test_an_issue_the_sponsor_opened_naming_the_goal_gives_its_sponsor_sections_and_comments():
    body = (
        "Serves sprint-metrics#174's API.\n\n"
        "## Who uses it (Sponsor, 2026-09-29)\n\nThe presentation site.\n\n"
        "## Acceptance criteria\n\n1. Given a thing."
    )
    issues = github(
        **{
            "sprint-metrics": {174: issue(174)},
            "crew": {
                280: issue(
                    280,
                    title="The crew runs the service",
                    body=body,
                    comments=[("mquarters", "Shared Postgres."), ("stranger", "No.")],
                )
            },
        }
    )
    found = collect(issues)
    assert by_source(found) == {(2, "mqucifer/crew#280")}
    assert [d.kind for d in found][0] == "body section"
    assert "The presentation site." in found[0].text
    assert "Given a thing" not in found[0].text
    assert [d.text for d in found][1:] == ["Shared Postgres."]
    assert all(d.title == "The crew runs the service" for d in found)


def test_a_defect_report_citing_the_goal_as_evidence_gives_nothing():
    # The Sponsor's login opens the crew's defect reports too (crew#440).
    issues = github(
        **{
            "sprint-metrics": {174: issue(174)},
            "crew": {
                429: issue(
                    429,
                    body="From Sprint 12. Goal sprint-metrics#174 says the service keeps its "
                    "history; #184 keeps it in memory.\n\n## Acceptance criteria\n\n1. Given.",
                )
            },
        }
    )
    assert collect(issues) == []


def test_a_comment_linking_an_issue_to_the_goal_makes_it_part_of_it():
    issues = github(
        **{
            "sprint-metrics": {174: issue(174)},
            "crew": {
                280: issue(
                    280,
                    body="Storage for the history service.",
                    comments=[("mquarters", "The service Goal is sprint-metrics#174")],
                )
            },
        }
    )
    assert by_source(collect(issues)) == {(2, "mqucifer/crew#280")}


def test_an_issue_that_is_not_the_sponsors_is_not_rule_2_even_if_it_names_the_goal():
    issues = github(
        **{
            "sprint-metrics": {174: issue(174)},
            "crew": {9: issue(9, by="stranger", body="About sprint-metrics#174.")},
        }
    )
    assert collect(issues) == []


# --- rule 3: a comment elsewhere ----------------------------------------------------------


def test_a_sponsor_comment_naming_the_goal_on_someone_elses_issue_is_a_decision():
    issues = github(
        **{
            "sprint-metrics": {174: issue(174)},
            "crew": {
                9: issue(
                    9,
                    by="mqucifer-crew[bot]",
                    body="Unrelated.",
                    comments=[
                        ("mquarters", "Not about it."),
                        ("mquarters", "For Goal #174, version by SemVer."),
                    ],
                )
            },
        }
    )
    [only] = collect(issues)
    assert (only.rule, only.source, only.text) == (
        3,
        "mqucifer/crew#9",
        "For Goal #174, version by SemVer.",
    )


def test_an_issue_that_only_has_the_number_in_it_is_left_out():
    # Search matches the word "174"; the rule is what decides.
    issues = github(
        **{
            "sprint-metrics": {174: issue(174)},
            "crew": {5: issue(5, body="Sprint 174 of something else.")},
        }
    )
    assert collect(issues) == []


# --- the search itself --------------------------------------------------------------------


def test_the_goals_own_cards_are_not_collected_twice():
    issues = github(
        **{
            "sprint-metrics": {174: issue(174, comments=[("mquarters", "Once.")])},
            "crew": {},
        }
    )
    assert [d.text for d in collect(issues)] == ["Once."]


def test_each_repository_is_searched_once_for_the_goal_number():
    issues = github(**{"sprint-metrics": {174: issue(174)}, "crew": {}})
    collect(issues, repos=["crew", "sprint-metrics", "crew"])
    assert issues.searched == [("crew", "174"), ("sprint-metrics", "174")]  # type: ignore[attr-defined]


def test_how_the_sponsor_names_a_goal():
    pattern = names_goal("sprint-metrics", 174)
    for text in (
        "sprint-metrics#174",
        "mqucifer/sprint-metrics#174",
        "Goal #174",
        "https://github.com/mqucifer/sprint-metrics/issues/174",
    ):
        assert pattern.search(text), text
    assert not pattern.search("sprint-metrics#1740")
    assert not pattern.search("#174")


# --- what a role is shown -----------------------------------------------------------------


def test_the_rendered_decisions_name_their_sources_in_full():
    issues = github(
        **{
            "sprint-metrics": {174: issue(174, comments=[("mquarters", "Postgres.")])},
            "crew": {},
        }
    )
    text = render(
        collect_decisions(issues, goal_repo="sprint-metrics", goal=174, repos=["crew"]),
        goal="mqucifer/sprint-metrics#174",
    )
    assert "## mqucifer/sprint-metrics#174 (comment" in text
    assert "Postgres." in text


def test_search_follows_pages_and_stops_at_githubs_cap():
    pages = []

    def answer(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        assert "is:issue" in request.url.params["q"]
        return httpx.Response(
            200, json={"items": [{"number": page * 1000 + i} for i in range(100)]}
        )

    issues = IssueClient(
        "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(answer)), trust=TRUST
    )
    assert len(issues.search("crew", "174")) == 1000
    assert pages == list(range(1, 11))


def test_a_repository_the_token_cannot_search_is_named_not_skipped_quietly():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/issues":
            return httpx.Response(422, json={"message": "Validation Failed"})
        found = issue(174, comments=[("mquarters", "Postgres.")])
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=found["comments"])
        if request.url.path.endswith("/sub_issues"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=found)

    issues = IssueClient(
        "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(answer)), trust=TRUST
    )
    got = collect_decisions(issues, goal_repo="sprint-metrics", goal=174, repos=["infra", "crew"])
    assert [d.text for d in got.decisions] == ["Postgres."]
    assert got.unsearched == ["infra", "crew"]
    assert "Not searched, no access: infra, crew" in render(got, goal="mqucifer/sprint-metrics#174")
