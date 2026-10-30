"""Whether the Architect's Mermaid diagrams render, by Mermaid's own parser (crew#922).

The Goal's target diagram is C4 (Simon Brown's model: context, containers and
components) in Mermaid's C4 syntax, with a sequence diagram when the Goal adds an
interaction, and GitHub renders each block. A diagram GitHub can't render is source
text nobody reads.

This used to be a hand-written copy of Mermaid's C4 and sequence grammar (crew#858),
and each quirk it lacked reached the Sponsor first: a `;` in a sequence message
(mqucifer/sprint-metrics#666), a second block nobody checked (crew#839), a `title`
Mermaid's lexer refuses (mqucifer/sprint-metrics#806). The Sponsor's decision
(2026-10-26): the standard decides, so Mermaid's parser reads every block, and its
own message is the finding, as GitHub shows it.

**How it runs**, as `tools/look.py` runs its browser: a public Node image, pinned by
digest, with the parser's packages installed once, from the lockfile beside this
module, into the sandbox's cache, in a container with the network. The check itself
runs in the sandbox's hardening with no network, read-only, and the packages mounted
read-only; the diagrams, the model's text, go in on stdin, never as arguments. A
parser that can't run is a problem named, never a pass.

Mermaid parses five things and then draws them wrong or not at all (crew#858's notes),
so those are checked after it, in code: an open quote, a relation without its label, a
relation to an alias nobody defined, an alias defined twice, and an alias with a space.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from crew_org.tools.sandbox import Mode, Sandbox

C4_TYPES = ("C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment")

# Node's LTS line, pinned by digest as every image the crew runs is. The parser's own
# version is in `mermaid_parse/package.json`: GitHub doesn't say which it renders with
# outside a browser, so it's the newest 11.x (11.17.2 on 2026-10-26). Its `katex` is
# overridden to 0.19.1 for KaTeX's prototype-pollution fix (GHSA-238p-pmpm-9mq7, crew#964):
# 11.x asks for ^0.16, which has no fix, and the parse never draws math.
IMAGE = (
    "node:24.22.1-bookworm-slim"
    "@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205"
)
PACKAGE = Path(__file__).with_name("mermaid_parse")
INSTALL_TIMEOUT = 300
TIMEOUT = 120
# A finding carries Mermaid's message, but not a page of the tokens it expected.
MESSAGE = 400

_MACRO = re.compile(r"^(\w+)\s*\((.*)\)\s*\{?\s*$")
_RELATION = re.compile(r"^(?:Bi)?Rel(?:_\w+)?$")
_STYLE = re.compile(r"^Update\w+$")
_ALIAS = re.compile(r"^[A-Za-z_][\w.]*$")
_POINTER = re.compile(r"^-*\^$")

# Each text's parse, once a process: the same diagram is checked by more than one step.
_parsed: dict[str, str | None] = {}


class Unparsed(Exception):
    """Mermaid's parser couldn't run."""


def problems(blocks: list[str], *, sandbox: Sandbox | None = None) -> list[str]:
    """What keeps each block from rendering, by its place and kind. Empty if nothing.

    Mermaid's message for a block it refuses, and the draw checks for a C4 block it
    accepts. A parser that can't run is one problem, saying why.
    """
    texts = [_unfenced(block) for block in blocks]
    if not texts:
        return []
    try:
        refused = _parse(texts, sandbox)
    except Unparsed as exc:
        return [f"Mermaid's parser couldn't run: {exc}"]
    found: list[str] = []
    for n, (text, error) in enumerate(zip(texts, refused, strict=True), 1):
        label = f"block {n}, {kind(text)[:40] or 'empty'}"
        if error is not None:
            found.append(f"{label}: {_said(error)}")
        elif kind(text) in C4_TYPES:
            found += [f"{label}: {p}" for p in _drawn_wrong(text)]
    return found


def kind(text: str) -> str:
    """The diagram's type, from its first line."""
    lines = _unfenced(text).splitlines()
    return lines[0].strip() if lines else ""


def aliases(text: str) -> set[str]:
    """The aliases a C4 diagram defines."""
    found = set()
    for line in _unfenced(text).splitlines():
        macro = _MACRO.match(line.strip())
        if macro and not _RELATION.match(macro.group(1)) and not _STYLE.match(macro.group(1)):
            args = _arguments(macro.group(2)) or []
            if args:
                found.add(args[0])
    return found


