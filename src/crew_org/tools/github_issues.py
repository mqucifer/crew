"""Issues and comments — the crew's audit trail.

Every state transition writes a comment saying what was decided, why, and on
what evidence. The comment history *is* the sprint artifact record; there is no
separate report. That makes this module the Sponsor's only window into what the
crew actually did, so what it writes has to read well to a human who was not
present.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

import httpx

from crew_org.tokens import BearerAuth, Token
from crew_org.tools.github_http import WRITES, GitHubTransport
from crew_org.tools.references import explicit

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
TIMEOUT = 30.0


class IssueError(RuntimeError):
    pass


class BranchUpdateConflict(IssueError):
    """GitHub could not bring a pull request's branch up to date: they conflict."""


_REVIEW_DECISION = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { reviewDecision }
  }
}
"""


# Where a pull request stands with its base branch's merge queue, in one read.
# The last of these timeline items says whether the queue removed it since its
# code last changed: a new commit (a rebuild, or `main` merged in) comes after.
_QUEUE_STATE = """
query($owner: String!, $repo: String!, $number: Int!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    mergeQueue(branch: $branch) { id }
    pullRequest(number: $number) {
      merged
      mergeQueueEntry { state }
      timelineItems(last: 1, itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT,
          REMOVED_FROM_MERGE_QUEUE_EVENT, PULL_REQUEST_COMMIT, HEAD_REF_FORCE_PUSHED_EVENT]) {
        nodes { __typename ... on RemovedFromMergeQueueEvent { reason } }
      }
    }
  }
}
"""

_ENQUEUE = """
mutation($id: ID!, $head: GitObjectID) {
  enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head}) {
    mergeQueueEntry { id }
  }
}
"""


@dataclass(frozen=True)
class QueueState:
    """A pull request and its base branch's merge queue."""

    # The base branch has a merge queue: pull requests join it, not merge.
    has_queue: bool = False
    queued: bool = False
    # Why the queue removed it, when nothing has changed in it since. None when
    # it was never removed, or has new commits since it was.
    removed: str | None = None
    # Merged, as GraphQL says it. REST's `merged` lags the queue by a second or
    # so, and a pull request read in that second looked approved and unqueued:
    # sprint-metrics#224 was queued again one second after it merged.
    merged: bool = False


# A check that ran and did not pass. `neutral` and `skipped` are not failures.
FAILED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}
)


@dataclass(frozen=True)
class Trust:
    """Whose comments the crew reads, from `org.yaml`'s `trust` (crew#399).

    The repositories are public: anyone can comment, and a comment is read into
    prompts as the Sponsor's direction, or as one of the crew's own verdicts.
    """

    sponsor: str
    crew: frozenset[str]
    tools: frozenset[str]

    @property
    def readable(self) -> frozenset[str]:
        return frozenset({self.sponsor}) | self.crew | self.tools


def load_trust() -> Trust:
    """The allow-list. Missing is a failure, never "trust everyone" (§19, rule 6)."""
    from crew_org.config import load_org  # noqa: PLC0415

    section = load_org().get("trust")
    if not section or not section.get("sponsor"):
        raise ValueError("org.yaml has no `trust` section naming the Sponsor (crew#399)")
    return Trust(
        sponsor=section["sponsor"],
        crew=frozenset(section.get("crew") or ()),
        tools=frozenset(section.get("tools") or ()),
    )


def author(comment: dict[str, Any]) -> str:
    return (comment.get("user") or {}).get("login") or ""


def from_sponsor(issues: Any, comment: dict[str, Any]) -> bool:
    """Is this comment the Sponsor's direction? Only the Sponsor's login counts (crew#399).

    A stand-in client in tests carries no `trust`, and its comments have no
    author: those are taken as written.
    """
    trust = getattr(issues, "trust", None)
    return trust is None or author(comment) == trust.sponsor


