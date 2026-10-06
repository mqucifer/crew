"""No bare issue numbers in anything the crew posts (crew#456).

GitHub links a bare `#123` to that number in whichever repository the text lands in. The
crew posts across repositories, and models copy numbers from their context.
"""

from __future__ import annotations

import json

import httpx

from crew_org.tools.github_issues import IssueClient, Trust
from crew_org.tools.references import explicit

OWNER, REPO = "mqucifer", "sprint-metrics"


def made(text, existing=frozenset({174, 184, 186, 406})):
    return explicit(text, owner=OWNER, repo=REPO, exists=lambda n: n in existing)


def link(n):
    return f"[{OWNER}/{REPO}#{n}](https://github.com/{OWNER}/{REPO}/issues/{n})"


# --- what is rewritten -------------------------------------------------------------------------


def test_a_bare_number_becomes_an_explicit_link_to_the_repository_it_is_posted_to():
    text, unresolved = made("Proposed by the Product Owner from #174.")
    assert text == f"Proposed by the Product Owner from {link(174)}."
    assert unresolved == []


def test_gh_style_numbers_are_made_explicit_too():
    assert made("See GH-186 for the image.")[0] == f"See {link(186)} for the image."


def test_every_number_in_a_text_is_made_explicit():
    text, _ = made("#184 and #186 share a format; (#406) too.")
    assert text == f"{link(184)} and {link(186)} share a format; ({link(406)}) too."


def test_a_number_that_is_no_issue_there_is_put_in_code_and_reported():
    text, unresolved = made("Follows #9999 and #174.")
    assert text == f"Follows `#9999` and {link(174)}."
    assert unresolved == [9999]


# --- what is left alone ------------------------------------------------------------------------


def test_explicit_references_and_other_repositories_are_left_as_written():
    for kept in (
        "mqucifer/crew#280",
        "crew#280",
        "PR#5",
        "[the service Goal](https://github.com/mqucifer/sprint-metrics/issues/174)",
        "[#174](https://github.com/mqucifer/sprint-metrics/issues/174)",
        "https://github.com/mqucifer/crew/issues/280#issuecomment-1",
        "<https://example.com/#12>",
    ):
        assert made(kept) == (kept, []), kept


def test_code_comments_and_markers_are_never_rewritten():
    for kept in (
        "`#174`",
        "```\nassert issue == #174\n```",
        '<!-- crew:panel-data {"problem": "see #184"} -->',
        "<!-- crew:by Product Owner -->",
    ):
        assert made(kept) == (kept, []), kept


def test_headings_entities_and_row_ids_are_not_numbers():
    for kept in ("## 1. Environments", "&#123;", "Follows the epic's R1, R4.", "| Q2 |", "C#7"):
        assert made(kept) == (kept, []), kept


def test_text_around_protected_parts_is_still_made_explicit():
    text, _ = made("Before `#184` after #186, and <!-- #406 --> then #174")
    assert text == f"Before `#184` after {link(186)}, and <!-- #406 --> then {link(174)}"


# --- the client applies it to every post ---------------------------------------------------------


TRUST = Trust(sponsor="mquarters", crew=frozenset(), tools=frozenset())


def client(missing=frozenset({9999}), fail=frozenset()):
    sent: list[tuple[str, str, dict]] = []
    looked_up: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            number = int(path.rsplit("/", 1)[1])
            looked_up.append(number)
            if number in fail:
                return httpx.Response(500, json={"message": "boom"})
            return httpx.Response(404 if number in missing else 200, json={"number": number})
        sent.append((request.method, path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={})

    issues = IssueClient(
        "t", OWNER, client=httpx.Client(transport=httpx.MockTransport(answer)), trust=TRUST
    )
    return issues, sent, looked_up


def test_a_comment_is_posted_with_explicit_references():
    issues, sent, _ = client()
    issues.comment(REPO, 406, "Split from #174.")
    [(method, path, body)] = sent
    assert method == "POST" and path.endswith("/issues/406/comments")
    assert body["body"] == f"Split from {link(174)}."


def test_an_issue_a_pull_request_a_review_and_an_edit_are_all_made_explicit():
    issues, sent, _ = client()
    issues.create(REPO, "A story", "Builds on #184.")
    issues.edit_issue(REPO, 406, body="Conclusion for #406.")
    issues.create_pull(REPO, title="t", head="h", base="main", body="Closes #406")
    issues.create_review(REPO, 7, event="COMMENT", body="Like #186.")
    bodies = [b["body"] for _, _, b in sent]
    assert bodies == [
        f"Builds on {link(184)}.",
        f"Conclusion for {link(406)}.",
        f"Closes {link(406)}",
        f"Like {link(186)}.",
    ]


def test_each_number_is_looked_up_once():
    issues, _, looked_up = client()
    issues.comment(REPO, 1, "#174 and #174 again")
    issues.comment(REPO, 2, "#174 once more")
    assert looked_up == [174]


def test_a_missing_number_is_recorded_and_not_linked():
    issues, sent, _ = client()
    issues.comment(REPO, 1, "See #9999.")
    assert sent[0][2]["body"] == "See `#9999`."
    assert issues.unresolved == {(REPO, 9999)}


def test_a_lookup_that_fails_links_it_as_github_would_have():
    issues, sent, _ = client(fail=frozenset({174}))
    issues.comment(REPO, 1, "From #174.")
    assert sent[0][2]["body"] == f"From {link(174)}."


def test_a_write_without_a_body_is_left_alone_and_looks_nothing_up():
    issues, sent, looked_up = client()
    issues.add_labels(REPO, 406, ["needs:human"])
    assert sent[0][2] == {"labels": ["needs:human"]} and looked_up == []


# --- what the crew reads back still reads -------------------------------------------------------


def test_a_builds_on_line_made_explicit_still_names_its_stories():
    from crew_org.flows.board_flow import BUILDS_ON, WAITS_FOR, builds_on

    line, _ = made(f"As a user.\n\n{BUILDS_ON} #184, #186\n\n**Estimate** — 3 points")
    assert link(184) in line and link(186) in line
    assert builds_on(line) == {184, 186}
    marker = WAITS_FOR.format(numbers="184,186")
    assert made(marker) == (marker, [])
