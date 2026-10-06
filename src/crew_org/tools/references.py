"""No bare issue numbers in anything the crew posts to GitHub (crew#456).

GitHub turns `#123` and `GH-123` into a link to that number in whichever repository the
text is posted to, and leaves a "mentioned this" on that issue. The crew works across
repositories, and models copy numbers from their context, so a bare number was a link
to the wrong issue as often as not. Every body the crew posts passes through `explicit`
first (`IssueClient._request`), so this doesn't depend on any prompt.

A bare number is written out as a link to that number in the repository the text is
posted to, which is what GitHub would have linked anyway, made visible. A number that
isn't an issue or pull request there is not linked at all: it is put in code, where
GitHub leaves it alone, and recorded.

Code, HTML comments (the crew's markers and data), links already written and URLs are
left as they are. A row ID like R1 or Q2 has no `#`, so it is never touched.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# What is never rewritten: fenced code, inline code, HTML comments, markdown links and
# images, autolinks in angle brackets, and bare URLs.
_PROTECTED = re.compile(
    r"```.*?```"
    r"|`[^`\n]*`"
    r"|<!--.*?-->"
    r"|!?\[[^\]\n]*\]\([^)\n]*\)"
    r"|<https?://[^>\s]*>"
    r"|https?://\S+",
    re.S,
)
# `#123` or `GH-123`, not part of a longer token: not `owner/repo#123`, `PR#5`, a URL's
# fragment, a heading's `##`, or an HTML entity `&#123;`.
_BARE = re.compile(r"(?<![\w/#&.-])(?:#|GH-)(\d+)\b")


def explicit(
    text: str, *, owner: str, repo: str, exists: Callable[[int], bool]
) -> tuple[str, list[int]]:
    """`text` with every bare reference made explicit, and the numbers that couldn't be.

    `exists(n)` says whether `n` is an issue or pull request in `owner/repo`.
    """
    unresolved: list[int] = []

    def link(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if exists(number):
            return f"[{owner}/{repo}#{number}](https://github.com/{owner}/{repo}/issues/{number})"
        unresolved.append(number)
        return f"`{match.group(0)}`"

    out: list[str] = []
    last = 0
    for protected in _PROTECTED.finditer(text):
        out.append(_BARE.sub(link, text[last : protected.start()]))
        out.append(protected.group(0))
        last = protected.end()
    out.append(_BARE.sub(link, text[last:]))
    return "".join(out), unresolved
