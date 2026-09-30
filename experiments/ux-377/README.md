# The UX Designer on sprint-metrics#62 (#377)

A read-only live run, 2026-09-30, through LiteLLM on the Spark. `run.py` has the
Business Analyst split #62 in memory, then the UX Designer writes its
presentation note against those stories. Nothing is posted; the stories get
stand-in numbers (9001–9004).

**Result** (`results/`): 4 stories, 2 criteria each, no coverage problems, nothing
beyond reach. Split in 307 s, note in 206 s.

- Every criterion asserts something a test can check in the output: an exact
  line, what comes before what, which lines appear and which don't. The docs
  story's criterion requires the worked example to match the tool's real
  output byte for byte.
- The note read the code it's about (`report.py`, `thresholds.py`, the CLI,
  the docs tests) and uses the real thresholds.

**Weak spot:** the sample output isn't consistent with itself (its health
section lists cycle and lead time twice, once against the threshold and once
against the prior sprint), though the criteria are. Nothing checks the sample
against the criteria. The criteria are what QA proves, and the sample is
illustration, but it's evidence to watch.
