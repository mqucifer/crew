"""A file read inside the call (crew#583, step C4, ADR 0024).

The Developer is shown the files its work names and the rest by name and
signature. What it needs beyond that it used to ask for in `need_files`, an answer
that did no work: on mqucifer/sprint-metrics#558's stories the first answer only
asked, on 7 of 7. Each ask re-sent the whole prompt with more in it, and on
mqucifer/sprint-metrics#567 that grew to 82k tokens and ten empty answers.

Here the model reads what it needs and carries on in the same call: a whole file,
or one definition by name, the way an editor goes to a definition. What it reads
shares one budget with what it was shown, so a call can't read its way past the
size at which answers come back empty. `need_files` stays as the last resort.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Any

from crew_org import calls
from crew_org.tools.ast_edit import definitions
from crew_org.tools.repo_context import CONTEXT_FILES, IGNORED_DIRS, WHOLE_FILE_CHARS, is_text

NAME = "read_file"


class Reader:
    """A worktree's files, read on request under a budget of characters.

    `shown` is what the prompt already holds in full: reading it again only
    spends the budget, so the model is told it's there.
    """

    def __init__(self, worktree: Path, *, budget: int, shown: list[str] | tuple[str, ...] = ()):
        self.worktree = worktree
        self.left = budget
        self.shown = set(shown) | set(CONTEXT_FILES)
        # What was read, in order, as path or path::name, for the event log.
        self.read: list[str] = []

    def reset(self, budget: int) -> None:
        """A new call over the same files: its own budget, and nothing read yet."""
        self.left = budget
        self.read = []

    def tool(self) -> calls.Tool:
        return calls.Tool(
            name=NAME,
            description=(
                "Read a file of the repository that is listed by name but not shown in full. "
                "Give `name` to read one definition (a function, a class, `Class.method` or a "
                "constant) instead of the whole file: a large file may only be read that way."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative path"},
                    "name": {
                        "type": "string",
                        "description": "Optional: one definition in a Python file, by name",
                    },
                },
                "required": ["path"],
            },
            run=self.run,
        )

    def run(self, arguments: dict[str, Any]) -> str:
        rel = str(arguments.get("path") or "").strip().replace("\\", "/").removeprefix("./")
        name = str(arguments.get("name") or "").strip()
        pure = PurePosixPath(rel)
        path = self.worktree / rel
        if not rel or pure.is_absolute() or ".." in pure.parts or IGNORED_DIRS & set(pure.parts):
            return f"`{rel}` isn't a path in the repository."
        if not path.is_file():
            return f"There's no file `{rel}` in the repository. The listing names every file."
        if path.suffix != ".py" and not is_text(path):
            return f"`{rel}` isn't text, so it can't be read here."
        if rel in self.shown and not name:
            return f"`{rel}` is already shown in full above."
        source = path.read_text(encoding="utf-8", errors="ignore")
        key = f"{rel}::{name}" if name else rel
        if key in self.read:
            return f"You've read `{key}` already, above."
        if name:
            node = definitions(source).get(name) if rel.endswith(".py") else None
            if node is None:
                return f"`{rel}` defines no `{name}`. " + self._names(rel, source)
            lines = source.splitlines()
            start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
            body = "\n".join(lines[start - 1 : node.end_lineno or node.lineno])
        else:
            body = source.strip()
            if len(body) > WHOLE_FILE_CHARS and rel.endswith(".py") and definitions(source):
                return (
                    f"`{rel}` is {len(body):,} characters, too large to read whole. "
                    + self._names(rel, source)
                    + _imports(source)
                )
        if len(body) > self.left:
            return (
                f"`{key}` is {len(body):,} characters, more than is left to read in this call "
                f"({self.left:,}). "
                + (self._names(rel, source) if not name and rel.endswith(".py") else "")
                + "If you can't do the work without it, name it in `need_files`."
            )
        self.left -= len(body)
        self.read.append(key)
        fence = "python" if rel.endswith(".py") else ""
        return f"`{key}`\n\n```{fence}\n{body}\n```"

    def _names(self, rel: str, source: str) -> str:
        names = list(definitions(source)) if rel.endswith(".py") else []
        if not names:
            return ""
        return f"Read one of its definitions by name: {', '.join(names)}. "


def _imports(source: str) -> str:
    """A module's import lines: no definition holds them, so reading by name can't reach them.

    The fourth real call (mqucifer/sprint-metrics#567) wrote the whole story, then asked
    for `report.py` in `need_files` only to see its imports, and an answer that asks
    isn't applied.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    lines = source.splitlines()
    found = [
        "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    if not found:
        return ""
    joined = "\n".join(found)
    return f"Its imports:\n\n```python\n{joined}\n```"