def unready(sandbox: Sandbox | None = None) -> tuple[str, bool] | None:
    """Why the parser isn't ready to run now, and whether a check would fail for it
    rather than fetch what it lacks first; None when it's ready. For `crew doctor`."""
    sandbox = _sandbox(sandbox)
    if reason := sandbox.unavailable_reason():
        return reason, True
    try:
        inspect = subprocess.run(
            [sandbox.engine, "image", "inspect", IMAGE], capture_output=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return f"{sandbox.engine} didn't answer within 60s", True
    if inspect.returncode != 0:
        return f"the image `{IMAGE}` isn't pulled: the first check pulls it", False
    if not _installed(sandbox).is_dir():
        return "the parser's packages aren't installed: the first check installs them", False
    return None


def version() -> str:
    """The parser's pinned mermaid version."""
    return str(json.loads((PACKAGE / "package.json").read_text())["dependencies"]["mermaid"])


def _parse(texts: list[str], sandbox: Sandbox | None) -> list[str | None]:
    """Mermaid's message for each text it refuses, None for each it accepts."""
    unseen = list(dict.fromkeys(t for t in texts if t not in _parsed))
    if unseen:
        sandbox = _sandbox(sandbox)
        if reason := sandbox.unavailable_reason():
            raise Unparsed(reason)
        for text, result in zip(unseen, _run(sandbox, unseen), strict=True):
            _parsed[text] = None if result.get("ok") else str(result.get("error"))
    return [_parsed[t] for t in texts]


def _run(sandbox: Sandbox, texts: list[str]) -> list[dict[str, Any]]:
    package = _install(sandbox)
    work = Path(tempfile.mkdtemp(prefix="mermaid-"))
    try:
        argv = sandbox.command(
            work,
            ["node", "/mermaid/check.mjs"],
            network=False,
            readonly={package: "/mermaid"},
            stdin=True,
            cache=False,
        )
        try:
            done = subprocess.run(
                argv, input=json.dumps(texts), capture_output=True, text=True, timeout=TIMEOUT
            )
        except subprocess.TimeoutExpired as exc:
            raise Unparsed(f"it took over {TIMEOUT}s") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        results = json.loads(done.stdout)
    except ValueError:
        results = None
    if done.returncode != 0 or not isinstance(results, list) or len(results) != len(texts):
        raise Unparsed(f"exit {done.returncode}: {done.stderr.strip()[-800:]}")
    return results


def _install(sandbox: Sandbox) -> Path:
    """The parser's packages, installed from the lockfile into the sandbox's cache."""
    target = _installed(sandbox)
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Installed beside, then moved in whole, so a check never meets half an install.
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=target.parent))
    try:
        for name in ("package.json", "package-lock.json", "check.mjs"):
            shutil.copy(PACKAGE / name, staging / name)
        argv = sandbox.command(
            staging,
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            network=True,
            env={"npm_config_cache": "/tmp/npm"},
            cache=False,
        )
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise Unparsed(f"installing it took over {INSTALL_TIMEOUT}s") from exc
        if done.returncode != 0:
            raise Unparsed(f"it didn't install: {done.stderr.strip()[-800:]}")
        try:
            os.rename(staging, target)
        except OSError:
            if not target.is_dir():  # Not another check's install, finished first.
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _installed(sandbox: Sandbox) -> Path:
    """Where this lockfile's install goes: a changed lockfile or script installs anew."""
    digest = hashlib.sha256(IMAGE.encode())
    for name in ("package-lock.json", "check.mjs"):
        digest.update((PACKAGE / name).read_bytes())
    return sandbox.cache_dir / "mermaid" / digest.hexdigest()[:16]


def _sandbox(sandbox: Sandbox | None) -> Sandbox:
    return replace(sandbox or Sandbox(), mode=Mode.REQUIRED, image=IMAGE)


def _said(error: str) -> str:
    """Mermaid's message on one line, without the pointer under its excerpt."""
    # A diagram of no type Mermaid knows is named by its first line, not all of it.
    head, sep, text = error.partition("for text: ")
    if sep:
        error = f"{head}{sep}{text.strip().splitlines()[0] if text.strip() else ''}"
    kept = " ".join(
        line.strip() for line in error.splitlines() if line.strip() and not _POINTER.match(line)
    )
    return kept if len(kept) <= MESSAGE else kept[: MESSAGE - 1] + "…"


def _drawn_wrong(text: str) -> list[str]:
    """What Mermaid parses in a C4 diagram and then draws wrong or not at all."""
    found: list[str] = []
    defined: dict[str, int] = {}
    relations: list[tuple[int, list[str]]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        macro = _MACRO.match(raw.strip())
        if not macro:
            continue
        name, inner = macro.groups()
        args = _arguments(inner)
        if args is None:
            found.append(f"line {n}: a quote is left open")
            continue
        positional = [a for a in args if not a.startswith("$")]
        if _STYLE.match(name):
            continue
        if _RELATION.match(name):
            if len(positional) < 3:
                found.append(f"line {n}: `{name}` has no label, and is drawn without one")
            relations.append((n, positional[:2]))
            continue
        alias = positional[0] if positional else ""
        if not _ALIAS.match(alias):
            found.append(f"line {n}: `{alias}` isn't an alias: letters, digits, `_`")
        elif alias in defined:
            found.append(f"line {n}: `{alias}` is defined twice, on lines {defined[alias]} and {n}")
        else:
            defined[alias] = n
    for n, ends in relations:
        found += [
            f"line {n}: the relation names `{end}`, which the diagram doesn't define"
            for end in ends
            if end not in defined
        ]
    return found


def _unfenced(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _arguments(inner: str) -> list[str] | None:
    """A macro's arguments, split at commas outside quotes, or None if a quote is open."""
    out, current, quoted = [], "", False
    for ch in inner:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            out.append(current.strip())
            current = ""
        else:
            current += ch
    if quoted:
        return None
    if current.strip() or out:
        out.append(current.strip())
    return out
