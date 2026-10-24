"""A verified release deploys itself on the Mac, from infra's compose files (crew#829)."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS
from crew_org.events import EventKind
from crew_org.flows import deploy
from crew_org.flows import role_tasks as rt
from crew_org.flows.deploy import Checked, Health, Reader
from crew_org.flows.merge import Landed, Landing
from crew_org.packages import sprint_metrics_answers as sma
from crew_org.tools.github_project import Card
from tests.role_task_fakes import Board, Issues, events, sink

COMPOSE = """\
name: sprint-metrics

services:
  sprint-metrics:
    # Pinned by tag; the crew's deploy changes only this line.
    image: ghcr.io/mqucifer/sprint-metrics:1.3.1
    container_name: crew-sprint-metrics
"""

INVENTORY = """\
name: sprint-metrics
host: mac
deploy:
  repo: sprint-metrics
  compose: compose/mac/sprint-metrics/compose.yaml
  service: sprint-metrics
  image: ghcr.io/mqucifer/sprint-metrics
  env_file: sprint-metrics.env
  health:
    url: http://127.0.0.1:8080/health
    status: 200
    json: {status: ok}
    within_s: 180
  readers:
    - name: crew
      repo: crew
      file: src/crew_org/packages/sprint_metrics_answers.py
      pin: RELEASE
"""
SECRET = "s3cret-db-password"


class Gh(Issues):
    def __init__(self) -> None:
        super().__init__()
        self.pulls: list[dict] = []

    def repository(self, repo: str) -> dict:
        return {"default_branch": "main"}

    def open_pulls(self, repo: str) -> list[dict]:
        return [p for p in self.pulls if p.get("state") == "open"]

    def create_pull(self, repo: str, *, title: str, head: str, base: str, body: str) -> dict:
        pull = {
            "number": 40 + len(self.pulls),
            "html_url": f"https://github.com/mqucifer/{repo}/pull/{40 + len(self.pulls)}",
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "state": "open",
        }
        self.pulls.append(pull)
        self.done.append(("pull", repo, head))
        return pull


class Cards(Board):
    """The fake board, with the cards open_task placed on it."""

    def __init__(self, issues: Gh) -> None:
        super().__init__()
        self.issues = issues
        self.status: dict[str, str] = {}

    def set_status(self, item: str, column: str) -> None:
        super().set_status(item, column)
        self.status[item] = column

    def cards(self) -> list[Card]:
        return [
            Card(
                item_id=f"item-N{n}",
                title=i.get("title", ""),
                status=self.status.get(f"item-N{n}", ""),
                number=n,
                repo="sprint-metrics",
                state="CLOSED" if i.get("state") == "closed" else "OPEN",
                work_type="Task",
            )
            for n, i in self.issues.issues.items()
            if f"item-N{n}" in self.status
        ]


class Infra:
    """The tick's infra checkout: `main`, and a branch worktree for the record."""

    def __init__(self, root: Path) -> None:
        self.main = root / "main"
        self.branches = root / "branches"
        self.commits: list[str] = []
        self.pushed = False
        (self.main / "compose/mac/sprint-metrics").mkdir(parents=True)
        (self.main / "compose/mac/sprint-metrics/compose.yaml").write_text(COMPOSE)
        (self.main / "inventory/services").mkdir(parents=True)
        (self.main / "inventory/services/sprint-metrics.yaml").write_text(INVENTORY)
        (self.main / "inventory/services/litellm.yaml").write_text("name: litellm\nhost: mac\n")
        self.path: Path | None = None

    def for_repo(self, repo: str) -> Infra:
        assert repo == "infra"
        return self

    def at_main(self) -> Path:
        return self.main

    def open(self, branch: str) -> Path:
        self.path = self.branches / branch.replace("/", "__")
        shutil.rmtree(self.path, ignore_errors=True)  # as Workspace.open closes one left over
        shutil.copytree(self.main, self.path)
        return self.path

    def commit(self, message: str) -> bool:
        self.commits.append(message)
        return True

    def push(self, *, force: bool = False) -> None:
        self.pushed = True

    def __enter__(self) -> Infra:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def compose_on_branch(self) -> str:
        assert self.path is not None
        return (self.path / "compose/mac/sprint-metrics/compose.yaml").read_text()


