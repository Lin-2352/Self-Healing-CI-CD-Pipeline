# Go-live: SEARCH/REPLACE + tensor split + MTP, measured end to end

> **Round 2 (2026-09-23) is at the bottom.** It fixes the defects this round found: the CI log is now in
> the prompt, heal PRs run their own CI, a dead healer no longer turns commits red, the ctx ladder
> boots first try, and n8n readiness is judged by effect. Result: 5/5 healed, 5/5 heal-PR CI green,
> real CI log in 5/5 prompts. Push→PR median 42 s.
>
> **Round 3 (2026-09-23, last section): the capped CI-feedback retry loop, live.** `multi_hunk` healed
> through a thinking retry (2/2 runs), `callback_arity` gave up cleanly at the cap, and 5/5 controls were
> green with 0 retries. Quality numbers behind it: `SHIPPED_QUALITY_EVAL.md`.

**Result: 5/5 real breakages healed live. Push→PR median 44 s, down from ~104 s. The model phase
fell from ~57 s to a median of 11.4 s.**

Date: 2026-09-22. Demo repo `Lin-2352/self-healing-cicd-demo`. Model endpoint: Kaggle 2×T4,
`Qwen3.8-27B-UD-Q4_K_XL`, n8n 1.123.81, all timings from GitHub's and n8n's own timestamps.

## Results

| case | PR | push→PR | file parses | byte-exact golden | smoke test |
|---|---|---|---|---|---|
| `missing_require` | #10 | 44 s | ✓ | ✓ | PASS |
| `syntax_missing_brace` | #11 | 55 s | ✓ | ✓ | PASS |
| `wrong_status_check` | #12 | 43 s | ✓ | ✓ | PASS |
| `missing_app_init` | #13 | 42 s | ✓ | ✓ | PASS |
| `typo_method` | #14 | 73 s | ✓ | ✓ | PASS |

"Healed" means **all three**: the file on the PR branch parses, is byte-identical to the
known-good file, and passes the real smoke test. These were re-scored from the PR branches
themselves, independent of the runner (see *Runner bug* below).

## Where the time goes

| case | total | Actions queue | build | notify | n8n + model |
|---|---|---|---|---|---|
| `missing_require` | 44 s | 3 s | 14 s | 7 s | 20 s |
| `syntax_missing_brace` | 55 s | 4 s | 14 s | 10 s | 27 s |
| `wrong_status_check` | 43 s | 3 s | 14 s | 7 s | 19 s |
| `missing_app_init` | 42 s | 2 s | 12 s | 7 s | 21 s |
| `typo_method` | 73 s | **38 s** | 11 s | 7 s | 17 s |

The 73 s outlier is **GitHub Actions queueing**, not the pipeline.

