# Qwen3.8-27B quant sweep results (Kaggle 2×T4)

Kernel `lacrimatct/qwen3-8-27b-full-quant-sweep-2xt4` v5, batch run, **252 min**, status COMPLETE.
All 9 configs completed. Raw data: `results.json` from `kaggle kernels output`.

## Environment (verified in-run)

**2× Tesla T4, 15360 MiB each = 30720 MiB total**, compute cap 7.5 · 31 GB RAM · 4× Xeon @2.0 GHz ·
AVX-512 present, no AMX · scratch `/kaggle/tmp` 1103 GB · llama.cpp `465e49b9` (2026-09-07).

## Results: ranked by measured generation speed

Speeds are **server-side `timings` from `llama-server`** on the real task prompt (the authoritative
number). Quality is the 5-case CI-failure eval, 3 pts each: valid JSON / `node --check` / correct fix.

| # | config | repo | GB | quality | gen tok/s | prompt tok/s |
|---|---|---|---|---|---|---|
| 1 | `base_q4_k_xl` (UD-Q4_K_XL) | unsloth | 17.6 | 15/15 | **8.8** | 62.2 |
| 2 | `gsq_iq2_s` (IQ2_S-mtp) | ISTA-DASLab | 9.6 | 14/15 | **8.4** | 57.5 |
| 3 | `base_q6_k` (UD-Q6_K) | unsloth | 22.0 | 15/15 | **7.7** | 64.8 |
| 4 | `gsq_iq3_xxs` (IQ3_XXS-mtp) | ISTA-DASLab | 10.4 | 15/15 | **7.3** | 59.8 |
| 5 | `opus_q4_k_m` | barozp | 16.8 | 14/15 | **7.2** | 61.1 |
| 6 | `opus_q5_k_m` | barozp | 19.5 | 14/15 | **7.0** | 61.0 |
| 7 | `opus_q6_k` | barozp | 22.4 | 15/15 | **6.0** | 58.9 |
| 8 | `opus_q8_0` | barozp | 29.0 | 14/15 | **5.9** | 57.6 |
| 9 | `opus_q4_k_m_ffn_cpu` | barozp | 16.8 | 14/15 | **1.1** | 22.8 |

## The most important caveat: the eval could not separate these models

**Four of the five cases scored 3/3 for every single config.** The only case anything ever missed
was `wrong_status_check`, and the miss was always `fix_correct=False` with valid JSON and valid
syntax. So "15/15 vs 14/15" is one binary case, too few to show a quality gap.

| config | missing_app_init | typo_method | missing_require | syntax_missing_brace | wrong_status_check |
|---|---|---|---|---|---|
| all 9 configs | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 or 2/3 |

Two conclusions follow:

1. **Quality is saturated on this task.** Even `IQ2_S` at **9.6 GB** produced valid JSON on 5/5 and
   a correct fix on 4/5. For "read a stack trace, repair one obvious bug", quantisation down to
   ~2-3 bits costs essentially nothing measurable.
2. **The eval is too easy to rank these models.** The only discriminating case is the one requiring
   semantics (log says `bad 200`, code checks `=== 201`) rather than pattern-matching an error
   string. Enabling thinking on the retry did **not** rescue it for any config. Ranking on quality
   here would be over-reading noise, so the table is ranked on speed instead.

## What the data does establish

- **Q8_0 (29.0 GB) loads and runs on 30.7 GB of split VRAM.** The earlier "Q8 cannot fit, Q6_K is the
  ceiling" claim is now disproven by measurement, not just by the video's arithmetic.
- **Bigger quant ≠ better here.** Q8_0 was the **slowest** (5.9 tok/s) and did not score above the
  small quants. There is no reason to pay 29 GB.
- **RAM offload is decisively not worth it on this box.** The same Q4_K_M model went from
  **7.2 → 1.1 tok/s** (≈6.5× slower) with FFN tensors on CPU, and prompt processing collapsed
  62 → 22.8 tok/s. 4 slow vCPUs are the bottleneck; AVX-512 does not save it.
- **Unsloth's Dynamic quants are meaningfully faster than the barozp merge at the same size**:
  `base_q6_k` 22.0 GB → 7.7 tok/s vs `opus_q6_k` 22.4 GB → 6.0 tok/s, a **28% gap** for the same
  footprint and the same 15/15 score.
- **Absolute speed is low: 5.9–8.8 tok/s.** Far below the 42 tok/s the RepoChad video measured on a
  single RTX 3090 at Q4_K_M. Cause: T4 bandwidth (~320 GB/s vs 936) plus tensor-split across PCIe
  with no NVLink. The spread across a 3× size range is only ~1.5×, so PCIe split overhead dominates
  over weight bandwidth.
- **Prompt processing is flat at ~58–65 tok/s** regardless of quant, consistent with the same
  interconnect bound.

## Method note: `llama-bench` failed where `llama-server` succeeded

`llama-bench` returned `failed to load model` for every config ≥16.8 GB while `llama-server` loaded
the identical files and ran them fine. It only succeeded on the two ~10 GB GSQ quants and the
CPU-offload config, which are the cases that fit on **one** 14.9 GB card. `llama-bench` is not
honouring the tensor-split the way the server does. All speeds above therefore come from the
server-side `timings`, which is what the plan designated authoritative anyway.

## Recommendation

**`unsloth/Qwen3.8-27B-GGUF` → `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.6 GB)**: fastest measured
(8.8 tok/s), perfect on the eval, comfortable headroom on 2×T4.

**If you want the second T4 free** (for a draft model, a second replica, or long context):
**`ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-IQ3_XXS` (10.4 GB)**: 15/15 and 7.3 tok/s, fits a single card.

Do **not** use Q8_0, and do **not** use CPU/RAM offload.

### Serving command for the winner

```
llama-server -m Qwen3.8-27B-UD-Q4_K_XL.gguf -ngl 99 -ts 0.5,0.5 -fa 1 \
  -c 32768 -ctk q8_0 -ctv q4_0 --host 0.0.0.0 --port 8000 --jinja --api-key <key>
```

## Caveats

- 5-case eval, single run, no repeats; quality differences of one case are noise.
- The GSQ entries are the **`-mtp`** files (MTP tensors present); they loaded fine, but no
  speculative decoding was enabled, so MTP's headline speedup is untested here.
- ~8 tok/s means a fix with a reasoning trace takes minutes, not seconds. Acceptable for a 3 a.m.
  auto-heal; not interactive.

## Worth testing next

1. **MTP / speculative decoding**: untested and it is the biggest known free speedup (+56% on a
   3090). `unsloth` ships `MTP/mtp-Qwen3.8-27B-Q4_0.gguf` as a draft model for `--model-draft`.
2. **Single-card vs split**: run `gsq_iq3_xxs` pinned to one T4 (`CUDA_VISIBLE_DEVICES=0`) to
   measure how much the PCIe tensor-split is actually costing.
3. **A harder eval**: multi-file bugs, ambiguous logs, cases needing repo context. Required before
   any quality claim can separate these quants.
4. **ik_llama.cpp**: AVX-512 is present, so its hybrid-offload advantage may partly rescue the
   RAM-offload path, though from 1.1 tok/s it has a long way to climb.
