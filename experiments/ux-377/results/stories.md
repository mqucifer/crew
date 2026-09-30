As a **Scrum Master**, I want **the markdown standup report to open with a one-line status (Healthy or Attention needed) before any metric detail**, so that **I can tell in one glance whether this sprint needs my investigation or is within normal bounds**.

## Acceptance criteria

1. **Given** a cards file with one completed card whose cycle time is 4 days (below the default threshold of 5) and throughput is 1 (meeting the default threshold of 1)
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** the stdout contains the line '**Status**: Healthy' appearing after the line 'Report date: 2024-01-15' and before the line '## Current Sprint'

2. **Given** a cards file with one completed card whose cycle time is 7 days (exceeding the default threshold of 5)
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** the stdout contains the line '**Status**: Attention needed' appearing after the line 'Report date: 2024-01-15' and before the line '## Current Sprint'

3. **Given** a cards file containing an empty array (no cards)
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** the stdout contains the line '**Status**: Attention needed' (because throughput 0 falls below threshold 1 and first_attempt_rate 0 falls below threshold 80)

<!-- crew:ux-criteria -->
*What the reader sees, added by the UX Designer:*

4. **Given** A cards file with one completed card (created 2024-01-01, started 2024-01-03, completed 2024-01-07) producing cycle time 4, lead time 6, throughput 1, first-attempt rate 100%, all within default thresholds, and no --prior-sprint flag
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** The line immediately following the blank line after 'Report date: 2024-01-15' is exactly '**Status**: Healthy', and the next non-blank line after that is '## Current Sprint' (no other content lines between the status line and the section heading)

