# A session deletes a file: one real run (crew#839)

mqucifer/sprint-metrics#769 ended blocked because the Developer couldn't delete a file. It emptied four doc pages that its own test said were gone. `run.py` runs one Developer session with `delete_file` on a clone of sprint-metrics, then runs the gates at `done` on its branch. The session runs held, as a delivery's does, and calls the model through LiteLLM on :4000 with the builders' key.

    git clone https://github.com/mqucifer/sprint-metrics <clone>
    git -C <clone> remote set-url --push origin DISABLED
    uv run python experiments/delete-file-839/run.py <clone>

The script refuses a clone whose push URL isn't disabled. It pushes nothing, comments nowhere and moves no card.

## The run, 2026-10-24

On sprint-metrics at 9602a12. The story has two criteria: "docs/scrape.md is removed: the file isn't in the repository", and no doc page nor the docs generator names it. Its Existing tests line names `tests/test_docs.py`.

| | |
|---|---|
| Ended | `done`, and **the gate passed** |
| Model calls | **25**, 639 s of model time |
| Tool calls | 49: 14 `search`, 12 `view`, 13 `replace_text`, 6 `run`, 2 `git`, 1 `delete_file`, 1 `done` |
| `delete_file` | 1 call, on call 8, target `docs/scrape.md` on its `tool.finished`, applied |
| Refused | 1 `replace_text` (`session.replace_text`, lines not quoted whole), retried |
| The branch | `D docs/scrape.md`, and `docs/architecture.md`, `src/sprint_metrics/_docs_gen.py`, `tests/test_docs.py` and `CHANGELOG.md` changed |

- **It deleted the file, didn't empty it,** in one call, and then removed the page from the docs generator, the architecture page and the docs tests. The suite passed twice before `done`.
- **The gate saw the deletion in the diff** and passed the branch, protected paths and the whole checks included.
- **Both `git diff base` calls failed in this harness, not the tool:** the session's `git` works only in a worktree of a clone, as every delivery's is, and `run.py` uses a plain clone (`GitUnavailable`). The session went on without them. A later run should use `git worktree add` from the clone.
