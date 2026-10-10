"""Who may change a story's contract during delivery: one Business Analyst pass (crew#584, A5).

A story that broke merged tests it didn't declare used to try again, and only
when the same tests failed twice did the whole epic go back to be split again:
sprint-metrics#529 took six attempts and about four hours that way. The tests a
shape change breaks often assert a whole shape without naming the new keys, so
the split couldn't have named them (crew#584).

On the first such failure, the Business Analyst, who writes stories' contracts,
decides for this story alone: the failing tests pin behaviour the story's
criteria and the epic's rows require changing, so the story's Existing tests
line is amended to declare them; or the story shouldn't change that behaviour,
or it can't tell, and the story goes back to refinement. Either way the tests
become rows of the epic's record (ADR 0023).
"""

from __future__ import annotations

import re
from typing import Literal

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agents

_TEST_ID = re.compile(r"^[\w/.-]+\.py::\w+$")


class ContractRuling(BaseModel):
    decision: Literal["amend", "return"] = Field(
        description=(
            "`amend` when the story's criteria or the rows it follows require the change "
            "these tests pin; `return` when the story shouldn't change it, or you can't tell"
        )
    )
    tests: list[str] = Field(
        default_factory=list,
        description="With `amend`: the failing tests the story now declares, as `path::test`",
    )
    asserts: str = Field(
        default="",
        description="With `amend`: what those tests must assert after the change, in a sentence",
    )
    why: str = Field(
        min_length=10,
        description="The criterion or row that decides it, quoted, or why it can't be told",
    )

    @field_validator("tests")
    @classmethod
    def _test_ids(cls, value: list[str]) -> list[str]:
        bad = [t for t in value if not _TEST_ID.match(t.strip())]
        if bad:
            raise ValueError(f"name each test as `path::test`, not {', '.join(bad)}")
        return [t.strip() for t in value]

    @model_validator(mode="after")
    def _an_amendment_says_what(self) -> ContractRuling:
        if self.decision == "amend" and not (self.tests and len(self.asserts.strip()) >= 10):
            raise ValueError("an amendment names the tests and what they must assert after")
        return self


def rule_on_contract(*, story: str, rows: str, failures: dict[str, str]) -> ContractRuling:
    """The Business Analyst's ruling on merged tests a story broke and didn't declare."""
    agents = build_agents("business_analyst")
    broken = "\n".join(f"- `{test}`: {why or 'failed'}" for test, why in sorted(failures.items()))
    task = Task(
        description=(
            f"## The story\n\n{story}\n\n"
            + (f"{rows}\n\n" if rows else "")
            + f"## Merged tests it broke that its Existing tests line doesn't name\n\n{broken}\n\n"
            "The Developer's change to this story breaks merged tests the story doesn't "
            "declare. Decide for this story alone. If its criteria, or the rows it follows, "
            "require changing what these tests pin, amend its contract: name the tests it "
            "now declares and say what they must assert after; the Developer updates them in "
            "this story. If the story shouldn't change that behaviour, or you can't tell "
            "from what's written, return it to refinement and say why. Quote the criterion "
            "or row that decides it."
        ),
        expected_output="Amend the story's contract, or return it, with why.",
        agent=agents["business_analyst"],
        output_pydantic=ContractRuling,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    ruling = getattr(crew.kickoff(), "pydantic", None)
    if not isinstance(ruling, ContractRuling):
        raise ValueError("the Business Analyst gave no answer in the ContractRuling form")
    return ruling
