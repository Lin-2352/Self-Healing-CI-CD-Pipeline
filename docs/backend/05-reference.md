# Chapter 5: Reference (settings, endpoints, and database)

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 4, Code tour](04-code-tour.md) · Next: [Chapter 6, Status and running](06-status-and-running.md)

This chapter is for looking things up. It lists every setting, every endpoint, and every database table, each with a fuller explanation than the short tables in `ci-heal-backend/README.md`. All facts come from `app/config.py`, the route files in `app/api/`, and `app/db/models.py`.

---

## 5.1 Settings

Every setting is an attribute of the `Settings` class in `app/config.py` and can be set as an environment variable with the same name in capitals, or as a line in `.env`. [Chapter 2](02-environments-and-docker.md) explains how that works. Each entry below gives the **default**, what it **changes**, and whether you must change it **before going live** against a real repository.

### General

**`APP_NAME`**: default `ci-heal-backend`. The service name reported by `/health` and shown as the title of the `/docs` page. It has no effect on behaviour, so it is only useful if you run several copies and want to tell them apart. You never need to change it before going live.

**`APP_ENV`**: default `dev`. One of `dev`, `test`, or `prod`. Only `dev` allows an empty `API_TOKEN` to open the dashboard routes and an empty `GITHUB_WEBHOOK_SECRET` to accept GitHub webhooks. It is also reported by `/health`. **Before live:** set it to `prod`, so a forgotten secret locks things down instead of opening them up.

**`LOG_LEVEL`**: default `INFO`. One of `DEBUG`, `INFO`, `WARNING`, or `ERROR`. It controls how much the server writes to its log output. `DEBUG` shows the most detail and is useful when something misbehaves, while `WARNING` keeps the log quiet. The backend never logs raw webhook bodies or unredacted logs at any level.

