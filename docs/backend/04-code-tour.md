# Chapter 4: A tour of the code, file by file

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 3, How it works](03-how-it-works.md) · Next: [Chapter 5, Reference](05-reference.md)

This chapter walks through every source file in `ci-heal-backend/app/` and `ci-heal-backend/scripts/`, and gives one paragraph to each test file. You do not need to read it in one go. Use it like a map: when you open a file in the editor and wonder what it is for, look it up here.

Each function or class is described in plain words: what it does, what goes in, what comes out, why it exists, and who calls it. Small helper functions that only serve one bigger function are grouped together, so the important parts stand out.

---

## 4.1 The folder structure

```text
ci-heal-backend/
├── .env.example            Template of every setting, with safe placeholder values
├── .gitignore              Keeps .env, caches, venvs, and *.db files out of git
├── .python-version         Says which Python to use (3.12)
├── Dockerfile              Recipe to build a container image of the server
├── README.md               Short reference: architecture, settings, endpoints, deployment
├── pyproject.toml          Package name, dependencies, pytest and ruff settings
├── app/                    The application itself
│   ├── __init__.py         Package marker and __version__ = "0.1.0"
│   ├── main.py             Builds the FastAPI app, startup and shutdown
│   ├── config.py           All settings, read from environment variables
│   ├── deps.py             Small helpers FastAPI injects into routes
│   ├── security.py         Constant-time comparison and GitHub signature building
│   ├── api/                HTTP routes (endpoints)
│   ├── core/               Pure rules with no database and no network
│   ├── services/           Work that talks to GitHub or a model
│   ├── llm/                Model clients and prompts
│   ├── db/                 Database tables, sessions, queries, statistics
│   └── schemas/            Pydantic shapes for input and output
├── docs/
│   └── LLM_CONTRACT.md     What a model server must accept and return
├── fixtures/               Example CI logs with known answers
├── scripts/
│   └── replay_failure.py   Runs one fixture through the pipeline offline
└── tests/                  Automated tests (pytest)
```

Some folders exist on disk but are not source code, so they are skipped: `.venv` (the virtual environment), `.pytest_cache` and `.ruff_cache` (tool caches), `__pycache__` (compiled Python files), and `ci_heal_backend.egg-info` (created by `pip install -e`).

### Why the code is split into layers

The folders under `app/` form layers, and each layer has one kind of job. This is a common way to organise any backend, whatever the language. The rule of thumb is: the outer layers (`api`) talk to the outside world over HTTP, the middle layers (`services`) do the work, and the inner layer (`core`) holds pure rules. Storage (`db`) and data shapes (`schemas`) are used by everyone. Keeping jobs separate means you can change how GitHub is called without touching the decision rules, and test the rules without a network.

### `app/api`: the doors into the application

Each file here defines a **router**, which is a group of endpoints. A route function reads the incoming request, calls a repository or service to do the real work, and returns data that FastAPI turns into JSON. Routes deliberately hold no business rules, so the same rules can be reused by scripts and tests. They rely on `app/deps.py` to receive the database session, the settings, and the token check.

### `app/core`: the pure rules

"Pure" means these functions never touch the database, the network, the clock, or the settings. You give them values and they give back values, and the same input always produces the same output. That makes them easy to understand and easy to test with simple tables of examples. The decision engine, the patch applier, the agreement scorer, the log trimmer, and the `RedactedText` type all live here.

### `app/services`: the workers

These modules do real work with the outside world: they call GitHub and the AI models. The orchestrator ties all the other services together to process one failure. Services ask `core` for decisions and use `db/repositories.py` for storage. If you want to know what happens to a failure, `app/services/orchestrator.py` is the best file to read first.

### `app/llm`: the model plug

This folder hides which AI provider is used. The rest of the code only talks to something called an `LLMClient`, and `factory.py` decides whether that is the fake model or a real one over HTTP. `prompts.py` holds the exact text sent to models, so if you want to change what the model is told, that is the only place to look.

### `app/db`: storage

This is everything to do with the database. `models.py` describes the four tables, `session.py` opens connections, `repositories.py` is the only place that reads or writes rows, and `stats.py` calculates the dashboard numbers inside SQL. Keeping all database access in one folder means a change of database (from SQLite to Postgres) touches as little code as possible.

### `app/schemas`: data shapes

These are Pydantic classes that describe the exact shape of data. Classes ending in `Create` describe what goes into the database, classes ending in `Read` describe what the API returns, and `ClassifierOutput` and `FixProposal` describe the only JSON the models are allowed to reply with. When data does not fit a shape, Pydantic rejects it with a clear error instead of letting bad data spread.

### `tests`, `scripts`, `fixtures`, and `docs`

`tests/` holds the automated checks run with pytest, which use the fake model and a fake GitHub so they need no network. `scripts/` holds command-line tools for humans; today that is only the offline demo `replay_failure.py`. `fixtures/` holds sample CI logs with known correct categories, used by both. `ci-heal-backend/docs/LLM_CONTRACT.md` is written for whoever runs the model server, and describes exactly what requests it must accept.

---

## 4.2 Top-level application files

### `app/__init__.py`

This file marks the `app` folder as a Python package, so other code can write `from app.config import ...`. It also defines `__version__ = "0.1.0"`, which the `/health` endpoint reports and FastAPI shows on the `/docs` page. It contains no functions. If you release a new version of the backend, this is where the version number changes.

### `app/main.py`

This file builds the web application and decides what happens when the server starts and stops. It is the first file Uvicorn loads, because the start command names `app.main:app`.

**`create_app(settings=None)`** builds and configures the whole FastAPI application. It sets up logging, prints warnings if the temperatures would make sampling pointless, creates the database engine, builds the two model clients and the GitHub client, stores them on `app.state` so routes can reach them, adds the CORS rules for the dashboard, and plugs in the five routers. Tests call it with their own settings to get a fresh app; normally it reads the environment through `get_settings()`.

**`_lifespan(engine, settings, factory)`** builds the startup and shutdown hook that FastAPI runs around the server's life. At startup it creates any missing tables, asks `recover_stuck` for failures that were left unfinished by a previous crash, and starts a task for each of them. At shutdown it cancels those tasks and closes the database engine. Without it, tables would never be created and interrupted work would be stuck forever.

**`app`** is a module-level variable holding the result of `create_app()`. It is the object Uvicorn serves when you run `python -m uvicorn app.main:app`. Because it is built when the module is imported, settings are read at that moment, which is why changing `.env` requires a restart.

### `app/config.py`

This file holds every setting the backend has, in one class. Its purpose is that switching providers, thresholds, or databases is always a settings change, never a code change. Secrets use `SecretStr` so they print as `**********`. [Chapter 2](02-environments-and-docker.md) explains how values are read from environment variables and `.env`.

**`Settings`** is the class with one typed attribute per setting, a default value, and allowed ranges. For example `classify_threshold` must be between 0 and 1, and `fix_candidates` between 1 and 5. pydantic-settings fills it from environment variables and `.env`, and refuses to start if any value is invalid. Tests and the replay script also build it directly with keyword arguments.

**`Settings.cors_origin_list`** and **`Settings.allowed_repo_set`** are properties that turn comma-separated text into clean lists. `CORS_ORIGINS` becomes a list of browser origins for the CORS middleware, and `ALLOWED_REPOS` becomes a set of `owner/name` strings used by the webhooks. Blank entries are dropped, and an empty `ALLOWED_REPOS` produces an empty set, which means no repository is allowed at all.

**`Settings._check_category_thresholds`** is a validator Pydantic runs automatically when settings load. It passes the raw `CATEGORY_THRESHOLDS` text to `parse_category_thresholds`, so a typo such as an unknown category name stops the server at startup with a clear message, instead of silently having no effect.

**`Settings.to_thresholds()`** copies the five decision gates and the per-category overrides into a frozen `Thresholds` object. The decision engine never reads settings itself, which keeps it pure and testable; callers build this object and pass it in. It is called by the orchestrator before each decision and by the replay script when it prints its report.

