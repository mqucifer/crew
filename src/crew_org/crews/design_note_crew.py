"""An epic's design note: the Architect's approach before its stories are built (#155).

§13 rations design: an epic above the complexity threshold, or labelled
`needs:design`, gets a note before any of its stories is built. Refinement
labelled the epics and nothing wrote the notes, so sprint-metrics#50's four
stories each extended the same report path their own way. #73's rebuild absorbed
#74's scope, and #74 was then asked to build what already existed.

The note is the least design that settles the open questions: the approach, the
interfaces and data shapes the stories share, what would be expensive to
reverse, the risks, and how the work divides across the stories.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator

from crew_org.agents import build_agent
from crew_org.permissions import load_agents


class StoryDirection(BaseModel):
    story: int = Field(description="The story's issue number")
    direction: str = Field(description="What this story builds, within the approach")


class DesignNote(BaseModel):
    approach: str = Field(description="How the epic is built, in a short paragraph")
    interfaces: list[str] = Field(
        default_factory=list,
        description="The functions, data shapes and formats the stories share, named",
    )
    expensive_to_reverse: list[str] = Field(
        default_factory=list, description="The decisions that would be costly to undo, and why"
    )
    risks: list[str] = Field(default_factory=list, description="Named plainly, not hedged")
    stories: list[StoryDirection] = Field(
        description="One direction per story, so no story builds another's part"
    )
    looked_at: list[str] = Field(description="What you read to write this: files, issues")
    beyond_reach: str | None = Field(
        None,
        description=(
            "Only if a decision genuinely exceeds what you can resolve: name specifically "
            "what you could not resolve. Never 'this is hard'."
        ),
    )

    @field_validator("stories")
    @classmethod
    def _covers_the_stories(cls, value: list[StoryDirection]) -> list[StoryDirection]:
        if not value:
            raise ValueError("a design note gives each story its direction")
        return value


def render(note: DesignNote) -> str:
    """The note as the epic's comment reads, and as the Developer and Reviewer are shown it."""

    def listed(title: str, items: list[str]) -> list[str]:
        return [f"**{title}**", *[f"- {i}" for i in items], ""] if items else []

    lines = [
        "## Design note",
        "",
        note.approach,
        "",
        *listed("Interfaces and data shapes", note.interfaces),
        *listed("Expensive to reverse", note.expensive_to_reverse),
        *listed("Risks", note.risks),
        "**How the stories divide the work**",
        *[f"- #{s.story}: {s.direction}" for s in note.stories],
        "",
        "_Looked at: " + ", ".join(note.looked_at) + "_",
    ]
    return "\n".join(lines)


def write_note(
    *, epic: str, stories: str, project: str, repository: str, feedback: str = ""
) -> DesignNote:
    """The Architect's note for one epic."""
    spec = load_agents()["architect"]
    architect = build_agent("architect", spec)
    refused = f"## Your last note was refused\n\n{feedback}\n\n" if feedback else ""
    task = Task(
        description=(
            (f"{project}\n\n" if project else "")
            + f"## The epic\n\n{epic}\n\n## Its stories, in the order they are built\n\n"
            f"{stories}\n\n## The code as it stands\n\n{repository}\n\n{refused}"
            "Write the design note for this epic: the least design that settles its open "
            "questions. Name the interfaces and data shapes the stories share, so each "
            "story builds on the one before instead of re-deciding it, and give each story "
            "its direction. Say what you looked at."
        ),
        expected_output="The approach, interfaces, costly decisions, risks, and per-story work.",
        agent=architect,
        output_pydantic=DesignNote,
    )
    crew = Crew(agents=[architect], tasks=[task], process=Process.sequential, verbose=False)
    return crew.kickoff().pydantic
