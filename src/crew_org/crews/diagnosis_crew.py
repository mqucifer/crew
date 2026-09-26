"""The Senior Engineer: diagnosing the crew's own code, never changing it (#9).

Two calls, map first (#231's direction). The crew's source is about 200k
tokens, more than a window holds with room to think, so the role is shown an
index of it and the failure, and names the files it needs; then it's shown
those, with line numbers, and diagnoses. It has no tools: what it reads is
chosen by it and read by the flow, and nothing it returns can write.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field

from crew_org.agents import build_agent
from crew_org.permissions import load_agents

MAX_FILES = 8


class FileRequest(BaseModel):
    files: list[str] = Field(
        description=f"Up to {MAX_FILES} paths from the index: those likeliest to hold the cause"
    )
    why: str = Field(description="What in the failure points at them")


class Finding(BaseModel):
    file: str = Field(description="The crew source file, as shown")
    line: int = Field(description="The line number, as shown beside the code")
    wrong: str = Field(description="What is wrong, as a fact about that code")
    change: str = Field(description="The one specific change that would fix it")
    evidence: str = Field(description="What in the failure or the code shows it")


class Diagnosis(BaseModel):
    summary: str = Field(description="What went wrong, in two or three sentences")
    findings: list[Finding] = Field(
        default_factory=list,
        description="Each defect in the crew's code, tied to a line you were shown",
    )
    unsure: str = Field(
        default="", description="What you'd need to see to be sure, if anything is uncertain"
    )


def _engineer():
    return build_agent("senior_engineer", load_agents()["senior_engineer"])


def _run(agent, description: str, expected: str, model: type[BaseModel]):
    task = Task(
        description=description, expected_output=expected, agent=agent, output_pydantic=model
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic


def choose_files(*, failure: str, index: str) -> FileRequest:
    """The files the Senior Engineer wants to read, from the index."""
    return _run(
        _engineer(),
        f"## What failed\n\n{failure}\n\n## The crew's source: an index\n\n{index}\n\n"
        f"Name up to {MAX_FILES} files from the index that most likely hold the cause.",
        "The files to read, and why.",
        FileRequest,
    )


def diagnose(*, failure: str, files: str, why: str) -> Diagnosis:
    """The Senior Engineer's findings, from the files it chose."""
    return _run(
        _engineer(),
        f"## What failed\n\n{failure}\n\n## The files you asked for ({why})\n\n{files}\n\n"
        "Diagnose it. Each finding names a file and a line shown above, what is wrong "
        "there, and the one change that fixes it.",
        "A summary, and each finding with its file, line, what's wrong and the change.",
        Diagnosis,
    )
