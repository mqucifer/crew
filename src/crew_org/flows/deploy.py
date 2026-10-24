"""A verified release deploys itself on the Mac (crew#829, ADR 0034).

The Sponsor's merge of a release pull request is the only approval a release and its
deploy need (the Sponsor, 2026-10-20). Once the release check has verified what the
release published, the tick deploys it: it bumps the pinned tag in infra's compose
file, pulls, restarts, checks the service's health, and rolls back to the tag that
was running if the check fails. No model is called and nothing runs between ticks.

**What a service is, and how its deploy is checked, is declared in infra,** in the
`deploy:` block of its inventory file (mqucifer/infra `inventory/README.md`), never
here. A repository with no service declaring it has nothing to deploy.

**A MAJOR release ahead of its readers waits.** Each reader the block names pins a
release; one ahead of any of them would break what that reader reads, so it's held,
recorded, and said on the standup until the reader moves on purpose.

Each deploy is DevOps's Task in the released repository, keyed by repository and
version, so a release is deployed once. It never waits in a column a session claims
from: the pass that opens it runs it and moves it to `Done`, or to `Blocked` when the
rollback failed too. A Task a tick died part-way through is still open with no
outcome, and the next pass runs it again from what's running.

A healthy deploy's new tag reaches infra's `main` by pull request, which the crew
merges itself once infra's guard checks pass: the release's merge was the approval.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, NEEDS_REFINEMENT
from crew_org.config import load_env
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows import role_tasks as rt
from crew_org.flows.merge import Landing, land
from crew_org.flows.moves import move_card
from crew_org.llm import reraise_if_down

INFRA = "infra"
SERVICES_DIR = "inventory/services"
BY = rt.DEVOPS.name
# On the infra pull request that records a healthy deploy's tag, so a later pass
# finds and merges it.
RECORD_PR = "<!-- crew:deploy repo={repo} version={version} -->"
_RECORD_PR = re.compile(r"<!-- crew:deploy repo=(\S+) version=(\S+) -->")
# On the Task, once its deploy has ended one way or another: it isn't run again.
OUTCOME = "<!-- crew:deploy-outcome {outcome} -->"
_OUTCOME = re.compile(r"<!-- crew:deploy-outcome (\w+) -->")
# Enough of the service's log to see why it didn't come up.
LOG_LINES = 200
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def secrets_dir() -> Path:
    """Where each compose project's settings are on the Mac (mqucifer/infra CONVENTIONS)."""
    return Path(os.environ.get("INFRA_SECRETS") or "~/.config/infra/secrets").expanduser()


@dataclass(frozen=True)
class Health:
    url: str
    status: int = 200
    # Fields the JSON body must hold, with these values.
    json: dict[str, Any] = field(default_factory=dict)
    within_s: float = 180


@dataclass(frozen=True)
class Reader:
    name: str
    repo: str
    file: str
    pin: str


@dataclass(frozen=True)
class Declared:
    """A service's `deploy:` block in infra's inventory."""

    name: str
    repo: str
    compose: str
    service: str
    image: str
    env_file: str
    health: Health
    readers: tuple[Reader, ...] = ()


