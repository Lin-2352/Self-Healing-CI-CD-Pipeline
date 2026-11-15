## Evaluation

### How to read this chapter

The project ran nine studies, each on the Kaggle machine or against the live pipeline. This chapter gives them in the order they answer questions about the healer: first which model file to load, then how to make it fast, then what output format to ask for, then how often the fix is right, and last how the whole pipeline behaves live. Each section states the question, the method, the decision rule when the results document names one, the headline numbers and the conclusion.

Every number comes from a results document in `docs/results/` or `docs/old/`, with the file named after it in brackets. Where saved data exists under `eval/runs/`, we recomputed the headline numbers from it. A section says so when the data confirmed the document, and says so when it did not or when the data was not kept. Disagreements between documents are named where they occur and collected in the last table.

### The evaluation harness

The `eval/` folder holds the test cases, the scorers, the scripts that build Kaggle kernels from them, and the saved results in `eval/runs/`.

**Cases.** There are 5 control cases (`eval/cases/eval_cases.json`), 6 hard single-file cases (`eval/cases/hard_cases.json`) and 6 multi-file cases (`eval/cases/multifile_cases.json`). The control cases are the five breakages the live runner pushes to the demo repository: a missing `app` line, a misspelled method, a missing `require`, a missing closing brace and a wrong status check. The hard cases come from the same file, but the CI log no longer points at the bug. The multi-file cases use the demo app's `src/app.js`, `src/format.js` and `test/api.test.js` (source: `docs/results/SHIPPED_QUALITY_EVAL.md`, `docs/results/MULTIFILE_EVAL.md`). We counted the cases in the three JSON files.

**Same code as production.** The later studies do not paraphrase the pipeline. They run the real node code from `tools/nodes/` (`build_ai_prompt.js`, `extract_ai_response.js`, and for multi-file cases `Pick Candidate Files`) under `node`, send the request body unchanged to the model, and apply the reply with the real extraction node, so its exactly-once and syntax gates count (source: `docs/results/SHIPPED_QUALITY_EVAL.md`, `docs/results/MODEL_BAKEOFF.md`).

**What "healed" means.** Live, a case counts as healed when three things hold: the file on the pull request branch parses, it is byte-identical to the known-good file, and the real smoke test passes (source: `docs/results/GOLIVE_RESULTS.md`). In the offline studies the test result comes first. The patched file is run as the real smoke test (`node test.js` with express), and exit code 0 means healed; for multi-file cases the full `npm test` must pass. Byte equality with the golden file is kept as a second, stricter column, and a `mustnot` pattern flags answers that cheat (source: `eval/shipped_quality_eval.py`, function `score`). Before any GPU time, a preflight proves that the golden file passes and that every broken file fails with exit code 1.

**Why test first, byte-exact second.** The alternative-quant study found a case, `route_wrong_status`, that scored 0 of 12 byte-exact and 12 of 12 functionally correct, because the models wrote `res.status(200).send("ok")` where the golden file has `res.send("ok")` (source: `docs/results/SPEED_QUALITY_EVAL.md`). A byte comparison alone would have marked a valid fix as a failure. The earliest studies scored with fix patterns and `node --check`, the alternative-quant study used byte equality as its main score, and the later ones run the test.

**Scorer self-tests.** Each scorer is tested locally before it is trusted. `eval/test_shipped_eval.py`, `eval/test_bakeoff_eval.py`, `eval/test_multifile_eval.py` and `eval/check_multifile_cases.py` feed known-good and known-bad outputs through it, and the saved outputs in `eval/runs/testall/` report `FAILURES: 0` for each. The shipped-quality test includes 15 real captured model replies, all of which score as functional heals. The self-test of the speed-levers study is a warning: it passed every assertion while the check it protected gave a false answer, because its fixture came from a dump of the binary and not from a real log (source: `docs/results/SPEED_LEVERS_EVAL.md`). After that the engagement tests ran against the four captured server logs.

