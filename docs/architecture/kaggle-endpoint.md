# Kaggle model endpoint

The healing flow needs a Qwen3.8-27B server it can call over HTTPS. We run it as a Kaggle script kernel on two Tesla T4 GPUs, with llama.cpp's `llama-server` behind a Cloudflare quick tunnel. This page describes the kernel, the local tools that deploy it, and how the new URL reaches n8n and ci-heal-backend.

## Files

| Path | Role |
|---|---|
| `kaggle/endpoint/deploy_src.py` | The kernel source. Everything that runs on Kaggle is in this one file. |
| `kaggle/endpoint/models.json` | Registry of model builds. `default` is `unsloth-ud-q4kxl`. |
| `kaggle/tools/build_endpoint.py` | Writes `kaggle/endpoint/push/qwen3-8-27b-live-endpoint.py` from `deploy_src.py` and one registry entry. |
| `kaggle/endpoint/push/` | Generated upload: the `.py` file and `kernel-metadata.json`. Not edited by hand (kaggle/README.md, "Directory layout"). |
| `kaggle/tools/kaggle_cli.py` | Shared helpers: status, push with checks, log download, run folders. |
| `kaggle/tools/push.py`, `status.py`, `watch_deploy.py`, `fetch_logs.py`, `backend_env.py` | The commands described below. |
| `launcher/start/1_start_model_on_kaggle.bat`, `launcher/end/stop_model_on_kaggle.bat` | One-click start and the stop helper. |
| `tools/golive/set_cloud_qwen_url.py`, `tools/golive/show_model_info.py` | Send the URL to cloud n8n; print URL, key and reachability. |

## Kernel metadata

`kaggle/endpoint/push/kernel-metadata.json` makes the kernel private, turns on the GPU and internet, sets `machine_shape` to `NvidiaTeslaT4`, and lists `linsxav/llama-server-builder` under `kernel_sources`. The kernel slug is `lacrimatct/qwen3-8-27b-live-endpoint` (`kaggle/tools/kaggle_cli.py`, `KERNELS`).

## Building the push file

`deploy_src.py` carries one line that starts with `MODEL_SPEC = {`. `build_endpoint.py` asserts that exactly one such line exists, replaces it with the chosen entry from `models.json` (key, repo, filename regex, fallbacks), compiles the result so a syntax error never gets written, and writes it to the file named by `code_file` in `kernel-metadata.json`. Kaggle only runs the code file and cannot read `models.json`, which is why the spec is pasted in. With the default model the output is byte-identical to `deploy_src.py` (`kaggle/tools/build_endpoint.py`). `--list` prints the registry and writes nothing.

## What the kernel does at boot

The steps run in this order (`kaggle/endpoint/deploy_src.py`).

### GPU check

The script asks `nvidia-smi` for each GPU's memory and adds the values up. If the total is under 24000 MiB it prints `ABORT: need 2xT4` and exits before downloading anything. On the real machine the log shows two 15360 MiB T4s, 30720 MiB in total (kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-11-15_v16/boot.log).

### Prebuilt server, no compiling

The CUDA build of `llama-server` is made once, in the separate kernel `linsxav/llama-server-builder`, and attached to this kernel through `kernel_sources`. At boot the script globs `/kaggle/input/**/llama-server-cuda.zip`, unpacks it to `/kaggle/working/bin`, marks the binary and the `.so` files executable, and sets `LD_LIBRARY_PATH` to that folder. It then runs `llama-server --help` and asserts a zero exit code. If the zip is missing it lists `/kaggle/input` and stops with an error; it does not fall back to compiling. The module docstring says this replaces about 40 minutes of cmake on 4 slow vCPUs with an unzip (`deploy_src.py`, module docstring).

### Model download and fallbacks

`fetch()` lists the `.gguf` files in a Hugging Face repo, drops anything with `mmproj` in the name, sorts the files whose path matches the registry regex, and downloads the first one into `/kaggle/tmp/models`. The default entry asks for `UD-Q4_K_XL.gguf` in `unsloth/Qwen3.8-27B-GGUF`. If nothing matches or the repo call fails, it tries the fallbacks in order, `UD-Q4_K_M.gguf` and then `UD-Q6_K.gguf`, both from the same repo (`deploy_src.py`, `MODEL_SPEC`; `models.json`). If none downloads, the script asserts. The file actually used is written to `endpoint.json`.

The 17.6 to 17.7 GB default file took 76 to 91 seconds to download in the boot logs we kept (kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-10-30_v12/boot.log, 2026-11-15_v15/boot.log, 2026-11-15_v16/boot.log).

## The llama-server command

`launch(ctx)` kills any running `llama-server`, sleeps 4 seconds, and starts a new one in the background with output going to `/kaggle/working/server.log` (`deploy_src.py`, `launch()`).

