"""Before an epic is split, the panel reads it and its notes are settled (crew#440).

Architect, UX Designer, QA and DevOps each read an approved epic once, then the
Product Owner writes the conclusion into the epic's body as its record, and the
Architect settles the design questions the record leaves for it (ADR 0018), so
none is open when the split reads the record. Run every tick like the other
refinement steps, so each part is safe to repeat: an epic with a conclusion isn't
settled again, one whose panel has run isn't read again, one with no question
left for the Architect isn't asked, and one waiting on a person stays waiting.

An epic with no Goal above it has nothing to be read against, so it is split as
before. The panel is one pass per epic: clashes between two epics are caught
after the split, by the criteria check and the Product Owner (crew#428).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from crew_org.crews.panel_crew import PanelContext, run_panel
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import panel as panel_flow
from crew_org.flows import record as record_flow
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
    code: Callable[[str], str] = lambda _about: "",
) -> bool:
    """True when the epic's record is settled and it may be split now.

    `code` is the repository as the split sees it, focused on what it is given:
    the Architect reads it for the files its questions name.
    """
    number = epic.number or 0
    if not epic.parent:
        return True
    # The epic's own repository first: that is where its Goal's cards are.
    where = list(dict.fromkeys([repo, *search]))
    context = None
    try:
        if not settle_flow.has_conclusion(issues.get(repo, number).get("body") or ""):
            context = _gather(issues, sink, repo, number, project=project, search=where)
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
            if settled.outcome is settle_flow.Outcome.WAITING:
                result.skipped.append((number, "waits for the Sponsor's answer on the epic"))
                return False
        # The design questions, before the split (ADR 0018).
        _, text = record_flow.split(issues.get(repo, number).get("body") or "")
        asked = record_flow.parse(text).for_architect()
        if asked:
            context = context or _gather(issues, sink, repo, number, project=project, search=where)
            designed = settle_flow.settle_design_questions(
                issues,
                sink,
                repo=repo,
                epic=number,
                context=context,
                code=code("\n".join(q.question for q in asked)),
            )
            if designed.outcome is settle_flow.Outcome.WAITING:
                result.skipped.append((number, "waits for a person to settle its design questions"))
                return False
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
    return True


def _gather(
    issues: IssueClient, sink: EventSink, repo: str, number: int, *, project: str, search: list[str]
) -> PanelContext:
    context = panel_flow.gather(issues, repo, number, project=project, search=search)
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
    return context
