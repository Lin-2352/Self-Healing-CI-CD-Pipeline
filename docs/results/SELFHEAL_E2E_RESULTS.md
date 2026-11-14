# End-to-end results: does ~9 tok/s work as a self-healing pipeline agent?

**Answer: yes.** 5/5 real breakages healed. Median push→PR **104 s**, worst case **116 s**,
against a "under ~6 min ⇒ workable" bar. Every PR was a one-line minimal fix whose branch
passed `npm test`.

One qualifier: **the model is the largest single phase, at ~55% of wall clock.** Speed
is not free here. The first draft of this file got that backwards; corrected numbers below.

**Scope:** those 5/5 ran through `heal_harness.py`, which executes the shipped Code nodes but
stands in for n8n itself. Phase D then ran the real workflow inside real n8n, confirmed the
prompt is byte-identical, and - after fixing four bugs the harness structurally could not
surface - **all 13 nodes now run and the n8n path opens a PR end to end.** See Phase D below.

Run date: 2026-09-07. Repo: `Lin-2352/self-healing-cicd-demo` (private).

## Setup under test

| piece | what ran |
|---|---|
| model | `unsloth/Qwen3.8-27B-GGUF` → `UD-Q4_K_XL` (17.6 GB), ctx **262144**, 27868/30720 MiB |
| host | Kaggle 2×T4, prebuilt `llama-server` from `linsxav/llama-server-builder` (2.4 s extract) |
| flags | `--flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 -b 2048 -ub 512 -t 4 -tb 4` |
| CI | GitHub Actions, `main.yml`, webhook gated on `failure_kind == 'code'` |
| orchestrator | `heal_harness.py` (now `tools/heal_harness.py`) — executes the **shipped** `Build AI Prompt` and `Extract AI Response` jsCode from `n8n/self_healing_pipeline.json` under `node`, not a Python paraphrase |

## Results

Timings come from GitHub's own job timestamps and llama-server's own `timings` block, not
wall-clock estimates. `push→hook` = `total − hook_to_pr`. The driver's polled "CI seconds" is
discarded: it lagged GitHub's run duration by ~12 s and, more importantly, *overlapped* the
heal rather than preceding it — see the correction note.