Inside n8n, per node (from each execution's recorded `executionTime`):

| execution | fetches (3 nodes) | model call | completion tokens | GitHub writes (5 nodes) |
|---|---|---|---|---|
| 3 | 4.5 s | 11.4 s | 133 | 7.3 s |
| 4 | 3.8 s | **19.9 s** | **369** | 6.5 s |
| 5 | 3.7 s | 11.1 s | 167 | 7.1 s |
| 6 | 4.2 s | 12.8 s | 195 | 6.7 s |
| 7 | 3.7 s | 9.4 s | 121 | 7.2 s |

- **Model phase: median 11.4 s, versus ~57 s before.** The whole-file prompt emitted ~655 tokens;
  SEARCH/REPLACE emits a median of 167 here.
- The 369-token call is `syntax_missing_brace`, matching the eval's 370 almost exactly: a pure
  insertion forces a large, unique SEARCH anchor. Anchor cost is set by the surrounding code, not
  the bug.
- The model call carries ~5–6 s of fixed overhead beyond generation (~800-token prefill plus two
  round trips through the Cloudflare quick tunnel). That's larger than the eval's 2.8 s because
  the eval had no tunnel.
- **GitHub API writes (~7 s) and fetches (~4 s) now cost as much as the model does.** They are
  the next-largest fixed block after CI itself.

## Actual production decode speed: ~23 tok/s, not 28

From llama-server's own `timings`, stored by n8n in each model response:

| execution | decode tok/s | tokens | prefill | prefill tok/s | MTP acceptance |
|---|---|---|---|---|---|
| 3 | 20.79 | 133 | 3.25 s | 274 | 0.830 |
| 4 | 23.10 | 369 | 2.15 s | 321 | 0.915 |
| 5 | 23.14 | 167 | 2.16 s | 323 | 0.883 |
| 6 | 22.22 | 195 | 2.21 s | 311 | 0.893 |
| 7 | 23.55 | 121 | 2.16 s | 320 | 0.895 |

**Median decode 23.10 tok/s. Plan against this, not the 28.02 from `SPEED_QUALITY_EVAL.md`.**
Three things differ from that eval, so do not read the gap as a single cause:

- **MTP acceptance fell from 0.965 to ~0.89.** This is the largest effect, and it has a clear
  mechanism. Whole-file output was mostly *copying the input*, which is exactly what a draft head
  predicts well. SEARCH/REPLACE output (plus a prose `pr_body`) is more novel, so speculation helps
  less. **The format change and the MTP gain partly offset each other.** The benches measured them
  separately and could not show this.
- **Stock b9690 here vs fork b10709 in the eval** (~4% measured difference).
- **ctx 131072 here vs 8192 in the eval.**

Prefill is now visible: **~2.2 s at ~320 tok/s** for a ~700-token prompt, about 20% of the model
call. Caching the fixed system-prompt prefix (`cache_prompt`) is the direct lever. *(Round 2
correction: it was already on by default. Calls 2–5 here had `cache_n = 205`, so it is a null lever.)*

## Levers confirmed engaged in production, by effect

- Tensor split: per-GPU VRAM **symmetric `[13287, 13287]`**.
- MTP: `accept_rate` **0.612** on the deploy's prose benchmark (temperature 0.7). Code heals
  measured 0.965 in the eval. Non-null is the proof that it ran.
- Context: **131072** (16× the eval's 8192), 26,566 / 30,720 MiB.

## Three defects found only by going live

1. **`Build AI Prompt` shipped a string literal split across 10 physical lines**, so the first two
   real heals died on `Unterminated string constant`. The recurring `\n` mangling turned JS escapes
   into real newlines. Nothing before live caught it: `gen_n8n --check` only compares the output to
   itself, the extract test never touches this node, and the n8n verify rig **stubbed this node**.
   The verify report said so explicitly ("Build AI Prompt was stubbed … hasn't been exercised in
   n8n end-to-end"). Because the report named that gap, this node was the first suspect. It's also why a parse gate belongs in the generator:
   the one gap the verification disclosed is exactly where the bug was. Fixed by emitting the
   literal with `json.dumps`, and `gen_n8n.py` now **refuses to write** a workflow whose Code nodes
   do not parse. Re-introducing the exact bug makes it exit 2 and name the node.
2. **The runner's "healed 0/5" was a false negative.** It fetched PR files through a helper that
   `.strip()`s stdout, which removed the file's trailing newline, so a byte-identical PR scored
   `golden=False`. Fixed in `pr_file()`. Every PR was re-scored from its branch.
3. **The ctx ladder hung instead of failing.** With tensor split, llama.cpp's auto-fit is disabled,
   so 262144 and 196608 each hung for the full 15-minute health timeout (~30 min of GPU) before
   131072 booted in under a minute. The ladder now starts at 131072 with a 300 s per-rung cap.

Also found: `n8n start` answers `/healthz` **before** it activates workflows. Probing the webhook
right after health gave a false 404 "not registered". Readiness should be judged by the
`Activated workflow` log line.

## State after teardown

- **`N8N_WEBHOOK_URL` in the demo repo points at a Cloudflare *quick* tunnel that died at
  teardown.** Until the next deploy re-points it, a code failure on `main` will turn the
  `notify-n8n-on-failure` job red (curl to a dead host), even though nothing else is wrong. The
  next go-live resets it (`tools/golive/`).
- The Kaggle kernel `qwen3-8-27b-live-endpoint` v7 serves until its `HOURS = 1.0` window lapses.
  The Kaggle CLI has no cancel.
- PRs #10–#14 remain open as evidence, alongside 8 older heal PRs from 2026-09-07.

## Still not true, and next (round 1; the CI-log and branch-CI items are fixed in round 2)

- **The model never sees the CI failure output.** Found by recording the exact request in the
  n8n verify rig: the user message carries repo, commit, job name, the failing step's *name*
  ("Run tests: failure") and the source file, and nothing else. `Fetch Job Logs` calls the jobs
  *metadata* endpoint, not the log. This is despite a system prompt that says "Analyze the CI/CD
  failure logs". The gap predates the go-live work. The 5/5 live heals worked because these bugs are
  visible in the source. **Every eval so far included the real error log, so eval quality
  measured a different input than production gets.** Fetching the failing job's log
  (`GET /repos/{repo}/actions/jobs/{job_id}/logs`, trimmed to the error region) is likely the
  cheapest real quality lever there is, and Phase 1 must measure both with-log and without-log.
- **CI still does not run on `ai-fix-*` branches** (`main.yml` triggers on push to `main` only).
  PRs #10–#14 have no CI run of their own; they were verified above by running the file directly.
  Adding a `pull_request` trigger is Phase 2 of the plan.
- Model-side speed is now ~11 s of a ~44 s cycle. The remaining time is CI (~14 s), notify (~7 s),
  GitHub API calls (~11 s) and Actions queueing, so the plan's quality-first ordering holds.

---

# Round 2 (2026-09-23): defects fixed, validated live

**5/5 healed byte-exact · 5/5 heal PRs green on their own CI · real CI log in 5/5 prompts ·
push→PR median 42 s.** The ctx ladder booted 131072 on the first rung.

| case | PR | push→PR | golden | PR CI | CI log in prompt | completion tok | model call |
|---|---|---|---|---|---|---|---|
| `missing_app_init` | #15 | 84 s* | ✓ | success | REAL | 173 | 11.7 s |
| `typo_method` | #16 | 40 s | ✓ | success | REAL | 116 | 8.7 s |
| `missing_require` | #17 | 42 s | ✓ | success | REAL | 128 | 9.3 s |
| `syntax_missing_brace` | #18 | 53 s | ✓ | success | REAL | 350 | 17.9 s |
| `wrong_status_check` | #19 | 40 s | ✓ | success | REAL | 128 | 9.1 s |

\* **Actions queueing again, not the pipeline.** The notify job was created at 00:14:25 but only got
a runner at 00:15:03. The curl itself took 2 s. The n8n run (26 s) was normal.

Evidence sources:
- `eval/runs/golive2/golive2_results.json` and `kaggle/logs/live_runs/ (round 2)` (runner);
- `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/ (boot.log per run)` (boot);
- `python tools/golive/exec_stats.py 8` (n8n's stored executions: what the model was actually sent,
  and llama-server's own timings).

## What changed, and what each fix measured

| fix | before | after (measured) |
|---|---|---|
| CI log in prompt: `Fetch Failed Job Log` → `Download Job Log` → trimmed in `Build AI Prompt` | step names only | the failing step's output, e.g. `ReferenceError: http is not defined` + stack, 17 lines / 456 chars for a real log. Present in **5/5** live prompts |
| `pull_request` trigger; notify on `push` only | heal PRs had no CI | **5/5 heal PRs ran CI, 5/5 green**. Loop gate tested on its own: a scratch PR (#20) with a broken `test.js` gave `build-and-test` = failure and notify = **skipped** (run 35802293458) |
| notify: `curl --fail --max-time 20 \|\| ::warning::` | dead tunnel = red commit | tested with the healer down: a broken push gave `build-and-test` = failure and notify = **success**, and the check-run annotations show `warning: n8n webhook unreachable - heal not triggered` (run 35802199387) |
| ctx ladder starts at 131072, 300 s per rung | ~30 min of GPU hung on 262144/196608 | **first rung booted**, 26,566 / 30,720 MiB, symmetric [13287, 13287], MTP accept 0.671 on the prose bench |
| readiness by effect (`tools/golive/up.py`) | `/healthz` green before activation → false 404 | webhook probed with a wrong secret. **403 after 88 s** = registered and guarded |
| `pr_body`: "at most 2 short sentences" | median 167 completion tokens | **median 128** (−23%). Bodies are 108–158 chars and now cite the logged error |
| `cache_prompt: true` | — | **no effect**: round 1 already had `cache_n = 205` on calls 2–5 (server default). Now 216. Kept explicit only as documentation |

## Two defects the verification caught before production

1. **The GitHub PAT would have leaked to the log blob host.** The first design used one HTTP node
   and let it follow GitHub's 302 to the signed blob URL, assuming the HTTP client drops
   `Authorization` across hosts. The real-n8n verify rig proved otherwise: the mock blob host
   (127.0.0.2) received the bearer token. The fix splits the fetch in two:
   - `Fetch Failed Job Log` does **not** follow redirects (`neverError` + `fullResponse`);
   - `Download Job Log` fetches the `Location` with **no credential**.

   The rig now asserts that the PAT reached the API host and did not reach the blob host.
2. **A new quick-tunnel hostname probed too early poisons the local DNS cache.** `up.py` saw
   "unreachable" for 120 s while public DNS already resolved the host and a forced-resolve probe
   returned 403. The tunnel probe now resolves over DoH (1.1.1.1), which is what GitHub's runners
   effectively see. After the fix, a fresh `up.py` (new n8n, new tunnel) was **ready through the
   tunnel in 7 s**.

## Speed, with the log in the prompt

- **Decode: median 27.73 tok/s** (round 1: 23.10), MTP acceptance median **0.915** (round 1: ~0.89).
  Same build, same ctx, same flags. The only input change is the prompt now carrying the CI log.
  One plausible mechanism is that the error text gives the draft head more predictable content
  (identifiers from the stack trace). **That is not proven**, and 5 calls is a small sample.
- **Prompt: +~200 tokens** (total 971–1156 vs ~890). Prefill 2.2–2.7 s after the first call.
- **Model call: median 9.3 s** (round 1: 11.4 s). Fewer output tokens and a faster decode outweighed the larger prompt.
- **Fetch cost: +~3 s.** The two log nodes take ~1.3 s + ~1.9 s. Net n8n time is roughly unchanged (~20–26 s).

## State after teardown

- n8n and the tunnel are stopped (`tools/golive/down.py`). `N8N_WEBHOOK_URL` points at the dead tunnel.
  With the notify fix, that now produces a **warning**, not a red commit.
- All heal PRs are closed and their branches deleted: #1–#14 as round-1 artifacts, #15–#19 after scoring, and #20 (scratch gate check). 0 open. `main` is green.
- Kaggle kernel v8 serves until its `HOURS = 1.0` window lapses (the CLI has no cancel).
- n8n is now installed durably at `%LOCALAPPDATA%\selfheal-n8n\app` (the `%TEMP%` install was wiped).

## Still open

- **CI-feedback retry loop** (deferred on purpose). It needs an attempt cap. Until then, a red heal
  PR stays red and does not re-trigger the healer.
- **Quality with vs without the log was not A/B-measured on the hard cases.** All 5 live cases healed
  in both rounds, so this data cannot show a quality gain. The hard-case eval (`eval/cases/hard_cases.json`)
  in the shipped format is where that would show.

---

# Round 3 (2026-09-23): the CI-feedback retry loop, live

**Loop mechanics: all verified live.**
- A failing heal PR woke the healer.
- Each retry landed on the same branch with an incremented trailer and used thinking.
- The loop stopped at the cap with a "needs a human" comment.
- Green PRs got no retry. That holds by construction: `failure()` is false on a green run, so it isn't a separate measurement.

**Quality:**
- `multi_hunk` (0/12 in every earlier config, and a first-pass failure) **healed through a retry, 2/2 live runs**.
- `callback_arity` **gave up cleanly at the cap** after 3 attempts.
- All 5 controls went green on the first attempt, with 0 retries.

| case | outcome | attempts | push→green | final file |
|---|---|---|---|---|
| `callback_arity` | **gave up at cap** (comment posted) | 1, 2, 3 | – | smoke fails |
| `multi_hunk` (run 1) | **green via retry** | 1, 2 | 161 s | smoke passes |
| `multi_hunk` (run 2, after the Heal Outcome fix) | **green via retry** | 1, 2 | 151 s | smoke passes |
| `missing_app_init` | green | 1 | 62 s | golden |
| `typo_method` | green | 1 | 59 s | golden |
| `missing_require` | green | 1 | 66 s | golden |
| `syntax_missing_brace` | green | 1 | 103 s | golden |
| `wrong_status_check` | green | 1 | 64 s | golden |

Model calls (`python tools/golive/exec_stats.py 13`):
- **First pass: 8.5–16.3 s**, decode ~30 tok/s.
- **Thinking retries: 51.6–67.2 s** (1160–1605 tokens). The quick tunnel's timeout (120 s per Cloudflare's own 524 text; earlier docs said 100 s) left ≥ 53 s of headroom in all 4 retries. Round 4 (2026-09-28) then saw one retry run 125.9 s and get cut off - see the full test run in eval/runs/testall/.

## How the loop works
- **Counter:** `main.yml` reads the PR head commit's `Heal-Attempt: N` trailer (stamped by the healer
  on every commit).
  - N < 3: wake the healer with `attempt=N`.
  - N ≥ 3: `gh pr comment` "needs a human".
  - no trailer (a human pushed): nothing.
  - Human PRs never wake it.
- **Retry request:** `Build AI Prompt` adds "Heal attempt N+1 …" and turns **thinking on (retries only)**.
  `Extract` keeps the incoming branch and stamps `Heal-Attempt: N+1`. The server caps thinking with
  `--reasoning-budget 1024`.
- **Why thinking is on retries only:** see `SHIPPED_QUALITY_EVAL.md`. Thinking healed 18/18 hard calls vs 12/18, but at ~4× the time.
  Always-on would add ~28 s to every heal. Retry-only costs hard bugs one extra CI cycle.
- **Gate tests without GPU:** a scratch PR run with trailers 3, 1 and none produced give-up + comment,
  a webhook call, and nothing, respectively. The results were read from job step conclusions and check-run annotations.

## Defect found live, fixed and re-verified
**On a retry, `Open Pull Request` asked GitHub for a PR from the fix branch into ITSELF.** The branch is
both head and base, so GitHub answered 422 "No commits between X and X", and `Heal Outcome` marked the
execution failed. The fix commit had already been pushed, so the loop still worked, but all 3 retries
showed as errors in n8n. `Heal Outcome` now reports `retry_pushed` for attempt > 1
(`tools/tests/test_heal_outcome.py`, 6 cases, including two that must still fail loudly). A second live
`multi_hunk` run confirmed it: the retry execution is `success`.

## Why `callback_arity` still fails: the retry prompt anchors on the wrong fix
- **Attempt 1** kept the wrong `(err, res)` callback signature and added an `if (err)` guard, so the
  response object now lands in `err` and CI logs `CODE_FAILURE: [object Object]`.
- **Attempts 2 and 3** chased that new symptom (error-message formatting) and never revisited the signature.
- The retry prompt says "keep the previous change unless it is itself wrong". That anchors the model on its
  own mistake. Arm C (18/18) was measured on the ORIGINAL file, not on a file carrying a wrong fix.
- **The two hard cases pull in opposite directions.** `multi_hunk` needed the retry to BUILD ON a partial
  fix; `callback_arity` needed it to THROW AWAY a wrong one. So the next step is a neutral retry prompt that
  shows the previous attempt's diff and asks whether it was incomplete or wrong. It must be measured
  offline on the exact live retry inputs, captured in `eval/cases/fixtures/retry_inputs_live.json`, before shipping.

## Known limits
- A first-pass fix rejected by the parse gate opens no PR, so it gets **no retry**.
- Quick tunnels time out at **120 s** (HTTP 524). Round 3 retries used ≤ 67 s; round 4 had one retry run 125.9 s and die, leaving a silent dead end.
- **A retry that produces no commit is a silent dead end.** A 524 from the tunnel, an Extract rejection
  on the retry, or an n8n error leaves the PR red with **no** "needs a human" comment, because main.yml only
  comments when the head trailer reaches the cap. Round 3 never hit this, and the runner's stall detection
  never fired. Fix for next round: have n8n comment on the PR when a retry fails.

State: n8n and the tunnel are down. PRs #21–#29 are closed with their branches deleted; 0 PRs open; `main` is green.
Kaggle v9 serves until its `HOURS = 1.5` window lapses. Data: `eval/runs/golive3/golive3_retry_results.json`,
`kaggle/logs/live_runs/ (round 3)`, `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/ (boot.log per run)`.

---

# Round 5 (2026-09-28): retry timeout and silent dead end fixed

**Both defects from the full test run are fixed and proven live. `callback_arity` healed live for the first time, on
attempt 3.** It had failed in every earlier setup.

| case | outcome | attempts | push→green |
|---|---|---|---|
| `callback_arity` | **green** | 1, 2, 3 | 249 s |
| `multi_hunk` | green | 1, 2 | 143 s |
| `missing_require` | green | 1 | 54 s |

All 6 n8n executions (#36–41) succeeded, including 3 thinking retries (53.9–64.7 s). There were 0 HTTP 524s and 0 stalls.

**Fix 1: the model call is streamed.** `request_body.stream = true`, and the Qwen node reads the body as text. A new Code
node, `Assemble Model Reply`, rebuilds the completion from the SSE and keeps the timings. The Cloudflare quick tunnel
cuts an origin that stays silent for **120 s**; a stream is never silent.

Proof, with the same long request through the same tunnel (`eval/runs/golive5/golive5_stream_proof.json`):

| request | result |
|---|---|
| streamed | **HTTP 200 after 166.5 s**, all 3600 tokens |
| not streamed | **HTTP 524 at 125.9 s**, the same cut-off as the original failure |

**Fix 2: every heal that ends without a fix posts a comment.** The Qwen node, `Assemble Model Reply` and
`Extract AI Response` now continue on error. A new IF node, `Fix Ready?`, routes the error to `Explain Failure` and then
`Post Failure Comment`. That comment goes on the heal PR for a retry (`main.yml` now sends `pr_number`), or on the
failing commit for a first heal. It was proven live with the model endpoint down, in `eval/runs/golive5/golive5_deadend_comments.json`:
- a commit comment on 7d95ab5;
- a PR #37 comment on the retry.

Not covered: a failure *after* the fix is pushed (Create Branch → Open PR) still fails the execution without a comment.

**Tests.**
- `tools/tests/test_assemble_reply.py`: 15 checks.
- The real-n8n rig now has 15 checks, including new `extract` checks (each rejection posts a commit comment) and a new
  `failure` mode (a 524 posts a PR comment; the streamed happy path works).
- The eval kernel forces `stream: false`: it runs on localhost with no tunnel.