**`parse_category_thresholds(raw)`** reads a JSON object such as `{"test_failure": 0.9}` and returns a dictionary of category to number. It rejects anything that is not a JSON object, any key that is not a real category, booleans (because JSON `true` would otherwise become 1), NaN, infinity, and numbers outside 0 to 1. It exists so a mistake in this setting is caught loudly.

**`warn_repeated_classifier_samples(settings)`** and **`warn_fix_temperature(settings)`** log a warning at startup when several samples would all be identical because the temperature is 0. With identical samples, voting and agreement can never move, which silently defeats two safety checks. They only warn; startup continues. `create_app` calls both.

**`get_settings()`** builds `Settings()` once and remembers it for the rest of the process, thanks to `@lru_cache`. Every later call returns the same object. `create_app` uses it when no settings are passed in, which is the normal case when the server starts.

### `app/deps.py`

FastAPI has a feature called **dependencies**: a route lists helper functions it needs, and FastAPI calls them first and passes in the results. For example, a route that declares `Depends(get_db)` automatically receives a database session. This file holds the shared helpers.

**`get_app_settings(request)`** returns the settings the running app was created with, read from `request.app.state.settings`. Reading from the app rather than the global `get_settings()` lets each test build an app with its own settings without touching environment variables. It is used by the health routes, the webhooks, and the token check.

**`get_db(request)`** gives a route one database session for the length of the request. It uses `session_scope`, which commits the changes if the route finishes normally and rolls them back if the route raises an error, and always closes the session. Every route that reads or writes the database depends on it.

**`get_classifier_llm(request)`** and **`get_fixer_llm(request)`** return the two model clients that were built once at startup and stored on the app. Only the `/health/llm` route uses them, to ask each model whether it is reachable. The pipeline itself receives its clients through `process_failure` instead.

**`require_api_token(request, authorization)`** protects the dashboard routes. It reads the `Authorization` header, takes the part after `Bearer `, and compares it with `API_TOKEN` in constant time. A mismatch raises HTTP `401`. If `APP_ENV` is `dev` and `API_TOKEN` is empty, it lets the request through so local learning stays simple. The failures, decisions, and stats routers attach it to all their routes.

### `app/security.py`

**`secrets_equal(left, right)`** compares two secret strings safely. It returns `True` only when both are non-empty, the same length, and equal. It uses `hmac.compare_digest`, which takes the same time no matter where the strings differ, so an attacker cannot guess a token character by character by measuring response times. An empty expected value never matches, which is why a forgotten `API_TOKEN` in `prod` locks everyone out instead of letting everyone in.

**`github_signature(secret, body)`** computes the value GitHub puts in the `X-Hub-Signature-256` header: an HMAC-SHA256 of the raw request body using the shared secret, written as `sha256=<hex digits>`. The webhook compares this with the header to prove a delivery really came from GitHub and was not changed. Tests also use it to build valid signed requests.

---

## 4.3 `app/api`: the HTTP routes

### `app/api/__init__.py`

This file only marks `app/api` as a package so its modules can be imported. It contains a one-line description and no functions. All seven `__init__.py` files in this project work the same way, so they are not described again below.

### `app/api/health.py`

Three status endpoints that need no token, so monitoring tools and humans can check the server quickly.

**`HealthResponse`**, **`ReadyResponse`**, and **`LLMHealthResponse`** are Pydantic classes that describe the exact JSON each endpoint returns. Declaring them lets FastAPI check the output and show it on the `/docs` page, so dashboard developers know what fields to expect without reading the code.

**`health(settings)`** answers `GET /health` with `status: ok`, the app name, version, current `APP_ENV`, and whether dry-run is on. It does not touch the database or models, so it only proves the process is alive. It is the quickest way to confirm, on a deployed server, which environment it is in and whether it may write to GitHub.

**`ready(session)`** answers `GET /health/ready` by running the simplest possible query, `SELECT 1`, on the database. It returns `200` when the database answers and `503` when it does not. Hosting platforms call this URL to decide whether to send traffic to the server. It deliberately ignores the models, so a model outage does not take the whole server offline.

**`llm_health(classifier_llm, fixer_llm)`** answers `GET /health/llm` by calling `ping()` on both model clients and reporting `ok` or `error` for each. It always returns `200`, because a model being down is information, not a server failure. The helpers `_check_database` and `_check_llm` do the actual checks; `_check_database` logs only the type of an error, never its message, because database errors can contain the connection string and password.

### `app/api/webhooks.py`

This is where outside systems tell the backend about finished CI runs. It must answer fast, so it only checks the request, saves a row, and hands the slow work to a background task.

**`N8nPayload`** is the small JSON body the team's n8n workflow sends: `run_id`, `repo`, `branch`, `commit`, `actor`, `failure_kind`, and `attempt`. Pydantic checks it, so a malformed body gets `422`. Only `run_id` and `repo` are fully trusted; the rest is re-read from GitHub when possible.

**`IncomingRun`** is one finished run in a single, common shape, whichever webhook it came from. It holds the repository, `run_id`, `run_attempt`, branch, commit SHA, conclusion, actor, workflow name, failure hint, attempt number, and source. Having one shape means `_intake` can handle both webhooks with exactly the same code.

**`github_webhook(...)`** handles `POST /webhooks/github`. It reads the body with a size cap, checks the HMAC signature, answers a GitHub `ping` with `200`, and ignores every event except a completed `workflow_run` with conclusion `failure` or `success`. It then builds an `IncomingRun` and passes it to `_intake`. Possible answers are `200` for ping, `202` otherwise, `401` for a bad signature, and `413` for a body that is too large.

**`n8n_webhook(...)`** handles `POST /webhooks/n8n`. It checks the `X-N8N-Token` header (there is no dev shortcut here), validates the body, checks the allowlist, and then fetches the real run from GitHub with `get_workflow_run`. The branch, commit, attempt, and conclusion are taken from GitHub's own answer where possible, so n8n cannot make the backend act on wrong data. If GitHub cannot be reached, it returns `502 github_unavailable`.

**`_intake(incoming, session, settings, background, request)`** is the shared decision about what to do with a finished run. In order: refuse a repository not on the allowlist; if the branch starts with `ai-fix-`, only verify the fix and stop; ignore runs by the bot account; close a waiting RETRY as success or failure; for a failure, insert the row or find the existing one; then commit and schedule `process_failure`. It replies `202` with a short label such as `queued`, `duplicate`, `verified`, `retry_success`, or `ignored`.

**`_verify_fix(session, incoming)`** finds the pending FIX decision whose `fix_branch` matches this `ai-fix-*` branch and records `verified_success` if the run passed or `verified_failure` if it failed. It never starts new work, which is what prevents an endless loop of fixes fixing fixes.

**Small helpers in this file.** `_raw_body` reads the body and raises `413` above `WEBHOOK_MAX_BYTES`. `_github_authorized` checks the signature, allowing an empty secret only in `dev`. `_object` parses JSON and returns `None` for anything that is not a JSON object. `_from_github` and `_from_api` build an `IncomingRun` from the two kinds of input, returning `None` when required fields are missing. `_repo_name`, `_login`, and `_integer` safely read one field each. `_repo_allowed` checks the allowlist, `_is_bot` compares the actor with `GITHUB_BOT_LOGIN` ignoring case, and `_accepted` builds the `202` reply without ever echoing the payload back.

### `app/api/failures.py`

Dashboard routes for failures. All of them require the API token.

**`list_failures(session, repo, processing_status, limit, offset)`** answers `GET /failures` with one page of failures, newest first, plus the total count. You can filter by repository and status. `limit` is how many rows per page (1 to 200) and `offset` is how many to skip, which is how the dashboard moves between pages.

**`get_failure(failure_id, session)`** answers `GET /failures/{failure_id}` with one failure and everything attached to it: its classifications, its fix candidates, and its decisions. If no failure has that id, it returns `404`. This is what a "details" screen in the dashboard would show.

