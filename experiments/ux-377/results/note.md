## Presentation note

**Who reads it:** The Scrum Master, opening the markdown standup report before the daily meeting. The question they bring: 'Is the crew doing well this sprint, and what needs my attention?' They need to answer it in a five-second scan of the top of the report, before any raw metric values.

**The finished output, as it should read**

~~~
# Crew Performance Report

Report date: 2024-02-10

**Status**: Attention needed
- Cycle time: 7 days (threshold: 5 days)
- Lead time: 9 days (threshold: 7 days)
- Cycle time: 7 days (was 6 days, +1)
- Lead time: 9 days (was 8 days, +1)

## Prior Sprint 2024-01

- **Cycle time**: 6 days
- **Lead time**: 8 days
- **Throughput**: 1 cards
- **WIP violations**: 0
- **Blocked aging**: 0 days
- **Escalation rate**: 0%
- **First-attempt rate**: 100%

## Current Sprint

- **Cycle time**: 7 days (was 6 days, +1) ⚠️
- **Lead time**: 9 days (was 8 days, +1) ⚠️
- **Throughput**: 1 (was 1, 0)
- **WIP violations**: 0 (was 0, 0)
- **Blocked aging**: 0 days (was 0 days, 0)
- **Escalation rate**: 0% (was 0%, 0)
- **First-attempt rate**: 100% (was 100%, 0)

## Crew Performance Summary

- **Completed**: 1
- **In progress**: 0
- **Blocked**: 0
~~~

**What each story adds for the reader** (added to each story's criteria)

- #9001
  - **Given** A cards file with one completed card (created 2024-01-01, started 2024-01-03, completed 2024-01-07) producing cycle time 4, lead time 6, throughput 1, first-attempt rate 100%, all within default thresholds, and no --prior-sprint flag **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run **Then** The line immediately following the blank line after 'Report date: 2024-01-15' is exactly '**Status**: Healthy', and the next non-blank line after that is '## Current Sprint' (no other content lines between the status line and the section heading)
  - **Given** A cards file with one completed card (cycle time 7 days, breaching the default threshold of 5) and no --prior-sprint flag **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run **Then** The stdout contains exactly one line beginning with '**Status**: ' and that line is exactly '**Status**: Attention needed'; no second line matching the pattern '**Status**:' appears anywhere in the output
- #9002
  - **Given** A cards file with two completed cards producing cycle time 6 days (exceeds default 5) and lead time 8 days (exceeds default 7), and no --prior-sprint flag **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run **Then** Between '**Status**: Attention needed' and '## Current Sprint', the line '- Cycle time: 6 days (threshold: 5 days)' appears before the line '- Lead time: 8 days (threshold: 7 days)', and both lines start with '- ' (a hyphen, space) with no bold markers
  - **Given** A cards file with one completed card producing cycle time 4 days, lead time 6 days, throughput 1, first-attempt rate 100% (no threshold breached), and no --prior-sprint flag **When** sprint-metrics cards.json --markdown --sprint-date 2024-01-15 is run **Then** The lines between '**Status**: Healthy' and '## Current Sprint' (excluding blank lines) number zero; no line starting with '- ' appears in that range
- #9003
  - **Given** A sprints JSON file where '2024-01' contains one card (created 2024-01-01, started 2024-01-03, completed 2024-01-09: cycle 6, lead 8, throughput 1) and '2024-02' contains one card (created 2024-02-01, started 2024-02-03, completed 2024-02-09: cycle 6, lead 8, throughput 1), so cycle and lead are unchanged but throughput delta is 0 **When** sprint-metrics sprints.json --prior-sprint 2024-01 --markdown --sprint-date 2024-02-10 is run **Then** The health section contains the line 'No changes since prior sprint' and contains no line matching the pattern '- <metric>: <value> (was <value>, <delta>)'
  - **Given** A sprints JSON file where '2024-01' contains one card (cycle time 6, lead time 8, throughput 1) and '2024-02' contains one card (cycle time 4, lead time 6, throughput 1), so both cycle and lead improved by 2 days and throughput is unchanged **When** sprint-metrics sprints.json --prior-sprint 2024-01 --markdown --sprint-date 2024-02-10 is run **Then** In the health section (between the status line and '## Current Sprint'), the line '- Cycle time: 4 days (was 6 days, -2)' appears before the line '- Lead time: 6 days (was 8 days, -2)', and no line containing 'Throughput', 'WIP', 'Blocked', 'Escalation', or 'First-attempt' appears in that health section (those deltas are all 0)
- #9004
  - **Given** The docs/formats.md file after this story is delivered, with the input cards.json containing one completed card (created 2024-01-01, started 2024-01-03, completed 2024-01-07) and one in-flight card (created 2024-01-04) **When** a reader looks at the '### Markdown' worked example under 'Worked examples' in docs/formats.md **Then** The example shows the line '**Status**: Healthy' between 'Report date: 2024-01-15' and '## Current Sprint', and running the same CLI command on the same input produces stdout whose content matches the example's markdown block byte-for-byte
  - **Given** The docs/formats.md file after this story is delivered, with the --prior-sprint example input (2024-01: cycle 6, lead 7; 2024-02: cycle 4, lead 6) **When** a reader looks at the '--prior-sprint' worked example in docs/formats.md **Then** The example's markdown block shows a line matching the pattern '- Cycle time: 4 days (was 6 days, -2)' between '**Status**:' and '## Prior Sprint 2024-01', and does NOT show a line for Throughput in the health section (its delta is 0), and the CLI run on the same input produces stdout matching that example block

_Looked at: #48 Goal: the tool presents, not just reports, #62 Epic: Lead the markdown standup report with a five-second health summary, #9001 Show crew health status at the top of the markdown report, #9002 List breached metrics as attention items under the health status, #9003 Show metric changes since the prior sprint in the health section, #9004 Update the docs/formats.md worked example to show the health summary, docs/formats.md (current worked examples and markdown output structure), docs/thresholds.md (DEFAULT_THRESHOLDS and breach directions), src/sprint_metrics/report.py (format_markdown_report, _sprint_section, _prior_sprint_section, _summary_section, _CANONICAL_ORDER), src/sprint_metrics/thresholds.py (DEFAULT_THRESHOLDS, calculate_flags, _flag), src/sprint_metrics/cli.py (main, --prior-sprint handling), tests/test_docs.py (test_worked_example_output_matches_doc, test_formats_worked_example_markdown_output), tests/test_crew_performance.py (test_markdown_report_for_empty_sprint, test_markdown_report_includes_crew_performance_summary)_