| Flag | What it does here |
|---|---|
| `-m <file>` | The downloaded GGUF. |
| `--n-gpu-layers 999` | Offload every layer to the GPUs. |
| `--ctx-size <ctx>` | Context length from the ladder below. |
| `--flash-attn on` | Flash attention. docs/results/SPEED_LEVERS_EVAL.md notes it was already on before the speed work and that tensor mode requires it. |
| `-sm tensor` | Split each layer's tensors across both T4s, instead of giving each GPU a block of layers. |
| `--cache-type-k f16 --cache-type-v f16` | Unquantized KV cache. The same document says tensor mode forbids a quantized KV cache, so the two settings change together. |
| `--spec-type draft-mtp --spec-draft-n-max 2` | Speculative decoding with the model's own multi-token-prediction head, drafting up to 2 tokens per step. Every registry entry needs an MTP build (`models.json`, `_about`). |
| `--parallel 1` and `-np 1` | One slot. The code comment says heal requests arrive one at a time. The two flags are the same option written twice. |
| `-b 2048 -ub 512` | Batch and micro-batch sizes. |
| `-t 4 -tb 4` | Four CPU threads for generation and four for batch work. |
| `--reasoning-budget 1024` | Cap on thinking tokens when thinking is on. The code has no comment on it; docs/results/MODEL_BAKEOFF.md lists it among the production flags. |
| `--host 0.0.0.0 --port 8000` | Listen on all interfaces, port 8000. |
| `--jinja` | Use the model's own chat template. |
| `--api-key selfheal-e2e-key` | Require `Authorization: Bearer <key>` on requests. |
| `--chat-template-kwargs '{"enable_thinking":false}'` | Default thinking off. Added only when the binary's `--help` text contains `--chat-template-kwargs`. |

### Why thinking is off by default

The code comment says Qwen3.8's chat template thinks unless a request says otherwise, and the server log shows `thinking = 1`. The n8n flow always sends `enable_thinking` with each request. ci-heal-backend only sends it to turn thinking on, so the server itself has to default to off. A per-request `enable_thinking:true` still works (`deploy_src.py`, comment above `THINK_OFF_FLAG`). kaggle/README.md adds that with thinking on by default the backend classifier's 512-token budget can run out before its JSON answer ("Serving ci-heal-backend"). If the binary lacks the option, the script prints a warning that thinking stays on and tells the operator to set `CLASSIFIER_/FIXER_LLM_THINKING_FLAG=explicit` in ci-heal-backend.

### Speed measurements behind the flags

On the earlier measurement rig, 12.35 tok/s with the old flags became 15.49 with `-sm tensor` plus f16 KV, 19.99 with MTP alone, and 26.83 with both (docs/results/SPEED_LEVERS_EVAL.md, "Results").

## Context ladder

The kernel tries these context sizes in order: 131072, 98304, 65536, 32768, 16384, 8192. Each rung gets `RUNG_TIMEOUT = 300` seconds for `/health` to answer 200, and `wait_health` gives up sooner if no `llama-server` process is left. A rung that boots must also answer the benchmark requests. A rung that fails, or whose requests all fail, is skipped. The first one that works is kept, and the script asserts if none does (`deploy_src.py`, `LADDER`, main loop). Every boot log we kept shows 131072 booting on the first try. In v16, 26662 of 30720 MiB were in use afterwards (kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-11-15_v16/boot.log).

## Benchmark and thinking check

`bench()` sends three chat requests of up to 256 tokens and records the server's own `timings` (generation tok/s, draft counts) rather than parsing the log. From these it derives:

- `tensor_split_engaged`: true when two GPUs report memory use within 64 MiB of each other.
- `mtp_engaged`: true when at least one request reported draft tokens.
- `accept_rate`: accepted draft tokens divided by drafted tokens, averaged over the requests that reported them.

If either lever is missing, the kernel prints a warning that the endpoint will be slower than measured, and serves anyway.

`thinking_check()` asks "What is 17 * 23?" twice, once as is and once with `chat_template_kwargs: {"enable_thinking": true}`. A reply counts as thinking if it has `reasoning_content` or a `<think>` block. The expected result is false by default and true when asked; the kernel prints a warning otherwise. It looks at the reply, not at a log line.

Measured at boot, default model, context 131072:

| Run | Avg tok/s | Best | MTP accept | Source |
|---|---|---|---|---|
| v11, 2026-09-28 | 23.9 | 24.4 | 0.613 | kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-09-28_v11_round5/endpoint.json |
| v12, 2026-10-30 | 23.8 | 24.4 | 0.64 | .../2026-10-30_v12/boot.log |
| v15, 2026-11-15 | 26.8 | 27.4 | 0.664 | .../2026-11-15_v15/boot.log |
| v16, 2026-11-15 | 22.4 | 23.7 | 0.719 | .../2026-11-15_v16/boot.log |

