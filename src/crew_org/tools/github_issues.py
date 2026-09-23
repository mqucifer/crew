"""Issues and comments — the crew's audit trail.

Every state transition writes a comment saying what was decided, why, and on
what evidence. The comment history *is* the sprint artifact record; there is no
separate report. That makes this module the Sponsor's only window into what the
crew actually did, so what it writes has to read well to a human who was not
present.
"""

from __future__ import annotations

from typing import Any

import httpx

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
TIMEOUT = 30.0


class IssueError(RuntimeError):
    pass


_REVIEW_DECISION = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { reviewDecision }
  }
}
"""


class IssueClient:
    def __init__(self, token: str, owner: str, *, client: httpx.Client | None = None) -> None:
        self.owner = owner
        self._client = client or httpx.Client(
            timeout=TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _request(self, method: str, path: str, **json: Any) -> dict[str, Any]:
        response = self._client.request(method, f"{API}{path}", json=json or None)
        if response.status_code >= 400:
            detail = response.json().get("message", response.text[:120])
            raise IssueError(f"{method} {path} -> {response.status_code}: {detail}")
        return response.json() if response.content else {}

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
        return response.json()

    def add_sub_issue(self, repo: str, parent_number: int, child_id: int) -> None:
        """Nest one issue under another so the board shows the hierarchy.

        Takes the child's database id, not its number.
        """
        self._request(
            "POST",
            f"/repos/{self.owner}/{repo}/issues/{parent_number}/sub_issues",
            sub_issue_id=child_id,
        )

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

    def pull_for_branch(self, repo: str, branch: str) -> dict[str, Any] | None:
        for pull in self.open_pulls(repo):
            if pull["head"]["ref"] == branch:
                return pull
        return None

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
