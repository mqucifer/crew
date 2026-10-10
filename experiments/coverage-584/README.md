# Does the coverage map find the tests a shape change breaks? (crew#583, C1)

**Status:** run 2026-10-10 on sprint-metrics at `2ba575e`, the commit epic 468's stories were written against. `check.py` reproduces it; `result.json` is its output.

## The result

**Yes, for the story that broke them.** sm#537 added four keys to the single-sprint response and schema. The map names all three tests crew#584 found by hand for it:

| Test crew#584 found | Named for sm#537 | How the map sees it |
|---|---|---|
| `test_schema_gen.py::test_single_sprint_schema_required_and_cycle_time` | yes | It runs `_generate_schema_files`, which reads `SINGLE_SPRINT_SCHEMA`, a table sm#537 changed |
| `test_schema.py::*_validates_against_schema` | yes, all 10 | They read the schema tables sm#537 changed; the single-sprint ones also run `format_json_report`, which it changed too |
| `test_docs.py::test_drift_check` | yes | It runs the docs generator over the changed code |

Text matching found 0 of 5 broken tests for sm#507 and 1 of 3 for sm#529 (crew#584).

- **Constants needed a second look.** Coverage sees a module-level table run once, at import, under no test. Without reading which names each definition and test uses, `test_schema_gen` was missed. With it, the test is named for sm#537 and sm#538, the two stories that changed a schema table.
- **The map is broad.** sm#536 changed `Card` and `parse_card`, which nearly every test runs: 335 of the 371 tests mapped. Choosing among them under a ceiling is C2's and C3's job, not the map's.

## Limits

- **Tests that need a database aren't in the map.** They're skipped in the sandbox, as they are in the crew's own check: sprint-metrics' `test_service.py` runs 1 of its tests without `SPRINT_METRICS_DB`. The exact-keys test sm#529 broke is one of them.
- **Tests that only run the program as a subprocess aren't in the map.** Coverage can measure a subprocess, but the child inherits the parent's configuration with its context already fixed, so its lines carry no test. The scrape tests kill their server, so nothing would be saved anyway.
- **Python only**, through pytest-cov, which is installed beside the project's dev dependencies and changes no lockfile (the plan's Q4).
