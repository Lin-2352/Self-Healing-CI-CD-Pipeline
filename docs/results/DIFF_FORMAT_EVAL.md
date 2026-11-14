# Diff-format output: speed and correctness of the heal

**Verdict: ship `search_replace`. Do not ship `unified_diff`.**

The model is ~55% of the push→PR wall clock, and today it re-emits the **entire file** to fix one
line. This measures whether a patch format is cheaper *without* costing correctness. It is the
measurement the plan pre-registered; no pipeline code changed to produce it.

- **Run:** Kaggle `lacrimatct/qwen3-8-diff-format-eval`, status `COMPLETE`
- **Model:** `Qwen3.8-27B-UD-Q4_K_XL.gguf`, ctx 32768, temp 0.1, 2× Tesla T4
- **Design:** 3 formats × 5 scenarios × 3 reps = **45 calls**, zero truncated
- **Source under repair:** the real `app/test.js` (1862 chars), with the same five breakages
  `run_scenario.py` pushes to the demo repo, and the **real** `node` output as the failure log

---

## Results

| format | prompt tok | gen tok | wall | applied | **strict** | `node --check` | **CLEAN** | byte-exact |
|---|---|---|---|---|---|---|---|---|
| `whole_file` (today) | 794 | **655** | **56.4 s** | 15/15 | n/a | 15/15 | **15/15** | **15/15** |
| `unified_diff` | 1150 | 233 | 23.3 s | 15/15 | **12/15** | **12/15** | **12/15** | — |
| `search_replace` | 861 | **171** | **17.0 s** | 15/15 | n/a | 15/15 | **15/15** | **15/15** |

**CLEAN** = applied **and** parses **and** matches the fix regexes. It is the only column that means
"this heal would actually have worked". **byte-exact** = the applied result equals the known-good
file character for character, which is a stricter bar than any regex.

**Baseline sanity (the rig is trustworthy):** `whole_file` reproduced 655 median tokens against the
predicted 630–700, at 12.29 tok/s against the known ~12. Had `enable_thinking: False` been silently
ignored, these cells would have carried hundreds of reasoning tokens and landed near 1200, so the
token column compares output formats, not reasoning length.

### Median completion tokens per scenario

| scenario | `whole_file` | `unified_diff` | `search_replace` |
|---|---|---|---|
| `missing_app_init` | 643 | 188 | 184 |
| `typo_method` | 661 | 223 | **154** |
| `missing_require` | 732 | 255 | **162** |
| `syntax_missing_brace` | 648 | 233 | 370 |
| `wrong_status_check` | 673 | 244 | **171** |

---

## Why `unified_diff` fails, and why the naive scoring hid it

All three `unified_diff` failures are the same scenario, `syntax_missing_brace`, and they are
**perfectly correlated with strict-apply failure**: the 3 patches that needed `--recount` are
exactly the 3 that produced unparseable JavaScript. Here is the patch, verbatim:

```diff
--- a/test.js
+++ b/test.js
@@ -42,9 +42,10 @@
       console.error(`CODE_FAILURE: ${err.message}`);
       server.close(() => process.exit(1));
     });

 // Fires when the listen() itself fails (EADDRINUSE, EACCES, ...). Without this
 // handler Node throws an unhandled 'error' event and the pipeline would hand the
 // AI a stack trace for a problem that is not in the source at all.
 server.on("error", infraFail);
+
```

The header claims one line was added (`-42,9 +42,10`). The only `+` line is **empty**. The patch
never adds the missing `});`. It is a no-op that merely *looks* like a fix.

Three separate safeguards had to fail together for this to score as a pass, and two of them are
things a real implementation would plausibly ship:

1. **`git apply` strict rejected it, correctly.** The hunk counts do not match the body.
2. **The `--recount` retry then accepted it.** Recount recomputes the counts *from the body*, so
   the malformed header stopped mattering and the no-op applied cleanly. A tolerant retry is a
   reasonable-looking thing to build; here it converted an honest rejection into a **silent
   corruption**. `applied_ok = 15/15` is an artifact of that retry, not a property of the model.
3. **The `fix_correct` regex passed it anyway.** `must` for this case is `\}\s*\)\s*;?\s*$`, which
   matched a *different*, pre-existing `});` further up the file. The regex never checked that the
   *missing* brace came back.

Only `node --check` on the applied result caught it. That is the whole argument for scoring
apply-then-parse rather than scoring the patch text.

**Consequence for the pipeline:** a `unified_diff` heal on this class of bug would have opened a PR
containing a file that does not parse: CI red, PR merged-looking, nothing healed. That is strictly
worse than not healing, because it looks like it worked.

`unified_diff` also needs the source shown **with line numbers**, costing 1150 prompt tokens vs 794
for whole-file. That does not meaningfully eat the time win (the wall-clock column already
includes prefill, and it still came in at 23.3 s), but it does consume context budget, which starts
to matter on files much larger than this one.

---

## Why `search_replace` wins

- **15/15 CLEAN, and 15/15 byte-exact against the known-good file**, including
  `syntax_missing_brace`, the case that broke `unified_diff`.
