# Does the graph's selection spare the Developer its first ask? (crew#583, C3)

**Status:** run 2026-10-10. Epic 468's eight first Developer attempts, from the proxy's log, resent through LiteLLM with their repository section rebuilt by C3's `focused_context`, at the stories' base with the coverage map. `replay.py` reproduces it; `results.json` is its output.

## Before choosing the rule

Measured offline first, on what each first attempt then asked for (23 files across the eight stories):

| Selection | Covers what was asked | Mean characters |
|---|---|---|
| The files the story names (before C3) | 0 of 23 | 49k |
| … and their imports | 5 of 23 | 67k |
| … and the files that import them | 11 of 23 | 180k |
| … and three covering test files | 12 of 23 | 268k |

Showing the whole graph would cover about half the asks at four to five times the size, past the 60k tokens where answers start coming back empty. So C3 shows the named code with its imports, and names the rest: the files that import it, and the merged tests that run it.

## The result

| Story | Prompt tokens before | Asked before | Prompt tokens after | Asked after |
|---|---|---|---|---|
| sm#507 | 35,677 | 2 files | 26,166 | **nothing**: 7 changes |
| sm#529 | 76,541 | 4 files | 26,687 | 6 files |
| sm#536 | 32,594 | 1 file | 26,221 | **nothing**: 6 changes |
| sm#537 | 38,620 | 6 files | 26,484 | 6 files |
| sm#538 | 56,849 | 3 files | 55,879 | 2 files |
| sm#539 | 35,460 | 3 files | 26,796 | 4 files |
| sm#540 | 36,603 | 2 files | 20,510 | 3 files |
| sm#541 | 35,517 | 2 files | 26,929 | **nothing**: 4 changes |

- **First answers that only asked: 5 of 8, down from 8 of 8.** The plan's success test wants none; C4's read tool, inside the call, is for the rest.
- **Prompts are smaller:** a mean of 29k tokens against 43k, and none past 56k.
- **What the rest asked for is mostly what C3 now names,** the files that import the named code (`service.py`, `schema.py`, `cli.py`), not what it misses. A read inside the call costs those a file each; today each costs a whole new prompt.
- **The scout model isn't needed for this:** the candidates the asks came from are the graph's. Whether C2's pinning tests need one is a separate question.
- Every story ran against the same base, `2ba575e`. sm#507 and sm#529 were first attempted against earlier code, so their rows compare the selection, not the day.
