"""A file a role asks to see in full, and why (#231, crew#449).

The Developer, the Business Analyst and QA can each ask for a file the context
only names. The ask was a bare path, so a replay could tell which files were
wanted but not what for (discussion 553). Each now says why, in a line. The
reason is kept in the crew's own record and never leaves it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class FileAsk(BaseModel):
    path: str = Field(description="The file's path, as the context lists it")
    why: str = Field(default="", description="In one line, what you need to see in it")

    @model_validator(mode="before")
    @classmethod
    def _a_bare_path(cls, value: Any) -> Any:
        # A bare path is still an ask: an answer in the old shape isn't refused.
        return {"path": value} if isinstance(value, str) else value


def paths(asks: list[FileAsk]) -> list[str]:
    """The paths asked for, cleaned as the context names them."""
    return [a.path.strip().removeprefix("./") for a in asks]


def reasons(asks: list[FileAsk]) -> dict[str, str]:
    """Each path's reason, for the record."""
    return {a.path.strip().removeprefix("./"): a.why for a in asks if a.why}
