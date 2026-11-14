# Kaggle batch-run environment — MEASURED (2026-09-07)

From the env probe in kernel version 2 of `lacrimatct/qwen3-8-27b-full-quant-sweep-2xt4`.
These are real numbers, replacing earlier estimates.

| Item | Measured |
|---|---|
| GPU (when `enable_gpu:true` only) | **Tesla P100-PCIE-16GB**, compute cap **6.0**, 16384 MiB |
| System RAM | **31 GB total, ~30 GB available** (earlier estimate said ~29) |
| CPU | **4 cores**, Intel Xeon @ 2.00 GHz |
| CPU flags | `avx2 avx512bw avx512cd avx512dq avx512f avx512vl` — **AVX-512 IS present**; no AMX |
| `/kaggle/working` | 20 GB |
| `/kaggle/tmp` and `/tmp` scratch | **1103 GB free** - disk is not a constraint |
| Internet | reachable (HTTP 200 to huggingface.co) |
| llama.cpp HEAD at build time | `465e49b9` (2026-09-07) |

## Corrections to earlier research

1. **AVX-512 is available.** An earlier research note assumed "no AVX-512, treat as absent",
   and concluded ik_llama.cpp's gains would be modest. With AVX-512 present, ik_llama.cpp's
   ~2.6x advantage on hybrid CPU-offload (7.3 vs 2.8 tok/s in the RepoChad video) should
   largely apply here. AMX is still absent, so KTransformers remains out.
2. **RAM is 31 GB, not ~29.**
3. **`enable_gpu: true` alone gives a P100, not T4x2.** Setting the accelerator in the
   notebook UI does **not** survive a CLI push either - the push overwrites it.
   **The fix is `machine_shape` in `kernel-metadata.json`.** Valid values, from the
   kagglesdk docstring (`kagglesdk/kernels/types/kernels_api_service.py`):

   * `NvidiaTeslaT4`   <- this is the "GPU T4 x2" option
   * `NvidiaTeslaP100`
   * `Tpu1VmV38`

   Anything else (e.g. `"T4x2"`, or a junk value) is silently accepted and normalises to
   `"Gpu"`, which means P100. `--accelerator` on the CLI is likewise accepted but ignored.
   Verify it stuck by pulling metadata back: `machine_shape` should read `NvidiaTeslaT4`,
   not `Gpu`. Quota dialog confirmed: **30 h/week, 29 h remaining**.

## Build gotcha

`cmake -DGGML_CUDA=ON` fails on the Kaggle image with:

```
CMake Error at ggml/src/ggml-cuda/CMakeLists.txt:180 (target_link_libraries):
  Target "ggml-cuda" links to: CUDA::cuda_driver  but the target was not found.
```

The image ships the CUDA toolkit without a discoverable `libcuda.so`. Fix used:
symlink the stub and add it to the library path -

```
ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/libcuda.so
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 \
      -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_CUDA_FA_ALL_QUANTS=OFF \
      -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs
```

(`GGML_CUDA_FA_ALL_QUANTS=OFF` also cuts build time materially on 4 slow cores.)

## Running headless with the Kaggle CLI

The account is **already authenticated** for the Kaggle API (`~/.kaggle/access_token`),
so the whole loop runs headless, with no browser and no dependence on the notebook UI:

```
python -m kaggle kernels pull  <owner/slug> -p DIR -m     # get source + metadata
python -m kaggle kernels push  -p DIR                     # push + start a batch run
python -m kaggle kernels status <owner/slug>              # RUNNING / COMPLETE / ERROR
python -m kaggle kernels output <owner/slug> -p DIR       # results.json + full run log
```

This solves the readback problem that blocked the earlier interactive run: batch versions
expose `/kaggle/working` **and** the complete stdout log via `kernels output`. Interactive
draft sessions do **not** (`ListKernelSessionOutput` returns 404 with 0 saved versions),
which is why the first run's results were unrecoverable.

Caveat: batch runs start with an empty `/kaggle/working`, so the sweep script's resume
logic does not carry across versions — each run is from scratch.

## HF repo/quant inventory (verified 2026-09-07)

| repo | gguf files | notes |
|---|---|---|
| `barozp/Qwen3.8-27B-Opus-Distill-GGUF` | 10 (~9k dl) | BF16, IQ1_M, IQ2_XXS, IQ3_XXS, Q3_K_M, Q4_K_M, Q5_K_M, Q6_K, Q8_0, + mmproj |
| `unsloth/Qwen3.8-27B-GGUF` | 30 (~10.3M dl) | UD-* dynamic quants Q2..Q8, BF16 sharded, **plus `MTP/mtp-Qwen3.8-27B-Q4_0.gguf`** (separate MTP draft model for speculative decoding) |
| `ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF` | 10 (~348k dl) | IQ2_XS, IQ2_S, IQ3_XXS, IQ3_S — **each in plain and `-mtp` form**, + imatrix + mmproj |
| `Jackrong/Qwopus3.6-35B-A3B-v1-GGUF` | 5 (~318k dl) | Q4_K_M, Q5_K_M, Q6_K, Q8_0, + mmproj |

Two selection subtleties in the sweep's regex matching:

- On the GSQ repo, `sorted()` picks the **`-mtp` variant** first (`-` sorts before `.`), so
  `gsq_iq3_xxs` / `gsq_iq2_s` actually test the MTP-enabled files. That is arguably the more
  interesting build (MTP is the largest free speedup, ~+56% on a 3090) — but if llama.cpp
  cannot load the extra MTP tensors those two configs will error. A follow-up sweep should
  A/B plain vs `-mtp` explicitly.
- On the unsloth repo, `q6_k(_[a-z]+)?\.gguf$` correctly resolves to plain `UD-Q6_K.gguf`
  rather than `UD-Q6_K_L/M` (`.` sorts before `_`).

Also available for a proper speculative-decoding test: `unsloth` ships a dedicated MTP draft
model, usable via `llama-server --model-draft`.
