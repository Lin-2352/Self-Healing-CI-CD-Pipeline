# Qwen3.8-27B hardware and deployment facts (with corrections to the earlier analysis)

Source: RepoChad, "Run Qwen3.8-27B on ANY GPU (8GB to 32GB): Here's How", 31 Aug 2026,
https://youtu.be/0xUxO_9zqTU (12:25, 105k views). Numbers are that reviewer's measurements.
They are internally consistent but come from one source, not independently replicated.

## Architecture (why the earlier VRAM estimate was wrong)

- 64 layers, hidden dim 5120, 248k vocab, **3:1 hybrid**: three gated-DeltaNet linear-attention
  blocks per one full-attention block, repeated 16×.
- **Only 16 layers keep a context-growing KV cache.** The other 48 DeltaNet layers hold a
  **fixed ~250 MB recurrent state**, independent of context length.
- KV cost across those 16 layers: **64 KB/token @ fp16, 32 KB @ Q8, ~16 KB @ Q4**.
  → 64k context ≈ **1 GB** at 4-bit KV. Full 256k context ≈ **4 GB**.

## Quant sizes (GGUF)

| Quant | Size |
|---|---|
| Q8 | **26 GB** |
| Q6_K / Q5_K_M | ~21 GB |
| ~3.7 bpw tuned | 12.2 GB |
| Q3 | ~12 GB |
| **GSQ-RCO IQ3_XXS** (ISTA-DASLab) | 10.1 GB |
| **GSQ-RCO IQ2_S** | 9.3 GB |
| **GSQ-RCO IQ2_XS** | 8.4 GB |

ISTA-DASLab **GSQ-RCO** low-bit quants retain strong GPQA-Diamond / LiveCodeBench vs older
generic 2-bit. Repo: `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`.

## Measured speeds

| Hardware | Config | Speed |
|---|---|---|
| RTX 3070 8 GB, DDR4, i5-11400F | Q4_K_M, heavy CPU offload, **ik_llama.cpp**, Q8 KV, MTP | **7.3 tok/s** @16k |
| same | same but **mainline llama.cpp** | **2.8 tok/s** (ik_llama ≈ **2.6×**) |
| RX 5700 XT 8 GB | Q3_XS 7.7 GB fully resident, Vulkan | 24.5 tok/s |
| RTX 3060 12 GB | Q4_K_S + **selective FFN tensors → CPU**, 4-bit KV, 96k ctx | 9.7 tok/s gen, 225 tok/s prompt |
| RTX 4070 Ti Super 16 GB | 3.7 bpw fully resident (12.2 GB wts + 2.2 GB KV @145k + 0.25 state + 1.0 buf = 15.7 GB) | 45 tok/s; 40 @21k; **75 with MTP** |
| RTX 3090 24 GB | Q4_K_M | 42 tok/s → **65.6 with native MTP n=2 (+56%)** |
| RTX 3090 | ExLlamaV3 4.0 bpw + Q8 cache | 43 / 37 @32k / 33 @65k |
| RTX 5090 32 GB | NVFP4 in vLLM + 4-bit TurboQuant KV + MTP | **~160 tok/s across full 262k**, KV only 5.5 GB |
| — | **D-Flash 2** learned block-diffusion drafter | 200–300 tok/s on structured coding (needs pinned known-good vLLM; stock builds corrupt output) |

**32 GB cards:** "Q5_K_M or Q6_K GGUFs at roughly 21 GB, leaving ample space for unquantized KV
caches and extended context windows."

## Key operational notes

- **llama.cpp CUDA issue #27623**: decode collapses past ~80k tokens (33 → 1.4 tok/s) while prompt
  ingestion stays fast. **Keep context 64k–80k** until fixed.
- **KV precision**: Q8 keys + Q4 values = stable compromise; pure Q4 K+V maximises context.
- **CPU offload**: offload **FFN tensors selectively**, not whole layers. FFN is compute-dense and
  tolerates CPU better than attention projections, and it reduces PCIe saturation.
- **RAM bandwidth decides viability**: dual-channel DDR4 craters generation speed; fast DDR5 stays usable.
- **MTP (multi-token prediction) is native** to Qwen3.8 and is the single biggest free speedup.
- Disable the vision projector to save memory when running text-only.

## Corrections to the earlier analysis

1. **"Q8 doesn't fit in ~30 GB, Q6_K is the ceiling."** Wrong twice: Q8 is 26 GB not 29, and KV is
   tiny (~1 GB @64k/4-bit) because 48 of 64 layers use a fixed state. Q8 ≈ 26 + 1 + 0.25 + 1 ≈ 28.5 GB,
   which fits a a 30 GB budget; Q6_K (~21 GB) is roomy.
2. **"RAM offload isn't worth it."** Too absolute, and based on whole-layer offload on mainline
   llama.cpp. Selective FFN-tensor offload + ik_llama.cpp is ~2.6× better than the earlier model assumed.
   The conclusion may still hold for Kaggle, whose 4 weak vCPUs and DDR4-class RAM are the
   "craters" case, but the earlier reasoning was wrong.
3. **Over-weighted the "Gated-DeltaNet is broken on Turing/sm75" risk.** That came from inferring
   from one GitHub thread. The real documented CUDA bug is #27623 (long-context decode collapse),
   not a Turing correctness failure. Vulkan is shown working on RDNA1 (RX 5700 XT).
4. **Ignored MTP entirely in the plan** despite it being native and worth +56% on a 3090.
5. **Dismissed options that deserved a test**: GSQ-RCO quants (the repo was found and skipped),
   ik_llama.cpp, and ExLlamaV3 exl3 (43 tok/s at 4.0 bpw).
