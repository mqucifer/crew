"""The deploy review: is what this change runs or ships fit to run as deployed? (#335)

The Code Reviewer judges the diff and QA the story's criteria. Neither asks
whether the thing can be run the way it will be, and sprint-metrics#269 merged
an image nobody could reach. The DevOps Engineer asks that, of any change to
something that runs or deploys, shown the code it runs as well as the diff.

The verdict has the Code Reviewer's shape, so a finding means the same thing
from either gate and the Developer answers both the same way.
"""

from __future__ import annotations

from crewai import Crew, Process, Task

from crew_org.agents import build_agents
from crew_org.crews.review_crew import MAX_DIFF_CHARS, ReviewVerdict

QUESTIONS = (
    "- Who runs it, and how is it reached?\n"
    "- As which user?\n"
    "- What's pinned, and what floats?\n"
    "- How do we see that it's working?"
)


def review_deploy(
    title: str,
    diff: str,
    *,
    evidence: str,
    acceptance_criteria: str = "",
    release: str = "",
    prior_verdicts: str = "",
    checks: str = "",
) -> ReviewVerdict:
    """Judge a change to something that runs or deploys, as it will be run.

    `evidence` is the runnable files and the code they run, at the head
    (`tools/deploy_evidence`). `release` is the project record's word on how
    it's released and checked.
    """
    if len(diff) > MAX_DIFF_CHARS:
        # The Code Reviewer already refuses a diff this size, and says why.
        return ReviewVerdict(
            summary="Too large to review in one pass; see the code review.", approve=True
        )
    criteria = (
        f"\n\n## Acceptance criteria this must satisfy\n\n{acceptance_criteria}"
        if acceptance_criteria
        else ""
    )
    previously = (
        f"\n\n## What was asked on an earlier push to this pull request\n\n{prior_verdicts}\n\n"
        "Check first whether this change answers your earlier findings. Don't raise a new "
        "one late unless this change introduced it.\n"
        if prior_verdicts
        else ""
    )
    agents = build_agents("devops_engineer")
    task = Task(
        description=(
            f"Review this change for whether what it runs or ships is fit to run as deployed."
            f"\n\n## Title\n\n{title}{criteria}{previously}\n\n"
            + (f"## How this project is released and checked\n\n{release}\n\n" if release else "")
            + f"## Diff\n\n```diff\n{diff}\n```\n\n"
            + (f"{evidence}\n\n" if evidence else "")
            + (f"{checks}\n\n" if checks else "")
            + "Answer these of what it runs, from the evidence above:\n"
            f"{QUESTIONS}\n\n"
            "A finding names the file, says what is wrong as a fact about what you were "
            "shown, and says what to do. Where a mechanical check would catch it next time "
            "(a linter rule, a smoke test run the way it's deployed, a dependency updater), "
            "the action names that check too: the tool, and a rule number only when you're "
            "sure of it.\n"
            "Block the change when what it runs or ships can't be run or reached as it will "
            "be deployed, or runs with more privilege than it needs. That holds even when "
            "the line at fault is older than this change: an image whose server can't be "
            "reached is the image's problem. A problem that doesn't touch what this change "
            "runs or ships is a note, not a reason to hold it up. So is hardening that "
            "doesn't stop it working.\n"
            "Leave code style and correctness to the Code Reviewer, and the story's "
            "criteria to QA. If it's fit to run, approve it."
        ),
        expected_output="A verdict with a summary and any findings.",
        agent=agents["devops_engineer"],
        output_pydantic=ReviewVerdict,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic
