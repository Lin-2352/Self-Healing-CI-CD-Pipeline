## ci-heal-backend

### Purpose and difference from the n8n healer

`ci-heal-backend` is a FastAPI service that acts as the second healer in this project. It watches for failed GitHub Actions runs, asks a language model what kind of failure it was, and then does one of three things: ask GitHub to re-run the failed jobs, open a pull request with a small fix, or open an issue for a person to read. It starts in a safe mode. `DRY_RUN` is true by default, so nothing is written to GitHub until the operator sets `DRY_RUN=false` (source: app/config.py).

The first healer is the n8n workflow described in an earlier chapter. It is a chain of workflow nodes that prompts the model and acts on the answer. The backend does the same job as ordinary code with a database behind it. Three differences matter in practice.

- The decision is made by a pure function, `decide` in `app/core/decision_engine.py`, with named thresholds. The model only supplies a category and a confidence. It never chooses the action.
- Every failure, classification, fix candidate and decision is stored in a database, so attempt budgets, outcomes and calibration statistics can be read back later.
- A fix is generated several times and the candidates are compared with each other. The pull request is only opened when they agree closely enough.

The log trimming rules are a direct port of the `trimCiLog` function from the n8n "Build AI Prompt" node, so both healers show the model the same kind of log excerpt (source: app/core/log_trim.py). The two model roles, classifier and fixer, each have their own URL, key and model name, and both can point at the self-hosted Qwen3.8-27B endpoint (source: app/config.py).

### Request flow

A failure travels through the service in the order below. The numbers refer to the code in `app/api/webhooks.py` and `app/services/orchestrator.py`.

1. **Webhook.** GitHub posts a `workflow_run` event to `POST /webhooks/github`. n8n can instead post a small JSON body (run id, repo, branch, commit) to `POST /webhooks/n8n`, and the backend then loads the real run from the GitHub API. Both routes reduce the delivery to one internal `IncomingRun` object.
2. **Authentication.** The GitHub route checks the `X-Hub-Signature-256` HMAC over the raw body. The n8n route compares the `X-N8N-Token` header with `N8N_SHARED_TOKEN`. A wrong value is a 401. A `ping` event returns 200.
3. **Intake rules.** Only a completed run with conclusion `failure` or `success` is acted on, and everything else gets 202 with status `ignored`. The repository must be in `ALLOWED_REPOS`, and an empty list allows nothing, otherwise the answer is 403. A run on a branch that starts with `ai-fix-` is never treated as new work. It is only used to verify an earlier fix. A run whose actor is the token owner (`GITHUB_BOT_LOGIN`) is ignored, so the bot cannot trigger itself. The body is limited to `WEBHOOK_MAX_BYTES`, 1,000,000 by default (source: app/api/webhooks.py, app/config.py).
4. **Claim.** The failure row is unique on repo, run id and run attempt, so a repeated delivery returns `duplicate`. The row is saved as `queued`, the request returns 202, and a background task takes over. The worker moves the row to `processing` with one UPDATE that only matches a row still in `queued`, so two workers cannot take the same failure (source: app/db/repositories.py).
5. **Fetch evidence.** The worker downloads the run log zip, compares the commit with its parent to get a diff, and loads up to eight files that the diff touches. The log token is not forwarded to the redirect host that serves the zip (source: app/services/github_client.py, app/services/orchestrator.py).
6. **Redact and trim.** Secrets in the log, diff and files are replaced with `[REDACTED:<KIND>]` placeholders first. Only then is the log cut to the failure region: from the last `##[endgroup]` before the first `##[error]` line, at most 40 lines and 3000 characters. Redacting first matters because a secret outside the kept region would otherwise escape the count (source: app/services/secret_redactor.py, app/core/log_trim.py).
7. **Classify.** The classifier role returns one of four categories with a confidence. The result is stored as a classification row.
8. **Decision stage 1.** The engine sees the category, the confidence, the retry count and the attempt count. It answers retry, escalate, or candidate fix.
9. **Fixer.** For a candidate fix, the fixer role is asked for `FIX_CANDIDATES` patches (3 by default), one after another, at temperature 0.7. Each reply must be a JSON object with a file path and SEARCH/REPLACE edits. Each patch is applied to the original file text in memory and checked by the guards described below. A call that fails or a patch that does not apply becomes an invalid candidate and does not stop the others (source: app/services/fix_generator.py).
10. **Agreement and stage 2.** Candidates are scored against each other. The engine runs again with the agreement score and answers fix or escalate.
11. **Act.** A fix opens a pull request. An escalation opens an issue. A retry asks GitHub to re-run the failed jobs. In dry-run each of these is only logged and a placeholder URL is stored.
12. **Verify later.** When a later `workflow_run` arrives, the backend closes the open decision. A re-run of a retried run closes the retry as `retry_success` or `retry_failure`. A run on the `ai-fix-*` branch closes the fix as `verified_success` or `verified_failure`. The whole pipeline for one failure is cut off after `PIPELINE_TIMEOUT_SECONDS`, 180 by default. If it fails, the row is marked `error` and the service tries to open an escalation issue (source: app/services/orchestrator.py).

