"""A role's finding outside the story it's judging, for the role it belongs to (ADR 0034).

On mqucifer/sprint-metrics#667 DevOps found that the `requires_db` tests never ran
in CI. As a reviewer it could only note it and set it aside as pre-existing, and QA
then accepted CI proof the job's log showed was skipped (crew#817). A finding the
change didn't cause now has its own place in a verdict, and is filed: a Task for the
role whose area it's in, or a bug.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Owner = Literal["devops", "architect", "ux", "qa", "bug"]


class OutsideFinding(BaseModel):
    what: str = Field(description="What is wrong or missing, as a fact about what you were shown")
    where: str = Field(description="The file, workflow or part it's in")
    evidence: str = Field(description="What shows it: a line, a log, a skipped job")
    owner: Owner = Field(
        description=(
            "Whose work it is. devops: CI, releases, where things run; architect: the "
            "design, how parts connect, the repository's diagram; ux: what a reader sees; "
            "qa: tests across stories; bug: behaviour that contradicts what the project "
            "already accepted"
        )
    )


OUTSIDE_DESCRIPTION = (
    "Problems you saw that this change didn't cause and needn't fix: each is filed for "
    "the role whose work it is, so being there before this change is no reason to drop "
    "one. Empty when there are none"
)
