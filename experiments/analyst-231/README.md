# The Business Analyst's focused split of sprint-metrics#62 (#231)

A read-only live run, 2026-09-30, through LiteLLM on the Spark. `run.py` runs the
loop refinement now runs: the project record, a map of the repository, the files
the epic names in full, and up to two asks for more. Nothing is posted.

**Before:** refinement handed the Business Analyst the whole repository, 159,310
characters (about 40k tokens), for every epic. Every split on 2026-09-30 was over
60k tokens with the rest of its prompt, the band where empty answers begin
(#312).

**This run** (`result.json`):

- The repository part came to 34,091 characters (about 8.5k tokens), with no files
  in full, because #62 names none.
- It split #62 into 4 stories without asking to see a file: the health verdict
  first, what's wrong, what needs attention, and what changed since the prior
  sprint.
- It took 368 s, against 307 s with the whole repository. The time goes on
  thinking, not reading. A smaller prompt is safer here, not faster.

The same epics, measured without a model call, at refinement's 60k-character
threshold: #62 and #184 about 7k tokens, #63 and #65 about 13k (the report code
and its tests in full), #64 about 17k (the metrics code too).

**To watch:** it split without asking, having seen `report.py` only by name.
Whether its criteria stay grounded in the report's real output is for the Code
Reviewer and QA to judge on real stories, and for the UX Designer's note, which
reads the code and adds the exact output.
