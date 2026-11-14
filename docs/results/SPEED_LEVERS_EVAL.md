# Two llama.cpp levers take Qwen3.8-27B from 12.4 to 26.8 tok/s on 2×T4

**Verdict: ship both. `-sm tensor` + `--spec-type draft-mtp` is +117% (2.17×) with no quality
loss: 60/60 correct fixes.**

The model was ~55% of the 104 s push→PR cycle at **12.35 tok/s**. The old 24-config sweep called
this a hard bandwidth wall. It was right about the hardware and wrong about the ceiling: both
levers below postdate that sweep, and the prebuilt server we already ship supports them.

- **Run:** Kaggle `lacrimatct/qwen3-8-speed-levers-tensor-split-mtp`, status `COMPLETE`
- **Model:** `Qwen3.8-27B-UD-Q4_K_XL.gguf`, temp 0.1, 2× Tesla T4 (14,911 MiB each)
- **Design:** 4 configs × 5 real eval scenarios × 3 reps = **60 calls**, zero errors
- **Context:** `-c 8192` for every cell (see *Context change* below)

---

## Results

| # | config | median tok/s | vs baseline | median wall | fix_correct | draft acceptance |
|---|---|---|---|---|---|---|
| A | baseline (today's flags) | 12.35 | — | 56.7 s | **15/15** | — |
| B | `-sm tensor` + f16 KV † | 15.49 | **+25%** | 45.3 s | **15/15** | — |
| C | `--spec-type draft-mtp` | 19.99 | **+62%** | 36.8 s | **15/15** | 0.953 |
| D | **both** | **26.83** | **+117%** | **27.8 s** | **15/15** | 0.960 |

† **B is not a clean single-variable result.** Tensor mode *forbids* quantized KV, so `-sm tensor`
and `-ctk/-ctv f16` change together by necessity. The +25% belongs to that pair, and the pair is
what ships; there is no way to isolate the split mode on its own.

**Baseline sanity passed:** cell A reproduced **12.35 tok/s** against the 12.1 on record, so the
rig is measuring the same thing the production run did.

Per-scenario medians are within ±1% of each other, so no single scenario drives the gain:

| scenario | A | B | C | D |
|---|---|---|---|---|
| `missing_app_init` | 12.36 | 15.48 | 19.72 | 26.55 |
| `typo_method` | 12.35 | 15.63 | 20.11 | 26.90 |
| `missing_require` | 12.34 | 15.52 | 19.87 | 26.94 |
| `syntax_missing_brace` | 12.35 | 15.48 | 20.08 | 26.75 |
| `wrong_status_check` | 12.34 | 15.47 | 20.01 | 26.86 |

---

## Both levers provably engaged

A flag that silently no-ops reads as "no gain", so engagement was proven from the server logs
rather than assumed. The discriminator is exact:

| cell | `SPLIT_MODE_TENSOR` hits | `draft-mtp` hits | VRAM per GPU (MiB) |
|---|---|---|---|
| A | 0 | 0 | `[7761, 9371]`, asymmetric |
| B | **1** | 0 | `[8721, 8721]`, symmetric |
| C | 0 | **16** | `[7917, 9793]`, asymmetric |
| D | **2** | **16** | `[9051, 9051]`, symmetric |

Three independent confirmations for tensor mode: the `SPLIT_MODE_TENSOR` log line appears in B and
D *only*; VRAM is **perfectly symmetric** in exactly those two cells (tensor parallelism splits
every tensor evenly, layer split cannot); and throughput rises 25%.

> **Correction to the rig, not the result.** The script's `engaged` check looked for the string
> `"tensor parallelism"` and reported `tensor: False` for B and D. That string belongs to a
> higher-verbosity line the server never printed. The check was wrong, not the lever.
>
> **The self-test did not catch it.** `test_speedlevers.py` passed
> every assertion while the check it existed to protect returned a false negative, because
> the test fed the checker a log line copied from a `strings` dump of the binary rather than from
> a real log. Both are now
> fixed: the discriminator is `SPLIT_MODE_TENSOR`, and the engagement cases assert against the
> four captured logs, now in
> `kaggle/logs/evals/qwen3-8-speed-levers-tensor-split-mtp/2026-09-15/server_logs/`, which fail loudly if those fixtures are absent.
>
> The same lesson produced two further guards after a later review: the printed summary now
> carries **`n`** (error rows drop out of the median set, so 10 of 15 calls failing used to print
> as a clean `fix=5/5`) and **`trunc`**, and a test drives that print block rather than grepping
> it, so a dropped column fails the suite.

MTP's own statistics line, verbatim from `C_mtp.log`:

```
statistics draft-mtp: #gen drafts = 223, #acc drafts = 214, #gen tokens = 446,
#acc tokens = 422, #mean acc len = 2.89, #acc rate/pos = (0.960, 0.933)
```

---

## Why the old "30 tok/s is impossible" conclusion was wrong

The previous analysis reasoned: a T4 has 320 GB/s, a dense 27B reads every weight per token, so
Q4 at 17.6 GB caps out near 18 tok/s and 30 tok/s would need 429 GB/s, which it called "physically impossible".

The arithmetic was right, but both premises are now false:

1. **"320 GB/s."** True only under layer split, where `-ts` gives each GPU a slice of layers and
   they run **sequentially**. `-sm tensor` splits every tensor, so both cards stream weights
   **concurrently**, about 640 GB/s in total. The symmetric VRAM is that change made visible.
2. **"every weight read yields one token."** MTP verifies a drafted block per pass, with a **mean of 2.89
   accepted tokens** per cycle here, at 0.96 acceptance.

At **26.83 tok/s** measured, 30 tok/s is no longer out of reach on this hardware.

The published guidance says to stick with layer split on this topology, and it was right about the
cost. Topology is **PHB** (PCIe host bridge, no NVLink), the setup where the docs say "stick with layer". It gave
+25% anyway, but *not* because the interconnect is free. The per-token arithmetic:

| | ms/token |
|---|---|
| A (layer split) | 80.97 |
| B if tensor split perfectly halved weight-read time | 40.49 (≈24.7 tok/s) |
| B actual | 64.56 |
| unaccounted overhead | 24.07 ms/token, 37% of B's budget |

So all-reduce over PCIe costs roughly **24 ms per token**, not a fraction of a millisecond. That is
also exactly why this run shows **+25% where Ampere-class boxes report +47–68%**: the interconnect
*is* the limiter here, as the guidance warns. The lever is still a net gain, and it
compounds with MTP. This does not show that the interconnect is free on PCIe; on another machine
the overhead could cancel the gain, so measure there.

---

## Caveats

- **`-c 8192` is provisional and is not part of the recommendation yet.** Every cell ran at
  8192 because `-sm tensor` forbids quantized KV and f16 KV needs room. The A/B/C/D comparison is
  internally fair, but **production runs 32768 and cell D was never tested there**, and the token
  figure comes from the fixture: the demo repo's 40-line `test.js` plus a 4-line node log.
  Measured across all 60 calls: prompt **740–835** tokens, generation **638–755**, so the
  worst case was **1,590 of 8,192 (19% used, 5.2× headroom)**. That is enough for this fixture, but
  production prompt size has no such bound.
  Production's `Build AI Prompt` concatenates the real changed files and the real Actions log from
  whatever push failed, which can be far larger. Cutting headroom 4× on a fixture measurement is
  not justified.

  Overflow fails safely. The server carries an explicit
  `exceed_context_size_error` type wired to `send_error`, with
  `"input (%d tokens) is larger than the max context size (%d tokens). skipping"` and
  `"request (%d tokens) exceeds the available context size (%d tokens), try increasing it"`. An
  over-length prompt is **rejected with a non-200**, not silently truncated, so the failure reaches n8n's guards rather than returning a "fixed" file with code missing.

  Ship the largest context that fits. D peaked at 9,051 MiB of
  14,911, leaving **5,860 MiB spare per card (11,720 total)**. A/B differ by only ~310 MiB at ctx 8192,
  which loosely suggests f16 KV is a few hundred MiB there and 32768 would add only ~2 GB,
  inside the headroom. That estimate is **confounded** (A and B differ in split mode too), so it
  needs a measurement, not a guess. Fold a ctx check into the `--spec-draft-n-max` follow-up run.
- **VRAM headroom is comfortable:** D peaked at **9,051 MiB of 14,911** per card (61%). MTP's
  overhead landed well inside the 500–1500 MiB/GPU reported.
- **`--spec-draft-n-max 2` is probably leaving speed on the table.** Mean accepted length is 2.89
  at 0.96 acceptance, so a larger window should extend the
  win. Untested; 3 and 4 are the obvious next values.
- **Acceptance is workload-specific.** 0.96 here because the task is code, which the MTP head
  predicts well. Prose is reported far lower (0.32–1.0 range). Do not quote 0.96 for other tasks.
- `llama_params_fit is not implemented for SPLIT_MODE_TENSOR` appears as a warning: auto-fit is
  disabled under tensor mode, so VRAM must be managed by hand. Harmless here; it means an OOM
  would surface as a hard failure rather than an automatic downshift.
- **This run did not save the raw model output**, unlike `DIFF_FORMAT_EVAL.md`. Every timing
  number here re-derives from the saved per-call rows, but **`fix_correct` cannot be independently
  re-scored**; it rests on the in-kernel regexes alone. Re-scoring raw output is how the `--recount`
  corruption was caught last time. **The rig now saves `raw` (plus a `raw_truncated` flag, since
  the 4000-char cap is otherwise silent), so the next run will not have this gap.** Mitigating it here: correctness is not the variable under test (both levers are
  lossless by construction), all 60 calls finished with `finish_reason: "stop"` (no truncation),
  and no row scored `fix_correct` without also passing `format_ok` and `node --check`.

## What shipping looks like

At the time of this eval the serve command lived in **`deploy4_src.py:132-135`**, a working copy (the same
string was duplicated in `deploy_src.py`, `deploy2_src.py`, `deploy3_src.py`; `deploy4` was the live one).
The levers below have since shipped in `kaggle/endpoint/deploy_src.py` (`launch()`). It read:

```
-m MODEL --n-gpu-layers 999 --ctx-size 32768 --flash-attn on
--cache-type-k q8_0 --cache-type-v q8_0 -b 2048 -ub 512 -t 4 -tb 4
--host 0.0.0.0 --port PORT -np 1 --jinja --api-key KEY
```

Replace the two `--cache-type-*` flags and add the two levers:

```
-sm tensor --cache-type-k f16 --cache-type-v f16
--spec-type draft-mtp --spec-draft-n-max 2 --parallel 1
```

Everything else is unchanged; `--flash-attn on` is already present and tensor mode requires it.
**Leave `--ctx-size` at 32768 until the context question above is measured**; do not ship 8192 on
the strength of this run.

**Projected cycle impact:** model phase **56.7 s → 27.8 s**, taking the measured 104 s push→PR
cycle to **~75 s**. This is a projection from bench medians, not an end-to-end measurement.

**It compounds with the format change.** `DIFF_FORMAT_EVAL.md` showed SEARCH/REPLACE cuts
generated tokens **655 → 171** at 15/15 byte-exact. Speed and token count multiply: 171 tokens at
26.83 tok/s is **~6.4 s** of generation, versus 56.7 s today. Both together project the cycle
toward **~55 s**. Neither is implemented yet; this one is a flag change, the other is real work in
the `Extract AI Response` node.

## Reproducing

```
speed_levers_eval.py   # the experiment (runs inside the Kaggle kernel)
test_speedlevers.py    # 33/33 - control logic; engagement cases run against REAL logs
watch_speedlevers.py   # follows `kernels logs -f`; `kernels output` is empty while RUNNING
```

Those three were working scripts and are not in the repository. The data is: per-cell server logs in
`kaggle/logs/evals/qwen3-8-speed-levers-tensor-split-mtp/2026-09-15/`, results in
`eval/runs/kout_speed/results.json`.
