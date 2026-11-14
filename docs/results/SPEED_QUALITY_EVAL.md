# Bonsai ternary vs Ridge vs Q4 on 2×T4: quality first, speed second

**Verdict: keep `UD-Q4_K_XL`.** Bonsai showed **no detectable quality difference** from Q4 at
**a third of the size**, but it cannot use tensor parallelism or MTP on this hardware, so it
gives up 25–39% of throughput.

**"No detectable difference" is deliberately weaker than "equal."** Q4, PQ2_0 and PTQ1_0 are
*identical on all six hard cases*: three unanimous 3/3, three unanimous 0/3. **Zero cases
separate Bonsai from Q4.** The set demonstrated discriminating power exactly once, and that
instance separated *Ridge*, not Bonsai. So this eval can only rule out a **large** quality gap;
it is not evidence of parity.

Ridge, the architecture-aware quant expected to win on quality-per-byte, is the only packing
that measurably **loses** quality, and it is the one result this case set did detect.

- **Run:** Kaggle `lacrimatct/qwen38-quality-speed-bonsai-ridge`, `COMPLETE`
- **Design:** 4 packings × 11 cases × 3 reps = **132 calls**, zero errors, zero truncations
- **All four are Qwen3.8-27B.** Bonsai and Ridge are *quantizations of it*, not other models
  (Bonsai's HF metadata is literally `base_model: Qwen/Qwen3.8-27B`).
- **One runtime for all four:** the PrismML fork `prism-b10709`, which turned out to carry
  `sm_75`, both ternary types, **and** both of our levers, so there is no binary confound.

---

## Results

| packing | GB | rung reached | control | **hard** | med tok/s | VRAM peak/GPU |
|---|---|---|---|---|---|---|
| `UD-Q4_K_XL` | 17.56 | `tensor+mtp` | 15/15 | **9/18** | 28.02 | 9,083 |
| `Ridge-3.7bpw` | 12.60 | `tensor+mtp` | 15/15 | **5/18** | 28.52 | 6,561 |
| `Bonsai PQ2_0` | 7.21 | **`layer`** | 15/15 | **9/18** | 21.02 * | 3,941 |
| `Bonsai PTQ1_0` | 5.95 | **`layer`** | 15/15 | **9/18** | 17.09 * | 3,357 |

Score is **byte-exact against the known-good file**. The normalised variant (forgiving line
endings and trailing whitespace) returned *identical* numbers for every packing (verified by
recomputation from the stored raw output), so nothing here turns on cosmetic formatting. It does
turn on *semantic* alternatives, which is a real limitation: see the scorer false negative below.

**\* Bonsai's speed is not like-for-like.** It ran on a different rung (layer split with
`q8_0` KV) against Q4/Ridge on tensor split with `f16` KV and MTP. Those rows answer "how fast is
each packing *at its best*", **not** "how do these configs compare". Do not read 28.02 vs 17.09 as
a controlled measurement.

---

## Why Bonsai cannot use either lever

Both failures are hard and specific, captured from the server logs rather than inferred:

**Tensor parallelism: the server aborts instead of refusing.**
```
GGML_ASSERT(split_state.ne[j] % div == 0) failed   [ggml-backend-meta.cpp:1086]
```
The tensor-parallel path needs tensor dimensions divisible across devices; Bonsai's ternary block
packing is not, and it `ggml_abort`s. This matters beyond our box: **multi-GPU tensor parallelism
is closed to Bonsai**, so its ceiling on any multi-card machine is single-GPU bandwidth.

**MTP: the tensors are missing.**
```
W llama_init_from_model: context type MTP requested but model doesn't contain MTP layers
E common_speculative_init_result: failed to create MTP context
```
The ternary conversion dropped the `nextn` tensors that ship inside unsloth's GGUF. Speculative
decoding is unavailable by construction.

The ladder made this visible without guessing: each packing tried `tensor+mtp` → `tensor` →
`layer+mtp` → `layer`, and the table records where it landed. Bonsai fell all the way. The VRAM
signature corroborates it: Q4/Ridge are perfectly symmetric (`9083/9083`, `6561/6561`, the
fingerprint of tensor split), Bonsai is asymmetric (`3781/3941`), the fingerprint of layer split.

---

## The quality metric had to be rebuilt, and it half-worked

The old metric was saturated: every quant down to IQ2_S at 9.6 GB scored 3/3 on 4 of 5 original
cases. **That prediction held exactly: all four packings scored 15/15 on the control set.** It
ranks nothing. Six harder cases were added, built from the same real file, where *the CI log does
not point at the bug*.

| hard case | Q4 | Ridge | PQ2_0 | PTQ1_0 | verdict |
|---|---|---|---|---|---|
| `https_protocol_mismatch` | 3/3 | 3/3 | 3/3 | 3/3 | saturated easy |
| `callback_arity` | 0/3 | 0/3 | 0/3 | 0/3 | saturated hard |
| `uninterpolated_url` | 3/3 | 2/3 | 3/3 | 3/3 | noise (1 rep) |
| `inverted_status_check` | 3/3 `111` | **0/3 `000`** | 3/3 `111` | 3/3 `111` | **decisive** |
| `route_wrong_status` | 0/3 | 0/3 | 0/3 | 0/3 | **scorer false negative** |
| `multi_hunk` | 0/3 | 0/3 | 0/3 | 0/3 | saturated hard |

