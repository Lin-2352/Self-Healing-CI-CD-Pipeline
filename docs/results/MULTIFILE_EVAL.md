# Phase 6: healing bugs that span files

**Result: the fast first pass healed 9/18 multi-file calls, below the ≥ 12/18 bar set before the data.
So by the rule, multi-file healing is *not* declared shipped yet.** With the production retry loop, the
same pipeline reaches **15/18**: every first attempt that applied but failed was healed on retry, 6/6.
Thinking on the first pass healed **16/18**. The single-file controls stayed **15/15**, so there is no
regression. A local re-score of every stored answer agreed **57/57**.

Date: 2026-09-28. Qwen3.8-27B UD-Q4_K_XL on the live Kaggle 2×T4 endpoint, with the production server
flags (`kaggle/endpoint/deploy_src.py`). Requests were streamed through the Cloudflare tunnel, exactly as
production sends them. Driver: `eval/run_multifile_eval_remote.py` and `eval/run_multifile_retry_remote.py`.
The Kaggle kernel version (`eval/multifile_eval.py`) holds the same logic. Data: `eval/runs/kout_multifile/`.

## What was tested

The demo app now has `src/app.js`, `src/format.js` and `test/api.test.js`, and `npm test` runs both tests.
Six cases (`eval/cases/multifile_cases.json`) are each derived from the golden tree by an exact mutation.
A preflight proves that golden passes and each broken tree fails with exit 1.

Every production step is the real node code:
- `Pick Candidate Files` reads each case's real CI output, re-rooted at the runner path;
- a GitHub contents-API item is fetched per candidate;
- `Build AI Prompt`, then the model, then `Extract AI Response`.

A heal counts only if the full `npm test` passes on the patched tree.

## Per case (3 reps; `1` = healed)

| case | candidates shown | fast pass (M) | thinking (MT) | retry after M (R) |
|---|---|---|---|---|
| `helper_throws` | format, app, api.test | 000 | 011 | **111** |
| `readme_first_then_code` | app | **111** | 111 | – |
| `second_changed_file_buggy` | app, format | **111** | 111 | – |
| `test_exposes_latent_bug` | format, app, api.test | 000 | **111** | **111** |
| `wrong_require_path` | api.test | **111** | 111 | – |
| `signature_changed_caller_not` | format | 000 | 011 | – (no PR to retry) |
| **single-file controls (B)** | test.js | **15/15** | | |

| arm | healed | picked a file that can fix it | median time |
|---|---|---|---|
| M: fast first pass (production) | 9/18 | 15/18 | 8.2 s |
| MT: thinking first pass | 16/18 | 18/18 | 39.6 s |
| R: production retry (thinking, with the post-attempt-1 CI log) | 6/6 | 6/6 | 45.9 s |
| B: single-file controls | 15/15 | 15/15 | 7.6 s |

## What the numbers say

1. **Choosing the file now works.** Every applied fix landed in a file that can fix the bug. The old
   workflow structurally could not reach two of these cases:
   - `second_changed_file_buggy`: the bug is in the second changed file, and the old workflow took only the first;
   - `test_exposes_latent_bug`: the buggy file is not in the commit at all, and only the stack trace points to it.

   Both now reach the right file.
2. **The fast pass fails in one recognisable way: it stops the crash but misses the spec.**
   - `helper_throws`: it fixed `toUppercase` to `toUpperCase`, which gives "Hello, ADA!". The test wants "Hello, Ada!".
   - The latent bug: it guarded the empty segment, which gives "Ada--Lovelace". The test wants the name unchanged.

   A retry sees the new CI log with the expected and actual bodies side by side, and **fixed 6/6**.
3. **One real gap: callers.** In `signature_changed_caller_not` there is no stack trace (just a wrong
   JSON body), and only `src/format.js` changed, so only that file is shown. The fast pass named the
   caller, `src/app.js`, which it was never shown, and was rejected 3/3. That opens no PR, so there is no
   retry; the failure branch posts a comment instead. With thinking, the model fixed the shown side
   2/3 times (it reverted the signature).

## Decision

- **Per the rule: not shipped as "multi-file healing works on the first pass"** (9/18 < 12/18).
- **What does ship and is safe:** the candidate-selection nodes. On single-file bugs they give prompts
  byte-identical to before (controls 15/15), and they are the only reason two of the six cases are
  reachable at all.
- **Expected production behaviour with the retry loop, from replay:** 15/18. The 3 misses are all the
  caller gap, and each ends in an explanatory comment. This has not yet been confirmed
  live on the demo repo.

## Caveats and known limits

- **Two calls hit a mid-stream connection reset** (`WinError 10054`) on the streamed tunnel path. This is a
  different transport failure from the 524 that streaming fixed. Both calls were re-run and are counted
  once. In production a reset lands in the failure branch as a comment, not a heal, so the **15/18
  projection assumes no resets**.
- **The Kaggle-kernel version of this eval (`eval/multifile_eval.py` `main()`) has never been run.** It
  shares the build and scoring functions used here (those are tested), but its boot, preflight and
  non-streamed path are untested.
- **Already in the production workflow.** The candidate-selection nodes are committed in
  `n8n/self_healing_pipeline.json`, and the demo repo's `main` has the new layout, so the next go-live
  deploys them. The GitHub write half for a file other than `test.js` (Get File SHA / Push Fix File / PR
  on `src/format.js`) **has never run live**. The next go-live is its first live test.
- **The JSON contents API has two edge cases the raw API did not:**
  - files over ~1 MB come back with empty `content` (a raw-download fallback would fix it);
  - a traced path that is a directory returns an array.

  Neither occurs in the demo repo.

## Next options (a choice, not yet made)

- **Thinking on the first pass when several files are shown.** 16/18 vs 9/18, at a median of 39.6 s instead of 8.2 s,
  and only for multi-candidate prompts. Single-file heals would keep the fast path.
- **Show callers.** For a changed file with no stack trace, add the repo files that `require` it.
  This needs one extra GitHub API call (repository tree + search) and changes the workflow's design.
- **Live multi-file round** on the demo repo with the retry loop. The rule was to report before running it.
