"""The crew on a project that isn't Python, end to end (#404's done-when).

The static-site fixture declares one part, javascript, with Playwright tests.
The Developer takes a real story through the model, the answer is applied, the
named test must be in its file, the sandbox runs Playwright, and QA judges it
against the test output and the test code. Read-only: a temporary copy, nothing
posted. Run from the repository root: uv run python experiments/multilang-404/run.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from crew_org import profiles
from crew_org.crews.delivery_crew import implement_story
from crew_org.crews.qa_crew import verify_story
from crew_org.flows.acceptance import collect_output
from crew_org.flows.delivery import ASK_LIMIT, names_a_test
from crew_org.project import brief, read_record
from crew_org.tools import workspace
from crew_org.tools.repo_context import repository_context
from crew_org.tools.sandbox import Mode, Sandbox

HERE = Path(__file__).parent
FIXTURE = HERE.parent.parent / "tests" / "fixtures" / "static-site"

STORY = """#1 Show what changed since the prior sprint in the health section

As a **crew member reading the report**, I want **a line saying what changed since the
prior sprint, right under the status**, so that **I see what shifted without reading on**.

## Acceptance criteria

1. **Given** the report page
   **When** it is opened
   **Then** the health section contains a paragraph reading exactly
   `Changed: Cycle time improved by 2 days`

2. **Given** the report page
   **When** it is opened
   **Then** that paragraph comes after the `Status: All clear` paragraph, inside the health
   section, and before the Current Sprint section
"""

ATTEMPTS = 3


def implement(context: str, feedback: str, asks: list[str]):
    """The Developer's answer; an ask to see files is answered as delivery answers it."""
    for _ in range(ASK_LIMIT + 1):
        implementation = implement_story(STORY, context=context, feedback=feedback)
        if not implementation.asks:
            return implementation
        asks.append(", ".join(implementation.need_files))
        # Every file is already shown whole: there is nothing more to show.
        feedback = (
            "You've been shown what you asked for. "
            "Do the work now with the files you can see, and leave `need_files` empty."
        )
    return implementation


def describe(implementation) -> dict:
    """What an answer contained: where its tests are, and what it claimed."""
    return {
        "summary": implementation.summary,
        "criteria_tests": [f"{c.path}::{c.test}" for c in implementation.criteria_tests],
        "proven_by_existing": [
            t
            for proof in getattr(implementation, "proven_by_existing", None) or []
            for t in proof.tests
        ],
        "new_files": [f.path for f in implementation.new_files],
        "text_edits": [t.path for t in implementation.text_edits],
    }


def main() -> None:
    site = Path(tempfile.mkdtemp()) / "site"
    shutil.copytree(FIXTURE, site)
    record = read_record(site)
    profiles.set_project(record)
    feedback, rounds = "", []
    check = implementation = None
    # As delivery does, a failed attempt's work stays in place and the next
    # attempt repairs it, seeing the files as they now are.
    for attempt in range(1, ATTEMPTS + 1):
        context = f"{brief(record)}\n\n{repository_context(site)}"
        started = time.monotonic()
        asks: list[str] = []
        implementation = implement(context, feedback, asks)
        seconds = round(time.monotonic() - started)
        row = {"attempt": attempt, "seconds": seconds, "asks": asks, **describe(implementation)}
        try:
            workspace.apply_implementation(site, implementation)
            unwritten = [
                f"{c.path}::{c.test}"
                for c in implementation.criteria_tests
                if not names_a_test(site, f"{c.path}::{c.test}")
            ]
            if unwritten:
                raise ValueError("these tests aren't in their files: " + ", ".join(unwritten))
        except Exception as exc:  # noqa: BLE001
            row["refused"] = str(exc)[:400]
            rounds.append(row)
            feedback = f"An edit could not be applied:\n\n{exc}"
            continue
        check = workspace.check(site, sandbox=Sandbox(mode=Mode.REQUIRED))
        row["check_ok"] = check.ok
        row["tests"] = [f"{c.path}::{c.test}" for c in implementation.criteria_tests]
        rounds.append(row)
        if check.ok:
            break
        feedback = "The checks failed:\n\n" + check.failure_report
    result = {"rounds": rounds}
    qa_rounds = []
    # A QA refusal goes back to the Developer with its reasons, as in delivery.
    for qa_round in range(1, 3):
        if check is None or not check.ok:
            break
        test_code = "\n\n".join(
            f"`{p.relative_to(site)}`\n\n{p.read_text()}"
            for p in sorted(site.glob("tests/**/*.spec.js"))
        )
        verdict = verify_story(
            STORY,
            test_output=collect_output(check.results),
            test_code=test_code,
            project=brief(record),
        )
        qa_rounds.append(
            {
                "round": qa_round,
                "accepted": verdict.accepted,
                "criteria": [c.model_dump() for c in verdict.criteria],
            }
        )
        if verdict.accepted:
            break
        reasons = "\n".join(
            f"- {c.criterion}: {c.evidence}" for c in verdict.criteria if not c.proven
        )
        asks = []
        implementation = implement(
            f"{brief(record)}\n\n{repository_context(site)}",
            "QA refused this: no test proves these criteria.\n\n" + reasons,
            asks,
        )
        rows = {"asks": asks, **describe(implementation)}
        try:
            workspace.apply_implementation(site, implementation)
        except Exception as exc:  # noqa: BLE001
            rows["refused"] = str(exc)[:400]
            qa_rounds.append({"repair": rows})
            break
        check = workspace.check(site, sandbox=Sandbox(mode=Mode.REQUIRED))
        rows["check_ok"] = check.ok
        rows["test_output"] = collect_output(check.results)[-600:]
        qa_rounds.append({"repair": rows})
    result["qa_rounds"] = qa_rounds
    result["page"] = (site / "index.html").read_text()
    result["tests"] = {
        str(t.relative_to(site)): t.read_text() for t in sorted(site.glob("tests/**/*.spec.js"))
    }
    (HERE / "result.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps({k: v for k, v in result.items() if k not in ("page", "tests")}, indent=2)[:6000]
    )


if __name__ == "__main__":
    main()