| # | scenario | break | push→hook s | fetch s | model s | tokens | tok/s | writes s | **push→PR s** | PR | diff | `npm test` |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 1 | `missing_app_init` | delete `const app = express();` | 37.3 | 2.7 | 56.9 | 636 | 12.12 | 5.3 | **102** | [#1](https://github.com/Lin-2352/self-healing-cicd-demo/pull/1) | +1 | exit 0 |
| 2 | `typo_method` | `app.lisen(` | 37.4 | 2.6 | 58.1 | 658 | 12.08 | 5.4 | **104** | [#2](https://github.com/Lin-2352/self-healing-cicd-demo/pull/2) | +1 −1 | exit 0 |
| 3 | `missing_require` | drop `require("http")` | 39.8 | 2.8 | 56.1 | 631 | 12.07 | 4.9 | **104** | [#3](https://github.com/Lin-2352/self-healing-cicd-demo/pull/3) | +1 | exit 0 |
| 4 | `syntax_missing_brace` | drop closing `});` | 39.4 | 2.6 | 56.3 | 637 | 12.15 | 5.1 | **104** | [#4](https://github.com/Lin-2352/self-healing-cicd-demo/pull/4) | +1 | exit 0 |
| 5 | `wrong_status_check` | `=== 200` → `=== 201` | 48.2 | 2.8 | 59.6 | 677 | 12.11 | 5.3 | **116** | [#5](https://github.com/Lin-2352/self-healing-cicd-demo/pull/5) | +1 −1 | exit 0 |

**5/5 passed.** Generation held at **12.11 tok/s mean** (spread 12.07–12.15, under half a
percent). Prefill ran 256–274 tok/s on ~2.25 KB prompts.

### The actual patches

```
PR #1  Fix missing Express app initialization in test.js  ->  +const app = express();
PR #2  Fix typo in test.js: app.lisen to app.listen       ->  -app.lisen(PORT   +app.listen(PORT
PR #3  Fix missing http module import in test.js          ->  +const http = require("http");
PR #4  Fix syntax error in test.js                        ->  +});
PR #5  Fix smoke test status code check                   ->  -=== 201         +=== 200
```

No whole-file rewrites and no collateral reformatting — the `.gitattributes` `eol=lf` pin was
the difference between a 1-line diff and a 40-line one on Windows.

## Where the 104 seconds actually go

```
push --40 s--> webhook --2.7--> fetches --57 s--> model --5.2--> PR
      (36%)                      (3%)             (55%)          (5%)
```

The **model is the dominant phase at ~55%**, and GitHub's share is smaller than it looks:
`build-and-test` itself runs in only **12 s** (checkout + `npm install` + test). The remaining
~28 s of the push→hook window is run queueing plus provisioning the second job's runner.

Stated against the target that drove three benchmark sweeps: **hitting 30 tok/s would cut the
model phase from ~57 s to ~23 s, taking total push→PR to ~68 s — a 33% cut.** The speed hunt
was well-founded. It is simply not the difference between workable and unworkable, because
104 s already clears the bar for a 3 a.m. auto-heal.

> **Correction.** The first draft of this file claimed Actions was the bottleneck at 93% and
> the model only ~13%. That came from adding the driver's polled `ci_seconds` (~100 s) to
> `hook_to_pr` (~65 s) as if they were sequential phases. They are not: `ci_seconds` runs until
> the workflow reports *completed*, which includes the notify job — and the notify job is
> itself blocked on the heal (next section). The two windows overlap almost entirely. The
> split above is rebuilt from GitHub's per-job timestamps.

## Finding: the webhook pins a GitHub runner for the whole heal — fixed

`notify-n8n-on-failure` took **67–73 s** per run, for a job whose only step is a single `curl`.

Cause: the webhook node was configured `responseMode: "lastNode"`, which holds the HTTP
response open until *Open Pull Request* finishes. The GitHub runner sat blocked for the entire
heal cycle.

This was not a harness artifact — the harness reproduced the shipped workflow's behaviour
correctly. Two real costs: ~70 s of billable runner time burned per heal, and a latent failure
mode where a slower heal (a larger file — see the scaling limit below) times the `curl` out and
the webhook *appears* to fail even though the PR was opened successfully.

**Fixed** in `n8n/self_healing_pipeline.json` and its generator: `responseMode: "onReceived"`.
The webhook acks immediately and the workflow continues asynchronously; GitHub ignores the
response body regardless.

**Verified live in Phase D** (run `34153373364`): `notify-n8n-on-failure` now takes **4 s**,
down from 67-73 s. The runner is released as soon as the webhook is acked instead of waiting out
the whole heal. The Phase C table above still reflects `lastNode`, since the fix shortens the
notify job rather than push->PR.

## Finding: scenario 5 passed here after failing every config offline

Scenario 5 is the only semantic bug in the set — nothing in the stack trace names the line or
the fix. The model has to read the smoke test, know that a bare `GET /` returns 200, and
conclude the *assertion* is wrong rather than the route. All 9 configs in the offline sweep got
it wrong on a synthetic snippet; with the real failed-step output and the complete real source
file, it was right first try.

**The sweep was measuring the prompt, not the model** — so its per-model quality rankings
deserve less weight than this deployed result.

## Finding: 12.1 tok/s, not the 8.8 the sweep measured

Same quant, same hardware, **+38%**. Two things changed together — the batch flags
(`-b 2048 -ub 512`) and the prebuilt binary — so this run cannot attribute the gain between
them. Either way, 8.8 was a floor rather than a ceiling.

## Scaling limit worth knowing before trusting this more broadly

Each fix generated 631–677 tokens: a short reasoning pass, then a JSON envelope containing the
**entire corrected file**. `test.js` is ~40 lines, so most of the output is the file itself.
That cost scales with **file size**, not repo size — a 400-line file is roughly 10× the tokens
and pushes the model phase past 8 minutes, at which point the `curl` timeout above becomes live
and the economics change.

For the single-file smoke-test failures this pipeline targets, 12 tok/s is comfortable. Beyond
that it needs a diff/patch output format rather than whole-file replacement.

## Verification

Every claim above is measured, not asserted:

- CI conclusion read from `gh run list` by head SHA; phase timings from `gh run view --json jobs`
- tok/s is llama-server's `timings.predicted_per_second`, not wall-clock division
- correctness = checked out each PR branch and ran `npm test` → exit 0, "smoke test passed"
- minimality = `git diff main...branch --stat`
- **infra gating proven end to end** (run `34134045465`): port 5000 squatted inside the
  workflow → `INFRA_FAILURE: port 5000 is already in use` → `npm test exited 75 ->
  failure_kind=infra` → `Set output 'failure_kind'` → `notify-n8n-on-failure: skipped`, with
  the harness count unchanged at 5. The `Set output` line is what makes this conclusive:
  `if: ... == 'code'` would *also* skip on an empty output, so a skipped job on its own could
  not distinguish a working gate from a broken one.
- webhook auth: a POST carrying a wrong `X-Webhook-Secret` returned 200 and recorded nothing

Raw data: `scenario_log.json` (driver) and `e2e_results.json` (harness), working files that were not kept in the repository.

## Phase D: the same workflow inside real n8n

Phase C ran the shipped Code nodes but stood in for n8n's HTTP nodes, credential store, and
webhook routing. Phase D put the real `n8n/self_healing_pipeline.json` into a real n8n and
pushed the same scenario-1 breakage at it.

**Headline: the prompt parity claim holds exactly, and n8n found three real bugs in the
shipped workflow that the harness structurally could not.** Two are fixed and verified; one
is still open, so **the n8n path does not yet open a PR end to end.**

### Parity: confirmed byte-for-byte

The whole chain through the model ran in n8n, so the numbers are directly comparable:

| | harness (Phase C) | n8n 1.123.77 | |
|---|---:|---:|---|
| prompt length | 2250 chars | **2250 chars** | identical |
| completion tokens | 636 | 649 | sampling variance |
| generation | 12.12 tok/s | **12.35 tok/s** | same |
| prefill | 256 tok/s | 281 tok/s | same |

`jsonBody: "={{ JSON.stringify($json.request_body) }}"` produces the same bytes as the
harness's Python `json.dumps`. **The Phase C timings transfer.**

### Bug 1: the webhook node had no `webhookId` - FIXED

On import, n8n registered the fallback path `selfheal/github%20failure%20webhook/github-failure`
and then **404'd that too**. The workflow was unreachable at any URL.

Cause: the generator never emitted a `webhookId`. Adding a stable uuid makes n8n register the
clean path. Verified after the fix:

```
webhook_entity        -> ('selfheal', 'github-failure', 'POST')
POST with X-Webhook-Secret     -> 200
POST with a wrong secret       -> 403      (header auth actually enforced)
```

### Bug 2: the AI branch name collides with itself - FIXED

The first n8n run died at `Create Branch`:

```
422 - {"message":"Reference already exists"}   refs/heads/ai-fix-missing-express-init
```

The model names the branch after the bug, deterministically, so **the same failure twice
produces the same branch name** - and Phase C's PR #1 had already taken it. In production this
means the second occurrence of any recurring failure fails to heal, *after* paying for the
model call.

Fixed by suffixing the commit sha in `Extract AI Response`. Verified two ways: offline against
the exact model output n8n recorded (`ai-fix-missing-express-init` -> `...-a9e07e2`), and live -
the next two runs created `...-e623af7` and `...-8ccd958` without a 422.

That fix is **necessary but not sufficient**: it separates different commits, not repeated
attempts at the same one. See *Retry safety* below for the part verification caught.

### Bug 3: `$('Extract AI Response')` resolved to empty downstream - FIXED

Both early n8n runs got past the model and past `Create Branch`, then died at `Get File SHA`
with a GitHub 404. The resolved URL showed why:

```
template : https://api.github.com/repos/{{ $('Extract AI Response').first().json.repo }}/contents/{{ ....file_path }}
resolved : https://api.github.com/repos//contents/
```

**Root cause, read out of n8n's own source rather than guessed.** n8n 1.123.77 defaults to
`N8N_EXPRESSION_ENGINE=vm` (isolated-vm), per
`@n8n/config/dist/configs/expression-engine.config.js`.

- `$('Node').first()` is a **typed RPC** (`@n8n/expression-runtime/.../isolated-vm-bridge.js`)
  that structured-clones the **entire item** across the isolate boundary.
- The Code node runs on **vm2**, so its output items are Decontextify **Proxies**
  (`n8n-nodes-base/dist/nodes/Code/utils.js`). `structuredClone(proxy)` throws `DataCloneError`.
- The bridge re-throws only `ExpressionError`, so that `TypeError` is **silently swallowed**.
  The expression yields `undefined`, which a mixed template renders as `''` - hence two empty
  URL segments and no error anywhere.
- `$json` and `$node[...]` take a different path (`getValueAtPath`) that walks the data **on
  the host** and returns only primitives, which always clone. Webhook and HTTP node outputs are
  plain JSON objects, so `.first()` against *those* clones fine.

That is exactly the matrix the executions showed: `CODE→CODE` ✓, `HTTP→webhook/HTTP` ✓,
`HTTP→$json` ✓, **`HTTP→CODE` ✗** - and only the three GitHub write nodes used that last form.

**Fix: `$('Extract AI Response').first().json.X` → `$node['Extract AI Response'].json.X`**
(13 occurrences). Measured against the real recorded run data before being applied: `.item`,
`.all()[0]`, `.last()` and `$items()` all share the same clone path and all returned
`undefined`; only `$node[...]` survived the boundary.

`pairedItem` was **not** the cause; that earlier guess was wrong. It is correct
practice and stays in, but it fixes nothing here. Note that every field `Extract AI Response`
emits is deliberately a **primitive**: `$node[...]` resolves primitives on the host, whereas a
nested object comes back as clone metadata.

### Bug 4: model prose broke the hand-built JSON bodies - FIXED

Found by reading the nodes, never reached in the earlier runs because Bug 3 blocked them.
`Push Fix File` and `Open Pull Request` interpolated `pr_title` / `pr_body` straight into a
hand-quoted JSON string. The recorded model output happened to contain no quotes or newlines;
the next one would not have. Now built as an object and serialised with `JSON.stringify`, the
same pattern the Qwen node already used successfully.

### Retry safety: the branch fix over-claimed - FIXED

Verification disproved a claim shipped in a code comment ("sha is unique per push, so this cannot
collide"). Suffixing the commit sha separates *different* commits - the recurring-failure case
that broke Phase D - but **re-running the same commit lands on the same branch again** and 422s,
after the model call is already paid for.

The heal is now **idempotent**, via two small guard Code nodes (11 → 13 nodes):

- `Create Branch` runs with `onError: continueRegularOutput`; **`Branch Ready`** passes a benign
  `422 Reference already exists` and **re-throws anything else**, so a real 401/404 still fails
  loudly rather than sailing through.
- `Open Pull Request` likewise; **`Heal Outcome`** reports `pr_opened` / `pr_already_open` and
  throws on anything else.
- **`Get File SHA` and `Push Fix File` stay strict on purpose** - no `onError`. Only the two
  endpoints with a genuinely benign duplicate case are tolerant. A 404 in the middle of the
  sequence means the branch or file is not where we think it is, and it must stop the run rather
  than become a silent no-op. Drive (b) shows the retry path needs no tolerance there: the branch
  already exists, `?ref` reads its blob SHA, and the update proceeds normally.

### Phase D verification - all six drives, no GPU

Driven against real n8n 1.123.77 with `mock_qwen.py` replaying a recorded model
response (~25 s per cycle). Execution ids are in `n8nhome1/.n8n/database.sqlite`.

| # | drive | result |
|---|---|---|
| a | fresh heal of a real breakage (commit `8ccd958`) | exec 11 **success** → PR #7, diff `+const app = express();`, `npm test` exit 0 |
| b | **replay the identical payload** | exec 12 **success** - branch reused, file updated, existing PR reported. This is the exact case that made verification fail |
| c | model returns `"` in the title and newlines + a backslash in the body | exec 13 **success** → PR #8 title `Fix missing "app" init in test.js`, body intact across 4 lines |
| d | `Create Branch` gets a **non-benign** 422 (invalid ref name) | exec 14 **error**, guard message quotes GitHub verbatim: `refs/heads/bad~ref~name-a9e07e2 is not a valid ref name`; no stray branch or PR |
| e | wrong `X-Webhook-Secret` | 403 (regression held) |
| f | resolved `Get File SHA` URL | `.../contents/test.js?ref=ai-fix-missing-express-init-8ccd958` - repo populated, `?ref` pinned to the fix branch |
| **g** | **live endpoint, real model, no mock** | exec 15 **success in 73.2 s** → PR #9, diff `+const app = express();`, `npm test` exit 0; 697 tokens at **11.59 tok/s**, prefill 256 tok/s, `Heal Outcome: pr_opened` |

The two guard nodes were also driven directly through `node` on seven synthetic error shapes
(`test_guards.py`, 7/7) before spending an n8n cycle.

### What Phase D proved

**All 13 nodes now run, and the n8n path opens a PR end to end.** Credentials import and
decrypt (3 of 3), the workflow activates from the CLI, the webhook routes through a Cloudflare
tunnel from GitHub Actions, and a retry no longer breaks anything.

Prompt parity from the first successful model call still stands: n8n sent the **same 2250
chars** and measured **12.35 tok/s** against the harness's 2250 / 12.12, so the Phase C timings
transfer.

Drive (g) closes the last gap: the same fixed workflow, against a real Kaggle endpoint with no
mock anywhere, healed a real breakage into PR #9 in **73.2 s**.

That 73.2 s is **webhook→PR**, not push→PR - the drive posts the webhook directly and skips the
Actions leg entirely. The comparable Phase C figure is 65 s hook→PR; the headline 104 s includes
~40 s of GitHub Actions queueing and running before the webhook fires. The fixes did not make
anything faster, they made it finish at all. Breakdown: 697 tokens at 11.59 tok/s = 60 s
generation, plus ~3 s prefill (823 tokens at 256 tok/s), leaving ~10 s for the four GitHub
fetches and four writes - in line with Phase C's 2.7 s + 5.2 s.

### n8n version matters

`npm i n8n@latest` installs **2.35.7**, whose activation model is not the one this workflow
targets: `update:workflow --active=true` is deprecated there, `publish:workflow` returns exit 0
while writing no rows, and `webhook_entity` stays empty through restarts, so the workflow can
never receive anything. Pinning **n8n@^1 (1.123.77)** activates normally. The shipped JSON is
1.x-shaped; running it on 2.x is a version mismatch, not a workflow error.

Two smaller environment notes: n8n Variables are licensed, so `$vars.QWEN_BASE_URL` does not
resolve on community edition - the import copy made by `make_n8n1_imports.py` substitutes
a literal, leaving the shipped JSON untouched. And an expression chain like
`$vars.X || $env.X || 'literal'` *throws* rather than falling through when `$vars` is undefined,
so it is not a safe fallback.

## Still open

- **`$node[...]` is the legacy accessor.** It is currently the *only* form that survives n8n's
  isolate boundary, but n8n could remove it. Documented fallback if that happens:
  `N8N_EXPRESSION_ENGINE=legacy` restores every accessor, at the cost of opting out of the new
  sandbox.
- **Ridge quant** benchmark - deferred, unrelated to this run.

### Reproducing Phase D

The helper scripts named here (`test_guards.py`, `mock_qwen.py`, `make_n8n1_imports.py`) and the recorded
replies were working files and are not in the repository. The maintained equivalent is the rig in
`tools/n8n_rig/` (see its README).

```
npm install n8n@^1                      # 2.x will not activate this workflow
python test_guards.py                   # 7 guard cases, no n8n needed - run this first
python mock_qwen.py --port 8010 --delay 2   # replays a real recorded reply
python make_n8n1_imports.py             # credentials + import copy pointed at the mock
n8n import:credentials --input=n8n1_credentials.json
n8n import:workflow    --input=wf_import1.json
n8n update:workflow --id=selfheal --active=true
n8n start                               # then verify webhook_entity is non-empty
# then POST payloads straight at localhost:5678/webhook/github-failure
```

`mock_qwen.py --response <file>` swaps the replayed reply; `mock_response_risky.json` and
`mock_response_badref.json` are the fixtures behind drives (c) and (d).

**Iterate on Bug 3 without a GPU.** `recorded_model_response.json` held a real
model reply captured from a live n8n execution. Serve it from a ~15-line local HTTP server on
`POST /v1/chat/completions` and point the import copy's Qwen URL at `http://localhost:<port>`:
the whole real workflow then runs end to end for free, in roughly 40 s per cycle instead of
~2.5 min. The model was never the unknown here - parity was already proven byte-for-byte - so
**a Kaggle session is not needed to close Bug 3.** Do this before spending GPU quota.

`N8N_USER_FOLDER` and `N8N_ENCRYPTION_KEY` must be identical for the import and the start, or
the credentials decrypt to nothing. On a fresh instance also set
`userManagement.isInstanceOwnerSetUp` (n8n skips activation on an uninitialised instance);
`N8N_USER_MANAGEMENT_DISABLED=true` handles this on 1.x.

Raw n8n execution data (not kept): `n8nhome1/.n8n/database.sqlite`, table `execution_data`
(flatted format - the numbers are string-pool indices, decode with `json.loads` and index in).
