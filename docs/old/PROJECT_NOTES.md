# Project notes: the model decision (2026-09-07)

The first plan for the n8n workflow used the n8n **OpenAI** node against `api.openai.com`. We replaced that with a
self-hosted OpenAI-compatible endpoint served from a Kaggle notebook (free 2xT4, 32 GB VRAM),
because the pipeline fires only a few times a day and the team already uses Kaggle as a
remote GPU. n8n integration is unchanged in shape: same node, different **base URL + key**.

## Documents

| file | what it holds |
|---|---|
| `research/QWEN38_HARDWARE_FACTS.md` | Qwen3.8-27B architecture + quant sizes + measured speeds per GPU tier, and corrections to earlier estimates |
| `research/KAGGLE_ENV_MEASURED.md` | real Kaggle specs, the CUDA build fix, the accelerator gotcha, HF repo inventory, and the CLI workflow |
| `qwen38_sweep.py` | the benchmark run on Kaggle (in the project's early history; not part of this repository) |
| `research/QWEN38_SWEEP_RESULTS.md` | measured ranking |

## Why a quality eval, not just tok/s

Comparing Q8 against IQ2 is a **quality-per-GB** question, so a throughput-only benchmark
cannot answer "which is best". The sweep scores each config on a 5-case CI-failure suite
that mirrors the real pipeline task: analyze a GitHub Actions failure log plus a broken
`test.js`, return the corrected file as strict JSON:

| case | injected bug | checked by |
|---|---|---|
| `missing_app_init` | `const app = express()` deleted | restores `app = express()` + `app.listen` |
| `typo_method` | `app.lisen(...)` | contains `app.listen`, no `lisen` |
| `missing_require` | `require("http")` removed | re-adds the http require |
| `syntax_missing_brace` | unclosed callback | closes the brace |
| `wrong_status_check` | `statusCode === 201` | corrected to `200`, not `201` |

Each scores 0–3: **valid JSON** with all five keys / **`node --check` passes** on
`fixed_code` / **fix is correct** (must-match and must-not-match regexes). Ranking is
quality → fix-rate → generation speed. Low-bit quants tend to fail the JSON leg first, which
is exactly the failure mode that would break the n8n flow.

## Serving + n8n wiring (once a winner is picked)

```
llama-server -m <winner>.gguf -ngl 99 -ts 0.5,0.5 -fa 1 -c 32768              -ctk q8_0 -ctv q4_0 --host 0.0.0.0 --port 8000 --jinja --api-key <key>
cloudflared tunnel --url http://localhost:8000
```

Then in n8n, replace the OpenAI credential's base URL with `https://<sub>.trycloudflare.com/v1`.
Everything downstream of the model node (extract-AI-response, create branch, push file, open
PR, Gmail) is unchanged from Part 1.

Notes that matter for the pipeline:
- **Strip `<think>...</think>` before `JSON.parse`**: all Qwen3.x are hybrid-reasoning.
- Consider a **GBNF grammar** on llama-server to hard-enforce the JSON schema.
- Keep context **64k–80k max**: llama.cpp CUDA issue #27623 collapses decode past ~80k.
- Kaggle GPU quota is **30 h/week per account** and sessions cap out, so a tunnel is fine for
  testing but is not a durable production endpoint, so the hosted-API fallback stays.