All four had the tensor split engaged, with equal memory on both GPUs. Version 14 crashed in `launch()` with a `TypeError` and its folder holds the successful restart as well, which the logs label v15 with the same tunnel address, so the table counts them as one session. The thinking check appears in the logs of v14, v15 and v16, not in v12 (which ran before it was added), and v16 shows false by default and true when asked. We have not looked into why speed differs between sessions.

## Tunnel and URL

Once the server is up, the kernel downloads `cloudflared` from the latest GitHub release and runs `cloudflared tunnel --url http://localhost:8000 --no-autoupdate`. It polls `tunnel.log` every 4 seconds, up to 45 times, and takes the first `https://*.trycloudflare.com` address. Only port 8000 is exposed, and llama-server still checks the API key (`deploy_src.py`).

This is a quick tunnel, with no Cloudflare account and no fixed name, and each start gets a new random hostname. The boot logs in kaggle/logs/endpoint show a different address for v11, v12, v15 and v16. So the URL has to be delivered again after every boot (see "Delivering the URL").

The quick tunnel also cuts a connection that stays silent for 120 seconds, which is why the n8n flow streams model replies. docs/results/GOLIVE_RESULTS.md records an unstreamed request failing with HTTP 524 at 125.9 s.

## endpoint.json

The kernel writes `/kaggle/working/endpoint.json` once the tunnel step finishes (`deploy_src.py`).

| Field | Meaning |
|---|---|
| `url` | The trycloudflare address, or `null` if none appeared within the 45 polls. |
| `api_key` | The API key. |
| `repo`, `file`, `model_gb` | Hugging Face repo and file actually downloaded, and its size in GB. |
| `ctx` | Context size that was chosen. |
| `vram_used_mib`, `vram_total_mib` | Memory in use after boot, and total across GPUs. |
| `tensor_split_engaged`, `mtp_engaged`, `accept_rate` | From the benchmark. |
| `gen_tps_avg`, `gen_tps_best` | Benchmark speed. |
| `thinking_by_default`, `thinking_when_asked` | From the thinking check. |

The only saved copy is kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-09-28_v11_round5/endpoint.json, which predates the two thinking fields.

## API key

The key is a constant, `API_KEY = "selfheal-e2e-key"`, in `deploy_src.py`. llama-server enforces it; the tunnel adds no other protection. `backend_env.py` and `show_model_info.py` both read the key by matching that line of `deploy_src.py` with a regex, so changing the constant changes it for both. n8n holds it as the credential "Qwen3.8 Endpoint Key" (tools/golive/n8n_import.py, tools/golive/show_model_info.py). Because the key sits in a file in the repository, anyone with repository access can read it.

## Time window and restart

After `endpoint.json` is written, the kernel sets a deadline of `HOURS = 1.5` from that moment and loops once a minute. The comment says the session ends by itself because the Kaggle CLI cannot reliably cancel a running kernel. Each minute the loop checks `pgrep -f llama-server`. If there is no process it prints `server died, restarting` and calls `launch()` at the context it already chose. The tunnel process keeps running, so the URL does not change. Every tenth minute it prints the full "ENDPOINT LIVE" block again; on other minutes it prints a one-line status with VRAM and URL. When the deadline passes it prints `window elapsed` and the script ends (`deploy_src.py`, end of file). The restart call's result is not checked, so a server that fails to come back is retried on the next minute.

## Deploying

The one-click path is `launcher/start/1_start_model_on_kaggle.bat`. It runs `status.py`, `build_endpoint.py`, `push.py endpoint`, `watch_deploy.py` and then `set_cloud_qwen_url.py`. The first three stop the batch on failure; a failed n8n update only prints that the model is still running. The batch file says boot takes about 4 minutes, and launcher/README.md gives the same figure.

### status.py

Prints one line per project kernel (endpoint, shipped_quality, multifile, bakeoff): name, slug, Kaggle status, and the last pushed version from `history.jsonl`.

### push.py

`push.py endpoint` calls `push()` in `kaggle_cli.py`:

1. Query the status of all four project kernels, not only the one being pushed. If any is `RUNNING` or `QUEUED`, refuse unless `--force` is passed. The code gives the reason: a push does not replace a running kernel, it starts a second session and doubles GPU quota use, and the CLI has no cancel.
2. Run `kaggle kernels push`. The output must contain `Kernel version N successfully pushed`, or the script exits. A comment records that a 409 once lost a set of fixes.
3. Pull the kernel back into a temporary folder and compare it with the local file after turning `\r\n` into `\n`. A mismatch aborts and names the version.
4. Append a `push` entry to `kaggle/logs/<kind>/<kernel>/history.jsonl`.

All Kaggle calls run with `PYTHONUTF8=1`, because the CLI crashes on Windows when it writes a non-ASCII log and leaves an empty file (`kaggle_cli.py`, module docstring).