class Docker:
    def __init__(self, running: str = "ghcr.io/mqucifer/sprint-metrics:1.3.1") -> None:
        self.running = running
        self.ran: list[tuple[str, ...]] = []
        self.tags: list[str] = []
        self.fail_pull = False

    def compose(self, compose: Path, env_file: Path, *args: str) -> tuple[int, str]:
        self.ran.append(args)
        if args[0] == "up":
            self.tags.append(deploy.pinned(compose.read_text(), "ghcr.io/mqucifer/sprint-metrics"))
        if args[0] == "pull" and self.fail_pull:
            return 1, "manifest unknown"
        return 0, f"{args[0]} done"

    def image_of(self, compose: Path, env_file: Path, service: str) -> str | None:
        return self.running

    def logs(self, compose: Path, env_file: Path, service: str) -> str:
        return f"connecting to postgresql://sprint_metrics:{SECRET}@litellm-temp-db failed"


def _checks(*answers: bool):
    left = list(answers)

    def check(health: Health) -> Checked:
        ok = left.pop(0)
        return Checked(ok, 3, 4.5, "" if ok else "503: starting")

    return check


@pytest.fixture
def world(tmp_path):
    gh = Gh()
    board = Cards(gh)
    infra = Infra(tmp_path / "infra")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "sprint-metrics.env").write_text(f"SPRINT_METRICS_DB_PASSWORD={SECRET}\n")
    return gh, board, infra, secrets, sink(tmp_path)


def _deploy(world, version: str, docker: Docker, check) -> deploy.Deploys:
    gh, board, infra, secrets, s = world
    return deploy.deploy_releases(
        gh,
        board,
        s,
        infra,
        verified=[("sprint-metrics", version)],
        docker=docker,  # type: ignore[arg-type]
        check=check,
        secrets=secrets,
    )


def _kinds(tmp_path) -> list[str]:
    return [e["kind"] for e in events(tmp_path)]


# --- the compose file and the readers ---------------------------------------------------


def test_the_tag_is_read_and_bumped_on_its_image_line_alone():
    image = "ghcr.io/mqucifer/sprint-metrics"
    assert deploy.pinned(COMPOSE, image) == "1.3.1"
    after = deploy.bumped(COMPOSE, image, "1.3.3")
    assert deploy.pinned(after, image) == "1.3.3"
    assert after.replace("1.3.3", "1.3.1") == COMPOSE
    assert deploy.pinned(COMPOSE, "ghcr.io/mqucifer/other") is None


def test_the_inventory_declares_the_deploy_and_its_check(world):
    _, _, infra, _, _ = world
    [service] = deploy.declared(infra.main)
    assert service.repo == "sprint-metrics"
    assert service.health == Health("http://127.0.0.1:8080/health", 200, {"status": "ok"}, 180)
    assert service.readers == (
        Reader("crew", "crew", "src/crew_org/packages/sprint_metrics_answers.py", "RELEASE"),
    )


def test_the_crew_as_a_reader_is_read_from_the_checkout_the_tick_runs():
    reader = Reader("crew", "crew", "src/crew_org/packages/sprint_metrics_answers.py", "RELEASE")
    assert deploy.reader_pin(Gh(), reader) == sma.RELEASE


def test_a_release_deploys_only_at_the_major_its_readers_pin():
    crew = Reader("crew", "crew", "f.py", "RELEASE")
    assert deploy.held_by("1.3.3", [(crew, "1.3.1")]) is None
    assert deploy.held_by("2.0.0", [(crew, "1.3.1")]) == (crew, "1.3.1")
    assert deploy.held_by("1.4.0", [(crew, None)]) == (crew, "unread")


# --- the deploy ---------------------------------------------------------------------------