**`reprocess_failure(failure_id, request, background, session, force)`** answers `POST /failures/{failure_id}/reprocess`. It puts a failure back on the queue and schedules processing again, which lets an operator retry a stuck or failed job by hand. The rules: a row that is `processing` always gets `409` (it is busy); a `done` row gets `409` unless `?force=true`; an `error` row is requeued; a `queued` row stays queued. `recovery_count` is reset to 0. It returns `202` with `{"status": "queued"}`.

### `app/api/decisions.py`

**`list_decisions(session, action, category, outcome, limit, offset)`** answers `GET /decisions` with one page of decisions, newest first. You can filter by action (`retry`, `fix`, `escalate`), category, and outcome, which lets a dashboard show, for example, "all fixes still waiting for verification". It requires the API token like the other dashboard routes.

### `app/api/stats.py`

Numbers for the dashboard and for evaluating the project. All routes require the API token.

**`_bounds(repo, since, until, include_dry_run)`** reads the filters every stats route shares and packs them into a `StatsFilter`. `since` is inclusive and `until` is exclusive, both compared with when the decision was created. Dry-run decisions are left out unless `include_dry_run=true`, because they never get real outcomes and would distort the numbers.

**`summary`** answers both `GET /stats` and `GET /stats/summary` (two addresses for the same thing). It returns the number of resolved decisions, how many succeeded, the success rate, how many failures have a ground-truth label, and the classifier's accuracy on those. A success means a FIX with `verified_success` or a RETRY with `retry_success`.

**`by_category`**, **`by_action`**, and **`by_outcome`** answer `/stats/by-category`, `/stats/by-action`, and `/stats/by-outcome`. They return the same kind of counts as the summary, split into groups. A dashboard could use them to show, for instance, that retries succeed more often than fixes, or which category fails most.

**`calibration(session, bounds, bins)`** answers `GET /stats/calibration`. It groups decisions by how confident the system was, compares each group's average confidence with its real success rate, and reports the overall gap (ECE). It does this for classifier confidence and for agreement score, and adds accuracy, precision, recall, and a confusion matrix when ground-truth labels exist. `bins` is 2 to 20, default 10. [Chapter 5](05-reference.md) explains calibration in more detail.

**`timeline`** answers `GET /stats/timeline` with the number of resolved decisions per day, using UTC dates. A dashboard can draw this as a line chart to show activity over time.

---

## 4.4 `app/core`: the pure rules

### `app/core/enums.py`

This file holds the fixed lists of allowed labels used everywhere. Each is a `StrEnum`, so each value is also a plain string that can go straight into JSON.

**`FailureCategory`** lists `dependency_error`, `configuration_error`, `test_failure`, `flaky_or_network`, and `unknown`. The model may only choose the first four; `unknown` is the backend's own fallback when it cannot decide. **`DecisionAction`** lists the three stored actions: `retry`, `fix`, and `escalate`. **`DecisionOutcome`** lists `pending`, `verified_success`, `verified_failure`, `retry_success`, `retry_failure`, `escalated`, and `error`. **`ProcessingStatus`** lists `queued`, `processing`, `done`, and `error`. **`FailureSource`** lists `github` and `n8n`.

One detail that surprises people: the database stores the enum's **name** (for example `DEPENDENCY_ERROR`, in capitals), while the API returns its **value** (`dependency_error`). The docstring of `_enum` in `app/db/models.py` says "store an enum as its string value", but SQLAlchemy's `Enum` column stores names unless told otherwise, and the code does not tell it otherwise. `app/db/stats.py` has a helper, `_text`, that converts a stored name back to the value when it reads raw query results.

### `app/core/decision_engine.py`

This is the heart of the safety story. It decides RETRY, FIX, or ESCALATE, and it never reads the database, the clock, or the settings, so the same inputs always produce the same answer. You can think of it as a triage nurse reading a chart.

**The rules**, checked in order, where the first match wins and comparisons use "greater than or equal". First, missing, unknown, NaN, or out-of-range inputs escalate. Second, if `attempt` has reached `max_heal_attempts`, escalate. Third, for `flaky_or_network`: retry if retries remain and confidence reaches the flaky gate, otherwise escalate. Fourth, for a fixable category: stage 1 gives `candidate_fix` if confidence reaches the classify gate; stage 2 also needs agreement to reach the agreement gate to give FIX, and a missing agreement escalates. Fifth, anything else escalates.

**`Stage`**, **`Action`**, and **`ReasonCode`** are enums. `Stage` is `CLASSIFICATION = 1` or `AGREEMENT = 2`. `Action` is `retry`, `escalate`, `candidate_fix`, or `fix`; note that `candidate_fix` is only an internal answer and is never stored. `ReasonCode` names which rule fired, for example `flaky_retry`, `below_classify_threshold`, or `fix`, and that code is saved with each decision so you can see why it happened.

**`Thresholds`** is a frozen dataclass holding `classify`, `agreement`, `flaky`, `max_retries`, `max_heal_attempts`, and the per-category overrides `by_category`. When it is created, `__post_init__` checks every value and refuses gates outside 0 to 1 or negative counts. Its method **`confidence_gate(category)`** returns the confidence needed for a category: a per-category override wins, otherwise flaky uses the flaky gate and everything else uses the classify gate.

**`Decision`** is a frozen dataclass holding the result: the action, the stage, the reason code, a human-readable explanation, and `thresholds_snapshot`, a read-only copy of the gates that were used. Saving the snapshot means that if someone changes the settings next month, old decisions still show the gates that actually produced them.

**`decide(*, stage, category, confidence, attempt, retries_done, thresholds, agreement_score=None)`** applies the rules and returns a `Decision`. Every argument must be passed by name. The orchestrator calls it twice per fixable failure (once per stage), and the replay script calls it to print the stage 1 result. Note that the CI's own hint about the failure (`failure_kind`) is deliberately not an input; only the classifier's verdict counts.

**Helpers in this file.** `_flaky` and `_fixable` implement rules three and four. `_decision` builds a `Decision` with a snapshot, and `_invalid` is the shortcut for rule one. `_read_stage`, `_read_category`, `_score_problem`, and `_count_problem` check each input and explain what is wrong with it. `_require_unit` and `_require_count` reject bad configured gates. `_snapshot` makes the read-only copy of gates, and `_fmt` formats numbers with two decimals for explanations.

### `app/core/redacted.py`

**`RedactedText`** is a special kind of string that means "this text is safe to send to a model". It behaves like a normal string, but you cannot create one by writing `RedactedText("x")`; that raises a `TypeError` on purpose. The model message class refuses anything that is not `RedactedText`, so raw logs physically cannot reach a model by accident. Type hints alone would not stop this, because Python does not enforce them while running.

There are only a few approved ways to get one. **`RedactedText.trusted(content)`** marks fixed text written by us, such as the system prompt, as safe; it refuses anything that is not a plain string. **`RedactedText._from_redactor(content)`** wraps text the redactor produced, and only the redactor should use it. **`RedactedText.join(parts, sep="")`** and **`RedactedText.format(template, **values)`** combine pieces that are already safe into a bigger safe piece. **`RedactedText.escape_delimiter(marker)`** breaks any copy of a closing marker inside the text, by adding a space after its first character, so a log containing `<<<END_UNTRUSTED_LOG>>>` cannot close the evidence block early.

### `app/core/log_trim.py`

This file is a direct copy, in Python, of the n8n workflow's `trimCiLog` function, so a log trimmed here looks exactly like the logs the Kaggle prompt was tested with. Matching the trimming matters because a model can behave differently on differently shaped input.

**`trim_ci_log(text)`** cleans and shortens a raw CI log. It removes timestamps, colour codes, and the `/home/runner/work/<repo>/<repo>/` path prefix. It keeps the lines from the last `##[endgroup]` before the first `##[error]` up to and including that error line, drops other `##[...]` marker lines, and caps the result at 40 lines (`LOG_MAX_LINES`) and 3000 characters (`LOG_MAX_CHARS`), keeping the end. If the log is empty or has no `##[error]` line, it returns `(log unavailable)`. Lines after the first error are not kept, which is worth knowing when an important hint appears later.