### watch_deploy.py

Runs `kaggle kernels logs <slug> -f` and reconnects every 20 seconds, for up to 45 minutes. Every distinct line goes to `stream.log`. Lines matching a keyword list (GPU, download, context rung, SELECTED, tensor split, MTP, thinking, errors, URL) also go to `boot.log`. Both live in `kaggle/logs/endpoint/<kernel>/<date>_v<N>/`. The docstring says `kernels output` returns nothing while a kernel is running and only `logs -f` streams. It also says `-f` replays from the start on reconnect, which is why lines are de-duplicated.

When it sees `URL : https://...trycloudflare.com` it writes that address to `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt`, prints `ENDPOINT URL saved`, and exits 0. If no URL appears in the window it prints `no endpoint within the window` and exits 1. The file sits outside the repository on purpose (docstring: "never in the project").

### fetch_logs.py

`fetch_logs.py endpoint [label]` downloads the kernel's top-level `.log`, `.json` and `.txt` files into a run folder. A file pattern keeps it from pulling `bin/`, which holds 164 MB of CUDA libraries per run (`kaggle_cli.py`). It renames `<slug>.log` to `kernel.log`, exits with an error if any downloaded file is empty, and records the fetch in `history.jsonl`. If the kernel is still `RUNNING` or `QUEUED` it prints that the log is partial. `--into <folder>` backfills an older run.

## Delivering the URL

### Cloud n8n

`tools/golive/set_cloud_qwen_url.py` reads `qwen_url.txt` and the `webhook_secret` from `%LOCALAPPDATA%\selfheal-n8n\secrets.json`. It then POSTs `{"url": ...}` to `https://self-heal.app.n8n.cloud/webhook/set-qwen-url` (override with `N8N_CLOUD_URL`) with an `X-Webhook-Secret` header. The workflow's `Set Model URL` node stores the value, and `Get Model URL` reads it before each model call, with the `QWEN_BASE_URL` variable as a fallback (launcher/README.md). The script checks that the reply echoes the same URL. A webhook is used because the n8n API is switched off on n8n Cloud trials (script docstring). Exit codes: 0 stored, 2 nothing to send yet, 1 failure. The secret is not printed.

### ci-heal-backend

`kaggle/tools/backend_env.py` reads `qwen_url.txt` and the key from `deploy_src.py`. In `ci-heal-backend/.env` it rewrites only the `CLASSIFIER_` and `FIXER_` lines for `LLM_PROVIDER` (`http`), `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` (`local`). It starts from `.env.example` if `.env` does not exist, and keeps all other lines. With `--check` it calls `GET /v1/models` through the tunnel and prints the HTTP status. The URL changes on every boot, so it has to run again after each deploy. `launcher/start/4_start_backend.bat` runs it with `--check` before starting the backend; `1_start_model_on_kaggle.bat` does not.

### show_model_info.py

Prints the saved URL, the chat path, the API key, and whether `GET /v1/models` answers, and copies the URL to the clipboard through `clip`. It does not query n8n. Its docstring says it reports whether the cloud n8n variable already holds the URL, but the code only prints a line saying the start script updates it.

## Stopping

The Kaggle CLI cannot stop a running session (`kaggle_cli.py` docstring; `stop_model_on_kaggle.bat`). The stop helper opens `https://www.kaggle.com/code/lacrimatct/qwen3-8-27b-live-endpoint` and prints the steps: View Active Events, the `...` menu on the session, Stop Session. After a key press it runs `status.py` and `fetch_logs.py endpoint`. If nobody stops it, the 1.5 hour window ends the script. `history.jsonl` records manual stops for v12 and v15, both through the website, and v12 ended with status `CANCEL_ACKNOWLEDGED` (kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/history.jsonl).

Kaggle GPU quota is used while the session runs (launcher/README.md). `docs/research/KAGGLE_ENV_MEASURED.md` records a weekly GPU quota of 30 hours, read from the Kaggle dialog on 2026-09-07; that figure was not checked again. Since a second push would start a second session, the working rule is one GPU session at a time, and `push.py` enforces it across all four project kernels.

The kernel page also has a "Pin as default version" choice for what the front page shows. Nothing in the repository documents or automates it, and the exact menu path is not confirmed here.

## Model choice

The 2026-10-30 bake-off ran four Q4-class Qwen3.8-27B builds with MTP through the real heal path. The current unsloth `UD-Q4_K_XL` stayed because it tied every challenger on first-try heals (14 of 24), healed most with thinking on (21 of 24 against 15 to 17), and was fastest at 23.4 tok/s against 16.9 to 19.3. All four loaded at context 131072 with the tensor split engaged. The table and per-case results are in `docs/results/MODEL_BAKEOFF.md`.
