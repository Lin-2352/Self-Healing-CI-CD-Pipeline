## The Model Endpoint on Kaggle

### Role of the service

The healer needs a language model that n8n and the backend can call over HTTPS. We could not pay for a hosted API, so the model runs on a free Kaggle notebook. A Python script starts a `llama-server` process from the llama.cpp project, loads Qwen3.8-27B into it, and puts a Cloudflare tunnel in front of port 8000. The result is an OpenAI-style chat API (`/v1/chat/completions`) at a temporary web address.

Everything that runs on Kaggle is one file, `kaggle/endpoint/deploy_src.py`. A small build script pastes the chosen model entry from `kaggle/endpoint/models.json` into it and writes the file that is uploaded (`kaggle/tools/build_endpoint.py`). Boot logs from each session are kept under `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/<run>/`. In this chapter "boot log v16" means the `boot.log` in the run folder whose name ends in `v16`.

This chapter was checked against the code and the saved logs.

### Hardware

The kernel is private, has the GPU and internet switched on, and sets `machine_shape` to `NvidiaTeslaT4` (source: `kaggle/endpoint/push/kernel-metadata.json`). That setting matters: with only `enable_gpu` Kaggle hands out a single P100 instead of two T4 cards, and the notebook's accelerator menu is overwritten by a command-line push (source: `docs/research/KAGGLE_ENV_MEASURED.md`). The same note measured 4 CPU cores and 31 GB of RAM on the machine.

The boot logs show two Tesla T4 cards with 15360 MiB each, 30720 MiB in total (source: boot log v16). The script adds up the memory that `nvidia-smi` reports. If the sum is below 24000 MiB it prints `ABORT: need 2xT4` and stops before it downloads 17 GB for nothing (source: `deploy_src.py`).

### The server binary

Compiling llama.cpp with CUDA on the four slow cores of a Kaggle machine took about 40 minutes (source: module docstring of `deploy_src.py`). The project therefore builds the server once, in a separate kernel called `linsxav/llama-server-builder`, and attaches that kernel as a source (`kernel_sources` in the metadata). At boot the script finds `llama-server-cuda.zip` under `/kaggle/input`, unpacks it into `/kaggle/working/bin`, sets `LD_LIBRARY_PATH` and asserts that `llama-server --help` runs. Unpacking took 3.5 to 7.0 seconds in the kernel logs we kept (source: `kernel.log` of v11 and v14). If the zip is missing, the script lists the input folder and stops. It has no fallback to compiling.

The build in production is a stock llama.cpp build, which `docs/results/GOLIVE_RESULTS.md` calls b9690. The quality study in Chapter 9 used a newer fork, b10709, which measured about 4 percent faster on the same settings (source: `docs/results/SPEED_QUALITY_EVAL.md`).

### Model file and quantization

The model is `Qwen3.8-27B-UD-Q4_K_XL.gguf` from the Hugging Face repository `unsloth/Qwen3.8-27B-GGUF`, 17.6 GB (source: `kaggle/endpoint/models.json`). GGUF is the single-file format llama.cpp reads: it holds the weights, the tokenizer and the chat template together. A name like Q4_K_XL says how the weights were compressed: roughly 4-bit blocks of the "K" kind, with the "XL" mix keeping some tensors at higher precision, and the "UD" prefix marks Unsloth's dynamic variant (this reading of the name is general knowledge about the format, not something the project measured). For scale, 17.6 GB over about 27 billion weights is around 5 bits per weight on average (our arithmetic from those two figures).

`fetch()` in the script lists the `.gguf` files in the repository, skips anything with `mmproj` in its name, and downloads the first file that matches the registry pattern. If that fails it tries `UD-Q4_K_M` and then `UD-Q6_K` from the same repository. Downloads took 65 s (v8), 363 s (v9), 92 s (v10), 84 s (v11), 91 s (v12), 89 s and 76 s (the two attempts logged under v14), and 82 s (v16) (source: boot logs of those runs). Only v9 was slow, and the logs do not say why.

The default stayed because of the four-model bake-off: it tied the others on first-try heals (14 of 24), healed 21 of 24 with thinking on against 15 to 17 for the others, and was the fastest (source: `docs/results/MODEL_BAKEOFF.md`; Chapter 9).

### Server flags

`launch()` kills any old server, waits 4 seconds and starts a new one in the background. The flags are these (source: `deploy_src.py`, `launch()`).