Two helpers support it: **`_clean_line(line)`** strips the timestamp, colours, and checkout path from one line, and **`_visible(line)`** drops `##[...]` directive lines and removes the `##[error]` marker so only the human-readable message remains.

### `app/core/patch_apply.py`

This file applies one SEARCH/REPLACE patch to one file, very carefully. Mistakes by the model never crash the program; they come back as a result with an error code, so the system can record them and carry on with the other candidates.

**`ApplyError`** lists every reason a patch can be refused: `malformed_edits`, `empty_search`, `search_not_found`, `search_ambiguous`, `no_change`, `path_traversal`, `absolute_path`, `forbidden_path`, `path_not_in_context`, `too_many_edits`, `too_many_changed_lines`, `secret_in_replacement`, `python_syntax`, and `weakens_tests`. Each code is stored with the candidate, so you can count why patches fail.

**`ApplyLimits`** holds the caps: `max_edits` (5 by default), `max_changed_lines` (50), and `allow_workflows` (false). **`ApplyResult`** holds the outcome: `applied_ok`, `error`, `new_content` (only filled when accepted), and `diff`, a unified diff showing exactly what would change.

**`apply_patch(*, file_path, edits, files, limits=None)`** runs every check in this order: the path is safe and allowed; the file was among those shown to the model; the blocks parse; there are not too many blocks; no replacement contains a secret; each SEARCH text matches exactly once and actually changes something; not too many lines change; a `.py` file is still valid Python; and the patch does not weaken tests. It returns an `ApplyResult`. The fix generator calls it once per candidate.

**Helpers, grouped by job.** Path checks: `_normalise_path` and `_normalise_key` reject empty, absolute, drive-letter, and `..` paths; `_has_drive` spots a `C:`-style prefix; `_is_forbidden` blocks `.git/`, `.env` files, and `.github/workflows/` unless allowed. Parsing: `_parse_blocks` splits the edit text into search/replace pairs, using `_take_until` to collect lines up to a marker. Applying: `_apply_blocks` replaces each search exactly once and keeps the file's original line endings; `_count` counts matches but stops at 2; `_to_lf` converts Windows line endings. Diffs: `_unified` builds the diff and `_changed_lines` counts `+` and `-` lines. Tests: `_weakens_tests` combines `_deleted_tests` (a `test_*` name vanished), `_test_names` and `_python_test_names` (read function names, using Python's `ast` module for `.py` files), and `_assert_delta` (asserts removed versus added). Finally, `_parses` checks a `.py` file with `ast.parse`, which reads code without running it.

### `app/core/agreement.py`

This file measures whether several candidate patches say the same thing. If three independent attempts make the same change, that is good evidence the change is right.

**`PatchCandidate`** is the scorer's simple view of one candidate: its index, the file it edits, its diff, and whether it applied. **`Agreement`** is the result: a status (`ok` or `insufficient_candidates`), the score, the index of the chosen candidate, and a short explanation for humans.

**`score_agreement(candidates)`** first checks that at least two candidates applied; if not, it returns `insufficient_candidates` with no score, which leads to escalation. Otherwise it computes the similarity of every pair, counting failed candidates as 0 so they pull the score down, and averages them. It then picks the **medoid**, the working candidate most similar on average to the others. The fix generator calls it after all candidates are applied.

**`normalise_diff(diff)`** prepares a diff for comparison by dropping header lines (`diff`, `index`, `---`, `+++`, `@@`) and blank lines, and trimming spaces. Two diffs of the same change can differ in headers or spacing, and this makes them compare as equal.

**Helpers.** `_applicable` says whether a candidate applied and has a diff. `_pairs` computes similarity for every pair. `_similarity` returns 0 if either side failed or they edit different files, otherwise it uses `_text_similarity`, which averages two measures: `_jaccard` (shared words divided by all distinct words) and Python's `difflib.SequenceMatcher` ratio (how much of the text lines up in order). `_medoid` and `_average` choose the best candidate, with the lowest index winning a tie. `_path` normalises slashes and `_clamp` keeps a value between 0 and 1.

---

## 4.5 `app/services`: the workers

### `app/services/secret_redactor.py`

This file finds secrets in text and replaces them with `[REDACTED:<KIND>]`. Its rules are written for specific known formats on purpose. A general rule like "hide every long random-looking string" would also destroy commit SHAs and other identifiers the classifier needs to understand the failure. Running the redactor twice changes nothing the second time, which makes it safe to apply at several points.

It recognises private key blocks, JWTs, GitHub tokens (`ghp_`, `github_pat_`, and other prefixes), AWS access keys, Slack, Stripe secret keys, Google API keys, npm, PyPI, Docker, SendGrid, Azure storage connection strings, `user:password@` inside URLs, Basic and Bearer authorization headers, `--token`-style command-line flags, and `NAME=value` lines where the name contains words such as `PASSWORD`, `TOKEN`, `SECRET`, or `API_KEY`.

**`RedactionResult`** holds the cleaned text (as `RedactedText`) and `count`, the number of secrets replaced. **`PreparedContext`** holds a whole cleaned bundle ready for a model: `log`, `diff`, `files`, and the total `redaction_count`. Both are frozen dataclasses, so nobody can swap in unredacted text after the fact.

**`redact(text)`** applies every rule in `_RULES`, in order, to any text (or `None`) and returns a `RedactionResult`. It is the only way untrusted text becomes `RedactedText`. Three thin wrappers call it under clearer names: **`redact_code`** for diffs and files, **`redact_outbound`** for text going to GitHub, and **`redact_secrets`**, which its docstring says was kept for earlier callers; nothing in `app/`, `scripts/`, or `tests/` calls it today.

**`prepare_log(text)`** redacts the full log first and trims it second, returning the cleaned text and the count. The order matters: trimming first would throw away secrets outside the error area before counting them, so the count would be too low. **`prepare_context(*, log, diff=None, files=None)`** prepares the log this way and redacts the diff and every file, returning a `PreparedContext`. The orchestrator and the fix generator both call it.

**Helpers.** `_apply` runs one rule and counts only real changes. The replacers `_placeholder`, `_redact_env`, `_redact_cli`, `_redact_url`, `_redact_bearer`, and `_redact_basic` each keep the readable part, such as the variable name, flag, or header word, and replace only the secret itself, so the log still makes sense. `_unquote` and `_is_benign` skip values that are clearly not secrets, such as empty values, `null`, `true`, `****`, or text that is already redacted.

### `app/services/classifier.py`

This file asks the classifier model which category a failure belongs to, checks the answer, and combines several answers when sampling is turned on.

**Self-consistency** means asking the same question several times and voting. With one sample, which is the default, the model's own confidence is used. With several, the stored confidence is the smaller of two numbers: the average confidence of the valid samples, and the share of all samples that voted for the winner. A tie, or no valid sample at all, produces `unknown` with confidence 0, which always escalates.

**`ClassificationResult`** is the final verdict: category, confidence, reasoning, model name, raw output, every individual sample, the total number of parse attempts, and how long it took. Its method **`to_create(failure_id)`** turns it into the shape the database expects, and the orchestrator calls it to store the verdict.

**`Classifier`** is the class that does the work. **`__init__(client, *, model, samples, temperature, max_retries, max_tokens)`** stores its settings and refuses impossible values such as fewer than one sample. **`classify(*, log, diff=None, failure_kind=None)`** builds the prompt, takes each sample one after another, then combines them into a `ClassificationResult`. It refuses a log or diff that is not `RedactedText`. **`_one_sample(index, messages)`** calls the model once; if the reply fails the format check, it adds a message explaining the problem and asks again, up to `max_retries` extra times, and records whether the sample ended `ok` or `invalid`.

