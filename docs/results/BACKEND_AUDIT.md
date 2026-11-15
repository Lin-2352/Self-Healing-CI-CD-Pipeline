# Audit of ci-heal-backend (2026-11-15)

A read-only review went through the `ci-heal-backend` code looking for security and reliability defects: webhook authentication, secret handling, duplicate deliveries, input validation, error paths, timeouts, concurrency and places where what the service reports differs from what happened. It read the code and did not run it. Each finding below was checked against the cited lines by reading; none was reproduced with a failing request, except the four that were then fixed test-first.

Four findings were fixed in the same change (commit `4bfd37d`). The other 17 are open and are listed with their triggers so that a later change can pick them up.

## Fixed

| # | finding | where | fix | test |
|---|---|---|---|---|
| 1 | A `workflow_run` from a fork pull request could reach the bot: the code never compared the run's head repository with the base repository. With `DRY_RUN=false` that lets an outside contributor steer the model with their own log and have the bot create a branch and a pull request in the base repository. | `app/api/webhooks.py` (`_from_github`, `_intake`) | `_is_fork_run` ignores a run whose `head_repository.full_name` differs from the repository (a null head repository counts as different) on both routes | `tests/test_webhooks.py::test_fork_pull_request_run_is_ignored` |
| 8 | Defaults failed open: with `APP_ENV` unset, and the example secrets copied, the webhook and operator API accepted known values. | `app/config.py`, `app/main.py`, `.env.example` | outside `dev` the app refuses to start if `GITHUB_WEBHOOK_SECRET`, `N8N_SHARED_TOKEN` or `API_TOKEN` is empty or `replace-me` | `tests/test_security.py` |
| 9 | On the n8n route, a run with no conclusion yet (still in progress) was treated as a failure and healed. | `app/api/webhooks.py` (`_from_api`) | the handler needs `status == "completed"` and a non-empty `conclusion`, otherwise it answers 409 | `tests/test_webhooks.py::test_n8n_unfinished_run_is_not_healed` |
| 10 | `hmac.compare_digest` raised `TypeError` for a non-ASCII header of the right length, giving a 500 instead of 401, and the status code leaked whether the length was right. | `app/security.py` | both values are compared as UTF-8 bytes and the function never raises | `tests/test_security.py`, `tests/test_webhooks.py` (non-ASCII signature, token and bearer header) |

Before the change `pytest` collected 157 tests; after it, 205 pass and `ruff check .` and `ruff format --check .` are clean. Each new test was run before its fix and failed for the stated reason, for example `TypeError: comparing strings with non-ASCII characters is not supported` for finding 10.

## Open

| # | finding | where | trigger | note |
|---|---|---|---|---|
| 2 | The SQLite write lock is held across LLM and GitHub awaits, while webhook intake runs synchronous database calls on the event loop. A second delivery can fail with `database is locked`, and GitHub does not redeliver. | `app/services/orchestrator.py` (flush before the awaits), `app/api/webhooks.py` | failure A is mid-pipeline, then failure B's webhook arrives | affects the default `DATABASE_URL`; Postgres avoids it |
| 3 | A failure stays in `processing` after a restart in the middle of a pipeline. Recovery runs only at startup and skips rows started within 900 s. | `app/main.py`, `app/db/repositories.py` | restart about 60 s into a pipeline | redelivery then returns "duplicate" |
| 4 | A rollback after a GitHub write loses the PR or issue record, and the escalation opens a second issue. | `app/services/orchestrator.py` | timeout after `create_pull` | `MAX_HEAL_ATTEMPTS` can be bypassed |
| 5 | A fix is marked verified by whichever run on its branch finishes first; branch creation at the failing commit can fire a `push` run that fails first. | `app/api/webhooks.py`, `app/services/pr_service.py` | two workflows on the repository | dashboard shows a status that differs from real CI |
| 6 | The retry and attempt budgets count rows, not outcomes, and ignore `dry_run`. | `app/db/repositories.py` | run in dry-run, then switch `DRY_RUN=false` | budget already used up |
| 7 | `find_open_retry` ignores `dry_run` and `rerun_requested`, so a dry-run retry decision can be resolved by a human's rerun. | `app/db/repositories.py` | dry-run retry, manual rerun succeeds | |
| 11 | The request body is read in full before the size cap and before authentication. | `app/api/webhooks.py` | unauthenticated multi-GB POST | check `Content-Length` and stream with a limit |
| 12 | Only `httpx` timeouts are caught; a connection error or reset escapes. | `app/services/github_client.py`, `app/llm/http_client.py` | tunnel down during a pipeline | all candidates fail, failure ends as ERROR plus an issue |
| 13 | Rate-limit sleeps are unbounded. | `app/services/github_client.py` | 403 with reset in 50 minutes | n8n request hangs |
| 14 | Non-idempotent POST and PUT calls are retried. | `app/services/github_client.py` | timeout after GitHub created an issue | duplicate issue |
| 15 | Default timeouts do not add up: the pipeline limit is 180 s, a classify call may take up to 180 s and each fixer call up to 360 s. | `app/config.py` | endpoint cold start of about 223 s | reported as ERROR |
| 16 | Size caps apply after decompression of a log zip member. | `app/services/github_client.py` | highly compressible log | memory use |
| 17 | Issue dedup uses a marker based on an autoincrement id and no state filter. | `app/services/github_text.py` | database reset | an old issue is reused |
| 18 | The published file path is the raw model string, not the normalised one. | `app/services/fix_generator.py`, `app/services/pr_service.py` | model returns `" app.py"` | a new file with a leading space is committed |
| 19 | A non-UTF-8 file is decoded with replacement characters and committed whole. | `app/services/github_client.py` | latin-1 source file | file corrupted |
| 20 | Authentication failures are not logged, logs carry no traceback and no delivery id. | `app/api/webhooks.py`, `app/services/orchestrator.py` | any failure | harder diagnosis |
| 21 | `/health/llm` is unauthenticated and calls the provider each time. | `app/api/health.py` | repeated GETs | uses the provider's rate limit |

Four smaller points were noted and not graded: a model that copies a `[REDACTED:...]` placeholder into a replacement writes it into the repository; only `.github/workflows`, `.git` and `.env*` are blocked from editing, so a build entry point such as `package.json` or a `Makefile` can be edited; the model's `reasoning` text goes into issue bodies redacted for secrets but not for mentions; the `actor` and `failure_kind` fields of the n8n payload have no length limit.

## Not confirmed

The review could not establish these from the code alone: whether GitHub accepts a fork pull request's commit for `git/refs` on the base repository (finding 1 closes the path either way); the exact timing in findings 2 and 5; how GitHub search treats the marker in finding 17; whether uvicorn passes non-ASCII header bytes through as text (the fix does not depend on it).

## What the audit found done well

The webhook signature is checked with HMAC-SHA256 and a constant-time comparison on the raw body before the JSON is parsed. The repository allowlist denies by default. Duplicate deliveries are absorbed by a unique constraint and a conditional status update. Ref writes are refused unless the branch starts with `ai-fix-`, and the model's branch name is never used. `DRY_RUN` is enforced inside the GitHub client for every write. Patches must match exactly once and pass a syntax check and a weakened-test check, and the model only ever sees redacted text.