**Only 2 of 6 hard cases discriminate, and only 1 does so decisively.** One is 3/3 for
everything, and three are 0/3 for every packing. The set escaped saturation, but barely: a future
run should replace the dead cases with mid-difficulty ones.

**One of those three 0/3 rows is a scorer error.** Re-running
every stored output under `node` shows `route_wrong_status` is **12/12 functionally correct** and
**0/12 byte-exact**, across all four packings. The models fixed it by writing
`res.status(200).send("ok")`, which is behaviourally identical to golden's `res.send("ok")` and
passes the smoke test (`exit=0, smoke test passed`), but is not textually identical. **Byte-exact
penalises valid alternative fixes.**

`callback_arity` and `multi_hunk` are genuine failures: 0/12 byte-exact *and* 0/12 functional.

**This does not change the ranking**, because the penalty falls equally on all four packings
(each would gain exactly +3). It does mean the absolute `hard_exact` figures understate every
packing by the same amount, and that "too hard to rank anything" was the wrong diagnosis for one
of the three. A future run should score **functional pass (run it) first, byte-exact second**;
byte-exact works as a tiebreaker but cannot judge correctness.

**Ridge's entire 4-point deficit is 1 point of noise plus one case it fails `000` while all three
others pass `111`.** That is a real, reproducible capability difference, consistent across
every rep, but the accurate statement is "Ridge deterministically fails one
specific hard case", **not** "Ridge is broadly worse". Its control score and syntax rate are
perfect and identical to the others.

---

## Correction: MTP *was* running, and the engagement check was wrong again

The rig reported `engaged={'tensor': True, 'mtp': False}` for Q4 and Ridge. That was wrong. Proof
from the same logs:

```
draft acceptance = 0.95455 (420 accepted / 440 generated), mean len = 2.91
```

`accept_rate` is **non-null 33/33** for both, median **0.965** (Q4) and **0.947** (Ridge), and
**0/33** for both Bonsai packings, matching their `layer` rung. MTP ran exactly where expected.

The needle searched the log for `draft-mtp`; b10709 prints `common_speculative_init_result` and
`draft acceptance` instead. **This is the second time a log-wording change produced a false
negative in this project**; the first was `"tensor parallelism"` vs `SPLIT_MODE_TENSOR` in an
earlier run. The rule since then: **judge a feature by its measurable effect
(`accept_rate`), not by a log string.** The analyzer was corrected to do so.

**Consequence for the numbers:** Q4's 28.02 tok/s *is* a tensor+MTP figure, directly comparable to
the 26.83 recorded on stock b9690, so the newer fork build is ~4% faster on the same
configuration. Had the needle been believed, that row would have been mislabelled tensor-only.

---

## Why Q4 stays

| candidate | quality | speed | conclusion |
|---|---|---|---|
| `Ridge` | **worse** (5/18) | +1.8% | quality regression buys almost no speed |
| `PQ2_0` | no difference detected (9/18) | −25% | same answers on every case, meaningfully slower |
| `PTQ1_0` | no difference detected (9/18) | −39% | same answers on every case, much slower |

Ranking quality first, nothing beats Q4: one candidate is measurably worse, and two are slower
with no measurable quality advantage. Note the speed conclusion does not depend on the quality
caveat: Bonsai loses on throughput whether or not its quality truly equals Q4's.
**Smaller is not a reason to switch here.** The decision rule said so before the data, and the
VRAM numbers confirm it: Q4 peaks at 9,083 MiB of 14,911 per card, so we were never short of room.
Bonsai's 5.95 GB footprint is an impressive engineering result that solves a constraint this
hardware does not have.

**Where Bonsai *would* win:** a single GPU with ~6–8 GB, where Q4 simply does not fit. There it
delivers the same answers this eval measured. On 2×T4 its inability to tensor-split is decisive.

## What this settles

- **The video's claim is true and reproducible.** 27B in 5.95 GB, running on a T4, producing
  genuine output: a verbatim read of a PTQ1_0 response shows a well-formed JSON envelope, a
  1862-char byte-exact file, and a PR title that correctly names the bug
  (*"Fix ReferenceError: app is not defined"*). It is not marketing, and it is not the silent
  gibberish that stock llama.cpp would have produced from the same family of file.
  **Indistinguishable-from-Q4 on 33 scored calls is the upper bound of that statement**; see
  the caveat above on what this case set could and could not have detected.
- **Colibri is closed.** Its repo ships 9 engines, all MoE; `qwen38` targets Qwen3.8-**Flash-Next**
  (sparse), and an upstream issue names "qwen3-dense" as a gap. Flash-Next is separately
  impossible here: the smallest of 11 GGUFs is **72.55 GB** against 61 GB of VRAM+RAM.
- **Ridge is tested and can be retired** as a candidate: the DeltaNet-aware quant did not beat
  generic Q4 on quality, which was the entire reason to try it.

## Reproducing

```
eval/build_hard_cases.py     # 6 harder cases, real node logs, from the same golden file
eval/quality_speed_eval.py   # the experiment (runs inside the Kaggle kernel)
eval/test_qualityspeed.py    # local validation before any GPU
eval/analyze_qs.py           # tables; judges MTP by accept_rate, refuses cross-rung speed
eval/runs/kout_qs/results.json
kaggle/logs/evals/qwen38-quality-speed-bonsai-ridge/2026-09-20/   # kernel log + per-rung server logs
```