**Module functions.** `build_classifier(settings, client)` builds a `Classifier` from the `CLASSIFIER_*` settings and is used by `process_failure` and the replay script. `parse_classifier_output(text)` checks a reply against the strict `ClassifierOutput` shape and raises on bad JSON or a bad shape. `_json_object` parses JSON or falls back to the first `{...}` it can find in the text. `_require_redacted` raises if a value is not `RedactedText`. `_aggregate` is the voting rule, and `_fallback` builds the `unknown`, confidence 0 result.

Unclear from the code: `ci-heal-backend/docs/LLM_CONTRACT.md` and the `LLMClient.complete` description mention turning on `enable_thinking` for classifier re-asks. `_one_sample` never passes `enable_thinking=True`, and no other code does either. The HTTP client supports the flag, but nothing switches it on today.

### `app/services/fix_generator.py`

This file asks the fixer model for several candidate patches, applies each one, and scores how much they agree.

**`GeneratedFix`** is one candidate: its index, the suggested branch name, the file path, the raw edits, the PR title and body, whether it applied, any error code, the diff, and the new file content. Its method **`to_create(failure_id)`** turns it into a database row, redacting the edits and diff once more before storage as an extra guard.

**`FixGeneration`** is the result of a whole round: `failed` (true if every model call raised), `failure_reason`, the list of candidates, the `Agreement`, and the `redaction_count`. Its method **`to_creates(failure_id)`** produces one database row per candidate, which the orchestrator stores.

**`FixGenerator`** is the class that does the work. **`__init__(client, *, candidates, temperature, max_tokens, limits)`** stores its settings and refuses fewer than one candidate. **`generate(*, log, diff=None, files, failure_kind=None)`** redacts the inputs with `prepare_context`, builds the prompt, and calls the model `candidates` times in a row. A model error becomes an invalid candidate instead of stopping the round. Finally it scores agreement. **`_from_reply(index, raw, files)`** parses one reply and applies it to the **original** files.

**Helpers.** `build_fix_generator(settings, client)` builds a generator from the `FIX_*` settings. `_parse_proposal` removes `<think>` blocks and code fences, parses JSON, and checks it against the strict `FixProposal` shape, returning `invalid_json` or `invalid_schema` on failure. `_json_object` is the same idea as in the classifier. `_from_apply` copies the apply result onto a `GeneratedFix` and redacts the PR title and body. `_failed_call` and `_invalid` build an invalid candidate whose path is `(unparsed)`. `_views` converts candidates into the simpler shape the agreement scorer wants, and `_require_redacted` refuses an unredacted prompt.

### `app/services/github_client.py`

This file talks to the GitHub REST API using httpx. It is the only place in the backend that sends requests to GitHub. Every write method checks dry-run first and returns early if it is on. Read methods always run.

**`GitHubWriteRefused`** is the error raised when a write targets a branch the bot must never touch. It is a real exception instead of an `assert`, because Python can be started with a flag (`-O`) that removes all `assert` lines, and a safety check must not disappear like that.

**`CallResult`** holds the result of one GitHub call: whether it worked, whether it was skipped for dry-run, an error label, and whatever came back (URL, SHA, text, number, data, or a list of items). **`ActionResult`** holds what a higher-level service did: whether it worked, whether it was dry-run, whether an existing PR or issue was reused, the URL, the branch, any error, and whether the label was applied.

**`GitHubClient`** is the class. Its constructor stores the token (as `SecretStr`), the API URL, the dry-run flag, timeouts, retry counts, size caps, the default branch, and two test hooks for waiting and reading the clock. `__repr__` prints only the URL and dry-run flag, never the token, and the `dry_run` property says whether writes are skipped.

**Read methods.** `get_workflow_run` fetches one run's details and is used by the n8n webhook. `list_jobs` lists a run's jobs across up to 20 pages; only tests use it today. `download_logs` follows GitHub's redirect to the log zip without sending our token to the other host, enforces `GITHUB_MAX_LOG_BYTES`, and unzips the files into one text. `compare` gets the diff between two commits. `get_file` downloads one file at a commit and refuses files over `GITHUB_MAX_FILE_BYTES`. `get_branch` returns the commit a branch points at. `find_open_pull` finds an open PR for a branch, and `find_issue` finds an issue whose body contains a marker.

**Write methods**, all skipped under dry-run. `create_branch` creates `refs/heads/<branch>`. `commit_file` creates or updates one file with a `PUT` request and adds a `Heal-Attempt: N` line to the commit message. `create_pull` opens a pull request after redacting and capping the title and body again. `create_issue` opens an issue. `add_labels` adds labels; callers treat a failure here as harmless. `rerun_failed_jobs` asks Actions to run the failed jobs again. Every branch-writing method first calls `_refuse`.

**`_refuse(branch)`** raises `GitHubWriteRefused` unless the branch starts with `ai-fix-`, is not `main`, `master`, or `GITHUB_DEFAULT_BRANCH`, and contains no `/`, `..`, or whitespace. **`_headers`** builds the standard GitHub headers and adds the token only when needed. **`_read`** sends one request with retries; if a token is needed but empty, it returns `missing_token` without sending anything. It retries timeouts, server errors (5xx), `429`, and a `403` that carries a rate-limit hint.

**Module functions.** `build_github_client(settings)` builds the client from the `GITHUB_*` settings and `DRY_RUN`, and `create_app` calls it. The remaining helpers each do one small job: `_repo_error` checks the `owner/name` form, `_bad_path` rejects paths that could escape the repository, `_fail` and `_created` build results, `_bound_text` redacts and caps text, `_with_trailer` adds the `Heal-Attempt` line, `_status_error` and `_error_code` turn status codes into labels like `not_found` or `forbidden`, `_retry_delay`, `_retryable`, and `_header_delay` decide how long to wait (honouring `Retry-After` and `X-RateLimit-Reset`), `_next_link` follows pagination, `_unzip` joins zip contents safely, and `_json`, `_as_str`, `_as_int` read values defensively.

### `app/services/github_text.py`

This file builds all the text the backend sends to GitHub, in one place, so wording, caps, and redaction are consistent.

**`AI_NOTE`** is the sentence `AI-generated, human review required.`, which is the first line of every PR and issue body. **`TITLE_CAP`** (120) and **`BODY_CAP`** (59999) are the maximum lengths, kept under GitHub's own limits so a long model reply cannot make the request fail.

**`make_fix_branch(category, sha)`** builds the fix branch name, `ai-fix-<category slug>-<first seven characters of the SHA>`, for example `ai-fix-dependency-error-abc1234`. The model's suggested branch name is never used, so the model cannot choose where code is written. **`failure_marker(failure_id)`** builds the text `ci-heal-failure-<id>`, which is written into issue and PR bodies so a later run can find the existing issue instead of opening a duplicate.

**`bound_outbound(title, head, footer)`** redacts the title and body, cuts them to the caps, and makes sure the footer (with the marker) stays at the end even when the body is cut. **`compose_pull_request(...)`** and **`compose_issue(...)`** build the full title and body, including the category, confidence, agreement (for PRs), the model's reasoning, the decision id, and the marker. **`dry_pull_url`** and **`dry_issue_url`** build the fake `dry-run://github/...` links stored in dry-run mode.

### `app/services/pr_service.py`

**`FixRequest`** collects everything needed to publish a fix: the repository, failure id, the failing branch and commit, the base branch for the PR, the file path and its new content, category, confidence, agreement, reasoning, heal attempt number, and PR title. Bundling them in one object keeps the method signatures short.

**`PullRequestService.open_fix(request, decision, decisions)`** first refuses if the failing branch itself starts with `ai-fix-`, returning the error `fix_loop`, so a fix never tries to fix itself. It builds the branch name and PR text, then calls `_open`. If `_refuse` raises, it returns the error `refused_branch` instead of crashing. It returns an `ActionResult`.

