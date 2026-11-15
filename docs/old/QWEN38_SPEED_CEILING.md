# Why 30 tok/s is not reachable for Qwen3.8-27B on Kaggle 2xT4

> Superseded in part: these sweeps split the model by layer and ran without speculative decoding. Splitting every tensor across both GPUs (`-sm tensor`) plus the model's own MTP head later measured about 27 tok/s; see `docs/results/SPEED_LEVERS_EVAL.md`.

Three measured sweeps (24 configurations total). Raw data: `qwen38_sweep_results.json`,
`qwen38_speed_results.json`, `qwen38_specdecode_results.json` (from the early sweeps; not part of
this repository).

## Every configuration lands in 7.4-9.1 tok/s

| sweep | config | GB | gpus | notes | gen tok/s |
|---|---|---|---|---|---|
| 2 | unsloth UD-IQ4_XS | 14.3 | 1 | fastest overall | **9.1** |
| 3 | unsloth UD-IQ4_XS | 14.3 | 1 | re-measured, temp 0, 3 reps | 8.9 |
| 2 | GSQ IQ2_S | 9.3 | 2 | smallest quant, split | 8.9 |
| 1 | unsloth UD-Q4_K_XL | 17.6 | 2 | sweep-1 quality winner | 8.8 |
| 3 | UD-IQ4_XS + n-gram 8 | 14.3 | 2 | speculation | 8.5 |
| 2 | Jackrong MTP Q3_K_M | 13.5 | 1 | | 8.4 |
| 3 | UD-IQ4_XS + n-gram 4/16 | 14.3 | 1 | speculation | 8.2 |
| 2 | GSQ IQ2_S | 9.3 | 1 | single card | 8.1 |
| 2 | HauhauCS Aggressive IQ3_XS | 12.2 | 1 | uncensored | 7.9 |
| 3 | GSQ IQ2_S + n-gram 8 | 9.3 | 1 | speculation | 7.8 |
| 2 | **DavidAU TURBO-Fable NEO-CODER MTP** | 14.5 | 1 | | **7.4** |
| 1 | barozp Opus-Distill Q8_0 | 29.0 | 2 | largest quant | 5.9 |

A **3x range in model size** (9.3 -> 29 GB) produces only a **1.5x range in speed**. That is the
signature of a fixed bottleneck, not a weight-size problem.

## The bottleneck is memory bandwidth, and 30 t/s is over the physical limit

A Tesla T4 has **320 GB/s**. A *dense* 27B model reads every weight for every token:

| quant | GB | GB/s needed @ 30 tok/s | vs T4 peak | verdict |
|---|---|---|---|---|
| UD-Q4_K_XL | 17.6 | 528 | 165% | impossible |
| UD-IQ4_XS | 14.3 | 429 | 134% | impossible |
| HauhauCS IQ3_XS | 12.2 | 366 | 114% | impossible |
| GSQ IQ2_S | 9.3 | 279 | 87% | unreachable in practice |

Measured efficiency is 24-41% of peak, which is normal for llama.cpp on Turing with a hybrid
Gated-DeltaNet architecture. Even at 100% efficiency, only IQ2_S is theoretically in range.

## Two hypotheses tested and rejected

**1. "The PCIe tensor-split is the bottleneck; use one card."** False.
Same file, `GSQ IQ2_S`: split across 2 cards **8.9** vs single card **8.1**. The split was
*faster*. llama.cpp's `-ts` splits **layers**, so the GPUs run sequentially - you never
aggregate bandwidth, but you also do not pay a large penalty.

**2. "Speculative decoding will break the bandwidth bound."** It did not.
N-gram / prompt-lookup speculation (`--spec-ngram-mod-n-max`) at n=4, 8 and 16 all came in
**at or below** the non-speculative baseline (8.1-8.5 vs 8.9). The task looked ideal for it -
the answer is largely a verbatim copy of the input file - but acceptance evidently did not
offset the drafting overhead on this hardware.

MTP draft-model speculation could not be measured: loading a 14.3 GB model plus a 2.8 GB draft
plus the DeltaNet recurrent-state cache **OOMs a single 14.9 GB T4**:

```
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 2543.62 MiB on device 0: out of memory
llama_init_from_model: failed to initialize the context: failed to allocate buffer for rs cache
```

It remains untested on 2 cards. But the published MTP figure is **+56%** (RTX 3090: 42 -> 65.6
tok/s), which from a 8.9 baseline gives ~14 tok/s - still less than half the 30 target.

Two further dead ends found along the way: sweep 2's MTP runs died on a **flag rename**
(`--draft-max` -> `--spec-draft-n-max`, since fixed), and the HauhauCS `FastMTP-32K` draft is
**vocab-incompatible** with its own parent model (`output.weight` 5120x32768 vs 5120x248320),
so it cannot be used as a draft at all.

## What would actually reach 30 tok/s

1. **A newer GPU.** RepoChad measured **42 tok/s** for this exact model at Q4_K_M on a single
   RTX 3090, and **65.6** with MTP. An Ampere+ card with ~1 TB/s solves it outright.
2. **A sparse MoE**, where generation cost scales with *active* parameters. Qwen3.8-Flash-Next
   is 131B-A6B - ideal in principle - but its smallest GGUF is **72.5 GB**, well past
   30 GB VRAM + 31 GB RAM. Ruled out.
3. **A smaller dense model.** ~8-12B at Q4 would clear 30 tok/s on a T4 comfortably, at a real
   cost in reasoning quality.

## Practical recommendation for 2xT4

Accept **~9 tok/s** and pick on quality. Sweep 1's winner, `unsloth Qwen3.8-27B-UD-Q4_K_XL`
(17.6 GB), scored 15/15 on the CI-failure eval at 8.8 tok/s. For the self-healing pipeline this
is fine: a fix takes a couple of minutes, which is irrelevant for a 3 a.m. auto-heal. It is not
suitable for interactive chat.
