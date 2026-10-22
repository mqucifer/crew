"""Run by `look`, in the Playwright container: serve the mounted site on localhost,
open the page at each width, and print what a reader meets there, as JSON.

Not imported by the crew; `look` copies it into the container's directory. Only
`main` needs playwright, so the tests import the rest.
"""

# ruff: noqa: E501 - the page's own script reads best whole
# mypy: ignore-errors
# It runs in the image, where playwright is; the crew's environment has none.
import functools
import http.server
import json
import sys
import threading


def in_site(path, base):
    """The site's path for a request's path, or None when it's outside the site's base.

    A GitHub Pages project site is served under `/<repo>/`, and its pages name their
    assets so (crew-presentation's are `/crew-presentation/...`): served at the root,
    every one of them answered 404.
    """
    if not path.startswith(base):
        return None
    return "/" + path[len(base) :]


class Site(http.server.SimpleHTTPRequestHandler):
    """The mounted files, under the site's base path."""

    base = "/"

    def translate_path(self, path):
        found = in_site(path, self.base)
        return super().translate_path(found) if found is not None else "/nonexistent"


def is_local(url, origin):
    """Whether the page asks the local server: anywhere on its origin, not only beside
    the page, so a nested page's root-relative and sibling assets load."""
    return url == origin or url.startswith(origin + "/")


READ = """() => {
  const w = document.documentElement.clientWidth;
  const seen = (e) => { const s = getComputedStyle(e); const r = e.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') +
    (e.classList.length ? '.' + [...e.classList].slice(0, 2).join('.') : '');
  const wide = [];
  for (const e of document.body.querySelectorAll('*')) {
    const r = e.getBoundingClientRect();
    if (seen(e) && r.right > w + 1 && ![...e.children].some(c => c.getBoundingClientRect().right > w + 1))
      wide.push({element: name(e), right: Math.round(r.right), text: (e.innerText || '').trim().slice(0, 60)});
  }
  return {
    title: document.title,
    scroll_width: document.documentElement.scrollWidth,
    viewport: w,
    headings: [...document.querySelectorAll('h1,h2,h3')].filter(seen)
      .map(h => h.tagName.toLowerCase() + ' ' + h.innerText.trim()),
    text: document.body.innerText,
    too_wide: wide.slice(0, 15),
    no_alt: [...document.images].filter(i => !i.hasAttribute('alt')).map(i => i.getAttribute('src')),
  };
}"""


def main():
    from playwright.sync_api import sync_playwright

    site, page_path, widths, height, base = (
        sys.argv[1],
        sys.argv[2],
        json.loads(sys.argv[3]),
        int(sys.argv[4]),
        sys.argv[5] if len(sys.argv) > 5 else "/",
    )
    Site.base = base
    handler = functools.partial(Site, directory=site)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    url = f"{origin}{base}{page_path.lstrip('/')}"
    # What the host fetched from other hosts, by URL; anything else off the site is refused.
    with open("/external/map.json") as mapped:
        fetched = json.load(mapped)

    def answer(route):
        wanted = route.request.url
        if is_local(wanted, origin):
            return route.continue_()
        if wanted in fetched:
            name, kind = fetched[wanted]
            return route.fulfill(path=f"/external/{name}", content_type=kind)
        return route.abort()

    out = {"url": url, "widths": {}, "fetched": len(fetched)}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        for width in widths:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route("**/*", answer)
            errors = []
            missing = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)[:200]))
            page.on(
                "requestfailed",
                lambda r, missing=missing: missing.append(f"{r.url[:120]} ({r.failure})"),
            )
            page.on(
                "response",
                lambda r, missing=missing: (
                    r.status >= 400 and missing.append(f"{r.url[:120]} ({r.status})")
                ),
            )
            response = page.goto(url, wait_until="networkidle")
            read = page.evaluate(READ)
            read["status"] = response.status if response else None
            read["errors"] = errors[:5]
            read["missing"] = missing[:10]
            page.screenshot(path=f"/work/shots/{width}.png", full_page=True)
            out["widths"][str(width)] = read
            page.close()
        browser.close()
    print("LOOKED " + json.dumps(out))


if __name__ == "__main__":
    main()
