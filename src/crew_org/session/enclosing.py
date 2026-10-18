"""What each line of a file sits in, for `search`'s hits and `view`'s header (crew#807).

The panel's QA searched sprint-metrics' `.github` for `postgres|SPRINT_METRICS_DB`, got
lines 26 and 40 of `tests.yml`, and read them as the `tests` job's. Both were in the
`docker` job: the hits were real lines, with nothing to say which job each sat in (the
Sponsor, crew#807: "That's a big bug."). So a line is named by what encloses it:
- Python: the innermost definition `view(definition=…)` takes, `Class.method` or
  `function`, decorators included, or a constant written over several lines. Any
  other line is at module level;
- YAML and JSON: the key path to it, `jobs.docker.services.postgres` or
  `jobs.tests.steps[2].run`, in yq's and jq's notation, items counted from 0. A line
  that starts with its own key is named by the mapping it's in, since the key is on
  the line;
- Markdown: the heading it's under. A heading is under the one above it of a higher
  level.

A file of these kinds that doesn't parse names nothing, and says what it didn't parse
as. Any other file names nothing. The text is what the tools read through their
symlink-safe reads (crew#800); nothing here reads a file.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml  # type: ignore[import-untyped]

from crew_org.tools.ast_edit import definitions

# The longest name shown, so a hit stays one short line: a long heading or key path
# keeps its start and its end (crew#807).
NAME_CHARS = 80

_KINDS = {
    ".py": "Python",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".md": "Markdown",
    ".markdown": "Markdown",
}
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_BARE_KEY = re.compile(r"[\w-]+")


@dataclass(frozen=True)
class Enclosing:
    """What encloses each line of one file, `names[n - 1]` for line n, or, when the file
    doesn't parse, `broken`: what it didn't parse as."""

    names: list[str]
    broken: str = ""

    def name(self, n: int) -> str:
        """Line n's name, counting from 1; empty in a file that didn't parse."""
        return self.names[n - 1] if 0 < n <= len(self.names) else ""


def enclosing(rel: str, text: str, kind: str = "") -> Enclosing | None:
    """What encloses each line of `text`, the file `rel`, by its kind: Python, YAML,
    JSON or Markdown, told by its suffix unless `kind` gives it. None for any other
    file."""
    kind = kind or _KINDS.get(PurePosixPath(rel).suffix.lower(), "")
    namer = {"Python": _python, "YAML": _yaml, "JSON": _json, "Markdown": _markdown}.get(kind)
    if namer is None:
        return None
    names = namer(text)
    if names is None:
        return Enclosing([], kind)
    return Enclosing([_cut(name) for name in names])


def _cut(name: str) -> str:
    if len(name) <= NAME_CHARS:
        return name
    head = NAME_CHARS // 3
    return f"{name[:head]}…{name[len(name) - (NAME_CHARS - head - 1) :]}"


def _join(path: str, key: str) -> str:
    """A key path one key deeper: `.key`, or `["a.b"]` for a key that isn't one word."""
    if _BARE_KEY.fullmatch(key):
        return f"{path}.{key}" if path else key
    return f"{path}[{json.dumps(key)}]"


def _python(text: str) -> list[str] | None:
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    lines = text.splitlines()
    names = ["module level"] * len(lines)
    # A class comes before its methods, so a method's lines are named over its class's.
    for qual, node in definitions(text).items():
        end = node.end_lineno or node.lineno
        if isinstance(node, ast.Assign | ast.AnnAssign) and end == node.lineno:
            continue  # a one-line constant names nothing its line doesn't say
        start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        names[start - 1 : end] = [qual] * (min(end, len(lines)) - start + 1)
    return names


def _yaml(text: str) -> list[str] | None:
    try:
        documents = [d for d in yaml.compose_all(text, Loader=_LOADER) if d is not None]
    except yaml.YAMLError:
        return None
    lines = text.splitlines()
    names = ["top level"] * len(lines)
    for number, document in enumerate(documents, 1):
        # Each span as [first line, last line, path], 0-based, a parent before its
        # children, so painted in order the innermost name is the one left.
        spans: list[list[Any]] = []
        last = _yaml_spans(document, "", spans, set(), lines)
        spans.insert(0, [document.start_mark.line, last, ""])
        suffix = f" (document {number})" if len(documents) > 1 else ""
        for first, end, path in spans:
            for n in range(first, min(end, len(lines) - 1) + 1):
                names[n] = (path or "top level") + suffix
    return names