**`_open(...)`** does the steps in order. It reuses an open PR for the branch if one exists. Otherwise it creates the branch from the failing commit if it is missing. In dry-run it records the fake link and stops. Otherwise it reads the current file to get its SHA (GitHub requires it to update a file), commits the new content, opens the PR into the base branch, and adds the `ai-fix` label. **`_save(...)`** stores the PR link, branch, and dry-run flag on the decision.

### `app/services/issue_service.py`

**`IssueRequest`** collects what an escalation needs: repository, failure id, category, confidence, reasoning, and an optional title. **`IssueService.open_issue(request, decision, decisions)`** first searches for an existing issue containing this failure's marker and reuses it, which avoids duplicate issues when a failure is reprocessed. In dry-run it records the fake link. Otherwise it creates the issue and adds the `ai-fix` label, then **`_save`** stores the issue link and dry-run flag on the decision. The orchestrator calls it for normal escalations and for best-effort escalations after a crash.

### `app/services/rerun_service.py`

**`RerunService.request_rerun(*, repo, run_id, decision, decisions)`** asks GitHub to rerun the failed jobs of a run. On success, or in dry-run, it marks the decision `rerun_requested = True`. If GitHub refuses, for example with `404` because the run no longer exists, it returns the error and leaves the decision unmarked, so you can tell from the database that the rerun never actually started. The orchestrator calls it for every RETRY.

### `app/services/orchestrator.py`

This is the conductor. It runs one failure through every step and records the result. It always works in its own database session, separate from the webhook request that triggered it, because it runs after that request has already finished.

**`Orchestrator.__init__(*, settings, github, classifier, generator)`** stores its collaborators and builds the PR, issue, and rerun services on top of the GitHub client. Passing the collaborators in, instead of creating them inside, is what lets the replay script and tests hand it fakes.

**`Orchestrator.run(session, failure_id)`** claims the failure, commits, and then runs `_pipeline` inside a time limit of `PIPELINE_TIMEOUT_SECONDS`. On timeout it records `pipeline timed out`. On any other crash it records only the error's type name, never its message, because messages can contain sensitive data. In both cases it then tries to open an escalation issue so a human hears about it. Results are written to the database; nothing is returned.

