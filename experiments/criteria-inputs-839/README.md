# What the criteria check is given: a replay of sprint-metrics#752's re-check (crew#839)

`replay.py` renders the start that sprint-metrics#752's re-check would get with today's
code. The re-check covered one repaired story, *Delete the single-sprint response code
and its schema with its last consumer*, and took 33.0 minutes. The replay reads the
session records the operator kept (the 752 first check and re-check, and 753's /range
story) and the sprint-metrics checkout those sessions read, at head 9602a12. With `gh` it
reads the stories the split created (for their Follows and Builds on lines) and the
board's statuses. It's read-only, and it makes no model calls.

    uv run python experiments/criteria-inputs-839/replay.py <records dir> <checkout>

Run 2026-10-24, with the checkout extracted by `git archive 9602a12` from a sprint-metrics clone.

## The re-check's start, then and now

| Part | Then | Now |
|---|---:|---:|
| step | 852 | 852 |
| goal | 896 | 896 |
| project_log | 2,768 | 2,768 |
| conclusion (the record) | 15,608 | 7,875 |
| siblings | 9,190 | 9,321 |
| planned | 16,824 | 33,879 |
| stories | 3,176 | 3,225 |
| project, map, hints, rules | 12,755 | 12,756 |
| recheck | — | 2,716 |
| order | — | 1,284 |
| facts (the pre-check) | — | 0 |
| **total** | **62,090** | **75,572** |

- **It carries the conflict and the earlier verdict.** The re-check part quotes the first check's conflict (criterion 4's "exactly the 8 accepted types" against the code's 7 `VALID_EVENT_TYPES`) and its why. It then shows criterion 4 before and after the repair, and asks whether the conflict is resolved and whether the repair made a new one.
- **The record halves**, 15.6k to 7.9k. The story follows R3, R12, R22 and R26, which are shown whole. The other 13 rows take a line each, so R2 and R21, which say the same thing, cost two lines.
- **The order part** lists 751, 752 (this epic) and 753, with the Product Owner's proposal order and each epic's status. It also shows R2 ("4.0.0 in landing order"), the record's row on order.
- **The pre-check finds nothing undeclared for this story.** Its Existing tests line already names every test that uses `format_json_report` or `single-sprint.json`.
- **The planned part doubles**, 16.8k to 33.9k, because criteria are no longer cut. Each story now sits under its epic with its status. Epic #684 shows as "Needs Refinement, Goal #666": another Goal's epic, not yet split again. The first check's conflict came from #717, under that epic. The statuses are the board's at the time of the replay, which is after the re-check: #718 to #721 closed at 17:53Z.

### If the start must shrink

The growth is all in the planned part. Two measures from this replay:

- **Another Goal's epics' stories take 23.2k** of the 33.9k (epic #684's five). Shown as title and status only, without their criteria, the start would be about 53k. But the conflict the first check found was against #717's criteria.
- **The board as it is now** (`planned_elsewhere` leaves out closed stories) gives 4 of the 8 stories, 14.9k, and a start of about 56.6k.

Nothing is cut in this PR. The session's start event now records each part's size and whether it was a re-check. The next tick shows whether a whole planned part costs time, before anything is taken out.

## The pre-check on 753's /range story

*Stop the /range response offering the escalation measures.* Its criteria say each entry "has no escalation_rate_percent key and no escalation_count key". Its Existing tests line names tests/test_sprint_range.py, tests/test_service.py, tests/test_crew_performance.py and tests/test_schema.py.

- **Names its criteria remove:** `escalation_rate_percent`, `escalation_count`.
- **Undeclared merged tests using them:** 7, in 4 files: tests/test_docs.py (4), tests/test_metrics.py (1), tests/test_prior_sprint.py (1) and tests/test_schema_gen.py (1).

Against the tests QA named:

| File | Named by | Pre-check |
|---|---|---|
| tests/test_report.py | the /range story's session (b0e18381) | **not found**: it pins the CLI table's "Escalation rate" header and its "0%" cell, a display string, not a name the criteria use |
| tests/test_scrape.py | the brief | **not found**: it uses an `--escalations` flag and Prometheus names, not those keys |
| tests/test_prior_sprint.py | the brief | found |
| tests/test_schema_gen.py | the brief | found |

So the pre-check catches two of the four, plus tests/test_docs.py and tests/test_metrics.py, which QA didn't name. The two it misses pin what's removed through a different spelling: a display label, or a CLI flag. Name matching can't reach those, so they stay the criteria check's to find by reading.
