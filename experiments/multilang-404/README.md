# The crew on a project that isn't Python (#404, phase 2c)

A read-only live run, 2026-09-30, through LiteLLM on the Spark. `run.py` copies
`tests/fixtures/static-site` (a static HTML page with Playwright tests, one
`javascript` part) and runs the delivery loop: the Developer's answer through the
model, applied, the tests it names checked to be in their files, the project's
checks run in the sandbox on the Playwright image, then QA judging the story from
the test output and the test code. A refusal from QA goes back to the Developer
with its reasons. Nothing is posted.

The story: add a "Changed: …" line to the page's health section, after the status
and before the next section.

**Run 1 (`result-1-files-by-name.json`): the page was never shown.** The
repository view showed only config, docs and Python in full, so `index.html` and
`tests/page.spec.js` appeared by name. Every answer asked to see them and changed
nothing, and QA refused each round, correctly. The fix: source in other languages
is shown whole (`repo_context.TEXT_SUFFIXES`), and a JavaScript part's
`node_modules` and Playwright reports are left out.

**Run 2 (`result-2-reset-between-attempts.json`): passed, on the second QA round.**
The first attempt added the line and two tests, and broke the existing test (it
read the only paragraph in the section, and now there were two). The harness then
put the site back to the fixture, which delivery doesn't: the repair fixed only
the old test, so the checks passed with no feature, and QA refused, naming both
criteria. The repair after QA's refusal did the whole story and QA accepted. The
harness was changed to keep failed work in place, as delivery does.

**Run 3 (`result.json`): passed on the first attempt and the first QA round.**

- The Developer edited `index.html` and `tests/page.spec.js` by find-and-replace,
  kept the existing test working with two paragraphs, and added two tests, one per
  criterion, named by title.
- Playwright ran 3 tests in the sandbox, all passing.
- QA accepted both criteria, citing the new tests by title, and the existing
  "health summary is the first section" test for "before the Current Sprint
  section".
- No asks. 101 s for the Developer.

**What this proves:** a part in another language goes through the same loop as
Python, with its own tools (§14 of the ways of working): tests found by title in
the declared test files, whole-file and find-and-replace edits, no guard, and QA
told which changed files had none.

## The Architect's parts (`design.py`, `design-result.json`)

A fresh design of the same site, with the fixture's design removed from the copy
it reads (the record file is in the repository it's shown).

- **First run:** it proposed the right part (`javascript`, tests in
  `tests/**/*.spec.js`, from `playwright.config.js`), and named its image as
  `mcr.microsoft.com/playwright:v1.63.0-jammy (pinned by digest, kept current by a
  dependency updater)`, a tag and a note with no digest. The design flow refused
  it, and a retry asking for a digest invites an invented one. So the flow now
  pins the image itself (`design.pin_image`): the Architect names a tag, and the
  digest is read from the registry.
- **Second run:** the same part; it named `mcr.microsoft.com/playwright:v1.63.0`,
  which the registry pinned to `sha256:eff16c30…a4a27`, the digest the fixture
  records. Setup `npm ci`, check `npm test`.