The agreement score is the mean pairwise similarity of the candidate diffs. For each pair the score is the average of a token Jaccard value and the `difflib` ratio on the diff with headers removed. An invalid candidate has similarity 0 with every other one and still counts in the mean, so a bad sample pulls the score down. Two valid candidates that edit different files also score 0. If fewer than two candidates applied, there is no score and the decision is escalate. The patch that is published is the medoid, meaning the candidate with the highest mean similarity to the others (source: app/core/agreement.py).

### Failure categories

The classifier must answer with exactly one of four labels. A fifth label, `unknown`, exists only for the case where the reply could not be parsed or the samples tied. It always escalates (source: app/core/enums.py, app/core/decision_engine.py).

| Category | Action when confident |
|---|---|
| `dependency_error` | generate patches, then fix or escalate |
| `configuration_error` | generate patches, then fix or escalate |
| `test_failure` | generate patches, then fix or escalate |
| `flaky_or_network` | re-run the failed jobs, no code change |

### Thresholds and attempt budgets

The defaults below are read from `app/config.py`. Each one can be changed with an environment variable of the same name in capitals.

| Setting | Default | What it controls |
|---|---|---|
| `CLASSIFY_THRESHOLD` | 0.75 | minimum confidence to go on to a fix |
| `AGREEMENT_THRESHOLD` | 0.6 | minimum agreement to open a pull request |
| `FLAKY_THRESHOLD` | 0.75 | minimum confidence to re-run a flaky failure |
| `MAX_RETRIES` | 1 | re-runs allowed for one run id |
| `MAX_HEAL_ATTEMPTS` | 3 | fix decisions allowed on one branch before escalating |
| `CATEGORY_THRESHOLDS` | `{}` | per-category confidence gates that replace the first and third rows |
| `CLASSIFIER_SAMPLES` | 1 | classifier votes |
| `FIX_CANDIDATES` | 3 | patches requested |
| `MAX_RECOVERIES` | 3 | startup requeues per failure |
| `PROCESSING_STALE_SECONDS` | 900 | age after which a `processing` row counts as abandoned at startup |

All comparisons are inclusive. The comment in the config explains why 0.75 was picked: with three classifier samples a two-to-one vote gives about 0.667, which falls under the gate and escalates. With one sample, the model's own confidence is used. With several samples, the stored confidence is the smaller of the mean self-reported confidence and the share of samples that voted for the winning category (source: app/config.py, app/services/classifier.py).

The decision engine checks its rules in a fixed order. Invalid input and `unknown` escalate first. Then, if the attempt count has reached `MAX_HEAL_ATTEMPTS`, it escalates. Then the flaky rule applies, and then the fixable rule. The two counters come from the database. `retries_done` is the number of RETRY decisions stored for the same repository and run id. `attempt` is the number of FIX decisions on the same repository and failing branch since the last `verified_success` on that branch (source: app/db/repositories.py).

### Safety rules

- **Branch names.** The backend never uses the name the model proposes. It builds `ai-fix-<category>-<sha7>` itself and creates it from the failing commit. The GitHub client refuses any ref write whose branch does not start with `ai-fix-`, or is `main`, `master` or the configured default branch, or contains a slash, `..` or whitespace. The refusal is an exception, not an assert, so it survives `python -O`. A failure that is already on an `ai-fix-*` branch is refused with `fix_loop`.
- **Protected paths.** A patch may only touch a file that was in the context shown to the model. Absolute paths, `..`, `.git/`, `.env*` and `.github/workflows/` are rejected. The workflow ban can be lifted with `FIX_ALLOW_WORKFLOWS`, the others cannot.
- **Size limits.** At most `FIX_MAX_EDITS` blocks (5) and `FIX_MAX_CHANGED_LINES` changed lines (50). Each SEARCH text must match the file exactly once.
- **Syntax check.** For a `.py` file the patched text must parse with `ast.parse`. The code is not run.
- **Weakened tests.** A patch is rejected if it adds a skip or xfail marker, removes a test function, or removes more assert lines than it adds. Changing an assert in place is allowed.
- **Redaction.** Text is redacted before it is stored, before it is sent to a model, and again before the pull request or issue text goes to GitHub. A replacement that contains a secret is rejected. The prompt builder refuses content that is not of the `RedactedText` type.
- **Prompt injection.** Logs, diffs and files are placed between `<<<UNTRUSTED_...>>>` markers, and the system prompt tells the model to treat them as evidence only. The reply schema rejects extra keys (source: app/llm/prompts.py, app/schemas/fix.py).
- **DRY_RUN.** Every write method of the GitHub client returns before touching the network when dry-run is on. Reads still run. A dry-run decision is flagged in the database and never receives a real CI outcome (source: app/services/github_client.py, .env.example).

(source for this list: app/core/patch_apply.py, app/services/github_client.py, app/services/secret_redactor.py)

### The LLM contract and the HTTP client

`ci-heal-backend/docs/LLM_CONTRACT.md` describes what any server must offer. The client in `app/llm/http_client.py` posts to `{root}/v1/chat/completions` with a bearer token. A base URL that already ends in `/v1` is accepted. Readiness is `GET /v1/models`, and any status below 400 counts as up.

The classifier must reply with one JSON object holding `category`, `confidence` (0 to 1) and `reasoning`, with no other keys. The fixer must reply with `branch_name`, `file_path`, `edits`, `pr_title` and `pr_body`. A reply may be wrapped in a code fence or a think block, and the client strips both. If the classifier reply does not fit the schema it is asked again, up to `CLASSIFIER_MAX_RETRIES` (2). After that the verdict is `unknown` with confidence 0.

Streaming is on by default. The Cloudflare tunnel in front of Kaggle closes a request that stays quiet for about two minutes, so a long completion has to stream. Calls on one client are serialised with a lock, because the Kaggle server runs with `--parallel 1`. Only HTTP 5xx (including 524) and timeouts are retried, within a total deadline per call: `CLASSIFIER_LLM_DEADLINE` 180 s and `FIXER_LLM_DEADLINE` 360 s, with per-attempt timeouts of 60 s and 120 s. A 4xx answer is final.

The contract says the client may send `chat_template_kwargs: {"enable_thinking": true}` on a classifier re-ask. The client does send that field when asked, but reading `_one_sample` in `app/services/classifier.py` shows that nothing passes the flag today, so no call turns thinking on. The teammate notes in `docs/backend/06-status-and-running.md` say the same.

### Database tables

The backend uses SQLAlchemy and creates four tables at startup. SQLite is the default (`sqlite:///./ci_heal.db`) with foreign keys, WAL mode and a 5 second busy timeout. A hosted PostgreSQL URL also works (source: app/db/models.py, app/db/session.py).

| Table | Holds |
|---|---|
| `failures` | one row per run attempt: repo, run id, attempt, branch, commit, actor, status (`queued`, `processing`, `done`, `error`), redacted log excerpt, redaction count, recovery count |
| `classifications` | category, confidence, reasoning, model name, raw output, every sample, parse attempts, latency |
| `fix_candidates` | candidate index, file path, edits, whether it applied, error code, diff |
| `decisions` | action, category, confidence, agreement, reason code, a snapshot of the thresholds, `dry_run`, expected run attempt, fix branch, PR and issue URLs, outcome |

The thresholds snapshot is stored with each decision, so a later change of configuration does not rewrite the reasons for an old decision. The read API (`/failures`, `/decisions`, `/stats/...`) needs `API_TOKEN` outside dev.

### Wiring to the Kaggle endpoint