def test_a_verified_release_deploys_and_records_its_tag_in_infra(world, tmp_path):
    gh, board, infra, _, _ = world
    docker = Docker()
    result = _deploy(world, "1.3.3", docker, _checks(True))

    assert result.healthy == [("sprint-metrics", "1.3.3")]
    [task] = gh.labelled("sprint-metrics", rt.DEVOPS.label)
    assert rt.marked(task["body"]) == rt.Marked(rt.DEVOPS, rt.DEPLOY, "sprint-metrics-1.3.3")
    assert [a[0] for a in docker.ran] == ["pull", "up"]
    assert docker.tags == ["1.3.3"]
    # The record: only the tag line changed, by pull request on infra.
    assert deploy.pinned(infra.compose_on_branch(), "ghcr.io/mqucifer/sprint-metrics") == "1.3.3"
    assert infra.pushed
    [pull] = gh.pulls
    assert "<!-- crew:deploy repo=sprint-metrics version=1.3.3 -->" in pull["body"]
    assert "merges this itself" in pull["body"]
    # The Task opened In Progress and ended in Done, closed, with its outcome said.
    moves = [d[2] for d in board.done if d[0] == "status" and d[1] == f"item-N{task['number']}"]
    assert moves == [IN_PROGRESS, DONE]
    assert task["state"] == "closed"
    assert "<!-- crew:deploy-outcome healthy -->" in gh.comments_on[task["number"]][0]["body"]
    kinds = _kinds(tmp_path)
    assert kinds.index("deploy.started") < kinds.index("deploy.healthy")
    healthy = next(e for e in events(tmp_path) if e["kind"] == "deploy.healthy")
    assert {k: healthy["detail"][k] for k in ("from", "to", "attempts")} == {
        "from": "1.3.1",
        "to": "1.3.3",
        "attempts": 3,
    }


def test_a_major_ahead_of_its_reader_opens_no_deploy_and_says_who_moves_first(world, tmp_path):
    gh, _, _, _, _ = world
    docker = Docker()
    result = _deploy(world, "2.0.0", docker, _checks())

    assert gh.labelled("sprint-metrics", rt.DEVOPS.label) == []
    assert docker.ran == []
    [(repo, version, why)] = result.held
    assert (repo, version) == ("sprint-metrics", "2.0.0")
    assert "crew pins 1.3.1" in why and "crew moves first" in why
    held = next(e for e in events(tmp_path) if e["kind"] == EventKind.DEPLOY_HELD)
    assert held["detail"]["reader"] == "crew" and held["detail"]["pinned"] == "1.3.1"


def test_a_release_not_newer_than_the_pinned_tag_is_left_alone(world):
    docker = Docker()
    for version in ("1.3.1", "1.2.9"):
        result = _deploy(world, version, docker, _checks())
        assert not result.moved and not result.held
    assert docker.ran == []


def test_with_no_settings_file_nothing_is_started(world):
    _, _, _, secrets, _ = world
    (secrets / "sprint-metrics.env").unlink()
    docker = Docker()
    result = _deploy(world, "1.3.3", docker, _checks())
    assert docker.ran == []
    assert "sprint-metrics.env" in result.held[0][2]


def test_a_failed_check_rolls_back_to_what_was_running_and_files_a_finding(world, tmp_path):
    gh, board, infra, _, _ = world
    docker = Docker(running="ghcr.io/mqucifer/sprint-metrics:1.3.0")  # started by hand
    result = _deploy(world, "1.3.3", docker, _checks(False, True))

    assert docker.tags == ["1.3.3", "1.3.0"]
    assert gh.pulls == [] and not infra.commits
    [(_, _, task)] = result.rolled_back
    finding = next(
        i for i in gh.issues.values() if getattr(rt.marked(i["body"]), "kind", "") == rt.FINDING
    )
    assert "failed its deploy at health" in finding["title"]
    # The log reached the finding and the Task, with the password taken out.
    told = finding["body"] + gh.comments_on[task][0]["body"]
    assert "[redacted]" in told and SECRET not in told
    assert gh.issues[task]["state"] == "closed"
    assert "deploy.rolled_back" in _kinds(tmp_path)


def test_a_pull_that_fails_starts_nothing_and_is_a_handled_ending(world, tmp_path):
    docker = Docker()
    docker.fail_pull = True
    result = _deploy(world, "1.3.3", docker, _checks(True))
    assert [a[0] for a in docker.ran] == ["pull", "up"]  # only the rollback's up
    assert docker.tags == ["1.3.1"]
    assert result.rolled_back
    rolled = next(e for e in events(tmp_path) if e["kind"] == "deploy.rolled_back")
    assert rolled["detail"]["stage"] == "pull"