**`_pipeline(session, failure)`** is the main sequence: fetch, redact, classify, decide stage 1, then either retry, escalate, or generate patches, decide stage 2, and fix or escalate. [Chapter 3, section 3.3](03-how-it-works.md#33-following-one-failure-from-start-to-finish) tells this story step by step. **`_fetch(failure)`** downloads the log, diff, and up to 8 changed files, and raises `log_unavailable` if the log is missing.

**`_retry`**, **`_fix`**, and **`_issue`** each store a decision and then call the matching service. `_retry` stores a RETRY with `expected_run_attempt` set to the current attempt plus one, so the rerun webhook can be matched later. `_fix` stores a FIX with its `ai-fix-*` branch; the heal attempt number written into the commit message is counted after this new row is stored. `_issue` stores an ESCALATE. **`_finish`** marks the failure `done`, or `error` if the GitHub action failed. **`_fail`** rolls back and marks it `error` with a message. **`_escalate_best_effort`** stores an ESCALATE with category `unknown` and reason `pipeline_error`, tries to open an issue, and swallows any error there, logging only its type.

**Module functions.** `recover_stuck(factory, stale_seconds, max_recoveries)` asks the repository to requeue stuck rows at startup and returns their ids. `process_failure(failure_id, *, settings, session_factory, github, classifier_llm, fixer_llm)` opens a session, builds the classifier and fix generator, and runs the orchestrator; the webhook, the reprocess route, and startup recovery all call it. `_decision_create` builds a decision row with a plain copy of the threshold snapshot, `_plain_snapshot` and `_plain` turn read-only mappings into normal dictionaries the JSON column can store, `_chosen` returns the medoid candidate if it applied, and `_changed_paths` reads file names out of the diff, at most 8, skipping deleted files and absolute paths.

**How the two counters are computed.** `retries_done` is the number of RETRY decisions already stored for the same repository and `run_id`. `attempt` is the number of FIX decisions on the same repository and failing branch since the latest FIX on that branch with outcome `verified_success`. So a verified fix resets the count, and `MAX_HEAL_ATTEMPTS` limits how many fixes in a row can be tried on one branch.

---

## 4.6 `app/llm`: talking to models

### `app/llm/base.py`

**`LLMError`** is the error raised when any model call fails. Its message never contains a header or key, so it is safe to log. **`LLMResponseError`** is a more specific version meaning "the provider answered, but the answer was unusable", such as a `4xx` status or a body that cannot be read. It is not retried, because asking again would get the same bad answer.

**`ChatMessage`** is one message to a model: a `role` (`system`, `user`, or `assistant`) and `content`. When it is created, `__post_init__` raises `TypeError` if the content is not `RedactedText`. This single check is what makes it impossible to send an unredacted log to any model, real or fake.

**`LLMClient`** is a Protocol, which describes what any model client must be able to do: an `async complete(messages, *, temperature, max_tokens, enable_thinking=False)` method that returns the reply text, and an `async ping()` method that says whether the model is reachable. Anything with those two methods counts as a model client, which is why swapping providers needs no code change.

### `app/llm/mock.py`

**`MockLLMClient`** is the fake model. You give it a list of replies when you create it, and each call to `complete` returns the next one, repeating the last reply when the list runs out. With no list, it answers `"{}"` every time, which is what the running server uses by default. `ping()` always returns `True`. It records every call in **`RecordedCall`** objects so tests can check exactly what was sent. It lets the server start and the tests run with no network and no key. `__repr__` shows only how many replies are scripted.

### `app/llm/http_client.py`

This is the real model client. It speaks the OpenAI-compatible chat format, which the team's Kaggle server, Groq, and Gemini all accept.

**`HttpLLMClient.__init__(...)`** stores the base URL, key, model name, timeouts, whether to stream, retry settings, and a lock. The lock makes sure only one call runs at a time on each client, because the Kaggle server handles one request at a time and would otherwise reject or slow down parallel calls.

**`complete(...)`** takes the lock and calls `_complete_with_retries`, which tries up to `max_retries + 1` times within the overall deadline, retrying only timeouts and server errors, and waiting a little longer each time (`backoff * 2**attempt`). **`_once`** sends one `POST` to `{root}/v1/chat/completions`, streaming or not. **`ping()`** sends `GET {root}/v1/models` and returns `True` when the status is below 400. `__repr__` shows model, host, and stream flag, never the key, and `_headers` builds the `Authorization: Bearer <key>` header.

**Helpers.** `_RetryableStatus` marks a 5xx answer (including Cloudflare's 524 timeout) as worth retrying. `_call_deadline` computes the total time budget. `_server_root` removes a trailing `/` and `/v1` so both URL forms work. `_request_body` builds the JSON body and adds `chat_template_kwargs.enable_thinking` only when asked. `_raise_for_status` turns 5xx into a retry and 4xx into `LLMResponseError`. `_assemble_json` reads `choices[0].message.content` from a normal reply. `_assemble_stream` and `_chunk_text` join the pieces of a streamed reply. `_asyncio_sleep` is the default wait between retries.

### `app/llm/factory.py`

**`build_llm_clients(settings)`** returns the classifier client and the fixer client as a pair, and `create_app` calls it at startup. **`build_llm_client(settings, role)`** reads either the `CLASSIFIER_LLM_*` or the `FIXER_LLM_*` settings and passes them to **`_build`**, which returns a `MockLLMClient` for `mock` or an `HttpLLMClient` for `http`. If `http` is chosen with an empty URL or key, `_build` raises immediately, so a broken deployment fails at startup with a clear message instead of failing later on the first real webhook.

### `app/llm/prompts.py`

This file holds the exact text sent to the models, so changing what the model is told happens in one place.

**`SYSTEM_PROMPT`** tells the classifier to reply with one JSON object, choose one of the four categories, and ignore any instructions found inside the untrusted markers. **`FIXER_SYSTEM_PROMPT`** tells the fixer to reply with one JSON object that edits exactly one file using SEARCH/REPLACE blocks, and to ignore instructions inside the markers. The markers are `<<<UNTRUSTED_HINT>>>`, `<<<UNTRUSTED_LOG>>>`, `<<<UNTRUSTED_DIFF>>>`, `<<<UNTRUSTED_REPLY>>>`, and `<<<UNTRUSTED_FILE>>>`, each with an `END` partner.

**`build_classifier_messages(*, log, diff, failure_kind)`** returns a system message plus a user message containing the hint, the log, and the diff, each inside its markers. **`build_retry_message(raw)`** builds the follow-up used when a reply fails the format check: it says the reply did not match and includes the previous reply, redacted and fenced. **`build_fixer_messages(*, log, diff, files, failure_kind)`** is like the classifier version but also includes each file inside its own markers.

### `app/llm/text.py`

**`strip_model_text(text)`** cleans a model reply before it is parsed. Some models think out loud inside `<think>...</think>` blocks, and many wrap JSON in ```` ```json ```` code fences. This function removes both, including an unclosed `<think>` block at the start, so the remaining text is just the JSON. The HTTP client calls it on every reply, and the fix generator calls it again before parsing a proposal.

---

## 4.7 `app/db`: storage

### `app/db/base.py`

**`Base`** is the SQLAlchemy declarative base class. Every table class inherits from it, and SQLAlchemy uses it to remember all tables, so `init_db` can create them in one call. It contains nothing else; it exists so all models share one registry.

### `app/db/types.py`

**`utcnow()`** returns the current time with the UTC timezone attached. It is used as the default for timestamp columns and anywhere the code needs "now", so all stored times use the same clock.

**`UTCDateTime`** is a custom column type. SQLite does not remember timezones, so a time saved as UTC would come back without its timezone and could be compared wrongly with a timezone-aware "now". `process_bind_param` converts any time to UTC before saving, and `process_result_value` reattaches UTC when reading, so every time the code sees is consistent.

### `app/db/models.py`

This file defines the four tables as classes: **`Failure`**, **`Classification`**, **`FixCandidate`**, and **`Decision`**. Each class lists its columns, indexes, and relationships, for example "a failure has many decisions". [Chapter 5](05-reference.md) explains what one row of each table means. **`_enum(enum_type)`** makes an enum column stored as a short text column rather than a database-specific enum type, so SQLite and PostgreSQL behave the same.

### `app/db/session.py`

**`make_engine(database_url)`** creates the SQLAlchemy engine, which manages connections to the database. For in-memory SQLite it keeps one shared connection, otherwise each new connection would see its own empty database. For any SQLite URL it registers **`_configure_sqlite`**, which turns on foreign keys, WAL mode, and a 5-second busy timeout for every new connection.

**`make_session_factory(engine)`** returns a factory that creates sessions. It sets `expire_on_commit=False`, which keeps objects readable after their session commits, so routes can turn them into JSON after the database work is finished. **`init_db(engine)`** creates any missing tables and never changes existing ones. **`session_scope(factory)`** gives out one session, commits if everything went well, rolls back on an error, and always closes it.

### `app/db/repositories.py`

This is the only place that reads and writes rows. A **repository** in this sense is a class whose job is to store and fetch one kind of record, so the rest of the code never writes queries directly. (This is different from a git repository.)

**`_TRANSITIONS`** lists the allowed status moves for a failure: `queued` can become `processing` or `error`; `processing` can become `done`, `error`, or `queued`; `error` can become `queued`; and `done` cannot change unless forced. **`_bounded_limit`** and **`_bounded_offset`** reject bad page numbers and cap a page at 200 rows.

**`FailureRepository`** handles failures. `get` returns one row or nothing. `get_or_create` inserts a failure or returns the existing one for the same repository, `run_id`, and `run_attempt`; it uses a savepoint, a mini-transaction, so two simultaneous deliveries cannot break each other. `transition_status` changes the status, sets timestamps, and raises `InvalidStatusTransition` for a move not in the list. `list_failures` returns a filtered page. `claim` moves a row from `queued` to `processing` in one statement so only one worker wins. `recover` finds stuck rows at startup and requeues them, or marks them `error` at the recovery limit.

**`ClassificationRepository`** and **`FixCandidateRepository`** each have `add`, used by the orchestrator to save rows, and `list_for_failure`, used by the replay script to print them.

**`DecisionRepository`** handles decisions. `add` inserts one and insists that a RETRY has `expected_run_attempt` and a FIX has `fix_branch`. `count_retries` and `count_fix_attempts` produce the two counters described in section 4.5. `resolve` sets the final outcome and time. `find_open_retry` and `find_open_fix` find the pending decision a later webhook should close. `record_pull_request`, `record_issue`, and `record_rerun` store links and flags. `list_decisions` returns a filtered page.

### `app/db/stats.py`

This file calculates the dashboard numbers inside SQL, so counting and averaging happen in the database instead of loading every row into Python.

**`_RESOLVED`** is the list of outcomes that count in statistics: `verified_success`, `verified_failure`, `retry_success`, `retry_failure`, and `escalated`. `pending` and `error` are left out. **`StatsFilter`** holds the shared filters. **`_success()`** is an SQL expression worth 1 for a verified FIX or successful RETRY and 0 otherwise, and **`_rate`** divides safely, returning nothing when the total is 0.

**`StatsRepository`** has one method per kind of answer. `summary` counts and computes the success rate and labelled accuracy. `grouped` does the same per category, action, or outcome. `timeline` counts per UTC day. `calibration` builds two score series (classifier confidence and agreement) plus ground-truth statistics; its helpers `_decision_series` and `_series` put scores into bins, compute ECE in SQL, and fill in empty bins so chart axes stay stable. `_ground_truth` computes accuracy, per-category precision and recall, a confusion matrix, and calibration where "success" means the predicted category equals the labelled one, using `_label_totals`, `_count_labeled`, `_confusion_counts`, `_category_scores`, and `_labeled`. `_decision_filters` applies the shared filters and `_dialect` says whether the database is SQLite or PostgreSQL.

**Module helpers.** `_bin` maps a score to a bin number, putting exactly 1.0 in the last bin. `_bin_numbers` produces the list of bin numbers in SQL. `_utc_day` groups by day using the right function for SQLite or PostgreSQL. `_matrix` expands counts into every predicted-by-actual cell, giving 25 cells for 5 categories. `_text` converts a stored enum name back to its API value.

### `app/db/errors.py`

**`InvalidStatusTransition`** is the error `transition_status` raises for a forbidden status move, such as `done` to `processing`. Nothing in `app/` catches it. The routes check the status themselves before asking for a change, so in normal use it should never be raised; if it is, it signals a bug.

---

## 4.8 `app/schemas`: data shapes

### `app/schemas/common.py`

**`Page[T]`** is the shape of every list answer: `items` (the rows on this page), `total` (how many exist in all), `limit`, and `offset`. The `[T]` means it works for any kind of row, so the same class serves both `GET /failures` and `GET /decisions`.

### `app/schemas/failure.py`

**`FailureCreate`** lists the fields needed to insert a failure, including the optional `ground_truth_category` used only for evaluation rows. **`FailureRead`** is how a stored failure looks in an API answer. **`FailureDetail`** is a `FailureRead` plus its classifications, fix candidates, and decisions, used by the single-failure endpoint.

### `app/schemas/classification.py`

**`ClassifierLabel`** lists the four labels the model is allowed to return; `unknown` is not among them because only the backend may use it. **`ClassifierOutput`** is the only JSON the classifier may reply with: `extra="forbid"` rejects any extra key, `confidence` must be between 0 and 1, and `reasoning` must not be empty. **`ClassificationSample`** is one sample as stored, and **`ClassificationCreate`** and **`ClassificationRead`** are the storage and API shapes.

### `app/schemas/fix.py`

**`FixProposal`** is the only JSON the fixer may reply with: branch name, file path, edits, PR title, and PR body, with extra keys rejected. Its validator **`_branch_is_a_single_safe_segment`** rejects `main`, `master`, slashes, `..`, and spaces in the suggested branch name, although the name is ignored later anyway. **`FixCandidateCreate`** and **`FixCandidateRead`** are the storage and API shapes.

### `app/schemas/decision.py`

**`DecisionCreate`** lists the fields to store a decision, including `expected_run_attempt` for a RETRY and `fix_branch` for a FIX, which are the keys later webhooks use to find it. **`DecisionRead`** is how a stored decision looks in an API answer, including its outcome and links.

### `app/schemas/stats.py`

This file defines the shapes of every `/stats` answer: `Summary`, `GroupCount`, `GroupedStats`, `CalibrationBin`, `CalibrationSeries`, `CategoryScore`, `ConfusionCell`, `GroundTruthStats`, `CalibrationReport`, `TimelineDay`, and `Timeline`. They hold data only and have no methods. A dashboard developer can read this file, or the `/docs` page, to see exactly which fields each stats endpoint returns.

---

## 4.9 `scripts/replay_failure.py`

This script runs one fixture log through the real `Orchestrator` with no network and no database file. It always forces dry-run and the in-memory database `sqlite:///:memory:`, and refuses to start otherwise, so it can never write to GitHub or leave a file behind.

**`ReplayGitHub`** is a fake GitHub built on top of the real `GitHubClient`. It returns the fixture as the log, a small diff for `app.py`, and the file `def value():\n    return 1\n`, and it reports that no PR, branch, or issue exists yet. Its write methods are the real ones, unchanged, and they stop at the dry-run check exactly as they would on a server.

**`main(argv=None)`** reads the command line: a fixture path (default `fixtures/dependency.log`) and `--llm mock` or `--llm http`. It returns exit code 0 when processing ended `done`, 1 otherwise, and 2 for a setup problem such as a missing label or missing HTTP settings. **`_run(fixture, llm)`** loads the label from `fixtures/labels.json`, builds settings and the in-memory database, inserts the failure with its ground-truth label, runs the orchestrator, and prints the report.

**Helpers.** `_settings(llm)` builds the forced settings; with `--llm http` it loads `.env` if present and switches both roles to `http`. `_client(settings, role, ground_truth)` builds a fake classifier that answers the true label at confidence 0.95, a fake fixer that returns `_proposal()`, or the real HTTP client. `_proposal()` is one JSON fix that changes `return 1` into `return 2`. `_report(...)` prints each step, as shown in [Chapter 6](06-status-and-running.md), and never prints the log or any secret.

---

## 4.10 `tests/`: one paragraph per file

**`conftest.py`** holds shared test setup. The `settings` fixture gives test settings with `app_env="test"`, an in-memory database, and `.env` ignored. The `client` fixture gives a FastAPI `TestClient`, which sends fake HTTP requests to the app without starting a real server. The `db_session` fixture gives a fresh, empty in-memory database session for each test, so tests never affect each other.

**`test_health.py`** checks that `/health` reports `ok` and the dry-run flag, that CORS allows the dashboard's origin, that `/health/ready` returns `503` only when the database check fails, and that a model being down does not make the service "not ready". Together these protect the endpoints hosting platforms rely on.

**`test_config.py`** checks that the defaults are safe (dry-run on, mock models), that environment variables override defaults, that bad values are rejected, that keys stay out of printed output, that the classify gate is 0.75, that per-category overrides must name real categories, and that the two startup warnings appear when they should.

**`test_env_example.py`** checks that `.env.example` contains exactly the same setting names as the `Settings` class, and that its values load without errors. This catches the common mistake of adding a setting in code and forgetting to document it in the template, or the other way round.

**`test_log_trim.py`** checks that empty logs and logs without `##[error]` are reported as unavailable, that the trimmer keeps the region from `##[endgroup]` to `##[error]`, and that the line and character caps cut from the start so the end of the region is kept.

**`test_secret_redactor.py`** checks that every known secret format is replaced and labelled, that running the redactor twice changes nothing more, that ordinary logs and identifiers are left alone, that `prepare_log` redacts before trimming, that `RedactedText` cannot be built by hand, and that edge cases such as Windows line endings, very long lines, and non-English text work.

**`test_llm.py`** checks that plain strings cannot be sent to a model, that the mock replies as scripted and records calls, that streaming strips `<think>` blocks and fences, that a 524 is retried and a 401 is not, that timeouts are retried and malformed streams are not, that calls on one client never overlap, that retries stop at the deadline, and that an `http` provider without a key fails at startup.

**`test_classifier.py`** checks that a valid reply is accepted, that a malformed reply is re-asked, that a reply which stays malformed falls back to `unknown`, that a split vote caps confidence at the majority share, that a tie gives `unknown`, that a prompt-injection log cannot change the output format, and that samples are stored and read back correctly.

**`test_decision_engine.py`** uses a table of cases, one row per reason code, to check every rule. It also checks that the engine imports neither settings nor a clock, that the CI's own failure hint is not an input, that bad gates are rejected, and that FIX requires a fixable category, both gates, and room under the attempt cap.

**`test_patch_apply.py`** checks that a SEARCH text must match exactly once and change the file, that Windows line endings work, that paths must stay inside the provided files, and that secrets, syntax errors, too many edits or changed lines, and test-weakening patches are all rejected. It also checks that garbage input never crashes, and that forbidden paths are rejected even if they were provided.

**`test_agreement.py`** checks that identical candidates score 1, different ones score below 1, an invalid candidate pulls the score down, all-invalid candidates are reported as insufficient, candidates editing different files score 0, and a tie for the medoid goes to the lowest index.

**`test_fix_generator.py`** checks that identical replies score 1 and are stored, that one bad reply does not drop the others, that a model error becomes an invalid candidate, that total failure is reported cleanly, and that a replacement containing a secret or a reply trying to inject extra fields does not apply.

**`test_github.py`** checks the GitHub client and the three services against a fake GitHub. Writes go to the configured URL, dry-run sends no writes, and writes to non-`ai-fix-*` or default branches are refused. Rate-limit waits are honoured, jobs paginate, logs unzip, and large files are not decoded. Open PRs and existing issues are reused, outbound text is redacted and capped, a label failure does not fail the PR, and a rerun records success but a `404` does not.

**`test_repositories.py`** checks database behaviour: `get_or_create` returns the existing row and survives a race, status changes set timestamps and reject illegal moves, times are UTC-aware, deleting a failure deletes its children, lists filter and paginate, RETRY and FIX require their link fields, the heal-attempt count resets after `verified_success`, recovery stops at the cap, and a SQLite file uses WAL and a busy timeout.

**`test_webhooks.py`** runs end-to-end flows through the API. A flaky failure retries and a later success closes it. A dependency failure opens a dry-run PR and verification succeeds. Low agreement and malformed classifier output open issues. Bad signatures, an unset secret outside `dev`, a wrong n8n token, and repositories outside the allowlist are rejected. Duplicate deliveries create one failure, bot runs are ignored except for verification, startup requeues stuck work, reprocess rules hold, a crash marks `error` and attempts an issue, only one claim succeeds, ping works, and `cancelled` is ignored.

**`test_stats.py`** seeds the database with known decisions and checks that the statistics, including ECE, match a calculation done by hand. It checks that filters drop dry runs, other repositories, and dates outside the window, that an empty database gives empty results instead of errors, that confidence 1.0 lands in the last bin, that the bin count is validated, and that the stats routes require the API token.

**`test_replay.py`** runs the replay script itself. It checks that the dependency and flaky fixtures run offline in dry-run and leave no database file, that the secrets fixture counts redactions without ever printing the secrets, and that the prompt-injection fixture keeps its labelled category.

### Files not covered

None. All 51 Python files in `app/` and `scripts/` are covered above, including the seven `__init__.py` package markers.

---

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 3, How it works](03-how-it-works.md) · Next: [Chapter 5, Reference](05-reference.md)