`kaggle/tools/backend_env.py` points both roles at the live endpoint. It reads the tunnel URL from the file `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt` and the key from `kaggle/endpoint/deploy_src.py`, then rewrites only the `PROVIDER`, `BASE_URL`, `API_KEY` and `MODEL` lines of both roles in `ci-heal-backend/.env`. Every other line is kept. With `--check` it also calls `GET /v1/models`. The key is never printed, and `.env` is ignored by git.

Qwen3.8's chat template thinks unless told not to. The backend only ever sends `enable_thinking` to switch thinking on, so before the change every call would have thought first, and thinking tokens count against the 512 token classifier limit. The endpoint now starts llama-server with thinking off by default, and a boot check asks the same question twice to confirm that the default is off and that `enable_thinking: true` still turns it on (source: docs/results/BACKEND_ON_KAGGLE.md).

### Measured replay on the endpoint

On 2026-11-15 the six fixtures in `ci-heal-backend/fixtures/` were replayed through the real pipeline with `scripts/replay_failure.py --llm http`. GitHub was mocked and `DRY_RUN` was on. The endpoint was the unsloth UD-Q4_K_XL build on two T4 GPUs at 26.8 tokens per second (source: docs/results/BACKEND_ON_KAGGLE.md).

| Fixture | Classified (confidence) | Decision | Candidates applied | Time |
|---|---|---|---|---|
| configuration | configuration_error (0.95) | fix | 3 of 3 | 28 s |
| dependency | dependency_error (0.98) | escalate, candidates disagreed | 3 of 3 | 30 s |
| flaky | flaky_or_network (0.95) | re-run the job | not asked for | 8 s |
| prompt_injection | test_failure (0.98) | fix, agreement 1.0 | 3 of 3 | 26 s |
| secrets | dependency_error (0.98), 2 secrets redacted | escalate, candidates disagreed | 3 of 3 | 40 s |
| test_failure | test_failure (0.98) | fix | 3 of 3 | 29 s |

All six were classified correctly on the first sample and all 15 candidates that were asked for applied to the file. Two cases escalated because the three dependency patches differed and agreement fell below 0.6. That is the gate doing its job, but the replay cannot say which patch would have fixed the build. The limits are plain: one run per fixture, six fixtures written for the backend's own tests, no pull request opened, and no CI run on a fix. The replay output also does not show whether any candidate followed the injected text in the prompt-injection fixture.

### Tests

The tests run offline with a mock model and a mocked GitHub client. At the commit before the audit fixes described below, `python -m pytest -q` in `ci-heal-backend` collected and passed 157 tests in 16 test modules (run on an export of that commit, in about 17 seconds). The modules cover the decision engine, agreement scoring, patch application, secret redaction, log trimming, the classifier and fix generator, the HTTP LLM client, the GitHub client, the repositories and statistics, the webhooks, health endpoints, settings, and a test that `.env.example` keeps the same keys as `Settings`. The audit fixes, committed as `4bfd37d`, added `tests/test_security.py` and new cases in `tests/test_webhooks.py`; after them 205 tests pass in 17 test modules.

### Known limitations from an audit

A read-only security review of the code produced 21 findings. Each one below was checked again against the code for this chapter, and all 21 were confirmed by reading it. None was reproduced by running a failing request, except the four that were then fixed test-first (items 1, 8, 9 and 10, marked "fixed" in the table). The wording of each row says what the code did when it was reviewed, not what was observed live.

