# Self-Healing CI/CD Pipeline: Project Understanding & Prep

Source: YouTube "Build a Self-Healing CI/CD Pipeline with AI" (freeCodeCamp / The Techie, Faizan Ali)
Video: https://www.youtube.com/watch?v=vj68el9hRvU
Article: https://www.freecodecamp.org/news/build-a-self-healing-ci-cd-pipeline-with-ai/

## What we are building

An autonomous pipeline that, when a GitHub Actions build fails, automatically:
1. Detects the failure and POSTs a webhook to an n8n workflow.
2. Uses the GitHub API to pull the failed job logs, the changed files, and the raw file content.
3. Sends logs + code to OpenAI (GPT-4o-mini) for root-cause analysis and a full-file fix.
4. Creates a new branch, commits the AI fix, and opens a Pull Request.
5. Emails the team. A human reviews and merges; the bot never writes to `main` directly.

## Architecture / flow

GitHub Action fails -> `notify-on-failure` job -> `curl` POST to n8n webhook
-> n8n: Webhook (auth header) -> HTTP: fetch job logs -> HTTP: fetch changed files (compare API)
-> HTTP: fetch raw file content -> Code node (build prompt) -> OpenAI (message a model)
-> Code node (extract + clean JSON, base64-encode fix) -> HTTP: create branch (git refs)
-> HTTP: get file SHA -> HTTP: PUT file content (push fix) -> HTTP: create PR -> Gmail notify

## Tech stack / accounts needed

- Git + GitHub account
- Node.js 20 (local): the sample app is Node + Express
- VS Code
- n8n cloud account (14-day free trial), which runs the automation
- OpenAI account with >= $5 credit + API key (or use n8n's free credits)
- Gmail (for notification node, OAuth via "Sign in with Google")

## GitHub setup

- Repo name in video: `self-healing-cicd-pipeline-using-n8n`
- Personal Access Token (classic) with scopes: `repo`, `workflow`, `admin:org` (admin), `admin:repo_hook`
- Repo Actions secrets (names must match main.yml exactly, case-sensitive):
  - `GH_TOKEN`: the PAT
  - `N8N_WEBHOOK_SECRET`: any strong string (e.g. `mysecret123!`), also stored in n8n
  - `N8N_WEBHOOK_URL`: n8n webhook URL (test URL during dev, production URL at the end)

## Sample app files

- `index.js`: trivial `console.log("hello from index.js")`
- `test.js`: smoke test: starts Express on port 5000, HTTP GET to self, exit 0 if 200 else exit 1
- `package.json` scripts: `"test": "node test.js"`, `"build": "node index.js"`
- `.gitignore`: `node_modules`
- deps: `express`

## `.github/workflows/main.yml` outline

- `name: CI Pipeline`
- `on: push: branches: [main]`
- job `build-and-test` (runs-on ubuntu-latest):
  - actions/checkout@v4
  - actions/setup-node@v4 with node-version: 20
  - `npm install`
  - `npm test`
  - `npm run build --if-present`
- job `notify-on-failure` (needs: build-and-test, `if: failure()`):
  - `curl -X POST "${{ secrets.N8N_WEBHOOK_URL }}"`
    `-H "Content-Type: application/json"`
    `-H "X-Webhook-Secret: ${{ secrets.N8N_WEBHOOK_SECRET }}"`
    `-d '{ "run_id": "${{ github.run_id }}", "repo": "${{ github.repository }}", "branch": "${{ github.ref_name }}", "commit": "${{ github.sha }}", "actor": "${{ github.actor }}" }'`

## n8n workflow nodes

1. **Webhook** ("GitHub Failure Webhook"): POST, Header Auth credential, secret name `X-Webhook-Secret` = `N8N_WEBHOOK_SECRET` value; response mode "when last node finishes", no response body.
2. **HTTP "fetch logs"**: GET `https://api.github.com/repos/{{repo}}/actions/runs/{{run_id}}/jobs`, Bearer auth (GitHub PAT credential).
3. **HTTP "fetch changed files"**: GET `https://api.github.com/repos/{{repo}}/compare/{{commit}}~1...{{commit}}`, Bearer.
4. **HTTP "fetch file content"**: GET `{{ $('fetch changed files').first().json.files[0].raw_url }}`, Bearer.
5. **Code node**: captures webhook payload + logs JSON; finds failed job via `.find(status/conclusion === 'failure')`; extracts failed steps; filters changed files to code extensions (js, ts, py, ...); normalizes file content; wraps code in markdown fences; builds one big `prompt` string (repo, branch, commit, code) instructing AI to return valid JSON only.
6. **OpenAI "message a model"**: model gpt-4o-mini; system message = "expert DevOps engineer / Node.js dev... return ONLY raw valid JSON, no markdown. Branch name must be unique/specific (e.g. `ai-fix-express-init`), not main/master. Fixed code = entire file content. Schema: branch_name, file_path, fixed_code, pr_title, pr_body"; user message = the dynamic prompt.
7. **Code node "extract AI response"**: parse OpenAI output; strip markdown fences via regex; JSON.parse; verify `fixed_code` exists; return object incl. `base64_content = Buffer.from(fixed_code).toString('base64')`, plus repo/commit.
8. **HTTP "create branch"**: POST `https://api.github.com/repos/{{repo}}/git/refs`, Bearer, JSON body `{ "ref": "refs/heads/{{branch_name}}", "sha": "{{commit}}" }`.
9. **HTTP "get file sha"**: GET `https://api.github.com/repos/{{repo}}/contents/{{file_path}}`, Bearer.
10. **HTTP "push fix file"**: PUT `https://api.github.com/repos/{{repo}}/contents/{{file_path}}`, Bearer, body: `message` = pr_title, `content` = base64_content, `sha` = file sha, `branch` = branch_name.
11. **HTTP "open pull request"**: POST `https://api.github.com/repos/{{repo}}/pulls`, Bearer, body: `title` = pr_title, `body` = pr_body, `head` = branch_name, `base` = main.
12. **Gmail "send a message"**: subject "CI/CD failure fixed and pushed by AI", body pulls repo/branch/PR title from prior nodes. Optionally disable n8n attribution.

## Going to production

- Switch webhook from Test URL to Production URL, Publish the n8n workflow.
- Update `N8N_WEBHOOK_URL` GitHub secret to the production URL.

## Notes / gotchas

- Secret name case sensitivity everywhere.
- Common transcript typos: `steps`, `node-version`.
- `compare` API `~1...` needs URL-encoding care (`~` and `...`).
- Workflow-run log archive download URL expires ~1 min; the video uses the `/jobs` endpoint (JSON) instead of the raw log archive.
- `github.event.pull_request` not used; the trigger is `push` to main.
- To intentionally break the build: delete `const app = express()` from test.js.

## Reference links

- GitHub Actions REST API: https://docs.github.com/en/rest/actions
- Workflow jobs API: https://docs.github.com/en/rest/actions/workflow-jobs
- Repo contents API: https://docs.github.com/en/rest/repos/contents
- Git refs API: https://docs.github.com/en/rest/git/refs
- n8n Webhook node: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
- n8n HTTP Request node: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/
- OpenAI models: https://platform.openai.com/docs/models


---

# Part 2: The model / "brain" decision (added 2026-09-07)

The tutorial wires the n8n **OpenAI** node to `api.openai.com`. We are replacing that with a
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