def _yaml_spans(
    node: yaml.Node, path: str, spans: list[list[Any]], seen: set[int], lines: list[str]
) -> int:
    """The spans under `node`, and its last line.

    A block collection's end mark is the next token's, often the next key's line, so
    its last line is its last child's. A block scalar's (`run: |`) is the start of the
    next line with content, so its last line is the one before, less blank lines.
    """
    if id(node) in seen:  # an alias: what it names is named where its anchor is
        return -1
    seen.add(id(node))
    start: int = node.start_mark.line
    if isinstance(node, yaml.CollectionNode) and not node.flow_style:
        last = start
    else:
        end = node.end_mark
        within = end.line >= len(lines) or not lines[end.line][: end.column].strip()
        last = end.line - 1 if end.line > start and within else end.line
        while last > start and last < len(lines) and not lines[last].strip():
            last -= 1
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            inner = _join(path, str(key.value) if isinstance(key, yaml.ScalarNode) else "?")
            # A key's span starts on the line after it: its own line says its name.
            span = [key.start_mark.line + 1, -1, inner]
            spans.append(span)
            span[1] = max(key.end_mark.line, _yaml_spans(value, inner, spans, seen, lines))
            last = max(last, span[1])
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            inner = f"{path}[{index}]"
            # A block item's line starts with its `-`; a flow item, `[main]`, is named like
            # a key, from the line after it.
            span = [item.start_mark.line + bool(node.flow_style), -1, inner]
            spans.append(span)
            span[1] = _yaml_spans(item, inner, spans, seen, lines)
            last = max(last, span[1])
    return last


# JSON as it's written, comments and trailing commas too (`devcontainer.json`,
# `tsconfig.json`): one token at a time, each a space or comment, a string, a mark, or
# a word.
_JSON_TOKEN = re.compile(
    r'(?P<space>\s+|//[^\n]*|/\*.*?\*/)|(?P<string>"(?:[^"\\\n]|\\.)*")'
    r'|(?P<mark>[{}\[\]:,])|(?P<word>[^\s{}\[\]:,"/]+)',
    re.S,
)
_JSON_WORD = re.compile(r"true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


@dataclass
class _Open:
    """An object or array the scan is in: its path, and where in it the scan is."""

    closer: str
    path: str
    state: str  # an object's "key", "colon", "value" or "comma"; an array's "value", "comma"
    key: str = ""
    index: int = -1


def _json(text: str) -> list[str] | None:
    """Each line's key path, named at its first token: a key by the object it's in, an
    item by its index. A line with no token is in the innermost object or array."""
    lines = text.splitlines()
    names: list[str] = [""] * len(lines)
    stack: list[_Open] = []
    started = False
    line, named, pos = 0, -1, 0
    while pos < len(text):
        token = _JSON_TOKEN.match(text, pos)
        if token is None:
            return None
        pos, word, kind = token.end(), token.group(), token.lastgroup
        if kind == "space":
            for _ in range(word.count("\n")):
                line += 1
                if line < len(names):
                    names[line] = stack[-1].path if stack else ""
            continue
        value = kind in ("string", "word") or word in ("{", "[")
        if kind == "word" and not _JSON_WORD.fullmatch(word):
            return None
        top = stack[-1] if stack else None
        if named != line and line < len(names):
            names[line] = _json_place(top, word)
            named = line
        if top is None:
            if started or not value:
                return None
            started = True
            if word in ("{", "["):
                stack.append(_open(word, ""))
        elif top.closer == "}":
            if top.state == "key" and kind == "string":
                try:
                    top.key, top.state = str(json.loads(word)), "colon"
                except ValueError:
                    return None
            elif top.state == "colon" and word == ":":
                top.state = "value"
            elif top.state == "value" and value:
                top.state = "comma"
                if word in ("{", "["):
                    stack.append(_open(word, _join(top.path, top.key)))
            elif top.state == "comma" and word == ",":
                top.state = "key"
            elif top.state in ("key", "comma") and word == "}":
                stack.pop()
            else:
                return None
        elif top.state == "value" and value:
            top.index, top.state = top.index + 1, "comma"
            if word in ("{", "["):
                stack.append(_open(word, f"{top.path}[{top.index}]"))
        elif top.state == "comma" and word == ",":
            top.state = "value"
        elif word == "]":
            stack.pop()
        else:
            return None
    if stack:
        return None
    return [name or "top level" for name in names]


def _open(opener: str, path: str) -> _Open:
    return _Open("}", path, "key") if opener == "{" else _Open("]", path, "value")


def _json_place(top: _Open | None, word: str) -> str:
    """Where a token is: in the object or array that holds it, or in the member or item
    it starts or continues."""
    if top is None:
        return ""
    if top.closer == "}":
        return _join(top.path, top.key) if top.state == "value" else top.path
    if top.state == "value" and word not in ("]", ","):
        return f"{top.path}[{top.index + 1}]"
    return top.path


_HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


def _markdown(text: str) -> list[str]:
    names: list[str] = []
    under: list[tuple[int, str]] = []  # the headings a line is under, outermost first
    fence = ""
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1).startswith(fence) and not line[opened.end() :].strip():
                fence = ""
        elif opened:
            fence = opened.group(1)
        elif heading := _HEADING.match(line):
            level = len(heading.group(1))
            while under and under[-1][0] >= level:
                under.pop()
            names.append(under[-1][1] if under else "top of file")
            under.append((level, f"{heading.group(1)} {heading.group(2) or ''}".strip()))
            continue
        names.append(under[-1][1] if under else "top of file")
    return names