**Re-scoring.** Every result file stores the raw model reply and a flag that says whether it was cut short. `eval/rescore_shipped.py`, `eval/rescore_multifile.py` and `eval/rescore_bakeoff.py` recompute every verdict from the raw text alone, with the same node code and a local express. We ran all three. The shipped-quality file re-scored 84 of 84 calls with no disagreement, the multi-file file 57 of 57, and the bake-off file 232 of 232, and the bake-off run also reproduced its decision (source: output of the three scripts, run for this chapter; the same counts are in `docs/results/SHIPPED_QUALITY_EVAL.md`, `MULTIFILE_EVAL.md` and `MODEL_BAKEOFF.md`). The speed-levers run did not save raw output, so its correctness column cannot be re-scored.

**Rig corrections.** Two studies each found that a check on log wording gave a false negative, and one found a recount option that hid a bad patch. The project's rule since then is to judge a feature by its measured effect, such as the accept rate or equal memory on both cards, and not by a log string (source: `docs/results/SPEED_LEVERS_EVAL.md`, `docs/results/SPEED_QUALITY_EVAL.md`, `docs/results/DIFF_FORMAT_EVAL.md`).

### Model choice sweep

**Question.** Which quantized Qwen3.8-27B file gives a good fix on two T4 cards?

**Method.** One kernel ran 9 configurations in a batch of 252 minutes on two T4 cards. Quality was a 5-case CI-failure set scored 3 points per case (valid JSON, `node --check`, correct fix). Speed came from the server's own `timings` field (source: `docs/old/QWEN38_SWEEP_RESULTS.md`). The document names no decision rule in advance. A second note covers three sweeps, 24 configurations in all (source: `docs/old/QWEN38_SPEED_CEILING.md`).

**Numbers.** The raw `results.json` of these sweeps was not kept in the repository, so we could not recompute them and the figures below rest on the documents alone.

| Config | Size (GB) | Quality | Generation tok/s |
|---|---|---|---|
| UD-Q4_K_XL (unsloth) | 17.6 | 15/15 | 8.8 |
| IQ2_S (ISTA-DASLab) | 9.6 | 14/15 | 8.4 |
| Q8_0 (barozp) | 29.0 | 14/15 | 5.9 |
| Q4_K_M with feed-forward tensors on CPU | 16.8 | 14/15 | 1.1 |

(source: `docs/old/QWEN38_SWEEP_RESULTS.md`)

**Conclusion.** Four of the five cases scored 3 of 3 for all nine configurations, so the set could not rank them. The one case any file missed needed semantic reading of the test. Speed was the only usable axis, and UD-Q4_K_XL was fastest at 8.8 tok/s with a perfect score, so it was chosen. Q8_0 did load in 30.7 GB of split memory but was the slowest file that ran on the GPUs, and moving feed-forward tensors to CPU memory cut one file from 7.2 to 1.1 tok/s.

Two later results weaken parts of this study. The 8.8 tok/s figure turned out to be low: the first live run with different batch flags measured 12.1 tok/s on the same file (source: `docs/old/SELFHEAL_E2E_RESULTS.md`), and the speed study below reached 26.83. The case that every config failed offline, `wrong_status_check`, was healed first try live with the real log and full file, so the sweep's synthetic snippet hid information the case needed (same source). The sweep's quality ranking therefore carries little weight, and the file choice was confirmed later by the bake-off.

### Speed levers

**Question.** The first sweeps said 30 tok/s was out of reach. Is that true if the server splits each tensor across both cards (`-sm tensor`) and uses the model's own multi-token-prediction head (MTP) for speculative decoding?

**Method.** Four configurations (A baseline, B tensor split, C MTP, D both) on 5 scenarios with 3 repetitions each, 60 calls, temperature 0.1, context 8192, with the same production file (source: `docs/results/SPEED_LEVERS_EVAL.md`). The document names no decision rule in advance, and states the verdict afterwards: ship both.

