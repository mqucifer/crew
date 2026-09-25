"""The crew command line.

A tick runs to quiescence: it drains every actionable card until the board is
stable or reaches a human gate. The Sponsor controls when the process runs, not
the individual transitions within it.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from crew_org.config import load_org
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.tui import LiveView, attach


class CrewTyper(typer.Typer):
    """The crew's command line, which reports a dead model backend in one line (#153).

    Wherever it's found, in a pre-flight or mid-command, a backend that can't be
    reached says which side is down and stops, rather than a traceback from the
    depths of a model client.
    """

    def __call__(self, *args, **kwargs):
        from crew_org.llm import backend_down, base_url, down  # noqa: PLC0415

        try:
            return super().__call__(*args, **kwargs)
        except Exception as exc:
            if not backend_down(exc):
                raise
            Console(stderr=True, soft_wrap=True).print(
                f"[red]{escape(down(base_url(), str(exc)))}[/]"
            )
            raise SystemExit(1) from None


app = CrewTyper(help="An agile engineering organization run as agents.", no_args_is_help=True)
sprint_app = typer.Typer(help="Sprint cadence commands.", no_args_is_help=True)
app.add_typer(sprint_app, name="sprint")

console = Console()
VAR = Path("var")


def _sink(demo: bool) -> EventSink:
    """Where a run is recorded.

    Only the synthetic demo writes nowhere. A real run that left no log was
    invisible to `crew capability`, which reads exactly that log — so the mode
    the crew ran in by default was the one mode it could not see itself in.
    """
    return EventSink(None if demo else VAR / "events" / "tick.jsonl")


@app.command()
def tick(
    demo: bool = typer.Option(
        False, "--demo", help="Render the live view from synthetic events. Needs no model."
    ),
    passes: int = typer.Option(None, "--passes", help="Cap the number of passes."),
    repo: str = typer.Option(
        None, "--repo", help="Work only this repository. Defaults to every repo in delivery.repos."
    ),
) -> None:
    """Take the board as far as it can go: refine, admit, review, verify, land, deliver.

    Runs to quiescence — a pass that moves nothing ends it. It lands what it
    produces: there is no dry mode, because a rehearsal cost the same inference
    as the real thing and left nothing that could land. Undo by reverting.
    """
    org = load_org()
    sink = _sink(demo)
    view = LiveView(org["board"]["columns"], budget=org["sprint"]["escalation_budget"])

    if demo:
        with attach(sink, view):
            _synthetic_tick(sink)
        return

    # The proxy is project-scoped and will not always be running. Say so plainly
    # rather than surfacing a connection error from deep inside an agent.
    from crew_org.auth import REVIEW_APP_PREFIX, resolve_credentials
    from crew_org.config import load_env
    from crew_org.escalation import EscalationLedger, EscalationPolicy
    from crew_org.flows import loop
    from crew_org.git_ops import Workspace
    from crew_org.llm import health
    from crew_org.process import ProcessRules
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient
    from crew_org.tools.sandbox import Sandbox

    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)
    console.print(f"[dim]{message}[/]")

    sandbox = Sandbox.from_config(org)
    unavailable = sandbox.unavailable_reason()
    if unavailable:
        console.print(f"[red]Sandbox unavailable.[/] {unavailable}")
        raise typer.Exit(code=1)

    env = load_env()
    try:
        token, identity = resolve_credentials(env)
        review_token, review_identity = resolve_credentials(env, prefix=REVIEW_APP_PREFIX)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc

    owner = env["GITHUB_OWNER"]
    allowed = set(org.get("delivery", {}).get("repos") or [env.get("PILOT_REPO", "crew")])
    if repo and repo not in allowed:
        console.print(
            f"[red]{escape(repo)} is not in delivery.repos[/] "
            f"({', '.join(sorted(allowed))}). The crew does not work there."
        )
        raise typer.Exit(code=2)
    # Narrowed to one repository, or all of them. The default repo is what a
    # card falls back to when it names none, which is a different question.
    allowed = {repo} if repo else allowed
    default_repo = repo or env.get("PILOT_REPO", "crew")
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    sprint = board.schema.field("Sprint").current_iteration()
    if not sprint:
        console.print("[red]No iterations configured on the Sprint field.[/]")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]acting as {escape(identity)} · reviewing as {escape(review_identity)} · "
        f"{escape(sprint)} · "
        f"{escape(', '.join(sorted(allowed)))}[/]"
    )

    # Refinement reads the code it is deciding about, and delivery opens its
    # worktrees off the same clone.
    ws = Workspace(owner, default_repo, token, _bot_identity(token, identity))
    # A repository without a usable record isn't worked (#132).
    skipped = loop.not_onboarded(ws, allowed)
    for skipped_repo, why in skipped.items():
        console.print(f"[yellow]{escape(skipped_repo)} not worked:[/] {escape(why)}")
    allowed = allowed - set(skipped)

    crew = loop.Crew(
        board=board,
        issues=IssueClient(token, owner),
        sink=sink,
        ws=ws,
        sandbox=sandbox,
        rules=ProcessRules.from_config(org),
        policy=EscalationPolicy.from_config(org),
        ledger=EscalationLedger(VAR / "ledger" / "escalations.jsonl"),
        org=org,
        repo=default_repo,
        repos=allowed,
        sprint=sprint,
        capacity=org["sprint"]["capacity_points"],
        reviewer=IssueClient(review_token, owner),
        reviewer_login=review_identity,
        sponsor=env.get("GITHUB_SPONSOR") or None,
        not_onboarded=skipped,
    )

    with attach(sink, view):
        result = loop.run(crew, max_passes=passes or loop.MAX_PASSES)

    _render_tick(result)
    _take_standup(crew, result, crew_repo=env.get("CREW_REPO", "crew"), owner=owner)


def _take_standup(crew, result, *, crew_repo: str, owner: str) -> None:
    """Every tick ends with a standup (#79), recorded on the sprint's issue.

    A standup that cannot be recorded is reported and does not fail the tick:
    the work the tick did has already happened.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from crew_org.flows.standup import (  # noqa: PLC0415
        aging_blocked,
        awaiting_approval,
        record_standup,
        waiting_on_a_person,
        write_standup,
    )
    from crew_org.tools.github_project import within  # noqa: PLC0415

    now = datetime.now(UTC)
    try:
        cards = within(crew.board.cards(), crew.repos)
        standup = write_standup(
            result,
            sprint=crew.sprint,
            at=now,
            waiting=waiting_on_a_person(cards),
            aging=aging_blocked(cards, crew.rules, VAR / "events", now),
            awaiting=awaiting_approval(cards),
            not_onboarded=crew.not_onboarded,
        )
        number, commented = record_standup(
            crew.issues,
            crew.sink,
            standup,
            sprint=crew.sprint,
            crew_repo=crew_repo,
            delivery_repos=sorted(crew.repos),
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Standup not recorded:[/] {escape(str(exc))}")
        return
    url = f"https://github.com/{owner}/{crew_repo}/issues/{number}"
    if commented:
        console.print(f"[dim]Standup:[/] {url}")
    else:
        console.print(f"[dim]Standup: nothing moved, as the last one said — {url}[/]")


def _render_tick(result) -> None:
    """Report by phase, for the run.

    A Sponsor reading card-by-card is back in the work; a Sponsor told the board
    is stable while two cards are stuck is worse off than one told nothing.
    """
    from crew_org.flows import loop

    console.print()
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("phase")
    table.add_column("")
    table.add_column("did", ratio=1)
    for name, _run in loop.PHASES:
        outcome = result.last(name)
        if outcome is None:
            continue
        if outcome.error:
            table.add_row(name, "[red]failed[/]", escape(outcome.error))
            continue
        totals = result.totals(name)
        did = ", ".join(f"{v} {k}" for k, v in totals.items() if v) or "nothing"
        if result.moved_in(name):
            mark = "[green]moved[/]"
        elif result.held(name):
            mark = "[yellow]held[/]"
        else:
            mark = "[dim]quiet[/]"
        table.add_row(name, mark, escape(did))
    console.print(table)

    # What the run blocked, then what is still waiting. The first happened and
    # the second is still true; reporting only the second meant a card that
    # blocked mid-run vanished from the summary entirely.
    for name, _run in loop.PHASES:
        for line in result.blocked(name):
            console.print(f"  [red]{name} blocked[/] {escape(line)}")
    for name, _run in loop.PHASES:
        for line in result.held(name):
            console.print(f"  [yellow]{name}[/] {escape(line)}")

    # A column that is *over* its limit, as opposed to at it. The limits are
    # enforced on the way in, so this can only happen after a limit is lowered
    # or cards are moved by hand — and until now nothing said it had.
    for column, (count, limit) in sorted(result.over_limit.items()):
        console.print(
            f"  [red]WIP breach[/] {escape(column)}: {count} cards against a limit of {limit} "
            "— finish the oldest before starting more"
        )

    passes = f"{result.passes} pass" + ("es" if result.passes != 1 else "")
    if result.failed:
        console.print(
            f"\n[yellow]{passes}, {len(result.failed)} phase failures.[/] The board moved as "
            "far as the rest of the pass could take it."
        )
    elif not result.settled:
        console.print(
            f"\n[yellow]{passes} — stopped at the pass cap, not settled.[/] "
            "Something is still moving; run again, or raise --passes."
        )
    elif result.blocked_any:
        console.print(
            f"\n[yellow]{passes} — {result.blocked_count} card(s) blocked.[/] "
            "They need a person; the rest of the board is waiting on what is listed above."
        )
    elif result.stuck:
        console.print(
            f"\n[yellow]{passes} — nothing further can move.[/] "
            "The board is not finished; it is waiting on what is listed above."
        )
    elif not result.moved:
        console.print(f"\n[dim]{passes} — the board is stable. Nothing to do.[/]")
    else:
        console.print(f"\n[green]{passes} — the board is stable.[/]")


def _synthetic_tick(sink: EventSink) -> None:
    """A representative tick, used to exercise the view without inference.

    Deliberately includes a VERIFY failure that repairs locally and a CAPABILITY
    failure that escalates, so the escalation panel is exercised too.
    """

    def emit(kind: EventKind, summary: str, role: str | None = None, card: int | None = None, **d):
        sink.emit(CrewEvent(kind=kind, summary=summary, role=role, card=card, detail=d))
        time.sleep(0.28)

    emit(EventKind.TICK_STARTED, "draining board", tick=1, sprint="S1")

    emit(EventKind.CARD_CLAIMED, "Goal: sprint metrics CLI", "Product Owner", 1)
    emit(EventKind.AGENT_STARTED, "decompose goal into epics", "Product Owner", 1)
    emit(EventKind.LLM_CALL_STARTED, "crew-local", "Product Owner", 1)
    emit(EventKind.LLM_CALL_FINISHED, "crew-local  1,284 tok", "Product Owner", 1)
    emit(EventKind.AGENT_FINISHED, "proposed 4 epics", "Product Owner", 1)
    emit(
        EventKind.CARD_MOVED,
        "to Needs Refinement",
        "Product Owner",
        1,
        **{"from": "Inbox (Goals)", "to": "Needs Refinement"},
    )

    emit(EventKind.AGENT_STARTED, "split epic into stories", "Business Analyst", 2)
    emit(EventKind.AGENT_FINISHED, "3 stories, AC written", "Business Analyst", 2)
    emit(
        EventKind.CARD_MOVED,
        "to Ready",
        "Business Analyst",
        2,
        **{"from": "Needs Refinement", "to": "Ready"},
    )

    emit(EventKind.CARD_CLAIMED, "Story: cycle-time metric", "Developer", 7)
    emit(
        EventKind.CARD_MOVED,
        "to In Progress",
        "Developer",
        7,
        **{"from": "Sprint Backlog", "to": "In Progress"},
    )
    emit(EventKind.AGENT_STARTED, "implement cycle-time metric", "Developer", 7)
    emit(EventKind.TOOL_STARTED, "pytest", "Developer", 7)
    emit(EventKind.TOOL_FAILED, "2 failed", "Developer", 7)
    emit(EventKind.ESCALATION_DECIDED, "VERIFY attempt 1/2 — repairing locally", "Developer", 7)
    emit(EventKind.TOOL_STARTED, "pytest", "Developer", 7)
    emit(EventKind.TOOL_FINISHED, "18 passed", "Developer", 7)
    emit(EventKind.AGENT_FINISHED, "PR #31 opened", "Developer", 7)
    emit(
        EventKind.CARD_MOVED,
        "to QAing",
        "Developer",
        7,
        **{"from": "In Progress", "to": "QAing"},
    )

    emit(EventKind.AGENT_STARTED, "design pagination strategy", "Architect", 9)
    emit(EventKind.ESCALATION_DECIDED, "CAPABILITY — justified, 1/3 of budget", "Architect", 9)
    emit(
        EventKind.ESCALATED,
        "cross-cutting GraphQL pagination",
        "Architect",
        9,
        failure_class="CAPABILITY",
    )
    emit(EventKind.AGENT_FINISHED, "design note attached", "Architect", 9)

    emit(EventKind.CARD_BLOCKED, "awaiting Sponsor epic approval", "Scrum Master", 3)
    emit(EventKind.TICK_FINISHED, "board stable — 2 cards at human gate")
    time.sleep(0.8)


@app.command()
def capability() -> None:
    """What the crew can do: time in each column, what was exercised, and who stepped in.

    Read off what the crew actually did — its move log and the board's own
    history — never off how cards were tagged.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.events import replay_dir
    from crew_org.flows.board_audit import audit
    from crew_org.flows.capability import measure
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient

    env = load_env()
    token, _ = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))

    cards = board.cards()
    # Before the measure, whether the thing being measured is true. `measure`
    # reads columns to compute waits; a column holding finished work reports a
    # queue that is not a queue.
    _print_board_audit(audit(cards))
    events = replay_dir(VAR / "events")
    moves = _attributed_moves(board, IssueClient(token, owner), events, env)
    _print_measure(measure(events, cards, moves=moves))


@app.command()
def moves(
    people: bool = typer.Option(False, "--people", help="Only the moves a person made."),
    limit: int = typer.Option(40, "--limit", help="How many, most recent last."),
) -> None:
    """Every card movement on the board, and who made it.

    Read from GitHub's own record of each Status change, not the crew's log, so
    a card a person moved by hand is here too. Each move is attributed: the
    crew (it is in the crew's log), the platform (`board.yml` was running), or a
    person, named, and marked when they acted with the crew's credentials.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.events import replay_dir
    from crew_org.flows.board_moves import Source
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient

    env = load_env()
    token, _ = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    attributed = _attributed_moves(
        board, IssueClient(token, owner), replay_dir(VAR / "events"), env
    )
    if attributed is None:
        raise typer.Exit(code=1)
    if people:
        attributed = [a for a in attributed if a.source is Source.PERSON]

    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    for column in ("when", "card", "from", "to", "who"):
        table.add_column(column, no_wrap=True)
    colour = {Source.CREW: "dim", Source.PLATFORM: "cyan", Source.PERSON: "yellow"}
    for a in attributed[-limit:]:
        table.add_row(
            a.move.at.strftime("%m-%d %H:%M"),
            f"{a.move.repo}#{a.move.number}",
            a.move.frm or "—",
            a.move.to or "—",
            f"[{colour[a.source]}]{escape(a.who)}[/]",
        )
    console.print(table)


def _crew_logins(env: dict[str, str]) -> set[str]:
    """The logins GitHub records the crew's two Apps as, without `[bot]`."""
    from crew_org.auth import REVIEW_APP_PREFIX, resolve_credentials  # noqa: PLC0415

    logins = set()
    for prefix in ("GITHUB_APP_", REVIEW_APP_PREFIX):
        try:
            logins.add(resolve_credentials(env, prefix=prefix)[1].removesuffix("[bot]"))
        except Exception:  # noqa: BLE001, S112
            continue
    return logins


def _attributed_moves(board, issues, events, env):
    """Every Status change on the board, attributed. None if it cannot be read.

    None rather than an empty list, so the measure falls back to what the crew
    declared and says it is a floor, instead of reporting nobody intervened.
    """
    from crew_org.flows.board_moves import BOARD_WORKFLOW, attribute, run_windows  # noqa: PLC0415

    try:
        moves = board.status_history()
        runs = [
            window
            for repo in sorted({m.repo for m in moves})
            for window in run_windows(issues.workflow_runs(repo, BOARD_WORKFLOW), repo)
        ]
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Could not read the board's history:[/] {exc}")
        return None
    return attribute(moves, events, runs, crew_logins=_crew_logins(env))


def _print_board_audit(a) -> None:
    """What the board says about itself, and contradicts.

    Printed above the measure rather than below it: these are not findings to
    act on later, they are a reason to distrust the numbers underneath.
    """
    if a.ok:
        return
    console.print()
    if a.finished_but_waiting:
        names = ", ".join(c.name(qualify=True) for c in a.finished_but_waiting[:8])
        more = (
            f" and {len(a.finished_but_waiting) - 8} more"
            if len(a.finished_but_waiting) > 8
            else ""
        )
        console.print(
            f"[red]{len(a.finished_but_waiting)} closed cards are not in Done[/] — "
            f"{escape(names)}{more}.\n"
            "[dim]Those columns read as occupied. Run the board workflow's sweep "
            "(Actions → board → Run workflow).[/]"
        )
    if a.waiting_but_finished:
        names = ", ".join(c.name(qualify=True) for c in a.waiting_but_finished)
        console.print(
            f"[yellow]{len(a.waiting_but_finished)} open cards sit in Done[/] — "
            f"{escape(names)}.\n"
            "[dim]Reopening an issue moves nothing, so no workflow catches this.[/]"
        )


def _humanise(delta) -> str:
    """A duration a person can compare at a glance, not to the second."""
    if delta is None:
        return "—"
    seconds = delta.total_seconds()
    if seconds >= 86400:
        return f"{seconds / 86400:.1f}d"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.0f}m"
    return f"{seconds:.0f}s"


def _print_measure(m) -> None:
    """The other half: what the crew can do, read off the move log.

    Printed beneath the ledger deliberately. Spend and ability answer different
    questions and neither is legible alone — five Implementation cards means
    nothing until you know whether Implementation is fast or stuck.
    """
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("column")
    table.add_column("capability")
    table.add_column("median", justify="right")
    table.add_column("worst", justify="right")
    table.add_column("n", justify="right")
    table.add_column("")

    for stat in m.columns:
        if not stat.exercised:
            # Distinct from a phase that runs and fails. "No cards" could mean
            # absent, working but uncarded, or never tried; this says which.
            table.add_row(stat.column, stat.capability, "", "", "", "[yellow]never exercised[/]")
            continue
        waiting = f"[yellow]{stat.still_waiting} still waiting[/]" if stat.still_waiting else ""
        table.add_row(
            stat.column,
            stat.capability,
            _humanise(stat.median),
            _humanise(stat.worst),
            str(len(stat.waits)),
            waiting,
        )

    console.print()
    console.print(table)

    if m.intervention and m.counted:
        console.print(
            "[yellow]Needed a person:[/] "
            + ", ".join(f"{cap} ×{n}" for cap, n in sorted(m.intervention.items()))
            + " [dim]— cards a person moved, by: "
            + ", ".join(f"{who} ×{n}" for who, n in sorted(m.intervened_by.items()))
            + "[/]"
        )
    elif m.intervention:
        console.print(
            "[yellow]Needed a person:[/] "
            + ", ".join(f"{cap} ×{n}" for cap, n in sorted(m.intervention.items()))
            + " [dim]— a floor, not a count: the board's history could not be read.[/]"
        )
    elif m.counted:
        console.print("[green]Needed a person:[/] none [dim]— no card was moved by hand.[/]")
    if m.counted and m.asked:
        console.print(
            "[dim]Asked for a person:[/] "
            + ", ".join(f"{cap} ×{n}" for cap, n in sorted(m.asked.items()))
            + " [dim]— blocked, or flagged needs:human.[/]"
        )
    if m.rework:
        console.print(
            "[yellow]Sent back by a gate:[/] "
            + ", ".join(f"{cap} ×{n}" for cap, n in sorted(m.rework.items()))
            + " [dim]— charged to what produced the work, not what caught it.[/]"
        )

    # The window is stated because a median without one is unreadable, and
    # because events before the board's current shape are not counted at all.
    window = f"{m.window_days:.1f} days"
    excluded = (
        f", {m.excluded} earlier events not counted — they predate this board" if m.excluded else ""
    )
    console.print(f"[dim]What the crew can do, over {window}{excluded}.[/]")


# The alias a tick's thinking roles use. Probing an alias rather than whatever
# the proxy lists first makes the doctor test what the crew runs.
DOCTOR_MODEL = "crew-local"


@app.command()
def doctor(
    base_url: str = typer.Option(
        None, "--base-url", help="Defaults to CREW_LLM_BASE_URL, the LiteLLM proxy."
    ),
    model: str = typer.Option(
        DOCTOR_MODEL, "--model", help="The proxy alias to probe. Defaults to crew-local."
    ),
    deep: bool = typer.Option(
        False, "--deep", help="Also run a real CrewAI crew end to end. Costs tokens."
    ),
) -> None:
    """Validate the inference path the crew uses before anything is built on it.

    Probes through the LiteLLM proxy, with its key, exactly as a tick calls the
    model: tool calling, constrained JSON, context length and thinking control.
    Everything goes through the proxy; the doctor used to go around it, and so
    checked a path no agent takes.
    """
    from crew_org.llm import base_url as proxy_url
    from crew_org.substrate import Status, run_all

    base_url = base_url or proxy_url()
    console.print(f"[dim]Probing {base_url} as {model}…[/]")
    results = run_all(base_url, model, deep=deep)

    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("check")
    table.add_column("")
    table.add_column("detail", ratio=1)
    marks = {
        Status.PASS: ("[green]pass[/]", ""),
        Status.FAIL: ("[red]FAIL[/]", "red"),
        Status.WARN: ("[yellow]warn[/]", "yellow"),
        Status.SKIP: ("[dim]skip[/]", "dim"),
    }
    for r in results:
        mark, style = marks[r.status]
        table.add_row(r.check, mark, Text(r.detail, style=style or ""))
    console.print(table)

    for r in results:
        if r.hint:
            console.print(f"[yellow]→[/] [bold]{r.check}:[/] {r.hint}")

    failed = [r for r in results if r.status is Status.FAIL]
    if failed:
        console.print(
            "\n[red]Phase 0 gate not passed.[/] Build nothing on the substrate until "
            "these clear — a failure here shows up later as unexplained agent failures."
        )
        raise typer.Exit(code=1)
    if any(r.status is Status.WARN for r in results):
        console.print("\n[yellow]Phase 0 gate passed with warnings.[/]")
        return
    console.print("\n[green]Phase 0 gate passed.[/] Substrate is sound.")


@app.command()
def auth() -> None:
    """Verify the credential the agents use — including what it must NOT do."""
    from crew_org.auth import REVIEW_APP_PREFIX, app_permissions, resolve_credentials, verify
    from crew_org.auth import Status as AuthStatus
    from crew_org.config import load_env

    env = load_env()
    try:
        token, identity = resolve_credentials(env)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc
    console.print(f"[dim]identity: {escape(identity)}[/]")

    owner = env.get("GITHUB_OWNER")
    if not owner:
        console.print("[red]GITHUB_OWNER is not set in .env.[/] Run scripts/bootstrap_github.sh.")
        raise typer.Exit(code=2)

    repos = [r for r in (env.get("CREW_REPO"), env.get("PILOT_REPO")) if r]
    checks = verify(
        token,
        owner=owner,
        repos=repos,
        project_number=int(env.get("GITHUB_PROJECT_NUMBER", 0)),
        owner_is_org=env.get("GITHUB_OWNER_TYPE", "organization") == "organization",
        app_slug=identity if identity != "personal access token" else None,
        app_permissions=app_permissions(),
    )

    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("check")
    table.add_column("")
    table.add_column("detail", ratio=1)
    marks = {
        AuthStatus.PASS: "[green]pass[/]",
        AuthStatus.FAIL: "[red]FAIL[/]",
        AuthStatus.WARN: "[yellow]warn[/]",
        AuthStatus.SKIP: "[dim]skip[/]",
    }
    for c in checks:
        table.add_row(c.check, marks[c.status], escape(c.detail))
    console.print(table)

    for c in checks:
        if c.hint:
            console.print(f"[yellow]→[/] [bold]{c.check}:[/] {escape(c.hint)}")

    # The reviewing identity is checked for one thing only: that it is somebody
    # else. An app cannot approve a pull request it opened, so a reviewer that
    # resolves to the delivery app leaves every story in Merging.
    try:
        review_token, review_identity = resolve_credentials(env, prefix=REVIEW_APP_PREFIX)
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n[red]reviewing identity: {escape(str(exc))}[/]")
        raise typer.Exit(code=1) from exc
    grants = app_permissions()
    if review_identity == identity:
        console.print(
            f"\n[yellow]reviewing identity: {escape(review_identity)} — the same app that "
            f"opens the pull requests.[/] It can comment but never approve, so approved work "
            f"waits on a person. Set {REVIEW_APP_PREFIX}ID and {REVIEW_APP_PREFIX}PRIVATE_KEY."
        )
    elif grants.get("pull_requests") != "write":
        console.print(
            f"\n[yellow]reviewing identity: {escape(review_identity)} — no write on pull "
            f"requests[/] (has {escape(str(grants.get('pull_requests') or 'none'))}), "
            "so it cannot post an approving review."
        )
    else:
        # What was verified, and nothing more. This line used to read "can
        # approve" on the strength of two facts — a distinct identity holding
        # pull_requests: write — that are both true and neither sufficient.
        # GitHub only counts an approval from an actor with repository write
        # access, so the app's reviews were recorded and ignored, and two cards
        # sat in Merging looking merely slow. Whether an approval counts is
        # answered per pull request by `reviewDecision`, not by a grant set.
        console.print(
            f"\n[green]reviewing identity: {escape(review_identity)}[/] — a separate identity "
            "with write on pull requests, so it can submit a review."
        )
        if grants.get("contents") != "write":
            console.print(
                "[yellow]→[/] [bold]reviewing identity:[/] it has no write on contents, so "
                "branch protection may record its approval without counting it. "
                "`crew tick` reports any pull request where that happens; granting "
                "contents: write would likely fix it and would also let the app push."
            )
    del review_token

    if any(c.status is AuthStatus.FAIL for c in checks):
        console.print("\n[red]Token is not fit for the crew.[/]")
        raise typer.Exit(code=1)
    if any(c.status is AuthStatus.WARN for c in checks):
        console.print("\n[yellow]Token usable, with warnings.[/]")
        return
    console.print("\n[green]Token is correctly scoped.[/]")


@app.command()
def qa(
    repo: str = typer.Option(None, "--repo", help="Defaults to the pilot repo."),
) -> None:
    """Verify delivered work against its acceptance criteria.

    A card is verified in its own repository; `--repo` only changes what a card
    that names none falls back to.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env, load_org
    from crew_org.flows.acceptance import close_finished_parents, run_qa
    from crew_org.git_ops import Workspace
    from crew_org.llm import health
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient
    from crew_org.tools.sandbox import Sandbox

    org, env = load_org(), load_env()
    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)

    box = Sandbox.from_config(org)
    blocked = box.unavailable_reason()
    if blocked:
        console.print(f"[red]Sandbox unavailable.[/] {blocked}")
        raise typer.Exit(code=1)

    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    repo = repo or env.get("PILOT_REPO", "crew")
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    issues = IssueClient(token, owner)
    ws = Workspace(owner, repo, token, _bot_identity(token, identity))

    sink = EventSink(VAR / "events" / "qa.jsonl")
    repos = set(org.get("delivery", {}).get("repos") or []) or None
    cards = board.cards()
    result = run_qa(board, issues, sink, ws, box, cards=cards, repo=repo, repos=repos)
    result.parents_closed = close_finished_parents(
        board, issues, sink, board.cards(), repo=repo, repos=repos
    )

    console.print()
    for outcome in result.verified:
        console.print(f"[green]#{outcome.card}[/] accepted")
    for outcome in result.returned:
        console.print(f"[yellow]#{outcome.card}[/] returned — {outcome.unproven} criteria unproven")
    for number, why in result.failed:
        console.print(f"[red]#{number}[/] {why}")
    for number, why in result.skipped:
        console.print(f"[dim]#{number}[/] {why}")
    for number in result.parents_closed:
        console.print(f"[green]#{number}[/] closed — all children done")
    if not (result.verified or result.returned or result.failed or result.skipped):
        console.print("[dim]Nothing in QAing.[/]")


@app.command()
def revert(
    pull: int = typer.Argument(..., help="The merged pull request to undo."),
    reason: str = typer.Option(..., "--reason", help="Why. Recorded on the card and the PR."),
    repo: str = typer.Option(None, "--repo", help="Defaults to the pilot repo."),
) -> None:
    """Open a pull request that undoes a merged one.

    Nothing lands here. The revert is reviewed like any other change and merged
    by the deliver phase once approved; when it lands, the card the work
    belonged to returns to Needs Refinement. A revert that does not apply
    cleanly is never forced: the card is blocked with the conflict named.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.flows.revert import request_revert
    from crew_org.git_ops import Workspace
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient

    env = load_env()
    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    repo = repo or env.get("PILOT_REPO", "crew")
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    issues = IssueClient(token, owner)
    ws = Workspace(owner, repo, token, _bot_identity(token, identity))

    sink = EventSink(VAR / "events" / "revert.jsonl")
    result = request_revert(
        board, issues, sink, ws, cards=board.cards(), repo=repo, pull_number=pull, reason=reason
    )

    card = f" — card {result.card.name(qualify=True)}" if result.card else " — no card"
    if result.pr is not None:
        console.print(f"[green]PR #{result.pr}[/] reverts PR #{pull}{card}")
        console.print("[dim]It lands through review and the deliver phase, like any change.[/]")
    elif result.conflict:
        console.print(f"[red]Not reverted[/] — PR #{pull} conflicts with main{card}")
        for path in result.conflict:
            console.print(f"  {path}")
        if result.card:
            console.print("[dim]The card is blocked for a person, with the conflict named.[/]")
        raise typer.Exit(code=1)
    else:
        console.print(f"[red]Not reverted[/] — {result.refused}")
        raise typer.Exit(code=1)


@app.command()
def review(
    repo: str = typer.Option(None, "--repo", help="Defaults to the pilot repo."),
) -> None:
    """Review every open pull request that has no crew verdict yet.

    Human-authored pull requests are reviewed on the same terms as the crew's.
    """
    from crew_org.auth import REVIEW_APP_PREFIX, resolve_credentials
    from crew_org.config import load_env
    from crew_org.flows.review import review_open_pulls
    from crew_org.llm import health
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient

    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)

    env = load_env()
    # The reviewing app, not the delivery one: GitHub will not accept an
    # approval from the identity that opened the pull request.
    token, identity = resolve_credentials(env, prefix=REVIEW_APP_PREFIX)
    owner = env["GITHUB_OWNER"]
    repo = repo or env.get("PILOT_REPO", "crew")

    console.print(f"[dim]acting as {escape(identity)} · reviewing {owner}/{repo}[/]")
    sink = EventSink(VAR / "events" / "review.jsonl")
    # The board moves with the verdict: a card leaves Reviewing for QAing or
    # goes back to In Progress. A pull request with no card still gets reviewed.
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    result = review_open_pulls(
        IssueClient(token, owner),
        sink,
        repo=repo,
        bot_login=identity,
        board=board,
        cards=board.cards(),
    )

    console.print()
    for outcome in result.reviewed:
        if outcome.event == "APPROVE":
            mark = "[green]approved[/]"
        elif outcome.event == "REQUEST_CHANGES":
            mark = "[yellow]changes requested[/]"
        else:
            mark = "[yellow]commented — a reviewer cannot approve its own pull request[/]"
        console.print(f"PR #{outcome.pr} — {mark}, {outcome.findings} findings")
    for outcome in result.skipped:
        console.print(f"[dim]PR #{outcome.pr} — skipped ({outcome.skipped})[/]")
    for number, why in result.failed:
        console.print(f"[red]PR #{number}[/] — {why}")
    if not (result.reviewed or result.skipped or result.failed):
        console.print("[dim]No open pull requests.[/]")


@app.command()
def deliver(
    limit: int = typer.Option(1, "--limit", help="How many stories to attempt."),
    sprint: str = typer.Option(None, "--sprint", help="Iteration name."),
) -> None:
    """Take sprint stories to a pull request.

    The work is implemented and verified in the sandbox, then committed, pushed
    and opened. There is no dry mode — the pull request is where a diff is
    reviewed before it lands, and writing one to `var/diffs/` instead was a
    substitute for using that gate.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.escalation import EscalationLedger, EscalationPolicy
    from crew_org.flows.delivery import deliver as run_delivery
    from crew_org.git_ops import Workspace
    from crew_org.llm import health
    from crew_org.process import ProcessRules
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient
    from crew_org.tools.sandbox import Sandbox

    org, env = load_org(), load_env()

    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)

    box = Sandbox.from_config(org)
    blocked = box.unavailable_reason()
    if blocked:
        console.print(f"[red]Sandbox unavailable.[/] {blocked}")
        raise typer.Exit(code=1)

    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    repo = env.get("PILOT_REPO", "crew")
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    issues = IssueClient(token, owner)
    sprint = sprint or board.schema.field("Sprint").current_iteration()

    bot = _bot_identity(token, identity)
    ws = Workspace(owner, repo, token, bot)

    console.print(f"[dim]acting as {escape(identity)} · sandbox {box.mode} · {sprint}[/]")

    sink = EventSink(VAR / "events" / "deliver.jsonl")
    view = LiveView(org["board"]["columns"], budget=org["sprint"]["escalation_budget"])
    with attach(sink, view):
        result = run_delivery(
            board,
            issues,
            sink,
            ProcessRules.from_config(org),
            EscalationPolicy.from_config(org),
            EscalationLedger(VAR / "ledger" / "escalations.jsonl"),
            ws,
            sprint=sprint,
            repo=repo,
            limit=limit,
            repos=set(org.get("delivery", {}).get("repos") or [repo]),
        )

    console.print()
    for number in result.reworked:
        console.print(f"[cyan]#{number}[/] re-worked — the reviewer's findings, answered")
    for number in result.landed:
        console.print(f"[green]#{number}[/] merged and done")
    for number in result.conflicted:
        console.print(f"[red]#{number}[/] merge conflict — blocked, needs a person")
    for number, pull in result.awaiting_approval:
        console.print(f"[yellow]#{number}[/] not merged — PR #{pull} has no approving review")
    for number, pull in result.unapprovable:
        console.print(
            f"[red]#{number}[/] not merged — PR #{pull} is approved and GitHub still "
            "requires a review; no tick can satisfy that gate"
        )
    for number, why in result.unmergeable:
        console.print(f"[red]#{number}[/] not merged — {why}")
    for number, blocker in result.waiting_on_a_sibling:
        console.print(f"[dim]#{number}[/] waits for #{blocker} in the same epic")
    for outcome in result.delivered:
        if outcome.landed:
            console.print(f"[green]#{outcome.card}[/] → PR #{outcome.pr} on `{outcome.branch}`")
        else:
            path = VAR / "diffs" / f"{outcome.card}.diff"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(outcome.diff or "")
            lines = len((outcome.diff or "").splitlines())
            console.print(
                f"[green]#{outcome.card}[/] verified — {lines} diff lines, nothing landed\n"
                f"   [dim]{path}[/]"
            )
    for outcome in result.blocked:
        console.print(f"[red]#{outcome.card}[/] {outcome.blocked_reason}")
        for suffix, content in (
            ("rejected.diff", outcome.rejected_diff),
            ("failure.txt", outcome.failure_detail),
        ):
            if not content:
                continue
            path = VAR / "diffs" / f"{outcome.card}.{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            console.print(f"   [dim]{path}[/]")
    if result.not_ours:
        console.print(
            "[dim]not the crew's to build: "
            + ", ".join(f"#{n}" for n in result.not_ours)
            + " — tracked on the board, outside delivery.repos[/]"
        )
    if result.rate_limited:
        console.print(
            "\n[yellow]Stopped on a subscription usage limit.[/] Resume with another run."
        )
    if not result.delivered and not result.blocked:
        console.print("[dim]Nothing in Sprint Backlog for this sprint.[/]")


@app.command()
def onboard(
    repo: str = typer.Argument(..., help="The project's repository, in GITHUB_OWNER."),
    from_file: str = typer.Option(
        None, "--from", help="A take-away file to carry on from, finished or not."
    ),
    terminal: bool = typer.Option(
        False, "--terminal", help="Interview in the terminal instead of a local page."
    ),
) -> None:
    """Interview the Sponsor about a project and propose its record (#130).

    The Product Owner reads the project, if there is anything to read, and
    proposes answers; otherwise it asks. The interview runs in a local page
    (#138), with a field for each question and the record beside it, or in the
    terminal with --terminal. Stop at any point with 'later' and the answers
    so far are written to a file you can finish in any editor. The finished
    record arrives as a pull request to the project.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.crews.onboarding_crew import interview_turn
    from crew_org.flows.onboard import (
        interview,
        open_record_pr,
        read_project,
        takeaway,
        terminal_ask,
    )
    from crew_org.git_ops import Workspace
    from crew_org.llm import health
    from crew_org.permissions import Capability, Permissions
    from crew_org.project import ProjectRecordError, load_raw, validate
    from crew_org.tools.github_issues import IssueClient

    Permissions.from_agents().require("Product Owner", Capability.ONBOARD_PROJECT)
    env = load_env()
    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)

    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    issues = IssueClient(token, owner)
    ws = Workspace(owner, repo, token, _bot_identity(token, identity))

    source = Path(from_file) if from_file else None
    project = read_project(ws, issues, repo)
    raw = project.existing or {}
    if source:
        try:
            raw = load_raw(source.read_text())
        except (OSError, ProjectRecordError) as exc:
            console.print(f"[red]{escape(str(exc))}[/]")
            raise typer.Exit(code=1) from None
    kept = source or VAR / "onboarding" / f"{repo}.yaml"

    if not project.repository:
        console.print(
            f"[dim]{owner}/{repo} has nothing to read yet; the Product Owner will ask.[/]"
        )
    elif project.existing and not source:
        console.print(f"[dim]{owner}/{repo} already has a record; starting from it.[/]")

    def tell(text: str) -> None:
        console.print(f"\n[cyan]Product Owner[/]\n{escape(text)}")

    def turn(**context):
        with console.status("[dim]The Product Owner is thinking…[/]"):
            return interview_turn(**context)

    session = server = None
    if terminal:
        hands = {"turn": turn, "ask": terminal_ask, "tell": tell}
    else:
        import webbrowser

        from crew_org.flows.onboard_page import Session, serve, thinking_turn

        session = Session(repo)
        server, url = serve(session)
        console.print(
            f"\nThe interview is at [bold]{url}[/]\n"
            "[dim]Answer there. Ctrl-C here ends it and keeps the answers so far.[/]"
        )
        webbrowser.open(url)
        hands = {
            "turn": thinking_turn(session, interview_turn),
            "ask": session.ask,
            "tell": session.tell,
            "show": session.show,
        }

    ended = interview(raw, repository=project.repository, intent=project.intent, **hands)

    def finish(kind: str, text: str, link: str = "") -> None:
        """Tell the page how it ended, and give it a moment to say so before stopping."""
        if session is None or server is None:
            return
        session.finish(kind, text, link)
        session.wait_until_seen(timeout=10)
        server.shutdown()

    # Kept however the interview ends: what was said is the evidence for the
    # record, and the only way to see why it came out as it did.
    log = VAR / "onboarding" / f"{repo}.transcript.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        stamp = time.strftime("%Y-%m-%d %H:%M")
        fh.write(f"\n## {stamp}\n\n" + "\n\n".join(ended.transcript) + "\n")

    if ended.settled and project.default_branch:
        url = open_record_pr(
            ws,
            issues,
            repo,
            validate(ended.raw),
            base=project.default_branch,
            transcript=ended.transcript,
        )
        console.print(f"\n[green]Record proposed[/] — {url}\n[dim]transcript: {log}[/]")
        finish("proposed", "The record is proposed as a pull request:", url)
        return

    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(
        takeaway(ended.raw, ended.questions, repo=repo, path=kept, proposed=ended.proposed)
    )
    if ended.interrupted:
        console.print(f"\n[red]The interview stopped:[/] {escape(ended.interrupted)}")
    if ended.settled:
        console.print(
            f"\n[yellow]{owner}/{repo} has no commits,[/] so there is no branch to propose "
            "the record against. Push a first commit (a README will do), then run:"
        )
    else:
        console.print("\n[yellow]Not finished.[/] The answers so far, and what is left, are in:")
    console.print(f"   {kept}\n   [dim]crew onboard {repo} --from {kept}[/]")
    console.print(f"[dim]transcript: {log}[/]")
    if ended.interrupted:
        finish(
            "stopped",
            f"The interview stopped: {ended.interrupted}. The answers so far are in {kept}.",
        )
    elif ended.settled:
        finish(
            "kept",
            f"The record is finished and kept in {kept}: the repository has no commits "
            "to propose it against yet.",
        )
    else:
        finish(
            "kept",
            f"Not finished. The answers so far, and what is left, are in {kept}. "
            f"Carry on with: crew onboard {repo} --from {kept}",
        )


@app.command()
def design(
    repo: str = typer.Argument(..., help="The project's repository, in GITHUB_OWNER."),
    reason: str = typer.Option(
        "", "--reason", help="Why an existing design is being revisited. Required if it has one."
    ),
) -> None:
    """The project's Architect proposes its design section, as a pull request (#144).

    Language, dependencies, sandbox needs, the commands that enforce done, and
    how a release happens, each with what it is based on. The Code Reviewer
    checks it against the crew-wide guidelines and the project's own before
    anything is opened. The project must be onboarded first.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.crews.design_crew import propose_design, review_design
    from crew_org.flows.design import design as run_design
    from crew_org.flows.design import open_design_pr, workflows
    from crew_org.flows.onboard import describe
    from crew_org.git_ops import Workspace
    from crew_org.llm import health
    from crew_org.permissions import Capability, Permissions
    from crew_org.project import ProjectRecordError, read_record
    from crew_org.tools.github_issues import IssueClient

    Permissions.from_agents().require("Architect", Capability.PROPOSE_PROJECT_DESIGN)
    env = load_env()
    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)

    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    issues = IssueClient(token, owner)
    ws = Workspace(owner, repo, token, _bot_identity(token, identity))

    if not issues.branches(repo):
        console.print(f"[red]{owner}/{repo} has no commits yet, so nothing to design against.[/]")
        raise typer.Exit(code=1)
    path = ws.current()
    branch = issues.repository(repo)["default_branch"]
    try:
        record = read_record(path)
    except ProjectRecordError as exc:
        console.print(f"[red]{owner}/{repo}'s record can't be read:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from None
    if record is None:
        console.print(
            f"[red]{owner}/{repo} isn't onboarded.[/] Its design works within its record, "
            f"so run [bold]crew onboard {repo}[/] first."
        )
        raise typer.Exit(code=1)
    if record.design and not reason:
        console.print(
            f"[red]{owner}/{repo} already has a design.[/] Say why it is being revisited "
            "with --reason, so the Architect changes only what that calls for."
        )
        raise typer.Exit(code=1)

    repository = describe(path, branch=branch, protection=issues.branch_protection(repo, branch))
    with console.status("[dim]The Architect is designing, and the Code Reviewer checking…[/]"):
        designed = run_design(
            record,
            repository=repository,
            ci=workflows(path),
            propose_design=propose_design,
            review_design=review_design,
            reason=reason,
        )

    if designed.record is None:
        console.print(f"\n[red]Refused after {designed.attempts} proposals.[/] No pull request:")
        for why in designed.refused:
            console.print(f"  - {escape(why)}")
        raise typer.Exit(code=1)
    url = open_design_pr(ws, issues, repo, designed, base=branch, reason=reason)
    console.print(f"\n[green]Design proposed[/] — {url}")


@app.command()
def export(
    repo: str = typer.Argument(..., help="The delivery repository whose sprint to export."),
    sprint: str = typer.Option(None, "--sprint", help="Iteration name. Defaults to the current."),
    out: str = typer.Option(None, "--out", help="Where to write it. Defaults to var/exports/."),
) -> None:
    """Export a sprint's stories and their attempts, for sprint-metrics to read (#157).

    Which stories landed on their first attempt, what the others took (each
    retry's failure class, role and first error line), the causes across them,
    and the sprint's escalations from the ledger. Nothing is sent anywhere: it
    writes one JSON file.
    """
    import json

    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.escalation import EscalationLedger
    from crew_org.flows.attempts import read_attempts, retries_text, sprint_report
    from crew_org.tools.github_project import ProjectClient

    env = load_env()
    token, _ = resolve_credentials(env)
    board = ProjectClient(token, env["GITHUB_OWNER"], int(env["GITHUB_PROJECT_NUMBER"]))
    sprint = sprint or board.schema.field("Sprint").current_iteration()
    stories = [
        c for c in board.cards() if c.sprint == sprint and c.work_type == "Story" and c.repo == repo
    ]
    report = sprint_report(
        sprint,
        stories,
        read_attempts(VAR / "events"),
        EscalationLedger(VAR / "ledger" / "escalations.jsonl").spent(sprint),
    )
    path = Path(out) if out else VAR / "exports" / f"{repo}-{sprint.replace(' ', '-')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    console.print(escape(retries_text(report)))
    console.print(f"[dim]{report['escalations']} escalations · written to {path}[/]")


def _bot_identity(token: str, identity: str):
    """Resolve the bot's numeric id, which GitHub needs for commit attribution."""
    import httpx

    from crew_org.git_ops import BotIdentity

    login = identity
    response = httpx.get(
        f"https://api.github.com/users/{login}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    return BotIdentity(login=login, user_id=response.json()["id"])


@sprint_app.command("start")
def sprint_start(
    sprint: str = typer.Option(
        None, "--sprint", help="Iteration name. Defaults to the current one."
    ),
    capacity: int = typer.Option(None, "--capacity", help="Points. Defaults to org.yaml."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan without admitting it."),
) -> None:
    """Fill the sprint from approved epics.

    Approving an epic was the scope decision, so this is mechanical: stories are
    pulled in priority order until capacity is reached.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env, load_org
    from crew_org.events import EventSink
    from crew_org.flows.sprint import plan_sprint, start_sprint
    from crew_org.process import ProcessRules
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient

    org, env = load_org(), load_env()
    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    issues = IssueClient(token, owner)

    sprint = sprint or board.schema.field("Sprint").current_iteration()
    if not sprint:
        console.print("[red]No iterations configured on the Sprint field.[/]")
        raise typer.Exit(code=1)
    capacity = capacity or org["sprint"]["capacity_points"]

    sink = EventSink(None)
    rules = ProcessRules.from_config(org)
    repo = env.get("PILOT_REPO", "crew")

    if dry_run:
        cards = board.cards()
        parents = {}
        from crew_org.flows.sprint import approved_epics

        for epic in approved_epics(cards):
            for child in issues.sub_issues(epic.repo or repo, epic.number or 0):
                parents[child["number"]] = epic.number
        plan = plan_sprint(cards, parents, sprint=sprint, capacity=capacity)
    else:
        plan = start_sprint(
            board,
            issues,
            sink,
            rules,
            sprint=sprint,
            capacity=capacity,
            default_repo=repo,
            repos=set(org.get("delivery", {}).get("repos") or [repo]),
        )

    _render_plan(plan, dry_run=dry_run)


def _render_plan(plan, *, dry_run: bool) -> None:
    """Report at the epic level. A Sponsor reading story-by-story is back in the work."""
    verb = "would admit" if dry_run else "admitted"
    console.print()
    table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("epic")
    table.add_column("stories", justify="right")
    table.add_column("points", justify="right")
    table.add_column("")
    for piece in plan.slices:
        table.add_row(
            piece.name[:50],
            str(len(piece.admitted))
            + (f" of {len(piece.admitted) + len(piece.deferred)}" if piece.deferred else ""),
            str(piece.points),
            "[green]complete[/]" if piece.complete else "[yellow]partial[/]",
        )
    console.print(table)
    console.print(
        f"[bold]{plan.sprint}[/] — {verb} {len(plan.admitted)} stories, "
        f"{plan.points} of {plan.capacity} points"
    )
    partial = [p for p in plan.slices if not p.complete]
    if partial:
        console.print(
            "[dim]Deferred to the next sprint: "
            + ", ".join(f"{p.name} ({len(p.deferred)} stories)" for p in partial)
            + "[/]"
        )
    if plan.not_ours:
        console.print(
            f"[dim]Not admitted[/] — {len(plan.not_ours)} ready stories are outside "
            "the crew's repositories: "
            + ", ".join(c.name(qualify=True) for c in plan.not_ours)
            + " [dim]— real work, and not the crew's to deliver.[/]"
        )
    if plan.unestimated:
        from crew_org.tools.github_project import many_repos  # noqa: PLC0415

        qualify = many_repos(plan.unestimated)
        console.print(
            f"[yellow]Not admitted[/] — {len(plan.unestimated)} stories with no epic have "
            "no estimate: "
            + ", ".join(c.name(qualify=qualify) for c in plan.unestimated)
            + " [dim]— set Points to plan them.[/]"
        )


@sprint_app.command("close")
def sprint_close(
    sprint: str = typer.Option(None, "--sprint", help="Iteration name."),
    no_merge: bool = typer.Option(False, "--no-merge", help="Report without merging."),
) -> None:
    """Close the sprint: merge what you approved, and report on the increment.

    This is the second gate. The crew cannot approve its own pull requests, so
    reviewing the increment is approving the pull requests that make it up.
    """
    from crew_org.auth import resolve_credentials
    from crew_org.config import load_env
    from crew_org.escalation import EscalationLedger
    from crew_org.flows.close import close_sprint
    from crew_org.llm import health
    from crew_org.process import ProcessRules
    from crew_org.tools.github_issues import IssueClient
    from crew_org.tools.github_project import ProjectClient

    env = load_env()
    ok, message = health()
    if not ok:
        console.print(f"[red]{message}[/]")
        raise typer.Exit(code=1)

    token, _ = resolve_credentials(env)
    owner, repo = env["GITHUB_OWNER"], env.get("PILOT_REPO", "crew")
    crew_repo = env.get("CREW_REPO", "crew")
    org = load_org()
    board = ProjectClient(token, owner, int(env["GITHUB_PROJECT_NUMBER"]))
    sprint = sprint or board.schema.field("Sprint").current_iteration()

    result = close_sprint(
        board,
        IssueClient(token, owner),
        EventSink(VAR / "events" / "close.jsonl"),
        EscalationLedger(VAR / "ledger" / "escalations.jsonl"),
        sprint=sprint,
        repo=repo,
        merge=not no_merge,
        rules=ProcessRules.from_config(org),
        events_dir=VAR / "events",
        crew_repo=crew_repo,
        delivery_repos=list(org.get("delivery", {}).get("repos") or []),
    )

    console.print(f"\n[bold]{result.sprint}[/]")
    for number in result.merged:
        console.print(f"  [green]#{number}[/] merged and done")
    for story, pull in result.awaiting_approval:
        console.print(
            f"  [yellow]#{story}[/] waiting on your approval of PR #{pull} — "
            f"https://github.com/{owner}/{repo}/pull/{pull}"
        )
    for card, days in result.aging_blocked:
        console.print(
            f"  [red]{card}[/] has been blocked {days} days — past the threshold, "
            "and still waiting on a person"
        )
    for story, pull in result.unapprovable:
        console.print(
            f"  [red]#{story}[/] PR #{pull} is approved and GitHub still requires a review — "
            f"the crew's approval was recorded and not counted. "
            f"https://github.com/{owner}/{repo}/pull/{pull}"
        )
    for number, pull in result.updating:
        console.print(
            f"  [yellow]#{number}[/] PR #{pull} was behind main — brought up to date; "
            "it merges once its checks pass on the new head"
        )
    for number, why in result.unmergeable:
        console.print(f"  [red]#{number}[/] {why}")
    if result.still_open:
        console.print(
            f"  [dim]{len(result.still_open)} stories did not reach QA: "
            + ", ".join(f"#{n}" for n in result.still_open)
            + "[/]"
        )

    if result.retro:
        console.print(f"\n[bold]Retro[/]\n{result.retro.summary}")
        for defect in result.retro.defects:
            console.print(f"\n[yellow]{defect.subject}[/] — {defect.problem}")
            console.print(f"  → {defect.change}")
    record = result.retro_record
    if record is not None and record.issue is not None:
        url = f"https://github.com/{owner}/{crew_repo}/issues/{record.issue}"
        if result.retro_already:
            console.print(f"\n[dim]Retro already recorded for {result.sprint}:[/] {url}")
        else:
            console.print(f"\n[bold]Recorded:[/] {url}")
            for defect_repo, number in record.filed:
                console.print(f"  filed {defect_repo}#{number}")
            for subject, why in record.failed:
                console.print(f"  [red]not filed[/] {subject} — {why}")
            for subject, number in record.explained:
                console.print(f"  [dim]explained by {crew_repo}#{number}, not filed:[/] {subject}")

    if result.complete:
        console.print("\n[green]Sprint complete.[/]")


if __name__ == "__main__":
    app()