| No. | Finding, as read from the code when it was reviewed | Where |
|---|---|---|
| 1 (fixed) | The run's origin was not checked. Neither `_from_github` nor `_from_api` reads the head repository or the triggering event, so a workflow run from a fork pull request can reach the bot. | app/api/webhooks.py |
| 2 | The worker flushes a write on the shared SQLite file and then awaits model and GitHub calls before it commits. Webhook intake is an `async def` that makes synchronous database calls on the event loop. A second delivery can wait on the lock in the loop thread, blocking the worker that holds it, until the 5 second busy timeout gives "database is locked". | app/services/orchestrator.py, app/api/webhooks.py, app/db/session.py |
| 3 | A failure can stay in `processing` after a restart. Startup recovery skips a `processing` row that started less than 900 seconds earlier, and it runs only at startup. A cancelled worker task is not caught by `except Exception` in `Orchestrator.run`, so the row is never moved on. | app/db/repositories.py, app/main.py |
| 4 | The worker commits once at the end. If the run times out or raises after a pull request was opened on GitHub, `_fail` rolls back and the decision row holding the PR URL is lost. | app/services/orchestrator.py |
| 5 | A fix is closed by the first finished run found on its branch. `find_open_fix` matches on repo and branch only, and neither the commit nor the workflow is compared. | app/api/webhooks.py, app/db/repositories.py |
| 6 | Attempt budgets count rows, not outcomes. `count_fix_attempts` and `count_retries` count every FIX or RETRY row, including dry-run rows and rows whose GitHub action failed. | app/db/repositories.py |
| 7 | `find_open_retry` has no `dry_run` filter, so a dry-run retry row can be closed by a real run. | app/db/repositories.py |
| 8 (fixed) | Defaults failed open outside Docker. `APP_ENV` defaults to `dev`, where an empty webhook secret accepts unsigned deliveries and an empty `API_TOKEN` leaves the read API open. Nothing rejects placeholder secrets, and a test loads `.env.example` values such as `replace-me` into `Settings`. The Dockerfile sets `APP_ENV=prod`, which closes both. | app/config.py, app/api/webhooks.py, app/deps.py, tests/test_env_example.py |
| 9 (fixed) | A null conclusion was treated as failure on the n8n route. `_from_api` replaces a non-string conclusion with `failure`. The GitHub route ignores it. | app/api/webhooks.py |
| 10 (fixed) | A non-ASCII header made `compare_digest` raise. The length check passes for an equal-length value, then `hmac.compare_digest` raises `TypeError` on a non-ASCII string, giving a 500 instead of a 401. | app/security.py |
| 11 | The request body is read in full by `request.body()` before the `WEBHOOK_MAX_BYTES` check. | app/api/webhooks.py |
| 12 | Only `httpx.TimeoutException` is caught, in both the GitHub client and the LLM client. Other transport errors, such as a refused connection, escape the retry loop. | app/services/github_client.py, app/llm/http_client.py |
| 13 | Rate-limit waits are unbounded. The delay comes from `Retry-After` or `X-RateLimit-Reset` with no upper cap. | app/services/github_client.py |
| 14 | Non-idempotent POSTs are retried. The same loop retries 5xx and timeouts for every method, including creating a ref, a pull request, an issue and labels. | app/services/github_client.py |
| 15 | The default timeouts do not add up. The pipeline limit is 180 s, while the classifier deadline alone is 180 s and the fixer deadline is 360 s, before any GitHub reads. | app/config.py |
| 16 | Zip size caps are applied after decompression. `_unzip` calls `archive.read` on a whole member before it compares with the cap. | app/services/github_client.py |
| 17 | The issue dedup marker is `ci-heal-failure-<id>`, a database row id, searched without an open-only filter. After a database reset, a new failure can match an old issue. | app/services/github_text.py, app/services/github_client.py |
| 18 | The published file path is the raw model string. Patch checking normalises the path, but the pull request service commits `chosen.file_path` as the model wrote it. | app/services/orchestrator.py, app/services/fix_generator.py |
| 19 | Non-UTF-8 files are corrupted on commit. `get_file` decodes with `errors="replace"`, and the patched text is written back as UTF-8. | app/services/github_client.py |
| 20 | Logs lack tracebacks and delivery ids. Failures are logged as the exception class name only, there is no `exc_info`, and the `X-GitHub-Delivery` header is never read. | app/services/orchestrator.py, app/api/webhooks.py |
| 21 | `GET /health/llm` has no authentication dependency. It only reports `ok` or `error` for each role. | app/api/health.py |

Items 1, 8, 9 and 10 are fixed in commit `4bfd37d`, each with a test that was run before the fix and failed for the stated reason. The fixes: a run from a fork is ignored on both routes (1); outside `dev` the app refuses to start when a secret is empty or `replace-me` (8); the n8n route needs a completed run with a conclusion, otherwise it answers 409 (9); and signatures and tokens are compared as UTF-8 bytes, so a non-ASCII header gives 401 instead of an error (10). After the fixes 205 tests pass. A status table in `docs/results/BACKEND_AUDIT.md` records the outcome. The other 17 items are open.