- **3.8× fewer completion tokens** (171 vs 655 median).
- **17.0 s vs 56.4 s** median, a **39 s** saving per heal.
- No line numbers exist in the format, so the failure mode that sank `unified_diff` is not
  reachable by construction. Correctness is enforced by the *content* match instead: the applier
  requires the SEARCH text to appear **exactly once**, so an ambiguous or stale edit is rejected
  rather than misapplied.
- Its prompt cost is near whole-file's (861 vs 794), since the source is shown unnumbered.

**Its one weak spot:** `syntax_missing_brace` cost `search_replace`
**370 tokens, more than `unified_diff`'s 233** and 2× its own next-worst case. It is still correct
and still well under whole-file's 648, but the margin narrows.

The cause is **anchor uniqueness cost**. The SEARCH block for that case
ran **10 lines / 480 chars**, because the missing `});` sits directly before a three-line comment
block and the anchor had to swallow the whole comment to be unique. Compare `missing_require`,
whose anchor was **1 line / 35 chars** (`const express = require("express");`).

The obvious generalization is **wrong**: `missing_app_init` (184
tok) and `missing_require` (162 tok) are *also* pure insertions, and they are the two cheapest
cells in the table. Insert-vs-modify does not predict cost. What predicts it is how much
surrounding text is needed to make the anchor unique. That depends on the code around the bug,
so it is not predictable in advance.

---

## Judged against the rule fixed *before* the data

> Ship diffs if a format holds `fix_correct` ≥ whole-file's and `applied_ok` is 100%, at materially
> fewer tokens. Don't ship if either correctness or apply-rate drops.

- **`search_replace` → SHIP.** 15/15 = baseline's 15/15, apply 100%, 3.8× fewer tokens.
- **`unified_diff` → do not ship.** Correctness drops (12/15 parse).

**On `unified_diff` and the letter of that sentence:** read in isolation, the ship sentence names
only `fix_correct` and `applied_ok`, and `unified_diff` is 15/15 on both. But `syntax_ok`
(`node --check` **on the applied result**) was pre-registered as a scored metric by the same plan
(*"Per call, record"*, line 35), and `unified_diff` is 12/15 on it. So no metric is being
introduced after seeing the data; the one-line ship summary was simply less complete than the
plan's own metric list. Judged on the metrics the plan actually fixed in advance, `unified_diff`
fails.

That its `fix_correct` and `applied_ok` both read 15/15 is not a point in its favour either: both
are measurement artifacts of the `--recount` retry and a too-weak `must` regex, as traced above.
The weak regex is a flaw in the eval that this run exposed, and the fix is to tighten it so that a
non-parsing file never scores as a correct heal.

---

## Expected pipeline impact

Model phase **56.4 s → 17.0 s**. Against the measured 104 s push→PR cycle in
`SELFHEAL_E2E_RESULTS.md`, that projects to **~65 s**, a ~38% reduction. That is larger than any hardware
or engine change available here, and it needs neither. (Colibri was investigated and cannot run a
dense model at all; see `SPEED_QUALITY_EVAL.md`.)

This is a projection from bench medians, not an end-to-end measurement. It assumes the other phases
(push→hook ~40 s, fetches 2.7 s, writes 5.2 s) are unchanged, which the follow-up work would need
to confirm: the apply step adds a fetch-and-splice the current path does not do.

## Work needed to ship it (not done here)

`Extract AI Response` currently base64-encodes a **complete file** for the GitHub contents API.
A SEARCH/REPLACE payload cannot go straight there. It needs:

1. fetch the current file content (the `Get File SHA` call already retrieves it),
2. apply the blocks in-node, enforcing the **exactly-once** rule (reject, don't guess),
3. **a whole-file fallback**: on any rejected edit, re-ask in `whole_file` format. A fallback costs
   both attempts (17.0 + 56.4 ≈ **73.4 s**), so the expected cycle is `17.0 + p × 56.4` for
   fallback rate *p*. That equals today's 56.4 s baseline at **p ≈ 0.70**: the design stays a net
   win unless the fallback fires on more than ~70% of heals.

   *p* is genuinely unmeasured. Across 15 real cells the model never produced a non-unique SEARCH
   block, but that is a 45-line file with five mechanical bugs. On a 300-line file an anchor like
   `});` is far likelier to be ambiguous, so the exactly-once rule would reject more often. The
   break-even is wide enough that this is a bounded risk rather than an open one, but it should be
   measured on a larger file before anyone quotes the 38%.
4. `node --check` equivalent before opening the PR: this run is the argument for that gate
   existing at all.

That is real work in `Extract AI Response`, and it needs its own plan.

## Data

- Results, including each cell's raw model output: `eval/runs/kout_diff/results.json`.
- Kernel and server logs: `kaggle/logs/evals/qwen3-8-diff-format-eval/2026-09-12/`.

The case builder, applier tests and analysis scripts used for this run were not kept in the repository.
A re-run has to re-apply every diff **strict-only** from each cell's saved `raw`, because the in-kernel
scorer records only the recount-tolerant outcome. That strict re-apply is what exposed the
`unified_diff` failure described above.
