# self_healing_CI-CD_Pipeline

When a GitHub Actions run fails, this project works out why and tries to repair it: it reruns flaky jobs,
opens a pull request with a fix for code bugs, or files an issue when it is not confident. The model that
reads the logs and writes the fixes is Qwen3.8-27B, self-hosted on a Kaggle notebook with two T4 GPUs.

## Repository layout

| path | what it is |
|---|---|
| [`ci-heal-backend/`](ci-heal-backend/) | FastAPI service. Classifies a failed run, then retries it, opens a fix PR or opens an issue, depending on confidence. Has its own tests and docs. |
| [`kaggle/`](kaggle/) | The Qwen3.8-27B endpoint (llama.cpp on 2×T4 behind a Cloudflare tunnel), the evaluation kernels that measured it, deploy tools and run logs. |
| [`n8n/`](n8n/), [`tools/`](tools/) | The earlier healer: an n8n workflow that calls the same endpoint and opens fix PRs, plus its node code, generator, unit tests and a rig that runs it in a real n8n engine. |
| [`eval/`](eval/) | Bug cases, scorers and the results data behind every measurement in `docs/results/`. |
| [`launcher/`](launcher/) | Double-click scripts that start and stop the model, n8n and the backend. |
| [`demo-app/`](demo-app/) | The small Express app that CI runs on, with its workflow in `.github/workflows/demo-ci.yml`. |
| [`docs/`](docs/) | The project report (`PROJECT_REPORT.md` and PDF), guides, architecture notes, the backend walkthrough, measured results, research and a captioned demo video. Start at `docs/README.md`. |

## How the parts connect

```text
GitHub Actions run fails
   |-- workflow_run webhook -----------> ci-heal-backend  --+
   |                                                       |-- POST /v1/chat/completions --> Kaggle endpoint
   '-- notify job (N8N_WEBHOOK_URL) --> n8n workflow ------+       (Qwen3.8-27B, OpenAI-compatible, streamed)
```

Both healers speak the same protocol to the endpoint: OpenAI-style chat completions, streamed so the
tunnel's 120-second idle limit never cuts a long answer, and a fix format of one JSON object holding
SEARCH/REPLACE edits (`ci-heal-backend/docs/LLM_CONTRACT.md`).

Turn on one healer per repository. Both react to the same failed run, so running them together would
open two competing fix PRs. The backend can also take its failures from n8n through `POST /webhooks/n8n`.

## Getting started

On Windows, `launcher/` has double-click scripts for every step below, with the order in
`launcher/README.md`.

1. Bring up the model: `kaggle/README.md`, section "Deploy the live endpoint". The tunnel URL is saved
   locally when the endpoint is ready.
2. Point the backend at it: `python kaggle/tools/backend_env.py --check` writes the endpoint URL and key
   into `ci-heal-backend/.env`. Then follow `ci-heal-backend/README.md` (Quick start). `DRY_RUN=true`
   is the default, so nothing is written to GitHub until you change it.
3. Or run the n8n healer instead: `docs/guides/SETUP.md`.

Tests that need no GPU:

```
cd ci-heal-backend && pytest && ruff check .
python run_tests.py            # n8n node suites, eval scorers, workflow generator
python run_tests.py --rig      # adds the real-n8n rig (needs n8n 1.x, see tools/n8n_rig/README.md)
```

## Measured so far

On the demo repository [`Lin-2352/self-healing-cicd-demo`](https://github.com/Lin-2352/self-healing-cicd-demo),
with the n8n healer: every easy bug fixed on the first try (15/15 in the eval, 5/5 live), 12 of 18 hard
single-file bugs on the first try and 18 of 18 with thinking on, and about 40 seconds from push to fix
PR. Generation runs at 22 to 27 tokens per second. Details and caveats are in `docs/results/`, and the
comparison of four Qwen3.8 builds is in `docs/results/MODEL_BAKEOFF.md`.