def declared(checkout: Path) -> list[Declared]:
    """Every service in infra's inventory that a release deploys."""
    found: list[Declared] = []
    for path in sorted((checkout / SERVICES_DIR).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        block = data.get("deploy")
        if not isinstance(block, dict):
            continue
        health = block.get("health") or {}
        found.append(
            Declared(
                name=str(data.get("name") or path.stem),
                repo=str(block["repo"]),
                compose=str(block["compose"]),
                service=str(block["service"]),
                image=str(block["image"]),
                env_file=str(block["env_file"]),
                health=Health(
                    url=str(health["url"]),
                    status=int(health.get("status", 200)),
                    json=dict(health.get("json") or {}),
                    within_s=float(health.get("within_s", 180)),
                ),
                readers=tuple(
                    Reader(str(r["name"]), str(r["repo"]), str(r["file"]), str(r["pin"]))
                    for r in block.get("readers") or ()
                ),
            )
        )
    return found


def _image_line(image: str) -> re.Pattern[str]:
    return re.compile(rf"^(\s*image:\s*[\"']?){re.escape(image)}:([\w.-]+)", re.MULTILINE)


def pinned(text: str, image: str) -> str | None:
    """The tag the compose file pins `image` at, or None when no line does."""
    found = _image_line(image).search(text)
    return found.group(2) if found else None


def bumped(text: str, image: str, tag: str) -> str:
    """The compose file with `image` pinned at `tag`, and nothing else changed."""
    return _image_line(image).sub(lambda m: f"{m.group(1)}{image}:{tag}", text, count=1)


def semver(version: str) -> tuple[int, int, int] | None:
    found = _SEMVER.match(version.strip())
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def newer(version: str, than: str) -> bool:
    a, b = semver(version), semver(than)
    return a is not None and b is not None and a > b


CREW = "crew"
# The checkout this tick runs from.
_CHECKOUT = Path(__file__).resolve().parents[3]


def reader_pin(issues: Any, reader: Reader) -> str | None:
    """The release `reader` pins: the crew's from the checkout this tick runs, so a deploy
    keeps to what the running tick reads, not to a `main` it hasn't pulled yet; any
    other reader's from its repository's default branch."""
    if reader.repo == CREW:
        path = _CHECKOUT / reader.file
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    else:
        branch = issues.repository(reader.repo).get("default_branch") or "main"
        text = issues.file_at(reader.repo, reader.file, branch) or ""
    found = re.search(rf"^{re.escape(reader.pin)}\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    return found.group(1) if found else None


def held_by(version: str, pins: list[tuple[Reader, str | None]]) -> tuple[Reader, str] | None:
    """The first reader `version` would break: a different MAJOR, or a pin unread."""
    major = (semver(version) or (None,))[0]
    for reader, pin in pins:
        theirs = semver(pin or "")
        if theirs is None or theirs[0] != major:
            return reader, pin or "unread"
    return None


def redacted(text: str, secrets: dict[str, str]) -> str:
    """`text` with every settings value taken out, matched here, never in a shell."""
    for value in sorted(secrets.values(), key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, "[redacted]")
    return text


class Docker:
    """The commands a deploy runs, as one place a test can stand in for.

    `project` overrides the compose file's own name, for a proof against a throwaway
    project; the tick never sets it.
    """

    def __init__(self, project: str | None = None, timeout: float = 600) -> None:
        self.project = project
        self.timeout = timeout

    def _base(self, compose: Path, env_file: Path) -> list[str]:
        base = ["docker", "compose"]
        if self.project:
            base += ["--project-name", self.project]
        return [*base, "--env-file", str(env_file), "-f", str(compose)]

    def _run(self, args: list[str]) -> tuple[int, str]:
        try:
            done = subprocess.run(  # noqa: S603 - fixed docker arguments, no shell
                args, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, f"{type(exc).__name__}: {exc}"
        return done.returncode, (done.stdout + done.stderr).strip()

    def compose(self, compose: Path, env_file: Path, *args: str) -> tuple[int, str]:
        return self._run([*self._base(compose, env_file), *args])

    def image_of(self, compose: Path, env_file: Path, service: str) -> str | None:
        """The image the service's running container was started from, or None."""
        code, out = self.compose(compose, env_file, "ps", "-q", service)
        container = out.splitlines()[0].strip() if code == 0 and out.strip() else ""
        if not container:
            return None
        code, out = self._run(["docker", "inspect", "--format", "{{.Config.Image}}", container])
        return out.strip() if code == 0 and out.strip() else None

    def logs(self, compose: Path, env_file: Path, service: str) -> str:
        return self.compose(
            compose, env_file, "logs", "--no-color", "--tail", str(LOG_LINES), service
        )[1]


@dataclass
class Checked:
    healthy: bool
    attempts: int
    seconds: float
    last: str = ""


def check_health(
    health: Health,
    *,
    get: Callable[..., Any] = httpx.get,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    every: float = 3,
) -> Checked:
    """Ask the health URL until it answers as declared, or `within_s` runs out."""
    start = clock()
    attempts = 0
    last = ""
    while True:
        attempts += 1
        try:
            answer = get(health.url, timeout=5)
            body: Any = None
            if health.json:
                try:
                    body = answer.json()
                except ValueError:
                    body = None
            fits = isinstance(body, dict) and all(body.get(k) == v for k, v in health.json.items())
            if answer.status_code == health.status and (not health.json or fits):
                return Checked(True, attempts, round(clock() - start, 1))
            last = f"{answer.status_code}: {answer.text[:200]}"
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"[:200]
        if clock() - start >= health.within_s:
            return Checked(False, attempts, round(clock() - start, 1), last)
        sleep(every)


@dataclass
class Deploys:
    # (repo, version, Task): a deploy Task opened.
    opened: list[tuple[str, str, int]] = field(default_factory=list)
    # (repo, version): deployed and healthy.
    healthy: list[tuple[str, str]] = field(default_factory=list)
    # (repo, version, Task): the check failed and the old release runs again.
    rolled_back: list[tuple[str, str, int]] = field(default_factory=list)
    # (repo, version, Task): the rollback failed too; the Sponsor's.
    rollback_failed: list[tuple[str, str, int]] = field(default_factory=list)
    # (repo, version, why): waiting, with nothing deployed. A reader to move first,
    # or a step that's the Sponsor's.
    held: list[tuple[str, str, str]] = field(default_factory=list)
    # The infra pull requests recording a deploy that the crew merged.
    merged: list[int] = field(default_factory=list)
    # (repo, why): something went wrong; the next pass tries again.
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        return bool(self.healthy or self.rolled_back or self.rollback_failed or self.merged)


def deploy_releases(
    issues: Any,
    board: Any,
    sink: EventSink,
    ws: Any,
    *,
    verified: list[tuple[str, str]],
    docker: Docker | None = None,
    check: Callable[[Health], Checked] = check_health,
    secrets: Path | None = None,
) -> Deploys:
    """Deploy each verified release a service in infra declares, once."""
    result = Deploys()
    try:
        merge_records(issues, sink, result=result)
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        result.failed.append((INFRA, f"deploy records not merged: {exc}"[:200]))
    if not verified:
        return result
    docker = docker or Docker()
    for repo, version in verified:
        try:
            _deploy(
                issues,
                board,
                sink,
                ws,
                repo=repo,
                version=version,
                docker=docker,
                check=check,
                secrets=secrets or secrets_dir(),
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            why = f"{type(exc).__name__}: {exc}"[:200]
            result.failed.append((repo, why))
            sink.note(EventKind.NOTE, f"{repo} v{version}: deploy not run: {why}"[:160], repo=repo)
    return result


def _deploy(
    issues: Any,
    board: Any,
    sink: EventSink,
    ws: Any,
    *,
    repo: str,
    version: str,
    docker: Docker,
    check: Callable[[Health], Checked],
    secrets: Path,
    result: Deploys,
) -> None:
    infra = ws.for_repo(INFRA)
    checkout = infra.at_main()
    service = next((d for d in declared(checkout) if d.repo == repo), None)
    if service is None:
        return  # nothing in infra runs this repository's releases
    text = (checkout / service.compose).read_text(encoding="utf-8")
    current = pinned(text, service.image)
    if current is None:
        raise ValueError(f"{service.compose} pins no {service.image} image")
    if not newer(version, current):
        return
    key = f"{repo}-{version}"
    found = rt.existing(issues, repo, rt.DEVOPS, rt.DEPLOY, key)
    if found is not None and (found.get("state") == "closed" or _ended(issues, repo, found)):
        return
    reader = held_by(version, [(r, reader_pin(issues, r)) for r in service.readers])
    if reader is not None:
        why = _held(sink, repo=repo, version=version, reader=reader[0].name, pin=reader[1])
        result.held.append((repo, version, why))
        return
    env_file = secrets / service.env_file
    if not env_file.is_file():
        # The settings are the Sponsor's to write (mqucifer/infra#34): nothing is started
        # without them.
        why = _held(sink, repo=repo, version=version, reader="", pin="", missing=env_file.name)
        result.held.append((repo, version, why))
        return

    title = f"Deploy {repo} {version} on the Mac"
    opened = rt.open_task(
        issues,
        board,
        sink,
        repo=repo,
        role=rt.DEVOPS,
        kind=rt.DEPLOY,
        key=key,
        title=title,
        body=rt.render(
            rt.DEVOPS,
            kind=rt.DEPLOY,
            key=key,
            why=f"`{repo}` released `{version}`, and the release check verified what it "
            "published. Its merge was the Sponsor's approval of the release and its deploy "
            "(ADR 0019, ADR 0034).",
            work=f"The tick deploys it, with no model: `{service.image}` goes from "
            f"`{current}` to `{version}` in mqucifer/infra `{service.compose}`, then pull, "
            f"restart and the health check `{service.health.url}`. A failed check rolls "
            "back to the release that was running and files DevOps's finding.",
            done_when=[
                f"`{service.name}` runs `{version}` and its health check passes",
                "The new tag is on mqucifer/infra's `main`",
            ],
        ),
        points=None,
        cards=board.cards(),
        column=IN_PROGRESS,
        by=BY,
        event="release.verified",
        rule="decision.deploy.opened",
    )
    task = opened.number
    if opened.new:
        result.opened.append((repo, version, task))
    _run(
        issues,
        board,
        sink,
        infra,
        service=service,
        repo=repo,
        version=version,
        task=task,
        text=text,
        current=current,
        env_file=env_file,
        docker=docker,
        check=check,
        result=result,
    )


def _ended(issues: Any, repo: str, task: dict[str, Any]) -> bool:
    """The Task's deploy already ended: it has an outcome, so it isn't run again."""
    return any(
        _OUTCOME.search(c.get("body") or "") for c in issues.comments(repo, int(task["number"]))
    )


def _held(
    sink: EventSink,
    *,
    repo: str,
    version: str,
    reader: str,
    pin: str,
    missing: str = "",
) -> str:
    """Record why a release isn't deployed, and say it for the standup."""
    why = (
        f"its settings file `{missing}` isn't in INFRA_SECRETS: the Sponsor's to write"
        if missing
        else f"{reader} pins {pin}, a different MAJOR: {reader} moves first"
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.DEPLOY_HELD,
            role=BY,
            summary=f"{repo} {version} not deployed: {why}"[:100],
            detail={
                "repo": repo,
                "version": version,
                "reader": reader or None,
                "pinned": pin or None,
                "missing": missing or None,
                "rule": "decision.deploy.held",
            },
        )
    )
    return why


def _run(
    issues: Any,
    board: Any,
    sink: EventSink,
    infra: Any,
    *,
    service: Declared,
    repo: str,
    version: str,
    task: int,
    text: str,
    current: str,
    env_file: Path,
    docker: Docker,
    check: Callable[[Health], Checked],
    result: Deploys,
) -> None:
    from crew_org.git_ops import branch_name  # noqa: PLC0415

    settings = load_env(env_file)
    branch = branch_name(task, f"deploy {repo} {version}", kind="chore")
    with infra:
        path = infra.open(branch)
        compose = path / service.compose
        running = docker.image_of(compose, env_file, service.service)
        # Back to what was running, not to what infra's file says: they agree, unless a
        # person started another release by hand.
        old = (running or "").rpartition(":")[2] if running else current
        frm = {"repo": repo, "service": service.name, "from": old, "to": version}
        sink.emit(
            CrewEvent(
                kind=EventKind.DEPLOY_STARTED,
                role=BY,
                card=task,
                summary=f"{repo}: deploying {version} over {old}"[:100],
                detail={**frm, "rule": "decision.deploy.opened"},
            )
        )
        compose.write_text(bumped(text, service.image, version), encoding="utf-8")
        log: list[str] = []
        stage = "pull"
        code, out = docker.compose(compose, env_file, "pull", "--quiet", service.service)
        log.append(f"$ docker compose pull --quiet {service.service}\n{out}")
        checked = Checked(False, 0, 0.0)
        if code == 0:
            stage = "up"
            code, out = docker.compose(compose, env_file, "up", "-d", service.service)
            log.append(f"$ docker compose up -d {service.service}\n{out}")
        if code == 0:
            stage = "health"
            checked = check(service.health)
            log.append(
                f"health: {service.health.url}, {checked.attempts} attempts in "
                f"{checked.seconds}s, last answer: {checked.last or 'healthy'}"
            )
        measures = {"attempts": checked.attempts, "seconds": checked.seconds}
        if checked.healthy:
            infra.commit(
                f"chore: {service.name} runs {repo} {version}\n\n"
                f"The crew's deploy, healthy (mqucifer/{repo}#{task}, mqucifer/crew#829)."
            )
            infra.push(force=True)
            pull = issues.create_pull(
                INFRA,
                title=f"chore: {service.name} runs {repo} {version}",
                head=branch,
                base="main",
                body=artifacts.signed(
                    f"{RECORD_PR.format(repo=repo, version=version)}\n"
                    f"The crew deployed `{service.image}:{version}` on the Mac, and its health "
                    f"check passed (mqucifer/{repo}#{task}). This records the running tag. "
                    "The release's merge was the Sponsor's approval, so the crew merges "
                    "this itself once the guard checks pass (mqucifer/crew#829).",
                    BY,
                ),
            )
            sink.emit(
                CrewEvent(
                    kind=EventKind.DEPLOY_HEALTHY,
                    role=BY,
                    card=task,
                    summary=f"{repo} {version} deployed and healthy"[:100],
                    detail={**frm, **measures, "pr": pull.get("number")},
                )
            )
            _end(
                issues,
                board,
                sink,
                repo=repo,
                task=task,
                outcome="healthy",
                to=DONE,
                rule="decision.deploy.healthy",
                said=f"Deployed `{version}` over `{old}`; healthy after {checked.seconds}s. "
                f"The tag is recorded in {pull.get('html_url', 'mqucifer/infra')}.",
                log=redacted("\n\n".join(log), settings),
            )
            result.healthy.append((repo, version))
            return

        log.append(
            f"$ docker compose logs --tail {LOG_LINES} {service.service}\n"
            + docker.logs(compose, env_file, service.service)
        )
        # The rollback: the tag that was running, restarted and checked again.
        compose.write_text(bumped(text, service.image, old), encoding="utf-8")
        code, out = docker.compose(compose, env_file, "up", "-d", service.service)
        log.append(f"$ docker compose up -d {service.service}   # back to {old}\n{out}")
        back = check(service.health) if code == 0 else Checked(False, 0, 0.0, out[:200])
        log.append(
            f"health after the rollback: {back.attempts} attempts in {back.seconds}s, "
            f"last answer: {back.last or 'healthy'}"
        )
    shown = redacted("\n\n".join(log), settings)
    finding = _finding(
        issues,
        board,
        sink,
        repo=repo,
        version=version,
        task=task,
        stage=stage,
        old=old,
        restored=back.healthy,
        log=shown,
    )
    detail = {**frm, **measures, "stage": stage, "finding": finding}
    if back.healthy:
        sink.emit(
            CrewEvent(
                kind=EventKind.DEPLOY_ROLLED_BACK,
                role=BY,
                card=task,
                summary=f"{repo} {version} failed at {stage}; {old} runs again"[:100],
                detail=detail,
            )
        )
        _end(
            issues,
            board,
            sink,
            repo=repo,
            task=task,
            outcome="rolled_back",
            to=DONE,
            rule="decision.deploy.failed",
            said=f"`{version}` failed at {stage}, and `{old}` runs again, healthy."
            + (f" DevOps's finding is #{finding}." if finding else ""),
            log=shown,
        )
        result.rolled_back.append((repo, version, task))
        return
    sink.emit(
        CrewEvent(
            kind=EventKind.DEPLOY_ROLLBACK_FAILED,
            role=BY,
            card=task,
            summary=f"{repo} {version} failed and so did the rollback to {old}"[:100],
            detail=detail,
        )
    )
    artifacts.label(
        issues,
        sink,
        repo=repo,
        number=task,
        by=BY,
        add=[rt.NEEDS_HUMAN],
        rule="decision.deploy.failed",
    )
    _end(
        issues,
        board,
        sink,
        repo=repo,
        task=task,
        outcome="rollback_failed",
        to=BLOCKED,
        rule="decision.deploy.failed",
        said=f"**For the Sponsor:** `{version}` failed at {stage}, and the rollback to "
        f"`{old}` didn't come up healthy either, so `{service.name}` may be down. "
        f"Start it by hand from mqucifer/infra `{service.compose}` "
        f"(runbooks/the-stack.md)." + (f" DevOps's finding is #{finding}." if finding else ""),
        log=shown,
    )
    result.rollback_failed.append((repo, version, task))


def _end(
    issues: Any,
    board: Any,
    sink: EventSink,
    *,
    repo: str,
    task: int,
    outcome: str,
    to: str,
    rule: str,
    said: str,
    log: str,
) -> None:
    """Say how the deploy ended on its Task, then move it: Done, or Blocked for a person."""
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=task,
        body=f"{OUTCOME.format(outcome=outcome)}\n{said}\n\n"
        f"<details><summary>The deploy's log</summary>\n\n```\n{log[-20000:]}\n```\n</details>",
        by=BY,
    )
    card = next((c for c in board.cards() if c.repo == repo and c.number == task), None)
    if card is not None and card.status != to:
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=to,
            by=BY,
            card=task,
            frm=card.status,
            summary=f"deploy {outcome.replace('_', ' ')}",
            rule=rule,
        )
    if to == DONE:
        issues.close(repo, task, reason="completed" if outcome == "healthy" else "not_planned")


def _finding(
    issues: Any,
    board: Any,
    sink: EventSink,
    *,
    repo: str,
    version: str,
    task: int,
    stage: str,
    old: str,
    restored: bool,
    log: str,
) -> int | None:
    """DevOps's finding for a deploy that failed, with its log. None if it couldn't be filed."""
    key = f"deploy-{version}"
    try:
        filed = rt.open_task(
            issues,
            board,
            sink,
            repo=repo,
            role=rt.DEVOPS,
            kind=rt.FINDING,
            key=key,
            title=f"{repo} {version} failed its deploy at {stage}",
            body=rt.render(
                rt.DEVOPS,
                kind=rt.FINDING,
                key=key,
                why=f"The deploy of `{version}` (#{task}) failed at {stage}. "
                + (
                    f"It rolled back, and `{old}` runs again."
                    if restored
                    else f"The rollback to `{old}` failed too: the Sponsor has it."
                ),
                work="Find why the release didn't come up from its log, and fix it in "
                "the release that follows.\n\n"
                f"<details><summary>The deploy's log</summary>\n\n```\n{log[-20000:]}\n```\n"
                "</details>",
                done_when=["Triaged: the Business Analyst writes what done means."],
            ),
            points=None,
            cards=board.cards(),
            column=NEEDS_REFINEMENT,
            by=BY,
            event="deploy.failed",
            rule="decision.deploy.failed",
        ).number
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        sink.emit(
            CrewEvent(
                kind=EventKind.FINDING_FILED,
                role=BY,
                card=task,
                summary=f"devops finding not filed: {exc}"[:100],
                detail={"repo": repo, "owner": "devops", "filed": None, "error": str(exc)[:200]},
            )
        )
        return None
    sink.emit(
        CrewEvent(
            kind=EventKind.FINDING_FILED,
            role=BY,
            card=task,
            summary=f"devops finding filed as #{filed}"[:100],
            detail={"repo": repo, "owner": "devops", "filed": filed, "round": version},
        )
    )
    return filed


def merge_records(issues: Any, sink: EventSink, *, result: Deploys) -> None:
    """Merge the crew's deploy records in infra once its guard checks pass."""
    for pull in issues.open_pulls(INFRA):
        if not _RECORD_PR.search(pull.get("body") or ""):
            continue
        number = int(pull["number"])
        try:
            landed = land(issues, INFRA, number)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            sink.note(EventKind.NOTE, f"infra PR #{number} not merged yet: {exc}"[:160])
            continue
        if landed.how == Landing.MERGED:
            result.merged.append(number)
            sink.note(
                EventKind.NOTE,
                f"infra: merged the deploy record, PR #{number}",
                repo=INFRA,
                rule="decision.deploy.record",
            )
        elif landed.reason:
            sink.note(EventKind.NOTE, f"infra PR #{number} not merged yet: {landed.reason}"[:160])