| Flag | Effect |
|---|---|
| `-m <file>` | The downloaded GGUF file. |
| `--n-gpu-layers 999` | Put every layer on the GPUs. |
| `--ctx-size <n>` | Context length, taken from the ladder below. |
| `--flash-attn on` | Flash attention. It was already on before the speed work, and tensor split needs it (source: `docs/results/SPEED_LEVERS_EVAL.md`). |
| `-sm tensor` | Split every tensor across both cards so both read weights at the same time. The default splits by layer, and the cards then work one after the other. |
| `--cache-type-k f16 --cache-type-v f16` | Unquantized attention cache. Tensor mode forbids a quantized cache, so these flags and `-sm tensor` change together (source: `docs/results/SPEED_LEVERS_EVAL.md`). |
| `--spec-type draft-mtp --spec-draft-n-max 2` | Speculative decoding with the model's own multi-token-prediction head: it drafts up to 2 tokens and the full model checks them in one pass. |
| `--parallel 1` and `-np 1` | One request slot. These are the same option written twice. Heal requests arrive one at a time. |
| `-b 2048 -ub 512` | Batch and micro-batch size for reading the prompt. |
| `-t 4 -tb 4` | Four CPU threads for generation and four for prompt batches. |
| `--reasoning-budget 1024` | Cap on thinking tokens when thinking is on. The code has no comment on it. |
| `--host 0.0.0.0 --port 8000` | Listen on port 8000 on all interfaces. |
| `--jinja` | Use the chat template stored in the model file. |
| `--api-key selfheal-e2e-key` | Require `Authorization: Bearer <key>`. |
| `--chat-template-kwargs '{"enable_thinking":false}'` | Default thinking to off. It is added only if the binary's help text lists this option. |

The speed work behind `-sm tensor` and MTP is in the last section of this chapter and in Chapter 9. Before it, the production flags used a quantized `q8_0` cache and layer split (source: `docs/results/SPEED_LEVERS_EVAL.md`, "What shipping looks like").

### Context ladder

The script tries context sizes of 131072, 98304, 65536, 32768, 16384 and 8192 tokens, largest first. Each try gets 300 seconds for `/health` to answer 200, and the wait ends early if the server process has died. A rung that boots must also answer three benchmark requests. The first rung that does is kept, and the script asserts if none does (source: `deploy_src.py`, `LADDER`).

The ladder used to start higher. In the first live round, sizes of 262144 and 196608 each hung for the full 15-minute health timeout, about 30 minutes of GPU time, before 131072 booted in under a minute. With tensor split, llama.cpp cannot fit the context to memory by itself, so a rung that is too large does not fail quickly (source: `docs/results/GOLIVE_RESULTS.md`, "Three defects found only by going live"; the server log of v11 has the warning `llama_params_fit is not implemented for SPLIT_MODE_TENSOR`). The model was trained for 262144 tokens, and the log of v11 says the 131072 setting leaves part of that unused.

Every boot log we kept shows the first rung, 131072, booting (source: boot logs v8 to v16). Memory in use afterwards was 26566 MiB of 30720 MiB in v8 to v12 and 26662 MiB in v15 and v16 (source: the same logs). The research notes explain why this fits: only 16 of the model's 64 layers keep a cache that grows with the context (source: `docs/research/QWEN38_HARDWARE_FACTS.md`, a secondary source, not measured by us).

### Thinking default

Qwen3.8's chat template makes the model think before it answers unless a request says otherwise. The v11 server log reads `chat template, thinking = 1` (source: `server.log` of v11). The n8n workflow always sends `enable_thinking` with each request: false on the first heal and true on a retry (source: `tools/nodes/build_ai_prompt.js`, line 155). The backend sends it only to turn thinking on, so before the change every backend call would have thought first. The classifier asks for at most 512 tokens, and thinking tokens count against that limit, so a long thought could leave no room for the JSON answer (source: `docs/results/BACKEND_ON_KAGGLE.md`).

The kernel therefore starts the server with thinking off, and a request that sets `enable_thinking: true` still turns it on. Thinking is expensive: in the shipped-quality study the median call took 36.8 s with it and 9.1 s without (source: `docs/results/SHIPPED_QUALITY_EVAL.md`). The policy that follows is thinking off for the first attempt and on for a retry, which the n8n chapter describes.

### Boot-time self-check

After a rung boots, the script does not trust its own flags. `bench()` sends three requests of up to 256 tokens at temperature 0.7 and reads the server's own `timings` field, not the log text. From that it works out three facts:

- **Tensor split engaged.** True when the two cards report memory use within 64 MiB of each other. Layer split leaves them uneven, tensor split makes them equal.
- **MTP engaged.** True when at least one request reported draft tokens.
- **Accept rate.** Accepted draft tokens divided by drafted tokens.

If either lever is missing the script prints a warning that the endpoint will be slower than measured, and serves anyway. This design came from two earlier mistakes in which a log-wording check reported a working lever as missing (source: `docs/results/SPEED_LEVERS_EVAL.md` and `docs/results/SPEED_QUALITY_EVAL.md`).

