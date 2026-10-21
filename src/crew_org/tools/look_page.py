"""Run by `look`, in the Playwright container: serve the mounted site on localhost,
open the page at each width, and print what a reader meets there, as JSON.

Not imported by the crew; `look` copies it into the container's directory.
"""

# ruff: noqa: E501 - the page's own script reads best whole
# mypy: ignore-errors
# It runs in the image, where playwright is; the crew's environment has none.
import functools
import http.server
import json
import sys
import threading

from playwright.sync_api import sync_playwright

site, page_path, widths, height = (
    sys.argv[1],
    sys.argv[2],
    json.loads(sys.argv[3]),
    int(sys.argv[4]),
)
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/{page_path.lstrip('/')}"
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
# What the host fetched from other hosts, by URL; anything else off the site is refused.
with open("/external/map.json") as mapped:
    fetched = json.load(mapped)
local = url.rsplit("/", 1)[0]


def answer(route):
    wanted = route.request.url
    if wanted.startswith(local):
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
