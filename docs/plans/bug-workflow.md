# The bug workflow

**Bottom line:** a bug a collaborator files (ADR 0027) is triaged by the Business Analyst, then built, reviewed and proven like a small story, with one addition: its test has to fail before the fix and pass after it. Most of the flow already handles a card with no epic. The work is:
- a way in, since nothing reads a filed bug today;
- one definition of deliverable work in place of about 25 checks for `Story`;
- the reproduction check;
- counting bugs in the record.

**Status:** 2026-10-12. Proposed by Claude when the Sponsor raised crew#407 to P1, beside the critical path, not on it. Nothing built. Who files a bug is decided in ADR 0027.

## Why

Four bugs filed on 2026-10-11 (mqucifer/sprint-metrics#593 to mqucifer/sprint-metrics#596) sit in Needs Refinement with no work type, and no step reads them:
- refinement reads Goals and Epics;
- sprint admission takes only `Story`;
- delivery, review, QA, merge and close all filter on `Story`.

On 2026-10-07 a defect went in as a Goal instead, because nothing else would move it (crew#407).

## What already works

Checked in the code on 2026-10-12:

| Piece | State |
|---|---|
| The board's Work Type | Already offers `Bug`, beside Goal, Epic, Story, Task and Spike |
| A new issue on a delivery repository | Lands in Needs Refinement, with no type |
| Sprint admission | Admits stories with no epic after the epics' work, ordered by priority label, then Rank (`flows/sprint.py`, "parentless") |
| Delivery and merge | Handle a card with no parent (`flows/delivery.py`: `story.parent is None`) |
| The sandbox | Already runs a project's tests on a story's base commit, for the coverage map (`tools/coverage_map.py`) |

What's missing is the way in, the type checks, and the reproduction check.

## The standards it follows

- **The bug report's fields:** what happens, what was expected, the evidence. These are ISO/IEC/IEEE 29119-3's incident report, cut to what ADR 0027 asks for.
- **Triage outcomes:** IEEE 1044's dispositions, in plain words. A bug is either:
  - accepted;
  - not a bug: it works as documented, or it asks for new behaviour (a Goal);
  - a duplicate;
  - in need of more information.
- **Fix with a reproducing test:** the regression-test-first practice from test-driven development. The test is written to fail against the code as it is, and the fix makes it pass. A test that already passed before the fix proves nothing about the bug.

## Steps

| Step | What | Files | Done when |
|---|---|---|---|
| B1 | **One definition of deliverable work.** A shared `DELIVERED_TYPES = {Story, Bug}` replaces the `== STORY_TYPE` checks in admission, delivery, acceptance, merge, close, `delivered` and `crew export`. Checks that mean "a story of an epic" stay as they are: the split, the backlog order under epics, and closing a finished parent | `flows/sprint.py`, `delivery.py`, `acceptance.py`, `merge.py`, `close.py`, `delivered.py`, `cli.py` | A `Bug` card in Ready is admitted, delivered, reviewed, proven and merged in tests, just as a story with no epic is |
| B2 | **Triage, the way in.** For an open issue labelled `bug` in Needs Refinement with no type, filed by a collaborator, the Business Analyst reads the report and the code (with the reading tools, ADR 0025) and answers in one of the four triage outcomes. **Accepted** becomes a card in story form: criteria that reproduce the bug as Given, When, Then (the expected behaviour as the Then), 1–5 points, Work Type `Bug`, moved to Ready, with the report kept above the criteria. **Not a bug** is commented with the reason and closed as not planned. A **duplicate** is commented naming the other issue, and closed as a duplicate. **More information** gets a question and `needs:human`. A bug from someone who isn't a collaborator gets `needs:human`, untouched | `flows/bugs.py`, called from the refinement pass after Goal decomposition; `crews/bug_triage_crew.py`; the analyst's `bug_triage` step in `agents.yaml` | mqucifer/sprint-metrics#593 is triaged to Ready, with criteria a tester could run |
| B3 | **The test reproduces the bug.** For a `Bug`, the Developer's criteria tests run on the base commit before the fix is applied. A test that passes there is refused with that reason: "it doesn't reproduce the bug". After the fix they must pass, as now. QA is told it's a bug, and is shown the report | `flows/delivery.py` (beside the existing base-commit run), `crews/delivery_crew.py` (the refusal), `crews/qa_crew.py` (what QA is shown) | A replay of a fix whose test passed before the fix is refused, and sm#593's real fix comes with a test that fails at its base |
| B4 | **Bugs are counted as bugs.** `delivery-history`'s `Card.type` adds `Bug`, which is a MINOR schema change. The sprint review marks each bug fixed as a bug, apart from the stories delivered. `crew package push` sends them | `packages/delivery_history.py`, `contracts/`, `flows/close.py`, `packages/sprint_metrics_feed.py` | The package's schema carries `Bug`, and the review shows a bug as `Bug: <title>` |
| B5 | **The proof.** sm#593 to sm#596 go through for real: triaged, admitted, fixed and merged by the crew | none | All four closed by the crew, or each triaged out with its reason |

**Order:**
1. **B1 and B4 in one PR.** It's type plumbing, and B4 changes the package's shape, which should land before `delivery-history` freezes it (the shape-first order on crew#521).
2. **B2.**
3. **B3.**
4. **B5** runs on the next tick after B3 merges.

Each PR is checked on a real card through LiteLLM before it's opened.

**Changed while building B2 (2026-10-12): the Business Analyst triages, not the Product Owner.** The Product Owner's role says it doesn't write acceptance criteria or size work, which is the Business Analyst's craft. Accepting a bug is both.

**Found by B2's first real triage (2026-10-12): B3 meets crew#614.** On mqucifer/sprint-metrics#593 the analyst found the project already has a test for the behaviour, `test_get_sprint_free_form_label_returns_metrics`, but it needs a database, so it's skipped wherever tests run without one. That's why CI never caught the bug. A reproducing test the sandbox skips neither fails before the fix nor passes after it. So B3 counts a skipped test as not run, and refuses it with that reason: "it was skipped, not run". A database-backed bug waits on mqucifer/crew#614 (the sandbox runs a project's tests with the services they need).

## Not in this plan

- **Whether a role's own finding is filed as a bug** rather than a retro finding. ADR 0027 leaves it open. It's proposed after B5, from what the four bugs show.
- **A bug's place in sprint admission beyond its priority label.** Admission orders a bug like any card with no epic. A separate bug budget isn't needed until bugs crowd out stories.