`thinking_check()` then asks "What is 17 * 23?" twice, once plain and once with thinking requested. A reply counts as thinking if it has `reasoning_content` or a `<think>` block. The expected result is false then true. Boot logs v14, v15 and v16 all show `thinking by default : False` and `thinking when asked : True` with the answer 391 both times (source: `stream.log` of v14 and v15, `boot.log` of v16). The older runs v8 to v12 predate this check.

The boot benchmark of each session that reached the end was this (source: the `SELECTED` lines of the boot logs).

| Session | Date | Average tok/s | Best tok/s | MTP accept rate |
|---|---|---|---|---|
| v8 | 2026-09-23 | 26.4 | 26.6 | 0.671 |
| v9 | 2026-09-23 | 26.6 | 27.5 | 0.633 |
| v10 | 2026-09-28 | 24.0 | 25.0 | 0.64 |
| v11 | 2026-09-28 | 23.9 | 24.4 | 0.613 |
| v12 | 2026-10-30 | 23.8 | 24.4 | 0.64 |
| v15 | 2026-11-15 | 26.8 | 27.4 | 0.664 |
| v16 | 2026-11-15 | 22.4 | 23.7 | 0.719 |

All seven had the tensor split engaged, with equal memory on both cards (source: the same logs). Version 14 and version 15 look like two sessions in the log folders, but the v14 kernel log shows a `TypeError` in `launch()` at the first rung, so v14 never served. Its `stream.log` continues with the output of v15, which was pushed about 3 minutes later (source: `kernel.log` of v14 and `history.jsonl`), and the two runs report the same tunnel address. This chapter counts them as one session.

The accept rates here are far below the 0.95 to 0.96 measured on real code fixes (source: `docs/results/SPEED_LEVERS_EVAL.md`). The boot benchmark asks for a general explanation at temperature 0.7, and that text is harder to guess than a code edit. Speed also varied between sessions from 22.4 to 26.8 tok/s with the same flags. The documents give no reason.

### Tunnel and changing address

When the benchmark is done, the script downloads `cloudflared` and starts `cloudflared tunnel --url http://localhost:8000`. It reads `tunnel.log` every 4 seconds, up to 45 times, until a `https://<words>.trycloudflare.com` address appears. This is a quick tunnel: it needs no Cloudflare account, and every start gets a new random name. The saved boot logs show seven different addresses for the seven sessions, for example `pilot-fixed-cooler-hello` in v8 and `hats-conducted-committees-hollywood` in v16.

A quick tunnel also cuts a request that stays silent for 120 seconds. An unstreamed request through it failed with HTTP 524 at 125.9 s, while the same request streamed succeeded after 166.5 s with 3600 tokens (source: `eval/runs/golive5/golive5_stream_proof.json`). Both the n8n workflow and the backend therefore ask for streamed replies. Only port 8000 is exposed, and the server still checks the API key. The key is a constant in the repository, so anyone who can read the repository can read it.

### Session window

After the tunnel step the script writes `endpoint.json` and sets a deadline of `HOURS = 1.5` from that moment. The comment gives the reason: the Kaggle command line cannot reliably cancel a running kernel, so the script ends itself. Once a minute the loop checks for a `llama-server` process and restarts it at the chosen context if none is found, and the tunnel stays up, so the address does not change. Every ten minutes it prints the full "ENDPOINT LIVE" block again and on other minutes a one-line status.

The log of v11 matches this. It reached the URL line at 223 s (3.5 s unpacking, 84 s download, about 90 s to boot, 34 s benchmark, 8 s tunnel) and its script output ends at about 94 minutes, which is 223 s plus 90 minutes (source: `kernel.log` of v11). The first live round used a window of 1.0 hours and the third round moved it to 1.5 (source: `docs/results/GOLIVE_RESULTS.md`). The launcher script quotes about 4 minutes from push to a usable address (source: `launcher/README.md`).

### How the address reaches n8n and the backend

`kaggle/tools/watch_deploy.py` follows the kernel log, and when it sees the `URL :` line it writes the address to `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt`, outside the repository. Two scripts read that file.

- **n8n.** `tools/golive/set_cloud_qwen_url.py` posts the address to the cloud workflow's `set-qwen-url` webhook, with a secret header. The node `Store Model URL` accepts only addresses that match `https://*.trycloudflare.com` and saves the address in the workflow's static data. The node `Get Model URL` reads it before each model call and falls back to the n8n variable `QWEN_BASE_URL` (source: `tools/nodes/store_model_url.js`, `tools/nodes/get_model_url.js`). A webhook is used because the n8n API is switched off on cloud trials.
- **Backend.** `kaggle/tools/backend_env.py` rewrites the `CLASSIFIER_` and `FIXER_` lines for provider, base URL, key and model name in `ci-heal-backend/.env`, and with `--check` calls `GET /v1/models` through the tunnel (source: `kaggle/tools/backend_env.py`). It has to run again after every boot.