5. **Given** A cards file with one completed card (cycle time 7 days, breaching the default threshold of 5) and no --prior-sprint flag
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** The stdout contains exactly one line beginning with '**Status**: ' and that line is exactly '**Status**: Attention needed'; no second line matching the pattern '**Status**:' appears anywhere in the output
**Existing tests** — this story changes what they assert: test_worked_example_output_matches_doc (asserts full markdown output equals the doc's worked example), test_markdown_report_for_empty_sprint, test_markdown_report_includes_crew_performance_summary — these assert on the full or structural markdown output and must be updated to include the new status line

**Estimate** — 3 points

---

Split from #62 *(Lead the markdown standup report with a five-second health summary)* by the Business Analyst.

---

As a **Scrum Master**, I want **the health section to name each breached metric with its value and threshold, so I know exactly what to investigate without reading the full table**, so that **I can triage the top of the report in five seconds instead of scanning every metric row**.

## Acceptance criteria

1. **Given** a cards file with one completed card whose cycle time is 7 days and throughput is 1, and a thresholds file setting cycle_time_days to 5
   **When** sprint-metrics cards.json --markdown --thresholds thresholds.json --sprint-date 2024-01-15 is run
   **Then** the stdout contains, between the line '**Status**: Attention needed' and the line '## Current Sprint', the line '- Cycle time: 7 days (threshold: 5 days)'

2. **Given** a cards file with one completed card whose cycle time is 4 days (no threshold breached)
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** no lines matching the pattern '- <metric>: <value> (threshold: <value>)' appear between the status line and the '## Current Sprint' heading

3. **Given** a cards file containing an empty array (no cards), so throughput is 0 and first_attempt_rate_percent is 0
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** the stdout contains the line '- Throughput: 0 (threshold: 1)' and the line '- First attempt rate: 0% (threshold: 80%)' between the status line and '## Current Sprint', in canonical metric order (throughput before first_attempt_rate)

<!-- crew:ux-criteria -->
*What the reader sees, added by the UX Designer:*

4. **Given** A cards file with two completed cards producing cycle time 6 days (exceeds default 5) and lead time 8 days (exceeds default 7), and no --prior-sprint flag
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** Between '**Status**: Attention needed' and '## Current Sprint', the line '- Cycle time: 6 days (threshold: 5 days)' appears before the line '- Lead time: 8 days (threshold: 7 days)', and both lines start with '- ' (a hyphen, space) with no bold markers

5. **Given** A cards file with one completed card producing cycle time 4 days, lead time 6 days, throughput 1, first-attempt rate 100% (no threshold breached), and no --prior-sprint flag
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** The lines between '**Status**: Healthy' and '## Current Sprint' (excluding blank lines) number zero; no line starting with '- ' appears in that range
**Builds on** — #1

**Estimate** — 2 points

---

Split from #62 *(Lead the markdown standup report with a five-second health summary)* by the Business Analyst.

---

As a **Scrum Master**, I want **the health section to list which metrics moved since the prior sprint, so I notice regressions before reading the full comparison**, so that **a worsening cycle time or dropped throughput is visible at the top without scrolling to the current-sprint detail**.

## Acceptance criteria

1. **Given** a sprints JSON file with '2024-01' containing one card (cycle time 6 days, throughput 1) and '2024-02' containing one card (cycle time 4 days, throughput 1)
   **When** sprint-metrics sprints.json --prior-sprint 2024-01 --markdown --sprint-date 2024-02-10 is run
   **Then** the stdout contains, in the health section (between the status line and '## Current Sprint'), the line '- Cycle time: 4 days (was 6 days, -2)' and does NOT contain a line for throughput (delta is 0)

2. **Given** a cards file with one completed card and no --prior-sprint flag
   **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run
   **Then** the health section contains no lines referencing prior values (no 'was' or 'since' text between the status/attention lines and '## Current Sprint')

3. **Given** a sprints JSON file where '2024-01' and '2024-02' contain identical cards producing identical metrics
   **When** sprint-metrics sprints.json --prior-sprint 2024-01 --markdown --sprint-date 2024-02-10 is run
   **Then** the stdout contains the line 'No changes since prior sprint' in the health section, and no metric-delta lines

<!-- crew:ux-criteria -->
*What the reader sees, added by the UX Designer:*

4. **Given** A sprints JSON file where '2024-01' contains one card (created 2024-01-01, started 2024-01-03, completed 2024-01-09: cycle 6, lead 8, throughput 1) and '2024-02' contains one card (created 2024-02-01, started 2024-02-03, completed 2024-02-09: cycle 6, lead 8, throughput 1), so cycle and lead are unchanged but throughput delta is 0
   **When** sprint-metrics sprints.json --prior-sprint 2024-01 --markdown --sprint-date 2024-02-10 is run
   **Then** The health section contains the line 'No changes since prior sprint' and contains no line matching the pattern '- <metric>: <value> (was <value>, <delta>)'

5. **Given** A sprints JSON file where '2024-01' contains one card (cycle time 6, lead time 8, throughput 1) and '2024-02' contains one card (cycle time 4, lead time 6, throughput 1), so both cycle and lead improved by 2 days and throughput is unchanged
   **When** sprint-metrics sprints.json --prior-sprint 2024-01 --markdown --sprint-date 2024-02-10 is run
   **Then** In the health section (between the status line and '## Current Sprint'), the line '- Cycle time: 4 days (was 6 days, -2)' appears before the line '- Lead time: 6 days (was 8 days, -2)', and no line containing 'Throughput', 'WIP', 'Blocked', 'Escalation', or 'First-attempt' appears in that health section (those deltas are all 0)
**Builds on** — #1

**Estimate** — 3 points

---

Split from #62 *(Lead the markdown standup report with a five-second health summary)* by the Business Analyst.

---

As a **Crew member reading the documentation**, I want **the worked example in docs/formats.md to show the markdown output including the health section**, so that **I can see what the top of the standup report looks like before I run the tool**.

## Acceptance criteria

1. **Given** the docs/formats.md file after this story is delivered
   **When** a reader looks at the '### Markdown' worked example under 'Worked examples'
   **Then** the example shows a '**Status**: Healthy' line between 'Report date:' and '## Current Sprint', matching what the CLI actually produces for the example input

2. **Given** the docs/formats.md file after this story is delivered
   **When** a reader looks at the '--prior-sprint' worked example
   **Then** the example shows the health section including a changes-since-prior line (e.g. '- Cycle time: 4 days (was 6 days, -2)') between the status line and '## Prior Sprint 2024-01'

<!-- crew:ux-criteria -->
*What the reader sees, added by the UX Designer:*

3. **Given** The docs/formats.md file after this story is delivered, with the input cards.json containing one completed card (created 2024-01-01, started 2024-01-03, completed 2024-01-07) and one in-flight card (created 2024-01-04)
   **When** a reader looks at the '### Markdown' worked example under 'Worked examples' in docs/formats.md
   **Then** The example shows the line '**Status**: Healthy' between 'Report date: 2024-01-15' and '## Current Sprint', and running the same CLI command on the same input produces stdout whose content matches the example's markdown block byte-for-byte

4. **Given** The docs/formats.md file after this story is delivered, with the --prior-sprint example input (2024-01: cycle 6, lead 7; 2024-02: cycle 4, lead 6)
   **When** a reader looks at the '--prior-sprint' worked example in docs/formats.md
   **Then** The example's markdown block shows a line matching the pattern '- Cycle time: 4 days (was 6 days, -2)' between '**Status**:' and '## Prior Sprint 2024-01', and does NOT show a line for Throughput in the health section (its delta is 0), and the CLI run on the same input produces stdout matching that example block
**Builds on** — #1, #2, #3

**Estimate** — 1 points

---

Split from #62 *(Lead the markdown standup report with a five-second health summary)* by the Business Analyst.