**`DRY_RUN`**: default `true`. When true, every write to GitHub (branches, commits, pull requests, issues, reruns) is skipped and a fake `dry-run://` link is recorded instead. Reads still happen. **Before live:** leave it `true` at first; set it to `false` only on a sandbox repository once everything else has been checked. [Chapter 2, section 2.5](02-environments-and-docker.md#25-what-dry_run-actually-changes) explains it fully.

### GitHub

**`GITHUB_TOKEN`**: default empty. The token used to read logs and files and, outside dry-run, to write branches, PRs, issues, and reruns. Use a fine-grained personal access token or a GitHub App installation token, not the automatic token inside GitHub Actions, because pushes made with that one do not trigger new workflow runs. **Before live:** required; without it every read returns `missing_token` and failures end in `error`.

**`GITHUB_API_URL`**: default `https://api.github.com`. The root address of the GitHub API. You would only change it for GitHub Enterprise Server, which runs at a company's own address. Tests also point it at a fake server. Most users never touch it.

**`GITHUB_TIMEOUT`**: default `30` seconds, allowed above 0 up to 300. How long one attempt at a GitHub request may take before it is abandoned and possibly retried. Large log downloads on slow connections might need more, but the default is fine for normal use.

**`GITHUB_MAX_RETRIES`**: default `2`, allowed 0 to 5. How many extra attempts are made after a GitHub request fails with a timeout, a server error, or a rate limit. The client waits between attempts and honours GitHub's `Retry-After` and rate-limit reset headers, so it backs off politely instead of hammering the API.

**`GITHUB_MAX_FILE_BYTES`**: default `100000` (about 100 KB). The largest single file that will be downloaded and decoded as context for the model. Bigger files are refused, because they would overflow the model's input and slow everything down. Most source files are far smaller than this.

**`GITHUB_MAX_LOG_BYTES`**: default `1000000` (about 1 MB). The largest log zip or diff that will be read. This protects the server's memory from an enormous log. Since only the lines around the first error are used anyway, very large logs rarely add useful information.

**`GITHUB_DEFAULT_BRANCH`**: default `main`. The backend refuses to write to this branch name, in addition to always refusing `main` and `master`. **Before live:** check it; if the target repository's main branch is called something else, such as `develop` or `trunk`, set it so that branch is protected too.

**`GITHUB_WEBHOOK_SECRET`**: default empty. The shared secret used to check GitHub's `X-Hub-Signature-256` signature on every webhook. You choose a long random value and enter the same value in the repository's webhook settings on GitHub. **Before live:** required; in `test` and `prod` an empty secret rejects every GitHub delivery.

**`GITHUB_BOT_LOGIN`**: default empty. The GitHub username of the account that owns `GITHUB_TOKEN`. Runs started by this account are ignored, except runs on `ai-fix-*` branches which are still used for verification. **Before live:** recommended, so the bot's own activity can never trigger another round of fixing.

### Webhooks and API access

**`N8N_SHARED_TOKEN`**: default empty. The value that must appear in the `X-N8N-Token` header on `POST /webhooks/n8n`. Unlike the GitHub webhook, there is no `dev` shortcut, so with an empty value that endpoint rejects everything. **Before live:** required only if the team's n8n workflow forwards failures to the backend.

**`API_TOKEN`**: default empty. The bearer token required on the dashboard routes (`/failures`, `/decisions`, `/stats`, and reprocess). The caller sends `Authorization: Bearer <token>`. Only `APP_ENV=dev` with an empty value leaves these routes open. **Before live:** required; set a long random value and give it only to the dashboard.

**`ALLOWED_REPOS`**: default empty. A comma-separated list of repositories in `owner/name` form, such as `acme/demo`. Both webhooks refuse any repository not on the list with `403`, and an empty list allows none at all. **Before live:** required; list only the repositories the backend is meant to act on, ideally one sandbox repository at first.

**`WEBHOOK_MAX_BYTES`**: default `1000000`, allowed 1024 to 10,000,000. The largest webhook body accepted; anything bigger gets `413`. GitHub's `workflow_run` payloads are far smaller, so this mostly protects against someone sending a huge body to tie up the server.

**`CORS_ORIGINS`**: default `http://localhost:5173,http://localhost:3000`. A comma-separated list of web addresses allowed to call the API from a browser. Browsers block a web page from calling a different site's API unless that API allows it, and this list is that permission. The defaults are the usual local addresses for a React development server. **Before live:** set it to the dashboard's real address.

### Pipeline and recovery

**`PIPELINE_TIMEOUT_SECONDS`**: default `180`. The time limit for processing one failure, including every model call and GitHub read. If it is exceeded, the failure is marked `error` with `pipeline timed out` and an escalation issue is attempted. With a slow Kaggle model and three fix candidates, you may need to raise it, because the fixer's own deadline defaults to 360 seconds.

**`PROCESSING_STALE_SECONDS`**: default `900` (15 minutes), allowed 30 to 86,400. At startup, a failure that has been `processing` for longer than this is assumed to have been interrupted by a crash, and is put back in the queue. It should comfortably exceed the time a normal run takes, so healthy work is not mistaken for stuck work.

**`MAX_RECOVERIES`**: default `3`, allowed 0 to 20. How many times startup may requeue the same failure. After that it is marked `error` with `recovery limit reached`, which stops a failure that crashes the server from crashing it again on every restart. An operator can still retry it by hand with the reprocess endpoint.

**`DATABASE_URL`**: default `sqlite:///./ci_heal.db`. Where data is stored, written as a SQLAlchemy URL. The default is a SQLite file in the folder the server is started from. **Before live** on a hosting platform with temporary disk, use a hosted PostgreSQL URL instead; note that `pyproject.toml` lists no PostgreSQL driver, so one must be installed as well. [Chapter 2, section 2.6](02-environments-and-docker.md#26-databases-per-environment) has the details.

### Decision gates

**`CLASSIFY_THRESHOLD`**: default `0.75`, allowed 0 to 1. The classifier confidence needed before the system tries a fix for a fixable category. The value 0.75 is deliberate: with three samples, a 2-to-1 vote gives about 0.667, which falls below it and escalates. The code comments say calibration statistics, not a single bad experience, should be what moves this number.

**`AGREEMENT_THRESHOLD`**: default `0.6`, allowed 0 to 1. The agreement score the candidate patches must reach before a pull request is opened. Lower values open more PRs from patches that differ more from each other; higher values demand that the patches be nearly identical. Like the classify gate, it should be tuned from calibration data.

**`FLAKY_THRESHOLD`**: default `0.75`, allowed 0 to 1. The confidence needed before a failure classified as flaky or network is retried. Below it, the failure escalates instead. Retrying is cheap and harmless, but a wrong "flaky" label could hide a real bug, which is why it still needs a confident classification.

**`MAX_RETRIES`**: default `1`, allowed 0 to 10. How many reruns may be requested for one workflow run before the system stops retrying and escalates. This is different from `CLASSIFIER_MAX_RETRIES`, which counts re-asks to the model when its reply has the wrong format. One rerun is usually enough to tell a flake from a real failure.

**`MAX_HEAL_ATTEMPTS`**: default `3`, allowed 1 to 20. How many FIX decisions may be made in a row on the same repository and branch before the system escalates instead. The count resets after a fix on that branch is verified as successful. This stops the bot from opening fix after fix when it is clearly not solving the problem.

**`CATEGORY_THRESHOLDS`**: default `{}`. A JSON object that sets a different confidence gate for specific categories, for example `{"test_failure": 0.9}` to demand more certainty before fixing test failures. It overrides the classify or flaky gate for those categories only. Unknown category names or values outside 0 to 1 stop the server at startup.

### Classifier model

**`CLASSIFIER_LLM_PROVIDER`**: default `mock`. Either `mock` (the fake model, no network) or `http` (a real OpenAI-compatible server). With `mock` and no scripted replies, every classification fails the format check and escalates. **Before live:** set it to `http` and fill in the three settings below, otherwise nothing will ever be fixed or retried.

**`CLASSIFIER_LLM_BASE_URL`**: default empty. The root address of the classifier's model server, for example the Kaggle tunnel address or `https://api.groq.com/openai/v1`. A trailing `/v1` is accepted and removed automatically, so both forms work. With `http` chosen and this empty, the server refuses to start.

**`CLASSIFIER_LLM_API_KEY`**: default empty. The secret sent as `Authorization: Bearer <key>` to the classifier's server. For the team's Kaggle server, it is whatever token that server was started with; for Groq or Gemini, it is the API key from their website. With `http` chosen and this empty, the server refuses to start.

**`CLASSIFIER_LLM_MODEL`**: default `local`. The `model` field sent in each request, which tells the server which model to use. The Kaggle server expects `local`. Groq and Gemini expect their own model names, such as `llama-3.3-70b-versatile` or `gemini-2.0-flash`, as shown in the README.

**`CLASSIFIER_LLM_TIMEOUT`**: default `60` seconds. The longest one attempt at a classifier request may take. **`CLASSIFIER_LLM_DEADLINE`** (default `180` seconds) is the total time for the whole call including retries and the waits between them. The deadline stops a run of retries from going on longer than the pipeline can afford.

**`CLASSIFIER_LLM_STREAM`**: default `true`. Whether to receive the reply in pieces as it is written. Streaming is on because the Cloudflare tunnel in front of the Kaggle server closes any request that stays silent for about two minutes; a steady flow of pieces keeps it open. **`CLASSIFIER_LLM_MAX_RETRIES`** (default `2`) is how many extra HTTP attempts follow a timeout or server error.

**`CLASSIFIER_SAMPLES`**: default `1`, allowed 1 to 5. How many times the classifier asks the same question and votes. One sample keeps normal runs cheap. Three samples give the self-consistency check described in Chapter 3, but only if `CLASSIFIER_TEMPERATURE` is above 0; otherwise all samples are identical and the server logs a warning at startup.

**`CLASSIFIER_TEMPERATURE`**: default `0.0`, allowed 0 to 2. How random the classifier's replies are. At 0 the same input always gets the same answer, which makes single-sample results stable and repeatable. Raise it, for example to 0.7, only when using several samples, so the votes can actually differ.

**`CLASSIFIER_MAX_RETRIES`**: default `2`, allowed 0 to 5. How many times the classifier asks again when a reply does not match the required JSON format. After that, the sample counts as invalid. This is about format mistakes by the model and is unrelated to `MAX_RETRIES`, which counts CI reruns.

**`CLASSIFIER_MAX_TOKENS`**: default `512`, allowed 16 to 4096. The longest reply the classifier may write, counted in model tokens. A category, a confidence, and a short reason fit easily. A model that thinks out loud in `<think>` blocks may need more room, or its JSON may be cut off.

### Fixer model

**`FIXER_LLM_PROVIDER`**, **`FIXER_LLM_BASE_URL`**, **`FIXER_LLM_API_KEY`**, and **`FIXER_LLM_MODEL`** work exactly like their classifier counterparts, but for the model that writes patches. Defaults are `mock`, empty, empty, and `local`. Having two separate roles means a small, fast hosted model can classify while the larger Kaggle model writes fixes, or both can point at the same server. **Before live:** set the provider to `http` and fill in the address and key.

**`FIXER_LLM_TIMEOUT`** (default `120` seconds) and **`FIXER_LLM_DEADLINE`** (default `360` seconds) are longer than the classifier's, because writing a patch takes the model longer than naming a category. **`FIXER_LLM_STREAM`** (default `true`) and **`FIXER_LLM_MAX_RETRIES`** (default `2`) behave like the classifier's settings with the same names.

### Fix candidates and patch limits

**`FIX_CANDIDATES`**: default `3`, allowed 1 to 5. How many patches are requested for each fix attempt. Agreement needs at least two working patches to produce a score, so with 1 the system will always escalate at stage 2. More candidates give a stronger agreement signal but take longer and cost more model time.

**`FIX_TEMPERATURE`**: default `0.7`, allowed 0 to 2. How random the fixer's replies are. It must be above 0, otherwise every candidate is the same patch, agreement is always perfect, and the check means nothing; the server logs a warning at startup if it is 0.

**`FIX_MAX_TOKENS`**: default `4096`, allowed 16 to 8192. The longest reply the fixer may write. A patch with its title and body needs much more room than a classification, which is why it is larger than `CLASSIFIER_MAX_TOKENS`.

**`FIX_MAX_EDITS`**: default `5`, allowed 1 to 20. The most SEARCH/REPLACE blocks one patch may contain. **`FIX_MAX_CHANGED_LINES`** (default `50`, allowed 1 to 2000) is the most lines one patch may add or remove. Both keep patches small enough for a human to review and stop a model from rewriting a whole file.

**`FIX_ALLOW_WORKFLOWS`**: default `false`. Whether patches may edit files under `.github/workflows/`, which control CI itself. It stays off by default because changing CI definitions is risky and needs an extra GitHub token permission. Other forbidden paths, such as `.git/` and `.env` files, stay blocked even when this is on.

---

## 5.2 The API endpoints

FastAPI also serves an interactive page at `/docs` where you can try every endpoint from the browser. The table lists every address; explanations follow below it.

| Method | Path | Who calls it | Protection |
| --- | --- | --- | --- |
| GET | `/health` | Humans, monitors | None |
| GET | `/health/ready` | Hosting platform | None |
| GET | `/health/llm` | Operator, dashboard | None |
| POST | `/webhooks/github` | GitHub | HMAC signature |
| POST | `/webhooks/n8n` | n8n | `X-N8N-Token` header |
| GET | `/failures` | Dashboard | Bearer `API_TOKEN` |
| GET | `/failures/{failure_id}` | Dashboard | Bearer `API_TOKEN` |
| POST | `/failures/{failure_id}/reprocess` | Operator, dashboard | Bearer `API_TOKEN` |
| GET | `/decisions` | Dashboard | Bearer `API_TOKEN` |
| GET | `/stats` and `/stats/summary` | Dashboard | Bearer `API_TOKEN` |
| GET | `/stats/by-category` | Dashboard | Bearer `API_TOKEN` |
| GET | `/stats/by-action` | Dashboard | Bearer `API_TOKEN` |
| GET | `/stats/by-outcome` | Dashboard | Bearer `API_TOKEN` |
| GET | `/stats/calibration` | Dashboard, evaluation | Bearer `API_TOKEN` |
| GET | `/stats/timeline` | Dashboard | Bearer `API_TOKEN` |

"Bearer `API_TOKEN`" is not checked when `APP_ENV=dev` and `API_TOKEN` is empty. That gives sixteen addresses in total, two of which (`/stats` and `/stats/summary`) return the same thing.

### Health endpoints

`GET /health` returns `status: ok`, the app name, the version, the environment, and the dry-run flag, for example `{"status":"ok","app":"ci-heal-backend","version":"0.1.0","environment":"dev","dry_run":true}`. `GET /health/ready` returns `200` with `{"status":"ok","database":"ok"}` when the database answers, and `503` when it does not, which is what hosting platforms watch. `GET /health/llm` always returns `200` and says `ok` or `error` for each of the two models, so a model outage is visible without the server being marked broken.

### Webhook endpoints

`POST /webhooks/github` receives GitHub's `workflow_run` events. It returns `200` for GitHub's `ping`, `202` with a short status label for everything else it accepts or ignores, `401` for a bad or missing signature, `403` for a repository not on the allowlist, and `413` for an oversized body. `POST /webhooks/n8n` receives the n8n workflow's small JSON body and returns `202`, or `401` for a wrong token, `403` for a disallowed repository, `422` for a malformed body, and `502 github_unavailable` when the run cannot be fetched from GitHub.

### Failure and decision endpoints

`GET /failures` returns a page of failures, newest first, filterable by `repo` and `processing_status`, with `limit` (1 to 200) and `offset` for paging. `GET /failures/{failure_id}` returns one failure with all its classifications, fix candidates, and decisions, or `404`. `POST /failures/{failure_id}/reprocess` puts a failure back in the queue and returns `202 queued`; it returns `409` if the failure is currently processing, or if it is done and `force=true` was not given. `GET /decisions` returns a page of decisions, newest first, filterable by `action`, `category`, and `outcome`.

### Stats endpoints

All stats endpoints accept the filters `repo`, `since` (inclusive), `until` (exclusive), and `include_dry_run` (default false). Only resolved decisions are counted: `pending` and `error` outcomes are left out. A **success** means a FIX with outcome `verified_success`, or a RETRY with outcome `retry_success`. The summary gives totals, successes, success rate, the number of labelled failures, and accuracy. The grouped endpoints give the same counts split by category, action, or outcome. The timeline gives counts per UTC day. Calibration is explained next.

### What calibration means

**Calibration** asks a simple question: when the system says it is 80% sure, is it right about 80% of the time? `GET /stats/calibration` puts decisions into bins by confidence (0.0–0.1, 0.1–0.2, and so on, with 10 bins by default and 2 to 20 allowed), and for each bin compares the average confidence with the real success rate. A weather forecaster is well calibrated if it rains on about 70% of the days they said "70% chance of rain". The same comparison is made for the agreement score.

**ECE**, the expected calibration error, sums up the whole chart in one number: the average gap between confidence and real success across all bins, weighted by how many decisions each bin holds. Zero means perfectly calibrated. When failures have a `ground_truth_category`, the endpoint also reports how often the predicted category was right (accuracy), precision and recall per category, and a **confusion matrix**, a 5-by-5 table counting each pair of predicted and true category.

---

## 5.3 The database

There are four tables. Each failure can have many classifications, many fix candidates, and many decisions.

```text
failures (1) ──< classifications (many)
          (1) ──< fix_candidates  (many)
          (1) ──< decisions       (many)
```

Each child row has a `failure_id` column pointing at `failures.id`, which is a foreign key. Deleting a failure automatically deletes all its children (`ondelete="CASCADE"`), so no orphan rows are left behind.

### `failures`: one row per failed run attempt

Each row is one failed workflow run attempt. The combination of `repo`, `run_id`, and `run_attempt` is unique, so if GitHub delivers the same webhook twice, the second delivery cannot create a second row. A rerun of the same workflow has the same `run_id` but a higher `run_attempt`, so it does get its own row if it fails again.

The columns describe **which run** (`repo`, `run_id`, `run_attempt`, `branch`, `commit_sha`, `actor`, `workflow_name`, `failure_kind_hint`, and `source`, which is `github` or `n8n`), **where the job stands** (`processing_status`, `processing_error`, `processing_started_at`, `processing_finished_at`, and `recovery_count`), and **what the model saw** (`redacted_log_excerpt`, up to 4000 characters, and `redaction_count`). `ground_truth_category` holds the correct answer and is set only for evaluation rows; live webhooks leave it empty. `created_at` records when the row was inserted.

The column `attempt` stores the heal-attempt number n8n sends. Unclear from the code: why it is stored, because nothing reads it for decisions. The decision engine's `attempt` input comes from counting FIX decisions in the database (`count_fix_attempts`), not from this column.

### `classifications`: one row per classifier verdict

Each row is one verdict from the classifier for a failure, usually one per processing run, so a reprocessed failure can have several. It stores the final `category` and `confidence`, the model's `reasoning`, the `model` name, the `raw_output`, a JSON list of every individual `samples` entry (category, confidence, raw reply, and whether it parsed), the number of `parse_attempts`, the `latency_ms` the call took, and `created_at`.

### `fix_candidates`: one row per proposed patch

Each row is one patch the fixer proposed, so a fix attempt with `FIX_CANDIDATES=3` creates three rows. It stores the `candidate_index`, the `file_path`, the redacted `edits`, whether it `applied_ok`, the `error` code if not (an `ApplyError` value, or `invalid_json`, `invalid_schema`, or `llm_error`), the redacted `diff`, and `created_at`. Looking at these rows shows exactly why a fix attempt did or did not lead to a pull request.

### `decisions`: one row per action chosen

Each row is one action the system chose for a failure: `retry`, `fix`, or `escalate`. It stores the `action`, the `category`, the `classifier_confidence`, the `agreement_score` (for fixes), the `reason` code from the engine (or `pipeline_error` after a crash), a JSON `thresholds_snapshot` of the gates used, and whether it was a `dry_run`. `repo` and `run_id` are copied here so a later webhook can find the decision quickly.

Two columns exist only for matching later events: `expected_run_attempt`, set for a RETRY to the attempt number the rerun will have, and `fix_branch`, set for a FIX to its `ai-fix-*` branch. The result of the action is stored as `pr_url`, `issue_url`, or `rerun_requested`. `outcome` starts as `pending` and later becomes `retry_success`, `retry_failure`, `verified_success`, or `verified_failure`. `created_at` and `resolved_at` record when it was decided and when the outcome arrived.

Unclear from the code: the outcome values `escalated` and `error` exist in `DecisionOutcome`, and the statistics count `escalated` as resolved, but no code in `app/` ever sets either one. So ESCALATE decisions stay `pending` forever and are not counted in the statistics today.

### How a later run finds its decision

When a rerun finishes, the webhook looks for a pending RETRY with the same `repo` and `run_id` whose `expected_run_attempt` equals the new run's `run_attempt`. When a run on an `ai-fix-*` branch finishes, it looks for a pending FIX with the same `repo` and `fix_branch`. These two lookups are the only link between an action and its result, which is why those columns are required for those actions.

### No migrations

There is no migration tool such as Alembic. At startup `init_db` creates tables that are missing, but never changes a table that already exists. After any change to `app/db/models.py`, stop the server and delete `ci_heal.db` (or use a fresh PostgreSQL database), otherwise the old table shape stays and the new code may fail when it uses a new column.

---

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 4, Code tour](04-code-tour.md) · Next: [Chapter 6, Status and running](06-status-and-running.md)