The one-click start file `launcher/start/1_start_model_on_kaggle.bat` runs the status check, build, push, watch and webhook steps in order. A failed webhook step only prints a message, so the model can be running while n8n still holds the old address. In that case the Qwen call fails and the workflow goes to its OpenAI fallback and then to a failure comment.

### Quota and stopping

A Kaggle quota dialog read on 2026-09-07 showed 30 hours of GPU per week (source: `docs/research/KAGGLE_ENV_MEASURED.md`). The figure is from one dialog on one day, and Kaggle may change it. At 1.5 hours per window and ignoring boot time, 30 hours is at most 20 windows a week (our arithmetic).

A push does not replace a running kernel. Kaggle starts a second session, which would use quota twice, so `push.py` refuses to push while any of the four project kernels is `RUNNING` or `QUEUED` unless `--force` is given. The Kaggle README adds that GPU kernels can wait in the queue for hours (source: `kaggle/README.md`).

The command line cannot stop a session. The helper `launcher/end/stop_model_on_kaggle.bat` opens the kernel page and prints the steps: View Active Events, the session menu, Stop Session. `history.jsonl` records two manual stops through the website, for v12 and v15, and v12 ended with status `CANCEL_ACKNOWLEDGED` (source: `history.jsonl`). A session nobody stops runs out its 1.5 hours and uses quota the whole time. This is a real limit of the setup, because it depends on a person remembering to stop it, and the endpoint is a development service, not a durable host (source: `kaggle/README.md`).

### Measured speed

Speed was measured in four settings, and the figures do not belong in one column, so the table names the setting of each.

| Setting | Median tok/s | Source |
|---|---|---|
| First 9-config sweep, layer split, no MTP | 8.8 for the chosen file | `docs/old/QWEN38_SWEEP_RESULTS.md` |
| First live round trip, old flags (`q8_0` cache, layer split) | 12.11 mean | `docs/old/SELFHEAL_E2E_RESULTS.md` |
| Speed rig A, same old flags, ctx 8192 | 12.35 | `docs/results/SPEED_LEVERS_EVAL.md` |
| Rig B, `-sm tensor` with f16 cache | 15.49 (+25%) | `docs/results/SPEED_LEVERS_EVAL.md` |
| Rig C, MTP only | 19.99 (+62%) | `docs/results/SPEED_LEVERS_EVAL.md` |
| Rig D, both levers | 26.83 (+117%) | `docs/results/SPEED_LEVERS_EVAL.md` |
| Fork build b10709, both levers | 28.02 | `docs/results/SPEED_QUALITY_EVAL.md` |
| Boot benchmark, seven sessions, ctx 131072 | 22.4 to 26.8 average | boot logs v8 to v16 |
| Live decode, round 1 | 23.10 | `docs/results/GOLIVE_RESULTS.md` |
| Live decode, round 2 | 27.73 | `docs/results/GOLIVE_RESULTS.md` |
| Bake-off baseline, ctx 131072 | 23.4 | `docs/results/MODEL_BAKEOFF.md` |

We recomputed rigs A to D from `eval/runs/kout_speed/results.json`: 60 calls, no errors, and the same medians. In rig D the MTP head accepted drafts at a median rate of 0.96, and the per-card memory was 9051 MiB on each card, against uneven pairs such as 7761 and 9371 MiB in rig A (source: the same file). The tensor-split gain is smaller than the +47 to +68 percent reported for Ampere machines. The study traces this to PCIe: the two T4 cards have no NVLink, and it estimates the cost of combining results at about 24 ms per token (source: `docs/results/SPEED_LEVERS_EVAL.md`).

The live figures are lower than the rig because the inputs differ. Round 1 had an MTP accept rate of about 0.89 against 0.965 in the rig, since a search-and-replace answer is less predictable than a whole file that mostly copies the input (source: `docs/results/GOLIVE_RESULTS.md`). The same document ran with a context of 131072 against 8192 in the rig. Round 2 decoded faster at the same settings, and the document says the reason is not proven.

Prompt reading ran at 274 to 323 tok/s in round 1, which is about 2.2 s for a prompt of roughly 700 tokens. A model call through the tunnel carried 5 to 6 s of fixed overhead on top of generation (source: `docs/results/GOLIVE_RESULTS.md`). The study that set `--spec-draft-n-max` named values 3 and 4 as the next thing to test, and we found no result for them in the documents, so 2 is the value in use but not a tuned one.
