"""A page as its reader meets it, at the reader's widths (crew#846, for crew#830 too).

The UX Designer asks whether an epic gives its reader what they need, and the site's
first live review found what no criterion caught: the phone-width bug
mqucifer/crew-presentation#98. So it can look: a headless browser opens the page at
each width and says what a reader would meet there, in text:
- the headings and the visible text, in reading order;
- whether the page scrolls sideways at that width, and the elements that cause it;
- images without alternative text.

A screenshot of each width is kept for a person or a filed correction (crew#830).

**What it serves, and how** (the operator, 2026-10-21): the site's JavaScript is
generated code, so the browser runs in the sandbox, hardened as it is and with no
network. The host fetches the published files (the `github-pages` artifact of the
default branch's last successful deploy, exactly what the reader is served) or takes a
directory of the checkout (the design mock), mounts them read-only, and the container
serves them on localhost. Nothing is built and nothing reaches the network from the
container.

A page can name stylesheets, scripts and fonts on other hosts (crew-presentation's
design system and fonts come from CDNs). The host fetches those too (`externals`),
and the browser answers each request from that copy; anything else off the site is
refused. The page is judged as styled, and the container stays offline.

The image carries the browsers but not the Python package that drives them. That is
installed once, pinned, into the sandbox's cache, in a container with the network
and no site: the sandbox's own rule, the network only to resolve dependencies.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crew_org.tools.sandbox import Mode, Sandbox

# Pinned, as every image the crew runs is. The image carries the browsers, and the
# package that drives them is installed once at the same version (`_driver`).
VERSION = "1.63.0"
IMAGE = f"mcr.microsoft.com/playwright/python:v{VERSION}-noble"
DRIVER = f"look/playwright-{VERSION}"
# A phone, a tablet, a laptop, when the Goal names none.
WIDTHS = (375, 768, 1280)
HEIGHT = 800
TIMEOUT = 180
SITE = "/site"
EXTERNAL = "/external"
# What the host fetches for a page from other hosts: a bound, not a crawl.
EXTERNAL_COUNT = 40
EXTERNAL_BYTES = 10_000_000
# Google Fonts answers with the font format the browser asks for.
CHROME = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
_NAMED = re.compile(
    r"""<(?:link|script|img)\b[^>]*?\b(?:href|src)=["'](https?://[^"']+)["']""", re.I
)
_IN_CSS = re.compile(r"""url\(\s*["']?(https?://[^"')\s]+)""", re.I)
TEXT_CHARS = 6000

# Run in the container, beside nothing else of the crew's.
SCRIPT = Path(__file__).with_name("look_page.py")


class NotLooked(RuntimeError):
    """The page couldn't be looked at: no files to serve, or the container failed."""


@dataclass(frozen=True)
class Looked:
    """What the reader meets at each width, and where each width's screenshot is."""

    page: str
    widths: dict[int, dict[str, Any]]
    shots: dict[int, Path] = field(default_factory=dict)

    def text(self) -> str:
        """As the UX Designer reads it."""
        lines = [f"`{self.page}`, as a reader meets it:"]
        for width, seen in self.widths.items():
            sideways = seen["scroll_width"] > seen["viewport"]
            lines.append(f"\n## At {width}px: {seen.get('title') or '(no title)'}")
            if seen.get("status") not in (200, None):
                lines.append(f"- The page answered {seen['status']}.")
            lines.append(
                f"- Scrolls sideways: {seen['scroll_width']}px of content in {seen['viewport']}px."
                if sideways
                else "- Fits the width: no sideways scroll."
            )
            for wide in seen.get("too_wide") or []:
                lines.append(f"  - `{wide['element']}` reaches {wide['right']}px: {wide['text']!r}")
            if seen.get("no_alt"):
                lines.append(f"- Images without alt text: {', '.join(map(str, seen['no_alt']))}")
            if seen.get("missing"):
                lines.append(f"- Didn't load: {'; '.join(seen['missing'])}")
            if seen.get("errors"):
                lines.append(f"- Script errors: {'; '.join(seen['errors'])}")
            if seen.get("headings"):
                lines.append("- Headings: " + " / ".join(seen["headings"]))
        first = next(iter(self.widths.values()), {})
        text = str(first.get("text") or "").strip()
        if text:
            cut = text[:TEXT_CHARS] + ("\n… (cut)" if len(text) > TEXT_CHARS else "")
            width = next(iter(self.widths))
            lines.append(f"\n## The text, in reading order (at {width}px)\n\n{cut}")
        return "\n".join(lines)


def published(github: Any, repo: str, into: Path) -> Path:
    """The published site's files, from the default branch's last deployed `github-pages`
    artifact, in `into`."""
    found = github.pages_artifact(repo)
    if found is None:
        raise NotLooked(f"{repo} has no published site to look at: no github-pages artifact")
    _, zipped = found
    with zipfile.ZipFile(io.BytesIO(zipped)) as outer:
        inner = outer.read("artifact.tar")
    with tarfile.open(fileobj=io.BytesIO(inner)) as tar:
        # Only regular files and directories, inside `into`.
        tar.extractall(into, filter="data")
    return into


def base_path(github: Any, repo: str) -> str:
    """The path the published site is served under: `/<repo>/` for a project site, `/`
    for an organisation's own site or one on its own domain, as GitHub says it's served."""
    site = github.pages(repo) or {}
    path = urlparse(site.get("html_url") or "").path or "/"
    return path if path.endswith("/") else f"{path}/"


class Fetched:
    """What one session's looks fetched, kept until it ends: each repository's published
    site with its base path, and other hosts' files by URL. Each look re-downloaded the
    artifact and every external before (crew#846's review)."""

    def __init__(self) -> None:
        self._dir: Path | None = None
        self.sites: dict[str, tuple[Path, str]] = {}
        # Each URL's file and content type: a stylesheet served without its type is refused.
        self.found: dict[str, tuple[str, str]] = {}

    def _in(self, name: str) -> Path:
        if self._dir is None:
            self._dir = Path(tempfile.mkdtemp(prefix="look-"))
        path = self._dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def site(self, github: Any, repo: str) -> tuple[Path, str]:
        """The published site's files and the path it's served under, fetched once."""
        if repo not in self.sites:
            files = published(github, repo, self._in(f"site/{repo}"))
            self.sites[repo] = (files, base_path(github, repo))
        return self.sites[repo]

    def externals(self) -> Path:
        return self._in("external")

    def close(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir, self.sites, self.found = None, {}, {}


def externals(
    files: Path, page: str, into: Path, found: dict[str, tuple[str, str]] | None = None
) -> dict[str, tuple[str, str]]:
    """The page's resources on other hosts, fetched into `into`, by URL.

    What the page names in a link, script or img, and the fonts and images its
    stylesheets name: the page's own, so the bound can't be spent on other pages'.
    `found` is what earlier looks fetched, which isn't fetched again. A URL that can't
    be fetched is left out, and the page meets it as a reader offline would.
    """
    import httpx  # noqa: PLC0415

    found = {} if found is None else found
    wanted = list(dict.fromkeys(_NAMED.findall((files / page).read_text(errors="replace"))))
    seen: set[str] = set()
    count = total = 0
    with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": CHROME}) as web:
        while wanted and count < EXTERNAL_COUNT and total < EXTERNAL_BYTES:
            url = wanted.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if url in found:
                count += 1
                # A stylesheet fetched before still names its fonts for this page.
                name, kind = found[url]
                if "css" in kind:
                    css = (into / name).read_text(errors="replace")
                    wanted += [u for u in _IN_CSS.findall(css) if u not in seen]
                continue
            try:
                got = web.get(url)
                got.raise_for_status()
            except httpx.HTTPError:
                continue
            name = hashlib.sha1(url.encode()).hexdigest()
            (into / name).write_bytes(got.content)
            found[url] = (name, got.headers.get("content-type", "application/octet-stream"))
            count += 1
            total += len(got.content)
            if "css" in got.headers.get("content-type", ""):
                wanted += [u for u in _IN_CSS.findall(got.text) if u not in seen]
    (into / "map.json").write_text(json.dumps(found))
    return found


def _driver(sandbox: Sandbox, work: Path) -> None:
    """The pinned package that drives the image's browsers, in the sandbox's cache."""
    if (sandbox.cache_dir / DRIVER / "playwright").is_dir():
        return
    argv = sandbox.command(
        work,
        ["pip", "install", "--quiet", "--target", f"/cache/{DRIVER}", f"playwright=={VERSION}"],
        network=True,
    )
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise NotLooked(f"installing playwright took over {TIMEOUT}s") from exc
    if done.returncode != 0:
        raise NotLooked(f"playwright didn't install: {done.stderr[-800:]}")


def look(
    files: Path,
    page: str = "index.html",
    widths: tuple[int, ...] = WIDTHS,
    *,
    sandbox: Sandbox | None = None,
    keep: Path | None = None,
    base: str = "/",
    fetched: Fetched | None = None,
) -> Looked:
    """Open `page` of the site in `files` at each width, in the sandbox, offline.

    The files are served under `base`, the path the published site is served under
    (`base_path`), so the paths its pages name for their assets resolve. Other hosts'
    files are kept in `fetched` for the session's next look, when given.

    Screenshots go to `keep` when given, as `<width>.png`; otherwise they go with the
    container's directory.
    """
    sandbox = replace(sandbox or Sandbox(), mode=Mode.REQUIRED, image=IMAGE)
    if reason := sandbox.unavailable_reason():
        raise NotLooked(reason)
    own = fetched is None
    kept = fetched or Fetched()
    work = Path(tempfile.mkdtemp(prefix="look-"))
    try:
        externals(files, page, kept.externals(), kept.found)
        _driver(sandbox, work)
        (work / "shots").mkdir()
        shutil.copy(SCRIPT, work / "look.py")
        argv = sandbox.command(
            work,
            ["python3", "look.py", SITE, page, json.dumps(list(widths)), str(HEIGHT), base],
            network=False,
            env={"PYTHONPATH": f"/cache/{DRIVER}"},
            readonly={files: SITE, kept.externals(): EXTERNAL},
        )
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise NotLooked(f"the browser took over {TIMEOUT}s") from exc
        said = next(
            (line[7:] for line in done.stdout.splitlines() if line.startswith("LOOKED ")), None
        )
        if done.returncode != 0 or said is None:
            raise NotLooked(f"the browser failed (exit {done.returncode}): {done.stderr[-800:]}")
        found = json.loads(said)["widths"]
        shots: dict[int, Path] = {}
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            for shot in (work / "shots").glob("*.png"):
                shutil.copy(shot, keep / shot.name)
                shots[int(shot.stem)] = keep / shot.name
        return Looked(page, {int(w): v for w, v in found.items()}, shots)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if own:
            kept.close()