class IssueClient:
    def __init__(
        self,
        token: Token,
        owner: str,
        *,
        client: httpx.Client | None = None,
        trust: Trust | None = None,
    ) -> None:
        self.owner = owner
        self.trust = trust or load_trust()
        # (repo, number, login) of each comment left unread, for the event log.
        self.ignored: set[tuple[str, int, str]] = set()
        # (repo, number) of each bare reference left unlinked because no such issue or
        # pull request exists there (crew#456).
        self.unresolved: set[tuple[str, int]] = set()
        self._exists: dict[tuple[str, int], bool] = {}
        self._client = client or httpx.Client(
            timeout=TIMEOUT,
            # Paced writes and throttles waited out (#293).
            transport=GitHubTransport(),
            # Asked for at every request, so a long run outlives its first token (#182).
            auth=BearerAuth(token),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _request(self, method: str, path: str, **json: Any) -> dict[str, Any]:
        # Every body the crew posts, made explicit first: no bare issue numbers (crew#456).
        posted = re.match(rf"/repos/{re.escape(self.owner)}/([^/]+)/", path)
        if method in WRITES and posted and isinstance(json.get("body"), str):
            repo = posted.group(1)
            json["body"], unresolved = explicit(
                json["body"],
                owner=self.owner,
                repo=repo,
                exists=functools.partial(self.exists, repo),
            )
            self.unresolved.update((repo, n) for n in unresolved)
        response = self._client.request(method, f"{API}{path}", json=json or None)
        if response.status_code >= 400:
            detail = response.json().get("message", response.text[:120])
            raise IssueError(f"{method} {path} -> {response.status_code}: {detail}")
        return response.json() if response.content else {}

    def exists(self, repo: str, number: int) -> bool:
        """Is `number` an issue or pull request in `repo`? Asked once per number."""
        key = (repo, number)
        if key not in self._exists:
            response = self._client.get(f"{API}/repos/{self.owner}/{repo}/issues/{number}")
            # Only a definite "no such issue" leaves it unlinked: on any other answer
            # it is linked, which is what GitHub would have done with the bare number.
            self._exists[key] = response.status_code not in (404, 410)
        return self._exists[key]

    def create(
        self,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.owner}/{repo}/issues",
            title=title,
            body=body,
            labels=labels or [],
        )

    def labelled(self, repo: str, label: str) -> list[dict[str, Any]]:
        """Every issue carrying `label`, open or closed. Pull requests left out."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._client.get(
                f"{API}/repos/{self.owner}/{repo}/issues",
                params={"labels": label, "state": "all", "per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            out += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                return out
            page += 1

    def closed_since(self, repo: str, since: str) -> list[dict[str, Any]]:
        """Issues closed at or after `since` (ISO 8601), done or not. Pull requests left out.

        Each carries GitHub's `state_reason`: "completed", or "not_planned".
        """
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._client.get(
                f"{API}/repos/{self.owner}/{repo}/issues",
                params={"state": "closed", "since": since, "per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            out += [
                i for i in batch if "pull_request" not in i and (i.get("closed_at") or "") >= since
            ]
            if len(batch) < 100:
                return out
            page += 1

    def open_issues(self, repo: str) -> list[dict[str, Any]]:
        """Every open issue, newest first. Pull requests left out."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._client.get(
                f"{API}/repos/{self.owner}/{repo}/issues",
                params={"state": "open", "per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            out += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                return out
            page += 1

    def ensure_label(self, repo: str, name: str, *, color: str, description: str) -> None:
        """Create a label if the repository does not have it. An existing one is left as is."""
        response = self._client.post(
            f"{API}/repos/{self.owner}/{repo}/labels",
            json={"name": name, "color": color, "description": description},
        )
        # 422 is "already exists", which is the common case and fine.
        if response.status_code not in (201, 422):
            response.raise_for_status()

    def get(self, repo: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.owner}/{repo}/issues/{number}")

    def comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/repos/{self.owner}/{repo}/issues/{number}/comments", body=body
        )

    def comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/issues/{number}/comments?per_page=100"
        )
        response.raise_for_status()
        # Only the accounts `org.yaml` trusts (crew#399). One check here covers
        # every reader: verdict markers are plain text anyone can type.
        kept = []
        for comment in response.json():
            login = author(comment)
            if login in self.trust.readable:
                kept.append(comment)
            else:
                self.ignored.add((repo, number, login))
        return kept

    def add_sub_issue(self, repo: str, parent_number: int, child_id: int) -> None:
        """Nest one issue under another so the board shows the hierarchy.

        Takes the child's database id, not its number.
        """
        self._request(
            "POST",
            f"/repos/{self.owner}/{repo}/issues/{parent_number}/sub_issues",
            sub_issue_id=child_id,
        )

    def repository(self, repo: str) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.owner}/{repo}")

    def branches(self, repo: str) -> list[dict[str, Any]]:
        """The repository's branches. Empty for a repository with no commits yet."""
        response = self._client.get(f"{API}/repos/{self.owner}/{repo}/branches?per_page=100")
        # An empty repository answers 404 on some paths and [] on others.
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json()

    def branch_protection(self, repo: str, branch: str) -> dict[str, Any] | None:
        """What protects `branch`, as far as a non-admin can see. None if unprotected.

        The protection endpoint itself needs admin, which the crew deliberately
        lacks; the branch's own summary is readable with read access.
        """
        found = self._request("GET", f"/repos/{self.owner}/{repo}/branches/{branch}")
        return found.get("protection") if found.get("protected") else None

    def create_pull(
        self, repo: str, *, title: str, head: str, base: str, body: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.owner}/{repo}/pulls",
            title=title,
            head=head,
            base=base,
            body=body,
        )

    def open_pulls(self, repo: str) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/pulls?state=open&per_page=100"
        )
        response.raise_for_status()
        return response.json()

    def closed_pulls(self, repo: str) -> list[dict[str, Any]]:
        """The most recently updated closed pull requests, merged or not."""
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 100},
        )
        response.raise_for_status()
        return response.json()

    def workflow_runs(self, repo: str, workflow: str) -> list[dict[str, Any]]:
        """Every run of one workflow file, newest first. Empty if it has none."""
        runs: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._client.get(
                f"{API}/repos/{self.owner}/{repo}/actions/workflows/{workflow}/runs",
                params={"per_page": 100, "page": page},
            )
            if response.status_code == 404:
                return runs
            response.raise_for_status()
            batch = response.json().get("workflow_runs") or []
            runs += batch
            if len(batch) < 100:
                return runs
            page += 1

    def check_runs(self, repo: str, sha: str) -> list[dict[str, Any]]:
        """The checks GitHub ran on a commit: name, status and conclusion."""
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/commits/{sha}/check-runs?per_page=100"
        )
        response.raise_for_status()
        return response.json().get("check_runs", [])

    def failed_checks(self, repo: str, sha: str) -> list[dict[str, Any]]:
        """The checks on a commit that finished and failed (#325)."""
        return [
            run
            for run in self.check_runs(repo, sha)
            if run.get("status") == "completed" and run.get("conclusion") in FAILED_CONCLUSIONS
        ]

    def job_log(self, repo: str, job_id: int) -> str:
        """One Actions job's log, or "" where it can't be read (#325).

        GitHub answers with a redirect to short-lived storage; httpx drops the
        token when it follows one to another host. A 403 is an App without
        Actions read access, and a 410 or 404 an expired log: said as "" so the
        caller can say which, rather than failing the pass.
        """
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/actions/jobs/{job_id}/logs", follow_redirects=True
        )
        if response.status_code in (403, 404, 410):
            return ""
        response.raise_for_status()
        return response.text

    def failed_jobs(self, repo: str, run_id: int) -> list[dict[str, Any]]:
        """The jobs of one Actions run that failed, on its latest attempt."""
        jobs = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/actions/runs/{run_id}/jobs",
            params={"filter": "latest", "per_page": 100},
        )
        jobs.raise_for_status()
        return [
            job
            for job in jobs.json().get("jobs") or []
            if job.get("conclusion") in FAILED_CONCLUSIONS
        ]

    def latest_runs(self, repo: str, branch: str) -> dict[str, dict[str, Any]]:
        """The newest finished push run of each workflow on `branch`, by workflow path.

        What the branch is now, per workflow: a red run followed by a green one
        of the same workflow is fixed.
        """
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/actions/runs",
            params={"branch": branch, "event": "push", "status": "completed", "per_page": 50},
        )
        response.raise_for_status()
        latest: dict[str, dict[str, Any]] = {}
        for run in response.json().get("workflow_runs") or []:  # newest first
            latest.setdefault(run.get("path") or run.get("name") or "", run)
        return latest

    def head_sha(self, repo: str, branch: str) -> str:
        """The commit `branch` points at."""
        response = self._client.get(f"{API}/repos/{self.owner}/{repo}/commits/{branch}")
        response.raise_for_status()
        return response.json()["sha"]

    def tag_exists(self, repo: str, tag: str) -> bool:
        response = self._client.get(f"{API}/repos/{self.owner}/{repo}/git/ref/tags/{tag}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def release_for_tag(self, repo: str, tag: str) -> dict[str, Any] | None:
        """The GitHub Release on `tag`, or None."""
        response = self._client.get(f"{API}/repos/{self.owner}/{repo}/releases/tags/{tag}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def attestations(self, repo: str, digest: str) -> list[dict[str, Any]]:
        """GitHub artifact attestations for a subject digest (`sha256:…`), or none."""
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/attestations/{digest}", params={"per_page": 10}
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("attestations") or []

    def merge_group_failures(self, repo: str, pull: int) -> list[dict[str, Any]]:
        """The failed jobs of the latest failed merge-group run that carried `pull` (#325).

        The queue tests a pull request on a temporary branch named
        `gh-readonly-queue/<base>/pr-<N>-<sha>`, so its checks aren't on the
        pull request's head, and the removal event doesn't say which run failed.
        """
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/actions/runs",
            params={"event": "merge_group", "status": "failure", "per_page": 50},
        )
        response.raise_for_status()
        runs = [
            run
            for run in response.json().get("workflow_runs") or []
            if f"/pr-{pull}-" in (run.get("head_branch") or "")
        ]
        if not runs:
            return []
        return self.failed_jobs(repo, runs[0]["id"])

    def file_at(self, repo: str, path: str, ref: str) -> str | None:
        """A file's text at `ref`, or None if there is no such file."""
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    def pull_diff(self, repo: str, number: int) -> str:
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        response.raise_for_status()
        return response.text

    def review_decision(self, repo: str, number: int) -> str | None:
        """GitHub's own verdict on whether this pull request is approved.

        `APPROVED`, `CHANGES_REQUESTED`, `REVIEW_REQUIRED`, or None when the
        branch has no review requirement at all.

        `pull_reviews` says what the crew submitted; this says what counted.
        An approving review from an identity without repository write access is
        recorded and ignored by branch protection, so the two disagree — and
        only this one distinguishes an approval that has not arrived yet from
        one that never can. There is no REST field for it.
        """
        body = self._client.post(
            GRAPHQL,
            json={
                "query": _REVIEW_DECISION,
                "variables": {"owner": self.owner, "repo": repo, "number": number},
            },
        )
        body.raise_for_status()
        payload = body.json()
        if payload.get("errors"):
            raise IssueError(payload["errors"][0].get("message", "unknown GraphQL error"))
        pull = ((payload.get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        decision = pull.get("reviewDecision")
        return str(decision) if decision else None

    def pull_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/pulls/{number}/reviews?per_page=100"
        )
        response.raise_for_status()
        return response.json()

    def create_review(self, repo: str, number: int, *, event: str, body: str) -> dict[str, Any]:
        """Submit a review. `event` is APPROVE, REQUEST_CHANGES or COMMENT."""
        return self._request(
            "POST",
            f"/repos/{self.owner}/{repo}/pulls/{number}/reviews",
            event=event,
            body=body,
        )

    def update_branch(self, repo: str, number: int, *, head: str | None = None) -> None:
        """Merge the base into a pull request's branch, as GitHub's "Update branch" does.

        Asynchronous: GitHub accepts it (202) and the branch moves shortly
        after, which re-runs its checks. `head` guards against updating a
        branch that moved since it was read. A conflict raises
        `BranchUpdateConflict`, never a forced update.
        """
        response = self._client.put(
            f"{API}/repos/{self.owner}/{repo}/pulls/{number}/update-branch",
            json={"expected_head_sha": head} if head else {},
        )
        if response.status_code == 202:
            return
        detail = response.json().get("message", response.text[:120])
        if response.status_code == 422 and "conflict" in detail.lower():
            raise BranchUpdateConflict(detail)
        raise IssueError(f"PUT update-branch #{number} -> {response.status_code}: {detail}")

    def _graphql(self, query: str, **variables: Any) -> dict[str, Any]:
        response = self._client.post(GRAPHQL, json={"query": query, "variables": variables})
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise IssueError(payload["errors"][0].get("message", "unknown GraphQL error"))
        return payload.get("data") or {}

    def queue_state(self, repo: str, number: int, *, branch: str) -> QueueState:
        """Whether `branch` has a merge queue, and where this pull request stands in it.

        GitHub's merge queue lands approved pull requests one after another,
        each tested on top of those ahead of it, so none is left behind `main`
        by the merge before it (#302). There is no REST field for any of this.
        """
        data = self._graphql(
            _QUEUE_STATE, owner=self.owner, repo=repo, number=number, branch=branch
        )
        repository = data.get("repository") or {}
        if not repository.get("mergeQueue"):
            return QueueState()
        pull = repository.get("pullRequest") or {}
        if pull.get("merged"):
            return QueueState(has_queue=True, merged=True)
        if pull.get("mergeQueueEntry"):
            return QueueState(has_queue=True, queued=True)
        last = ((pull.get("timelineItems") or {}).get("nodes") or [{}])[-1] or {}
        if last.get("__typename") == "RemovedFromMergeQueueEvent":
            reason = str(last.get("reason") or "no reason given")
            # The queue's own word for landing it. Not a failure to rebuild.
            if reason.strip().lower() == "merged":
                return QueueState(has_queue=True, merged=True)
            return QueueState(has_queue=True, removed=reason)
        return QueueState(has_queue=True)

    def enqueue(self, pull_id: str, *, head: str | None = None) -> None:
        """Add a pull request to its base branch's merge queue.

        `pull_id` is the pull request's GraphQL node id (`node_id` in REST).
        `head` refuses the enqueue if the branch moved since it was read.
        """
        self._graphql(_ENQUEUE, id=pull_id, head=head)

    def merge_pull(self, repo: str, number: int, *, method: str = "squash") -> dict[str, Any]:
        return self._request(
            "PUT", f"/repos/{self.owner}/{repo}/pulls/{number}/merge", merge_method=method
        )

    def pull(self, repo: str, number: int) -> dict[str, Any]:
        """One pull request, including mergeability — the list endpoint omits it."""
        return self._request("GET", f"/repos/{self.owner}/{repo}/pulls/{number}")

    def pulls_for_branch(
        self, repo: str, branch: str, *, state: str = "all"
    ) -> list[dict[str, Any]]:
        """Every pull request ever opened from this branch, newest first.

        `pull_for_branch` answers "is there one open now". This answers "what
        happened to the last one", which is a different and, for a re-delivery,
        more useful question: a pull request closed without merging is the
        reason a story came back, and nothing else records it.
        """
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/pulls",
            params={
                "state": state,
                "head": f"{self.owner}:{branch}",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
            },
        )
        response.raise_for_status()
        return response.json()

    def pull_for_branch(
        self, repo: str, branch: str, *, known: Any = None
    ) -> dict[str, Any] | None:
        """The open pull request from `branch`.

        `known` is the one the board already links to the card (`Card.open_pull_on`),
        and costs nothing (#55). Without it — a pull request opened by hand with
        no `Closes #N`, or one opened since the board was read — every open pull
        request in the repository is scanned.
        """
        if known is not None:
            return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
        for pull in self.open_pulls(repo):
            if pull["head"]["ref"] == branch:
                return pull
        return None

    def search(self, repo: str, text: str) -> list[int]:
        """Numbers of the issues in `repo` whose title, body or comments mention `text`.

        Search is loose (it matches words, not phrases), so a caller filters what
        it gets. GitHub returns at most 1,000 results.
        """
        found: list[int] = []
        page = 1
        while True:
            response = self._client.get(
                f"{API}/search/issues",
                params={
                    "q": f"{text} repo:{self.owner}/{repo} is:issue",
                    "per_page": 100,
                    "page": page,
                },
            )
            if response.status_code == 422:
                # GitHub's answer for a repository the token can't see, as well as a bad query.
                raise IssueError(f"search of {repo} -> 422: {response.json().get('message', '')}")
            response.raise_for_status()
            batch = response.json()["items"]
            found += [i["number"] for i in batch]
            if len(batch) < 100 or len(found) >= 1000:
                return found
            page += 1

    def sub_issues(self, repo: str, number: int) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{API}/repos/{self.owner}/{repo}/issues/{number}/sub_issues?per_page=100"
        )
        response.raise_for_status()
        return response.json()

    def close(self, repo: str, number: int, *, reason: str = "completed") -> dict[str, Any]:
        """Close an issue. `reason` is "completed" or "not_planned"."""
        return self._request(
            "PATCH",
            f"/repos/{self.owner}/{repo}/issues/{number}",
            state="closed",
            state_reason=reason,
        )

    def edit_issue(self, repo: str, number: int, *, body: str) -> dict[str, Any]:
        """Replace an issue's body."""
        return self._request("PATCH", f"/repos/{self.owner}/{repo}/issues/{number}", body=body)

    def edit_pull(self, repo: str, number: int, *, body: str) -> dict[str, Any]:
        """Replace a pull request's description."""
        return self._request("PATCH", f"/repos/{self.owner}/{repo}/pulls/{number}", body=body)

    def close_pull(self, repo: str, number: int) -> dict[str, Any]:
        """Close a pull request without merging it."""
        return self._request("PATCH", f"/repos/{self.owner}/{repo}/pulls/{number}", state="closed")

    def reopen(self, repo: str, number: int) -> dict[str, Any]:
        return self._request("PATCH", f"/repos/{self.owner}/{repo}/issues/{number}", state="open")

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        self._request("POST", f"/repos/{self.owner}/{repo}/issues/{number}/labels", labels=labels)

    def remove_label(self, repo: str, number: int, label: str) -> None:
        """Drop a label if present. A 404 means it was not there, which is fine."""
        response = self._client.delete(
            f"{API}/repos/{self.owner}/{repo}/issues/{number}/labels/{label}"
        )
        if response.status_code not in (200, 404):
            raise IssueError(f"could not remove {label!r} from #{number}: {response.status_code}")

    def has_comment_marked(self, repo: str, number: int, marker: str) -> bool:
        """Has the crew already written this kind of comment here?

        Ticks are reconciliation passes and run repeatedly, so every write must
        be idempotent or a goal accrues one identical proposal per tick.
        """
        return any(marker in (c.get("body") or "") for c in self.comments(repo, number))
