# Do the field names survive when every decision is a row? (crew#583, A0)

**Status:** run 2026-10-10, 18 calls through LiteLLM, three runs of each of six arms. `replay.py` reproduces it. `results/replay.jsonl` holds the scores; the answers stay in `var/experiments/record-468/`, because they quote prompts.

## The question

On mqucifer/sprint-metrics#468 the Product Owner named the new fields `first_attempt_count` and `first_attempt_total` in three answers. The 21:04 split and the 21:25 design note were each shown only the latest answer (21:02), which left the names to the design note. The split's criteria named neither pair, and the design note chose `first_attempt_numerator`/`_denominator`, which shipped in 1.2.0.

Step A0 of `docs/plans/context-record-build.md` resends those two calls, unchanged except for the decisions. The latest answer is taken out, and the conclusion is replaced by one record holding every decision so far as rows (D1). If the names don't survive, D1 is questioned before anything is built.

## The arms

| Arm | What replaces the latest answer |
|---|---|
| `*-orig` | Nothing: the stored request, resent as it was |
| `*-rows` | The record as written: the settle's two rows, and every Product Owner answer as a row (R3 to R10), oldest first. Q1 is still open, and R10 is the 21:02 answer that deferred the names |
| `*-settled` | The same, with Q1 settled by R9, the 20:22 answer that named the fields and their nesting. This is the record A3 would hold |

## The result

**Yes: with the decisions as rows, the names survive.** Counted in the split's criteria and in the whole design note:

| Arm | Runs that named `first_attempt_count`/`_total` | Runs that used `numerator`/`denominator` | Empty answers |
|---|---|---|---|
| split-orig | 0 of 2 answered | 0 | 1 |
| **split-rows** | **2 of 3** | 0 | 0 |
| **split-settled** | **2 of 2 answered** | 0 | 1 |
| design-orig | 1 of 3 | **2 of 3** | 0 |
| **design-rows** | **3 of 3** | 0 | 0 |
| **design-settled** | **3 of 3** | 0 | 0 |

- **The baseline loses the names, as on 2026-10-09.** No baseline split named them, and two of three baseline design notes chose `numerator`/`denominator`.
- **The one miss with rows is a contradiction left in the record.** In split-rows run 3, the stories followed R10, "the stories must not pin field names before the design note exists", which sits unreplaced beside R9. With Q1 settled (split-settled), every split that answered named the fields. A record changed only by rows that replace (A1), and a Product Owner shown its own earlier rows (A3), is what keeps that contradiction out. A3's real call bore this out: shown its rows, the Product Owner didn't defer the names again.
- **Two of nine splits came back empty,** at 110k and 114k prompt tokens, with 13k and 15k thinking: the empty-answer edge the review found (C5), in the baseline as well as the record arms. It's prompt size, not the record. C3 and D3 address it.

The record adds about 3,300 prompt tokens to each call (113,899 against 110,637 for the split).
