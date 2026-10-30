<!-- crew:task role=qa kind=story-test key=crew-presentation-s215 -->

**Role:** QA Engineer. A Task this role owns and works itself (ADR 0034).

## Why

The story mqucifer/crew-presentation#215 merged, so what it built is on crew-presentation's main, and a test of it runs on every pull request there (ADR 0006 in mqucifer/project-print). Its test comes after the merge: one of work not yet merged would fail every other pull request.

## The work

Add a test of the story's acceptance criteria to `crew-presentation/`, one per criterion your tests can reach.

Your tests reach crew-presentation only as the next section says: no imports of crew-presentation's code, no reads of its database. Add each test to `crew-presentation/README.md`'s table. crew-presentation as it is now is at `.project/crew-presentation/` in your worktree, to read and never to change: its docs say how it behaves there. Your tests run in CI against crew-presentation's main when your pull request opens, and a red one comes back to you.

If no criterion can be reached that way, change nothing and say why in `done`'s `no_test`: that's recorded, so the retro sees what the tests can't reach.

## What your tests reach

**The surface:** the site's pages, as its `build.py` writes them, served over
HTTP at `QA_BASE_URL`. The tests read the pages and the package the site was
built from (its input), never the site's code.

In CI, `crew-presentation/start.sh` starts crew-presentation and gives your tests `QA_BASE_URL`, `QA_SITE_STATE`, `QA_PACKAGE`, and nothing else of it.

## The story as it merged

**Accept delivery-history 2.0.0 (schema major 2)**

As a **the site operator (or the Sponsor)**, I want **the site to accept delivery-history packages on schema major 2**, so that **when the pin moves to the 2.0.0 release on 2026-11-06, the site builds and deploys instead of failing its pull request**.

## Acceptance criteria

1. **Given** a delivery-history package whose manifest schema_version major is 2 (for example 2.0.0)
   **When** the site's major-version check — the build and the contract check — runs against it
   **Then** the package is accepted: the check does not refuse it as an unsupported major, and the build proceeds to validate its files against the schema the package carries. Today a major-2 package is refused before any field is read, because SUPPORTED_MAJOR_VERSIONS is (1,) in build.py (line 34) and its copy in tests/test_contracts.py (line 128); the merged tests test_build_fails_on_unsupported_major_version (line 223) and test_unsupported_major_version_fails_with_version (line 211) feed "2.0.0" and assert the refusal, and they are updated to exercise a major the site genuinely cannot display (for example 3.0.0) instead

2. **Given** a package whose manifest schema_version major the site does not support (for example 3.0.0)
   **When** the major-version check runs
   **Then** the package is refused, with output naming that major and that the site cannot display it — accepting 2 does not lower the gate to accept any major

3. **Given** a well-formed 2.0.0 package (schema 2.0.0, with sprints, cards, sprint-metrics answers and events) that validates against the schema it carries
   **When** the build runs
   **Then** it completes and writes the same set of pages the site writes for a 1.x package — one sprint-overview page per sprint, one story page per card, and the replay page — so a 2.0.0 package is end-to-end buildable with every page the site builds today

**Existing tests** — this story changes what they assert: tests/test_contracts.py::test_build_fails_on_unsupported_major_version, tests/test_contracts.py::test_unsupported_major_version_fails_with_version

**Estimate** — 2 points

---

Split from [mqucifer/crew-presentation#211](https://github.com/mqucifer/crew-presentation/issues/211) *(Adopt delivery-history 2.0.0 before it replaces 1.x on 2026-11-06)* by the Business Analyst.

## Done when

- [ ] Each criterion of mqucifer/crew-presentation#215 your tests can reach has a test in `crew-presentation/`