**Numbers.** We recomputed the medians from the 60 saved calls, which had no errors (checked in `eval/runs/kout_speed/results.json`).

| Config | Median tok/s | Gain over A | Median wall time | Correct fixes | Draft accept rate |
|---|---|---|---|---|---|
| A baseline | 12.35 | none | 56.7 s | 15/15 | none |
| B tensor split with f16 cache | 15.49 | +25% | 45.3 s | 15/15 | none |
| C MTP only | 19.99 | +62% | 36.8 s | 15/15 | 0.953 |
| D both | 26.83 | +117% | 27.8 s | 15/15 | 0.960 |

(source: `docs/results/SPEED_LEVERS_EVAL.md`, matched by `eval/runs/kout_speed/results.json`)

Both levers were shown to have run by their effect. In configs B and D the two cards held equal memory (8721 and 8721 MiB, and 9051 and 9051 MiB), while A and C were uneven (for example 7761 and 9371 MiB in A) (checked in `eval/runs/kout_speed/results.json`).

**Conclusion.** The two levers together made the model 2.17 times faster with no loss on this fixture, and the older argument that 30 tok/s was physically impossible rested on two premises that no longer hold (one card's bandwidth at a time, one token per weight read). The document's own caveats matter. Config B is not a clean single change, because tensor mode forbids a quantized cache and the two changed together. The context was 8192 here against 32768 in production at the time, the correctness column rests on in-kernel patterns and cannot be re-scored, and the projected cycle of about 75 s was a projection from medians, not a measurement.

### Speed and quality of alternative quantizations

**Question.** The Bonsai ternary files (5.95 and 7.21 GB) and the Ridge file (12.6 GB, built for this model's layer mix) are much smaller than UD-Q4_K_XL. Do they give the same fixes, and how fast do they run?

**Method.** 4 packings, 11 cases (5 control and 6 hard) and 3 repetitions: 132 calls, no errors and no truncations, one runtime (a fork build) for all four. Each packing tried four settings from tensor split plus MTP down to layer split and kept the best that worked (source: `docs/results/SPEED_QUALITY_EVAL.md`). The document says a decision rule was fixed before the data and gives its effect: smaller size is not a reason to switch. It does not quote the rule.

**Numbers.** (Checked in `eval/runs/kout_qs/results.json`: all 132 calls, counts and medians match.)

| Packing | Size (GB) | Setting reached | Control | Hard, byte-exact | Median tok/s |
|---|---|---|---|---|---|
| UD-Q4_K_XL | 17.56 | tensor split and MTP | 15/15 | 9/18 | 28.02 |
| Ridge 3.7 bpw | 12.60 | tensor split and MTP | 15/15 | 5/18 | 28.52 |
| Bonsai PQ2_0 | 7.21 | layer split | 15/15 | 9/18 | 21.02 |
| Bonsai PTQ1_0 | 5.95 | layer split | 15/15 | 9/18 | 17.09 |

(source: `docs/results/SPEED_QUALITY_EVAL.md`)

The Bonsai speeds are not like for like, since those files fell back to layer split. The server aborted on tensor split for them and found no MTP tensors in the converted files (source: same document).

**Conclusion.** UD-Q4_K_XL stays. Ridge is measurably worse: it fails `inverted_status_check` in all three repetitions while the others pass all three, and it gains 1.8 percent speed. The Bonsai files show no detectable quality difference but run 25 and 39 percent slower. The document chooses the phrase "no detectable difference" over "equal", because the three packings other than Ridge were identical on all six hard cases, so the set can only rule out a large gap. Only 2 of 6 hard cases separated anything. One of the three cases that every packing failed, `route_wrong_status`, was a scorer error as described in the harness section. The data also shows a detail the document leaves out: Ridge scored 6 of 18 on the hard set by the fix pattern but 5 of 18 byte-exact (checked in `eval/runs/kout_qs/results.json`), and the document reports the byte-exact figure.

### Diff format comparison

**Question.** The model rewrites the whole file to fix one line, and generation took about 55 percent of the push-to-PR time. Is a patch format cheaper without making fixes worse?

**Method.** Three output formats (whole file, unified diff, search and replace) on the five control scenarios with 3 repetitions: 45 calls, none truncated, context 32768, temperature 0.1. The score is applied, parses with `node --check`, and matches the fix patterns; byte-exact against golden is a stricter column (source: `docs/results/DIFF_FORMAT_EVAL.md`).

**Decision rule, fixed before the data.** "Ship diffs if a format holds `fix_correct` at or above whole-file's and `applied_ok` is 100%, at materially fewer tokens. Don't ship if either correctness or apply-rate drops." The document adds that the plan had also listed `node --check` on the applied result as a scored metric, so using it was not a rule added after the data.

**Numbers.** (Checked in `eval/runs/kout_diff/results.json`: medians, apply counts and parse counts all match.)

| Format | Prompt tokens | Generated tokens | Wall time | Applied | Parses | Byte-exact |
|---|---|---|---|---|---|---|
| Whole file | 794 | 655 | 56.4 s | 15/15 | 15/15 | 15/15 |
| Unified diff | 1150 | 233 | 23.3 s | 15/15 | 12/15 | not scored |
| Search and replace | 861 | 171 | 17.0 s | 15/15 | 15/15 | 15/15 |

(source: `docs/results/DIFF_FORMAT_EVAL.md`)

**Conclusion.** Search and replace ships: the same correctness as whole file with 3.8 times fewer generated tokens and 39 seconds saved per heal. Unified diff does not ship. All three failures were the missing-brace scenario, where the patch added only an empty line. A strict `git apply` rejected it, a `--recount` retry then accepted it, and the fix pattern matched a different `});` elsewhere in the file, so the apply and fix columns read 15/15 although 3 files did not parse. The stored summary in the data file still shows 1.0 for both columns for that reason. Only the syntax check caught it. The document projects a cycle of about 65 s and says this was not measured, and that the fallback rate on a larger file was unknown. The two source lengths that appear do not conflict: the document's 1862 characters is the length of the known-good `test.js` (1862 bytes), and the data file's `source_chars` of 1839 is the length of the broken file the model was shown, which is 23 characters shorter.

### Shipped-format quality, with and without the CI log and thinking

**Question.** On the format that shipped, does putting the real CI log in the prompt raise the heal rate, and does thinking help the hard cases?

**Method.** 11 cases (5 control, 6 hard) with 3 repetitions in three arms: A without the log, B with the log, C with the log and thinking (budget 1024 tokens, hard cases only). That is 84 calls with no errors and no truncation, on the production server flags (source: `docs/results/SHIPPED_QUALITY_EVAL.md`).

**Decision rule, fixed before the data.** The log is judged by whether it adds at least 3 healed hard calls (B minus A), and thinking by whether it adds at least 3 (C minus B) without failures (source: same document, "Decisions, by the rules set in advance").

**Numbers.** (Checked in `eval/runs/kout_shipped/results.json`.)

| Arm | Control healed | Hard healed | Hard byte-exact | Median wall time on hard cases | MTP accept |
|---|---|---|---|---|---|
| A no log | 15/15 | 12/18 | 6/18 | 9.2 s | 0.904 |
| B with log | 15/15 | 12/18 | 9/18 | 9.1 s | 0.883 |
| C log and thinking | not run | 18/18 | 9/18 | 36.8 s | 0.756 |

(source: `docs/results/SHIPPED_QUALITY_EVAL.md`)

The hard-case times and the byte-exact counts matched the data. The accept rates match the medians over all calls of each arm, and the data gave 0.894 and 0.878 for the hard calls alone, so the table is slightly mixed in what it averages. Case by case, `callback_arity` and `multi_hunk` were 0 of 3 in arms A and B and 3 of 3 in arm C, and nothing else changed.

**Conclusion.** The log did not change the heal rate (12 to 12, short of the 3 needed), but it moved fixes closer to the golden file (6 to 9 byte-exact), costs about 3 seconds of extra log-fetch time in the workflow (the model call itself took 9.1 s against 9.2 s) and stays in the prompt. Thinking passes its rule (+6 of 18) and is the only setting that ever fixed those two cases, at about four times the time: a median of 36.8 s and a worst case of 77.9 s. That led to the shipped policy of thinking on retries only. The document's caveats are that arm C saw the original broken file, not a file with a wrong first fix, so it does not measure retries, and that 3 repetitions in one session fix the magnitudes only for that session.

The data folder also holds a second run of the same kernel, `eval/runs/kout_shipped_v2/results.json`, started on 2026-09-27, which no results document describes. We recomputed it. Arm A healed 15 of 15 controls and 10 of 18 hard cases, arm B 15 of 15 and 12 of 18, and arm C 18 of 18, with a median of 45.75 s and a worst case of 58.5 s on arm C. The B and C heal counts repeat the first run, and arm A was 2 lower. The documents do not say what changed between the runs.

### Multi-file healing

**Question.** Can the pipeline heal bugs whose cause is in a different file from the one that changed?

**Method.** Six multi-file cases, each made from the golden tree by an exact mutation, with 3 repetitions on the live endpoint through the tunnel, streamed as in production. Arms: M the fast first pass, MT thinking on the first pass, R the production retry on the failures of M, and B the single-file controls. A heal counts only if the full `npm test` passes (source: `docs/results/MULTIFILE_EVAL.md`).

**Decision rule, fixed before the data.** Multi-file healing is declared shipped only if the fast first pass heals at least 12 of 18.

**Numbers.** (Checked in `eval/runs/kout_multifile/results.json`. The file has 59 rows, because two calls hit a connection reset and were re-run; counting each of the 57 final calls once gives the figures below.)

| Arm | Healed | Fix landed in a file that could fix it | Median time |
|---|---|---|---|
| M fast first pass | 9/18 | 15/18 | 8.2 s |
| MT thinking first pass | 16/18 | 18/18 | 39.6 s |
| R retry after M | 6/6 | 6/6 | 45.9 s |
| B single-file controls | 15/15 | 15/15 | 7.6 s |

(source: `docs/results/MULTIFILE_EVAL.md`)

**Conclusion.** By the rule, multi-file healing is not declared shipped (9 is below 12). The candidate-selection nodes ship anyway, since they leave single-file prompts byte-identical (controls 15 of 15) and are the only way two of the six cases can be reached. The fast pass fails in one recognisable way: it stops the crash but misses what the test expects. The retry, which sees the new log, fixed all 6 such cases. The projection of 15 of 18 with the retry loop is from replay, assumes no connection resets, and had not been confirmed live. The remaining misses are the caller gap: `signature_changed_caller_not` needs a fix in `src/app.js`, which the pipeline does not show the model.

### Four-model bake-off

**Question.** Does another Qwen3.8-27B build of similar size beat the current file on our cases?

**Method.** Four Q4-class builds, all with an MTP head, on one 2xT4 session with the production flags and context 131072. There were 17 cases (5 control, 6 hard, 6 multi-file) with 2 repetitions: 232 calls. F means thinking off, as on the first heal, and T means thinking on, as on a retry (source: `docs/results/MODEL_BAKEOFF.md`).

**Decision rule, fixed before the data.** A model must pass the controls (10 of 10), reach an MTP accept rate of at least 0.5, show the tensor split, and not think in F replies. Eligible models are ranked by first-try heals on the 24 hard and multi-file calls, and a challenger replaces the baseline only with at least 3 more first-try heals at 95 percent or more of the baseline's tok/s.

**Numbers.** (Checked in `eval/runs/kout_bakeoff/results.json`.)

| Build | Controls | F heals of 24 | T heals of 24 | tok/s | Speed vs baseline | T median / max time |
|---|---|---|---|---|---|---|
| UD-Q4_K_XL, baseline | 10/10 | 14 | 21 | 23.4 | 100% | 50.5 s / 110 s |
| Fable Cold Fusion 735 | 10/10 | 14 | 15 | 17.6 | 75% | 14.4 s / 65 s |
| Jumbo Fusion 747 | 8/10, ineligible | 14 | 16 | 19.3 | 82% | 12.6 s / 62 s |
| Coder390 EfficientThink | 10/10 | 14 | 17 | 16.9 | 72% | 32.4 s / 78 s |

(source: `docs/results/MODEL_BAKEOFF.md`)

The heal counts, speeds and control results match the data exactly. The time column matches our recomputation from the raw calls (50.55 s, 14.4 s, 12.55 s and 32.35 s medians). The `summary` block inside the data file gives different values for three of the builds (56.5 s, 12.9 s and 34.7 s), and we did not find how that field is computed, so we used the raw calls.

**Conclusion.** The baseline is kept. All four heal the same 7 of the 12 hard and multi-file cases on the first try and miss the same 5, so another build at this size cannot raise first-try accuracy. What differs is thinking: with thinking on, the baseline writes a median of 991 tokens and the "efficient thinking" builds 176 to 557, and the baseline heals more on retry. The Q4_K_M builds were also slower, and the document says the cause was not isolated. `signature_changed_caller_not` failed on every build and arm, 0 of 16 for the same pipeline reason as before. The document's caveats are 2 repetitions, so a one-heal gap is noise, challengers run at temperature 0.1 without tuning, and self-made cases on one demo repository.

### Live end-to-end rounds

**Question.** Does the whole pipeline, from a pushed failure to a green pull request, work on a real repository, and how long does it take?

**Method.** A runner pushes a breakage to the demo repository `Lin-2352/self-healing-cicd-demo`, waits for the pull request, and scores the branch. Timings come from GitHub's own timestamps and from n8n's stored executions (source: `docs/results/GOLIVE_RESULTS.md`). The documents state no accept or reject rule for these rounds, except that the first baseline was judged against a bar of under about 6 minutes (source: `docs/old/SELFHEAL_E2E_RESULTS.md`).

**Baseline before the live rounds.** The first end-to-end measurement ran the shipped Code nodes under a harness that stood in for n8n itself, on a 13-node flow, with whole-file output and 12.1 tok/s. It healed 5 of 5 with a median of 104 s from push to pull request and a worst case of 116 s. The model phase was 56 to 60 s, about 55 percent of the time. Running the real workflow inside real n8n then found four bugs the harness could not (source: `docs/old/SELFHEAL_E2E_RESULTS.md`). The raw files of that run were not kept in the repository, so these numbers rest on the document. The current workflow has 26 nodes (source: `docs/architecture/n8n-workflow.md`).

**Rounds.** The table gives the headline of each round. We checked the push-to-PR times and outcomes against the files in `eval/runs/golive1` to `golive5`. The per-node times and the model decode speeds come from n8n's database, which is not in the repository, so we could not recheck those.

| Round | Date | Result | Push to PR or green (median) |
|---|---|---|---|
| 1, new format and levers | 2026-09-22 | 5/5 healed, byte-exact, smoke test passes | 44 s to PR |
| 2, CI log in prompt, PR CI | 2026-09-23 | 5/5 healed, 5/5 heal PRs green on their own CI | 42 s to PR |
| 3, retry loop | 2026-09-23 | 5 controls green on attempt 1; `multi_hunk` green via retry twice; `callback_arity` gave up at 3 attempts | 59 to 103 s to green for controls; 161 s and 151 s for `multi_hunk` |
| 4, full test run | 2026-09-28 | in the data: 5 controls green on attempt 1, `multi_hunk` green in 173 s, `callback_arity` stalled after attempt 1 | 60 to 70 s to green for controls |
| 5, streaming and failure comments | 2026-09-28 | `callback_arity` green on attempt 3 in 249 s, `multi_hunk` green in 143 s, `missing_require` green in 54 s | not reported as a median |

(source: `docs/results/GOLIVE_RESULTS.md` for rounds 1 to 3 and 5; `eval/runs/golive4/golive4_retry_results.json` for round 4, which the results document mentions only in passing)

**What the data shows that the document explains.** The stored file for round 1 has 7 rows and marks only 2 of them as healed (#13 and #14, both with the golden file). Two rows ended with no pull request (the broken prompt-building node of the first runs) and three (#10 to #12) scored `file_is_golden: false`. The document says the runner stripped the file's trailing newline and so judged identical files as different, and that all pull requests were re-scored from their branches. We could confirm the two later rows (#13 and #14) as golden in the data and the 44 s median over the five pull requests, but the re-scoring of #10 to #12 is not in the stored file. The round 2 file has 5 of 5 golden, median 42 s, as the document says.

**Findings that changed the system.** Round 1 found that the model never saw the CI log, that a string split over ten lines broke the prompt node, and that a ladder size too large hung. Round 2 fixed those and found that GitHub's redirect would have sent the access token to the log host. Round 3 showed the retry loop works but that retry prompts anchor on a wrong first fix. Round 5 fixed the 120 s tunnel cut-off by streaming. A streamed request answered after 166.5 s, and the same unstreamed request failed with HTTP 524 at 125.9 s (checked in `eval/runs/golive5/golive5_stream_proof.json`).

**Speed in production.** The median decode speed from n8n's stored timings was 23.10 tok/s in round 1 and 27.73 tok/s in round 2, with a model call of a median 11.4 s and then 9.3 s (source: `docs/results/GOLIVE_RESULTS.md`; not rechecked). Thinking retries took 51.6 to 67.2 s in round 3, and one retry in round 4 took 125.9 s.

**Conclusion.** Once the round 1 defects were fixed, every control case that was run healed on the first attempt (rounds 2 to 5). Push to pull request went from a median of 104 s for the baseline to 44 s and 42 s after the format and speed changes. The hard cases need the retry loop, and one of them, `callback_arity`, needed three attempts.

### The backend on the Kaggle endpoint

**Question.** Can `ci-heal-backend`, the second healer, use the same endpoint for both its classifier and its fixer?

**Method.** Both model roles were pointed at the endpoint (kernel v15) and the backend's six fixture failures were replayed through its real pipeline, with GitHub mocked and `DRY_RUN` on (source: `docs/results/BACKEND_ON_KAGGLE.md`). The document states no decision rule.

**Numbers.** (The replay output is in `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-11-15_v15/backend_replay.log`; we checked the first fixture there and the rest are from the document.)

| Fixture | Expected class | Classified, with confidence | Decision | Time |
|---|---|---|---|---|
| configuration | configuration_error | configuration_error (0.95) | fix, 3 of 3 candidates applied | 28 s |
| dependency | dependency_error | dependency_error (0.98) | escalate, candidates disagreed | 30 s |
| flaky | flaky_or_network | flaky_or_network (0.95) | rerun the job | 8 s |
| prompt_injection | test_failure | test_failure (0.98) | fix, agreement 1.0 | 26 s |
| secrets | dependency_error | dependency_error (0.98) | escalate, candidates disagreed | 40 s |
| test_failure | test_failure | test_failure (0.98) | fix | 29 s |

(source: `docs/results/BACKEND_ON_KAGGLE.md`)

**Conclusion.** All six fixtures were classified correctly on the first sample, every reply parsed, and all 15 fix candidates applied. The two escalations are the agreement gate working as set: the three dependency patches differed and agreement fell below 0.6. The study also found the thinking default that Chapter 7 describes. The limits are one run per fixture, fixtures written for the backend's own tests, no pull request opened and no CI run on a fix. The prompt-injection case was fixed like an ordinary failure, and the replay does not show whether any candidate followed the injected text.

### Limits of the evaluation

The table lists only limits that the project documents themselves state or that we found in the saved data.

| Limit | Evidence | Source |
|---|---|---|
| Small case sets: 5 control, 6 hard and 6 multi-file cases | Only 2 of 6 hard cases separated any packing, and one of the 5 original cases was the only one any model missed | `docs/results/SPEED_QUALITY_EVAL.md`, `docs/old/QWEN38_SWEEP_RESULTS.md` |
| Control set is saturated | All four packings scored 15 of 15 on the controls, so the controls rank nothing | `docs/results/SPEED_QUALITY_EVAL.md` |
| One demo app, with self-made cases | The bake-off names this itself; the diff study used a 45-line file with five mechanical bugs, and the fallback rate on larger files is unmeasured | `docs/results/MODEL_BAKEOFF.md`, `docs/results/DIFF_FORMAT_EVAL.md` |
| Few repetitions, one session each | 3 repetitions in one session (shipped quality), 2 in the bake-off where a 1-heal gap is noise; the first sweep was a single run | `docs/results/SHIPPED_QUALITY_EVAL.md`, `docs/results/MODEL_BAKEOFF.md`, `docs/old/QWEN38_SWEEP_RESULTS.md` |
| Speed differs between sessions | Boot benchmark ranged from 22.4 to 26.8 tok/s with the same flags, and the documents give no reason | boot logs v8 to v16 |
| Speed numbers come from different settings | Rig medians (ctx 8192), fork build, boot benchmark and live decode do not form one series | `docs/results/SPEED_LEVERS_EVAL.md`, `docs/results/GOLIVE_RESULTS.md` |
| Caller gap | `signature_changed_caller_not` healed 0 of 16 in the bake-off, and the fix belongs in a file the pipeline does not show | `docs/results/MODEL_BAKEOFF.md` |
| Byte-exact scoring is too strict | `route_wrong_status` was 12 of 12 working and 0 of 12 byte-exact, so exact counts understate every packing by the same amount | `docs/results/SPEED_QUALITY_EVAL.md` |
| Thinking arm did not measure retries | Arm C saw the original file, not a file carrying a wrong first fix | `docs/results/SHIPPED_QUALITY_EVAL.md` |
| Offline evals skip the tunnel | They run on localhost with `stream: false`, while live calls add 5 to 6 s of overhead | `docs/results/GOLIVE_RESULTS.md` |
| Multi-file projection not confirmed | 15 of 18 is from replay, assumes no connection resets, and the write path for a file other than `test.js` had never run live; the kernel version of that eval had never been run | `docs/results/MULTIFILE_EVAL.md` |
| Live rounds are five cases each | Round times include GitHub Actions queueing, for example a 73 s and an 84 s outlier | `docs/results/GOLIVE_RESULTS.md` |
| Some raw data is not in the repository | Sweep results, the first end-to-end files, the speed-levers scripts and n8n's execution database | `docs/old/SELFHEAL_E2E_RESULTS.md`, `docs/old/QWEN38_SPEED_CEILING.md`, `docs/results/SPEED_LEVERS_EVAL.md` |
| Speed-levers correctness cannot be re-scored | The run saved no raw output | `docs/results/SPEED_LEVERS_EVAL.md` |
| Backend replay stops at the decision | One run per fixture, six fixtures, no pull request and no CI | `docs/results/BACKEND_ON_KAGGLE.md` |
| Documents differ from the data in places | Bake-off time summary against raw calls; a second shipped-quality run not described; round 1 stored as 2 of 7 healed before re-scoring | `eval/runs/kout_diff/results.json`, `eval/runs/kout_bakeoff/results.json`, `eval/runs/kout_shipped_v2/results.json`, `eval/runs/golive1/golive_results.json` |
