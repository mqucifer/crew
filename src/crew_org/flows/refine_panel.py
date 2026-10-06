"""Before an epic is split, the panel reads it and the Product Owner settles its notes (crew#440).

Architect, UX Designer, QA and DevOps each read an approved epic once, then the
Product Owner writes the conclusion into the epic's body. The split reads that
conclusion. Run every tick like the other refinement steps, so each part is safe
to repeat: an epic with a conclusion is left alone, one whose panel has run isn't
read again, and one waiting on a Sponsor reply stays waiting.

An epic with no Goal above it has nothing to be read against, so it is split as
before. The panel is one pass per epic: clashes between two epics are caught
after the split, by the criteria check and the Product Owner (crew#428).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from crew_org.crews.panel_crew import run_panel
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import panel as panel_flow
from crew_org.flows import settle as settle_flow
from crew_org.llm import reraise_if_down
from crew_org.tools.github_issues import IssueClient

if TYPE_CHECKING:
    from crew_org.flows.board_flow import TickResult
    from crew_org.tools.github_project import Card


class PanelFailed(RuntimeError):
    """No member of the panel answered."""


def panel_step(
    issues: IssueClient,
    sink: EventSink,
    result: TickResult,
    epic: Card,
    repo: str,
    *,
    project: str,
    search: list[str],
) -> bool:
    """True when the epic has its conclusion and may be split now."""
    number = epic.number or 0
    if not epic.parent:
        return True
    if settle_flow.has_conclusion(issues.get(repo, number).get("body") or ""):
        return True
    # The epic's own repository first: that is where its Goal's cards are.
    where = list(dict.fromkeys([repo, *search]))
    try:
        context = panel_flow.gather(issues, repo, number, project=project, search=where)
        # Seen before it crowds the context (crew#468).
        sink.note(
            EventKind.NOTE,
            f"#{number} panel context: {len(context.decisions):,} chars of Goal decisions, "
            f"{len(context.project_log):,} of the project's log, {len(context.siblings)} siblings",
            card=number,
            decisions_chars=len(context.decisions),
            project_log_chars=len(context.project_log),
            siblings=len(context.siblings),
        )
        notes = panel_flow.panel_on(issues, repo, number)
        if notes is None:
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_STARTED,
                    role="Refinement panel",
                    card=number,
                    summary=f"panel on {epic.title[:50]}",
                )
            )
            notes = attributed(run_panel, card=number, repo=repo)(context)
            if not notes.answers:
                raise PanelFailed("; ".join(f"{r}: {why}" for r, why in notes.failed.items()))
            panel_flow.post(issues, repo, number, notes)
        settled = settle_flow.settle_epic(
            issues,
            sink,
            repo=repo,
            epic=number,
            context=context,
            panel=notes,
            known=set(where),
        )
    except panel_flow.NotUnderAGoal:
        return True
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        result.failed.append((number, f"the panel couldn't settle it: {type(exc).__name__}: {exc}"))
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FAILED,
                role="Product Owner",
                card=number,
                summary=str(exc)[:100],
            )
        )
        return False
    if settled.outcome is settle_flow.Outcome.WAITING:
        result.skipped.append((number, "waits for the Sponsor's answer on the epic"))
        return False
    return True
