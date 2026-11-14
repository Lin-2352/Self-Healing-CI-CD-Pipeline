# Two findings: model self-identification, and architecture-aware quantization

## 1. "Which model are you?" is confabulation, not a misconfiguration

The deployed endpoint reported itself as *"Qwen3-235B-A22B-Instruct-2507"*. It was not.
Ground truth from the running server:

```
served file : Qwen3.8-27B-TurboFCFusion-735-882-Here-Uncen-NEO-CODER-MAX-MTP-Q6_K.gguf
model_path  : /kaggle/tmp/models/Qwen3.8-27B-TurboFCFusion-...-Q6_K.gguf
n_ctx       : 98304
```

Probing the same question six times:

| setting | answers |
|---|---|
| temperature 0, thinking off | "Qwen" / "Qwen" / "Qwen" (deterministic) |
| temperature 1.0 (llama-server default) | "Qwen3, a deep reasoning LLM by Alibaba" / "Qwen (Tongyi Qianwen)... Tongyi Lab" / "Qwen, developed by Alibaba Group's Tongyi Lab" |

Three different answers, none matching the observed "235B-A22B" string. Conclusions:

- **Models cannot introspect their own weights.** Self-identity is recalled from training data,
  so it is the least reliable question you can ask one.
- **This model is a Cold-Fusion merge of several Qwen derivatives.** If any parent was trained
  on Qwen3-235B-A22B outputs (very common for distills), that identity string is baked in.
- **Abliteration makes it worse** - removing refusal directions perturbs exactly the
  "I am X, I cannot Y" self-description text.
- The high default **temperature 1.0** is what let it wander to a specific wrong version.

**Verify a deployment by `/v1/models` and `/props` (`model_path`), never by asking the model.**

## 2. Architecture-aware quantization: a gap in the earlier benchmarks

Source: Kai, "I Tested Every Qwen3.8-27B Quant" (youtu.be/hg26j0erUSo, 1 Sept 2026) and the
Ridge model card it cites. The specifics below are taken from the primary sources.

**Ridge - `empero-ai/Qwen3.8-27B-Ridge-GGUF`** (455k downloads, 338 likes), 12.6 GB / 3.69 bpw:

> "a probed mix of types written for this architecture: 64 layers = 16 x (3 x GatedDeltaNet ->
> FFN + 1 x GatedAttn -> FFN). **The Gated-DeltaNet state path is Q8_0. Mixers are Q4_K, not IQ2.**"

Claimed **+9.3% perplexity vs BF16** (7.82 vs 7.15).

Every quant benchmarked so far (Unsloth UD, ISTA-DASLab GSQ-RCO, barozp
Opus-Distill, DavidAU TURBO) quantizes **uniformly**. Ridge deliberately protects the
DeltaNet *state* tensors, which is precisely what generic quantizers damage on this hybrid
architecture. None of the sweeps tested an architecture-aware quant.

The video's other claims, consistent with the sweeps measured here:

- **"The biggest quant that fits is not the best choice."** Sweep 1: Q8_0 (29 GB) was the
  *slowest* config at 5.9 tok/s and scored no better than 17 GB quants.
- **"The KV cache, not the weights, is what blows up your card."** Confirmed exactly by the
  context-ladder failures on a 24 GB Q6_K across 30.7 GB of VRAM:

  | ctx | failure |
  |---|---|
  | 262144 | OOM allocating **4352 MiB** KV on device 1 |
  | 196608 | OOM allocating **3264 MiB** KV on device 1 |
  | 131072 | failed to allocate compute buffers (755 MiB) |
  | 98304 | **booted** - 27344 / 30720 MiB, 8.7 tok/s |

- **Q4 is the practical floor** for this model; below that, quality degrades sharply.
- Speculative decoding via the built-in **MTP draft head** (untested here - it OOM'd a single
  T4 when paired with a 14.3 GB model, and is still unmeasured on two cards).

## What to test next

`Ridge 3.69bpw` at **12.6 GB fits a single T4**, freeing the entire second card for context.
That is plausibly better quality *and* far more context than the 24 GB Q6_K deployed here.
It will not be faster - Qwen3.8-27B is dense, so ~7-9 tok/s is bandwidth-bound regardless -
but it directly tests the "architecture-aware beats bigger" thesis, which the earlier sweeps did not.