def test_a_failed_rollback_goes_to_the_sponsor(world, tmp_path):
    gh, board, _, _, _ = world
    result = _deploy(world, "1.3.3", Docker(), _checks(False, False))

    [(_, _, task)] = result.rollback_failed
    assert ("label", task, rt.NEEDS_HUMAN) in gh.done
    task_moves = [d[2] for d in board.done if d[0] == "status" and d[1] == f"item-N{task}"]
    assert task_moves == [IN_PROGRESS, BLOCKED]
    assert gh.issues[task]["state"] == "open"
    assert "**For the Sponsor:**" in gh.comments_on[task][0]["body"]
    assert "deploy.rollback_failed" in _kinds(tmp_path)


def test_a_deploy_that_ended_is_never_run_again_and_one_cut_short_is(world):
    gh, _, _, _, _ = world
    _deploy(world, "1.3.3", Docker(), _checks(False, False))  # ended: rollback failed
    again = Docker()
    _deploy(world, "1.3.3", again, _checks(True))
    assert again.ran == []

    # A tick that died after opening the Task left no outcome: the next pass runs it.
    [task] = [
        i
        for i in gh.labelled("sprint-metrics", rt.DEVOPS.label)
        if getattr(rt.marked(i["body"]), "kind", "") == rt.DEPLOY
    ]
    gh.comments_on[task["number"]] = []
    rerun = Docker()
    result = _deploy(world, "1.3.3", rerun, _checks(True))
    assert [a[0] for a in rerun.ran] == ["pull", "up"]
    assert result.healthy and not result.opened


def test_a_repository_no_service_declares_has_nothing_to_deploy(world):
    gh, board, infra, secrets, s = world
    docker = Docker()
    result = deploy.deploy_releases(
        gh,
        board,
        s,
        infra,
        verified=[("crew-presentation", "0.4.0")],
        docker=docker,  # type: ignore[arg-type]
        check=_checks(),
        secrets=secrets,
    )
    assert docker.ran == [] and not result.moved and not result.failed


def test_the_crew_merges_its_deploy_records_once_infra_s_checks_pass(world, monkeypatch):
    gh, board, infra, secrets, s = world
    gh.pulls = [
        {"number": 41, "state": "open", "body": "<!-- crew:deploy repo=sm version=1.3.3 -->"},
        {"number": 42, "state": "open", "body": "a person's change"},
    ]
    landed: list[int] = []

    def land(issues, repo, number):
        landed.append(number)
        return Landed(Landing.MERGED)

    monkeypatch.setattr(deploy, "land", land)
    result = deploy.deploy_releases(gh, board, s, infra, verified=[], secrets=secrets)
    assert landed == [41] and result.merged == [41]


# --- the health check -------------------------------------------------------------------


class _Answer:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> object:
        return self._body


def test_healthy_is_the_declared_status_and_json_fields():
    health = Health("http://127.0.0.1:8080/health", 200, {"status": "ok"}, 30)
    answers = iter(
        [httpx.ConnectError("refused"), _Answer(503, {}), _Answer(200, {"status": "ok"})]
    )

    def get(url, timeout):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    clock = iter(range(100))
    checked = deploy.check_health(health, get=get, sleep=lambda s: None, clock=lambda: next(clock))
    assert checked.healthy and checked.attempts == 3


def test_a_check_that_never_passes_ends_at_its_limit_with_the_last_answer():
    health = Health("http://127.0.0.1:8080/health", 200, {"status": "ok"}, 10)
    clock = iter(range(0, 100, 4))
    checked = deploy.check_health(
        health,
        get=lambda url, timeout: _Answer(200, {"status": "degraded"}),
        sleep=lambda s: None,
        clock=lambda: next(clock),
    )
    assert not checked.healthy and checked.attempts == 3
    assert "degraded" in checked.last


def test_settings_values_are_taken_out_of_what_is_filed():
    assert (
        deploy.redacted(f"url {SECRET} end", {"P": SECRET, "short": "ab"}) == "url [redacted] end"
    )
