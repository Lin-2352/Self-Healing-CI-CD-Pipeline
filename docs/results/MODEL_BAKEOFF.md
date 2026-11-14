# Model bake-off: four Qwen3.8-27B builds on our own heal cases (2026-10-30)

**Verdict: keep the current model (unsloth UD-Q4_K_XL).** It ties every challenger on first-try heals, heals the
most with thinking on (21/24 against 15–17/24), and is the fastest (23.4 tok/s against 16.9–19.3). The rule
was fixed before the data and recommends it. A local re-score of all 232 stored answers agrees 232/232 and
reproduces the decision.

Kernel `lacrimatct/qwen3-8-model-bakeoff` v1 ran on one Kaggle 2×T4 session.
- Data: `eval/runs/kout_bakeoff/results.json`.
- Logs: `kaggle/logs/bakeoff/qwen3-8-model-bakeoff/2026-10-30_v1/` (`kernel.log` plus one server log per model).
- Re-score: `python eval/rescore_bakeoff.py`.

## Setup
- **Models:** all four are Q4-class GGUFs with an MTP head, picked from Hugging Face's Qwen3.8 ≤32B list filtered to T4×2.
  - baseline: unsloth UD-Q4_K_XL, 17.6 GB;
  - DavidAU Fable Cold Fusion 735-882 NEO-CODER-MAX MTP Q4_K_M, 18.5 GB;
  - DavidAU Jumbo Fusion IQ 747-900 reasoning MTP Q4_K_M, 16.8 GB;
  - nerkyor Coder390-EfficientThink Q4LynnStyle-Q8MTP, 19.8 GB.
- **Server:** production flags (`-sm tensor`, f16 KV, MTP n=2, `--parallel 1`, `--reasoning-budget 1024`), ctx 131072.
  All four booted at the full 131072.
- **Heal path:** production's real nodes: Pick Candidate Files → fetch → Build AI Prompt → model → Extract AI
  Response. The request body is used verbatim, except `stream:false` (localhost, no tunnel).
- **Scoring:** the demo's own test suite on the patched tree; exit 0 = healed.
- **Cases:** 17 cases × 2 reps.
  - 5 control, 6 hard single-file and 6 multi-file.
  - **F** = thinking off, as production's first heal. **T** = thinking on, as production's retry.
- **Rule (in `decide()`, self-tested before the push):**
  - Must pass: controls 10/10, MTP accept ≥ 0.5, tensor split engaged, no thinking in F replies.
  - Then rank by F heals on the 24 hard + multi calls.
  - A challenger replaces the baseline only with ≥ 3 more F heals at ≥ 95% of the baseline's tok/s.

## Results
| model | controls | F heals (24) | T heals (24) | tok/s | vs base | MTP accept | F median | T median / max | T tokens (median) |
|---|---|---|---|---|---|---|---|---|---|
| **unsloth UD-Q4_K_XL** (baseline) | 10/10 | 14 | **21** | **23.4** | 100% | 0.641 | 9.5 s | 50.5 / 110 s | 991 |
| Fable Cold Fusion 735 | 10/10 | 14 | 15 | 17.6 | 75% | 0.602 | 10.2 s | 14.4 / 65 s | 233 |
| Jumbo Fusion 747 | **8/10** ✗ | 14 | 16 | 19.3 | 82% | 0.580 | 9.0 s | 12.6 / 62 s | 176 |
| Coder390-EfficientThink | 10/10 | 14 | 17 | 16.9 | 72% | 0.625 | 10.1 s | 32.4 / 78 s | 557 |

- Every model kept both GPUs evenly loaded (equal VRAM on each card), which confirms the tensor split engaged.
- Thinking leak in F was 0 for all four, so the DavidAU templates do honour `enable_thinking:false`.
- Jumbo is ineligible: it failed `syntax_missing_brace` 0/2 with a malformed reply (`Bad control character in string literal`).

## Per case (F / T, out of 2 each)
| set | case | baseline | fable735 | jumbo747 | coder390 |
|---|---|---|---|---|---|
| control | missing_app_init | 2 | 2 | 2 | 2 |
| control | typo_method | 2 | 2 | 2 | 2 |
| control | missing_require | 2 | 2 | 2 | 2 |
| control | syntax_missing_brace | 2 | 2 | **0** | 2 |
| control | wrong_status_check | 2 | 2 | 2 | 2 |
| hard | https_protocol_mismatch | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| hard | callback_arity | 0 / **2** | 0 / 0 | 0 / 0 | 0 / 0 |
| hard | uninterpolated_url | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| hard | inverted_status_check | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| hard | route_wrong_status | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| hard | multi_hunk | 0 / **2** | 0 / 0 | 0 / 0 | 0 / 1 |
| multi | helper_throws | 0 / **2** | 0 / 0 | 0 / 2 | 0 / 0 |
| multi | readme_first_then_code | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| multi | second_changed_file_buggy | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| multi | test_exposes_latent_bug | 0 / 1 | 0 / 1 | 0 / 0 | 0 / **2** |
| multi | wrong_require_path | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| multi | signature_changed_caller_not | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

## What it means
1. **The first-try ceiling is set by the input.** All four builds heal exactly the same 7 hard + multi cases on the
   first try and miss exactly the same 5. A different model at the same size cannot lift fast-pass accuracy.
   What can lift it is what the model is given: the prompt, and the files it is shown.
2. **Thinking depth is where the accuracy is, and the "efficient thinking" fine-tunes trade it away.**
   - The fusion/efficient builds think 4–6× less (176–557 tokens against 991) and heal 4–6 fewer retries.
   - The baseline's long thinking is what rescues `callback_arity`, `multi_hunk` and `helper_throws`.
3. **The Q4_K_M builds are slower.** They run at 16.9–19.3 tok/s against 23.4, though they are about the same
   size. UD-Q4_K_XL's mix of tensor types decodes faster under `-sm tensor` on the T4. This is an
   observation; the cause was not isolated.
4. **`signature_changed_caller_not` fails for every model and every arm.** The fix belongs in `src/app.js`,
   the caller, which Pick Candidate Files does not show the model. The model's own reply names `src/app.js`
   and is refused by the "not one of the files shown" gate. The gap is in the pipeline (the Phase 6
   "caller gap").

## Caveats
- 2 reps per case. A 1-heal difference is noise; the T gap (21 vs ≤17) and the speed gap are not.
- The challengers ran with production's sampling (temperature 0.1). DavidAU's cards recommend other
  settings, which were not tuned here.
- These are self-made cases on one demo repo.

## Next levers (ranked by expected heals)
1. **Close the caller gap:** let Pick Candidate Files add the caller of a changed function (or the file the trace
   names one frame up). That targets `signature_changed_caller_not`, which is 0/16 everywhere.
2. **Decide when to think on the first try.** With thinking on, the first try would heal 21/24 instead of 14/24,
   but the median call takes 50 s instead of 10 s. Today the retry loop recovers most of this anyway, at the cost
   of a failed PR CI run per retry.
3. Model swaps at this size: not worth more GPU time; this bake-off settles it.
