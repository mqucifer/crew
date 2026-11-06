# A criterion's rule, on real data: a replay of crew-presentation#296's split (crew#1037)

Before mqucifer/crew#1038 merges, the Sponsor asked for one real run on existing data
(mqucifer/crew#1037, comment of 2026-11-06T02:13Z). `replay.py` runs this branch's Business
Analyst split of mqucifer/crew-presentation#296, then QA's criteria check on the stories
it makes. Each part reads what a tick would give it today, through the functions
`refine_epics` calls:
- the epic;
- its Goal, mqucifer/crew-presentation#214;
- the Goal's record (its rows unchanged since it was settled at 2026-11-05T15:51Z);
- the board's planned and delivered stories;
- crew-presentation's main.

It is a fresh split: no feedback, nothing kept, and no repair or re-check after the
check.

    uv run python experiments/criterion-rule-1037/replay.py --inputs   # gathers, no model calls
    uv run python experiments/criterion-rule-1037/replay.py

- **Model calls** go through LiteLLM on :4000 only, with the crew's aliases (`crew-analysis`
  for the split, QA's `crew-code-think` for the check). The key is read in the process from
  the main checkout's `.env`.
- **One session at a time.** The criteria check's per-story sessions, which a tick runs
  at once, are run one after another here, because the Spark is near its session limit.
- **Nothing is written to GitHub or the crew's `var/events`.** GitHub is read with the
  caller's `GH_TOKEN`, through clients that refuse every write.
- **Output** goes to `runs/<time>/`, which git ignores:
  - `transcript.jsonl`: every call, its request and answer;
  - `events.jsonl`;
  - `inputs.json`, `split.json`, `check.json` and `result.json`.

## The run, 2026-11-06 (`runs/20261106T022350Z`)

It started at 02:23Z, on crew-presentation main at 0029c88. Its inputs:
- 12 binding rows: R2 to R5, R12 to R16, R28, R29 and R31;
- 7 planned stories of other epics;
- 84 delivered stories;
- the crew's coverage map.

It was paused for 11 minutes between two calls, at the operator's request, while the
Spark was over its session limit. Wall time counts the pause; model time doesn't.

### The split: 4 stories, every criterion with its rule

| Story | Criteria | Follows |
|---|---:|---|
| Show the sprint's stories under their epics on the sprint overview | 7 | R2, R3, R5, R12, R14, R31 |
| Show each story's attempts and what happened to it | 7 | R2, R3, R13 |
| Show the time each story and each epic took | 6 | R3, R4, R15, R28, R29 |
| Show the delivered count and the story card as they are when they disagree | 2 | R2, R16 |

`done` was refused once, for a vague word ("'visible' names a quality") in one `then`,
and the session fixed it. No criterion came without a rule. Some rules are long: story 1's
first rule is a paragraph that also specifies the epic heading.

### QA's check: 2 conflicts, both against R2; none on "escalated"

- **Kept: R2 against every story of the sprint.** Story 1's criterion 1 rule ("Every
  story card whose sprint is this one appears under its epic's heading, whatever its
  status") and its criterion 3 rule (a story closed as not planned still appears) are
  each reported against R2, "The story list is `roster.completed_stories`". The record
  as shown still says that: the Sponsor's answer of 2026-11-05T20:45Z, that R12 decides
  the rows, is entered in the record only before a re-split, and this was a fresh split.
- **Not raised: "escalated".** Story 2's criterion 3 rule includes "after it was
  escalated". Its criterion 5 rule is "plain words ... no internal code — no event kind,
  failure-class code, rule id or column move". QA's session compared the two
  explicitly and judged them by their rules: plain English words aren't codes, so no
  conflict.
- **The new refusal never fired.** In all 4 sessions QA quoted the rule in its first
  `done`. The drop came from QA's own reading of the rules, not from
  `between_rules`.

### Against the split of 2026-11-05

The 2026-11-05 figures come from the crew's event log for card 296 (`var/events/tick.jsonl`,
read only). That log holds counts and sizes, not the stories' text. The two surviving
conflicts are quoted in the story-problem comment of 20:08Z.

| | 2026-11-05, main | 2026-11-06, this branch |
|---|---|---|
| Stories | 5: group under epics; attempt blocks; status and what happened; time; epic heading's done count | 4: group under epics (with the heading's counts); attempts and what happened (with the status badge); time; delivered count against the cards |
| First check | 4 conflicts on 3 stories: group 1, attempt blocks 2, status 1 | 2 conflicts on 1 story, both group against R2 |
| R2 against every story | raised, and kept to the story problem | raised |
| "escalated" against the event-kind ban | raised, and kept to the story problem | not raised |
| After the check | repair (113.1 model min) and re-check (46.4): 2 conflicts left, epic not split | not run |
| Split, model time | 44 calls, 90.8 min | 54 calls, 93.9 min |
| Split, tokens | 3.51M prompt, 70.2k completion (55.1k reasoning) | 4.37M prompt, 77.4k completion (64.2k reasoning) |
| First check, model time | 77 calls, 125.9 min, 5 sessions at once (34.3 min wall) | 53 calls, 66.9 min, 4 sessions one at a time (67.0 min wall) |
| First check, tokens | 3.60M prompt, 95.2k completion (86.2k reasoning) | 2.81M prompt, 78.4k completion (72.6k reasoning) |

It is one run against one. The inputs differ too: main had moved on since 2026-11-05, and so had
the board's planned stories. What the 2026-11-05 attempt-blocks story's two conflicts
were isn't in the event log.
