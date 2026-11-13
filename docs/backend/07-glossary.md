# Chapter 7: Glossary

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 6, Status and running](06-status-and-running.md)

Every special word used in this guide, in alphabetical order. Each entry explains the general idea first, then how this project uses it, so it makes sense whatever programming background you come from.

---

**Agreement score.** A number from 0 to 1 that measures how similar several candidate patches are to each other. Every pair of patches is compared, and the average similarity is the score. A high score means independent attempts made the same change, which suggests the change is right. It is computed in `app/core/agreement.py`, and a pull request needs at least 0.6 by default.

**Alembic.** A tool for Python projects that changes existing database tables step by step when the code's table definitions change, for example adding a column without losing data. This kind of tool is called a migration tool. This project does not use Alembic or any migration tool, so table changes require deleting the database.

**API (Application Programming Interface).** An agreed set of requests one program can send to another, with rules for what to send and what comes back. A web API is a list of URLs plus methods such as GET and POST. This backend offers an API to the dashboard and to GitHub, and it uses GitHub's API to read logs and open pull requests.

**API token.** A secret string that proves a caller is allowed to use certain endpoints, much like a password made for programs instead of people. In this project, `API_TOKEN` must be sent as `Authorization: Bearer <token>` to use the dashboard routes. Only `APP_ENV=dev` with an empty token leaves those routes open.

**`APP_ENV`.** The setting that names the running situation: `dev` for your own computer, `test` for automated tests, and `prod` for a real server. In this code it changes exactly three things: whether an empty API token opens dashboard routes, whether an empty webhook secret is accepted, and the `environment` value shown by `/health`.

**`ast`.** A module built into Python that reads source code and turns it into a tree describing its structure, without running the code. The patch applier uses it to check that a patched `.py` file is still valid Python and to list the names of test functions, so it can tell when a patch deletes a test.

**Async and await.** Keywords that let a program pause one task while it waits for something slow, such as a network reply, and work on other tasks meanwhile. `async def` marks a function that can pause, and `await` marks where it pauses. Almost every function here that calls GitHub or a model is asynchronous, so one slow call does not freeze the server.

**Background task.** Work that a web framework runs after it has already sent the reply to the caller. FastAPI's `BackgroundTasks` feature is used here so a webhook can answer GitHub within a moment, while the slow pipeline of downloading logs and calling models continues afterwards inside the same server process.

**Backend and frontend.** The frontend is the part a person sees and clicks, usually a web page. The backend runs on a server, stores the data, and does the real work, and the two talk over the network. `ci-heal-backend` is the backend; the planned React dashboard would be the frontend, and it is not connected yet.

**Bearer token.** A way of sending a token in the HTTP header `Authorization: Bearer <token>`. "Bearer" means whoever carries the token is allowed in, like a ticket that is not tied to a name. This project uses bearer tokens for the dashboard routes and when calling model servers such as Kaggle, Groq, or Gemini.

**Branch.** A separate line of work inside a git repository, so changes can be made without affecting the main line until someone decides to combine them. The main line is usually called `main`. This backend writes only to branches whose names start with `ai-fix-`, such as `ai-fix-dependency-error-abc1234`, and refuses to write to `main` or `master`.

**Calibration.** How well a confidence number matches reality. A system is well calibrated if, among all the times it said "80% sure", it was right about 80% of the time. The endpoint `/stats/calibration` measures this by putting decisions into confidence bins and comparing each bin's average confidence with its actual success rate.

**`candidate_fix`.** The decision engine's stage 1 answer meaning "this failure is worth trying to fix". It is an internal step only: it triggers patch generation and is never stored in the database. After the patches are scored, stage 2 turns it into either a stored FIX or a stored ESCALATE.

**CI/CD.** Short for Continuous Integration and Continuous Delivery (or Deployment). CI automatically builds and tests the code every time someone pushes a change; CD automatically releases it when the tests pass. Together they act as a robot quality checker. This project helps when that robot reports a failure.

**Classifier.** The part of the backend that asks an AI model which of the four failure categories a CI failure belongs to, and how sure it is. It lives in `app/services/classifier.py`, checks every reply against a strict format, asks again on a bad reply, and can ask several times and vote when sampling is enabled.

**Commit and SHA.** A commit is one saved snapshot of a repository's files, with a message describing the change. Each commit has a unique ID called a SHA, a long string of hexadecimal characters. People often shorten it to the first seven characters, which is what this backend puts at the end of its fix branch names.

**Confusion matrix.** A table that counts, for every pair of "predicted category" and "true category", how many times that pair happened. Correct answers sit on the diagonal and mistakes sit elsewhere, so it shows exactly which categories get mixed up. The calibration endpoint returns a 5-by-5 matrix when labelled failures exist.

**Container.** A running copy of a Docker image: an isolated process with its own files and network that shares the computer's operating system core. Containers start quickly and behave the same on any machine with Docker. Anything a container writes inside itself, such as a SQLite file, is lost when the container is removed.

**CORS (Cross-Origin Resource Sharing).** A browser rule that stops a web page on one address from calling an API on another address, unless that API says it is allowed. The setting `CORS_ORIGINS` lists the addresses allowed to call this backend from a browser, which matters for the dashboard. It does not affect non-browser callers such as GitHub.

**Database.** A program or file that stores data safely and lets you search it quickly, even while many programs read and write at once. This backend stores failures, classifications, fix candidates, and decisions in four tables, using SQLite by default and PostgreSQL in principle for hosted deployments.

**Decision engine.** The small set of fixed rules in `app/core/decision_engine.py` that chooses RETRY, FIX, or ESCALATE from the category, confidence, agreement, and counters. It never reads the database, the clock, or the settings, so the same inputs always produce the same answer. The AI model never chooses the action; this engine does.

**Dependency (FastAPI).** A helper function that FastAPI calls automatically before a route runs, passing its result into the route. A route declares what it needs, such as `Depends(get_db)` for a database session, and FastAPI provides it. This keeps setup code like sessions and token checks in one place, `app/deps.py`.

**Dependency error.** One of the four failure categories: a library or package could not be installed, for example because the requested version does not exist. The sample `fixtures/dependency.log` shows one, where `requests==2.31.0` cannot be found. These are often fixable by changing a version number in a requirements file.

**Diff and unified diff.** A diff is text showing how a file changed: removed lines start with `-` and added lines start with `+`. A unified diff is the standard format that adds a few lines of surrounding context and headers. This backend reads diffs from GitHub as evidence and produces diffs for each candidate patch so they can be compared and reviewed.

**Docker.** A tool that packs a program together with its operating system files, language runtime, and libraries into an image that runs the same way on any machine with Docker installed. It solves "it works on my computer". This project includes a `Dockerfile`, explained line by line in Chapter 2, but Docker is optional.

**Dockerfile.** A text file of step-by-step instructions that Docker follows to build an image, such as "start from Python 3.12", "copy the code", and "install the dependencies". Each step adds a layer, and unchanged layers are reused to make rebuilds fast. This project's `Dockerfile` copies only the code needed to run, never `.env`.

**Dry run.** A mode where the system rehearses everything but does not touch the real world, like a fire drill. With `DRY_RUN=true`, the default, the backend still reads logs, asks models, and records decisions, but every write to GitHub is skipped and a fake `dry-run://` link is stored instead.

**ECE (Expected Calibration Error).** One number that summarises calibration: the average gap between confidence and actual success across all bins, weighted by how many decisions fall in each bin. Zero means perfectly calibrated, and larger numbers mean the confidence is less trustworthy. The calibration endpoint computes it inside SQL.

**Endpoint.** One URL combined with one HTTP method, such as `GET /health` or `POST /webhooks/github`. Each endpoint is handled by one function in the code, often called a route or handler. This backend has sixteen endpoint addresses, listed with explanations in Chapter 5.

**Engine (SQLAlchemy).** The object that manages connections to the database for the whole application. It knows the database address, opens connections when needed, and reuses them. This backend creates one engine at startup in `app/db/session.py` and configures SQLite connections with foreign keys, WAL mode, and a busy timeout.

**Enum (enumeration).** A fixed list of allowed values with names, such as `retry`, `fix`, and `escalate`. Using an enum instead of free text means a typo cannot silently create a new, meaningless value. This project's enums live in `app/core/enums.py` and are `StrEnum`s, so each value is also a plain string.

**Environment variable.** A named piece of text the operating system hands to every program it starts, such as `DRY_RUN=true`. It is the standard way to configure a program without changing its code, supported by every operating system, hosting platform, and container tool. This backend reads all its settings from environment variables and an optional `.env` file.

**`.env` and `.env.example`.** `.env` is a file of `NAME=value` lines that the backend reads as settings, holding your real secrets, and it is never committed. `.env.example` is the shared template listing every setting with safe example values. A test fails if the template and the settings class list different names.

**Escalate.** To hand a problem to a human. In this backend, ESCALATE means opening a GitHub issue that explains the category, confidence, and reasoning, so a developer takes over. It happens whenever the system is not confident enough to retry or fix, and after crashes as a best effort.

**Exception.** The way most languages signal that something went wrong: the code "raises" or "throws" an error, which stops the current function and travels upward until something "catches" it. This backend uses exceptions for real errors, but returns ordinary result objects for expected problems such as a patch that does not apply.

**FastAPI.** The Python web framework this backend is built with. You write normal functions and mark them with decorators such as `@router.get("/health")`, and FastAPI handles receiving requests, checking inputs, turning results into JSON, and generating the interactive documentation page at `/docs`.

**Fixer.** The part of the backend that asks an AI model to write candidate patches for a failure, applies each one with strict checks, and scores how much they agree. It lives in `app/services/fix_generator.py` and asks for three candidates by default, at temperature 0.7, so the attempts are independent.

**Fixture.** A prepared, known starting point for a test or demo. In pytest, a fixture is a reusable setup function a test asks for by name, such as `db_session`. In this project, `fixtures/` is also a folder of sample CI logs with known correct categories, used by the replay script and tests.

**Flaky test, flaky or network failure.** A flaky test sometimes passes and sometimes fails without any change to the code, often because of timing or randomness. A network failure happens when a download or connection blips. Both mean nothing in the code is wrong, so this backend retries them instead of trying to fix code.

**Foreign key.** A column in one table that holds the ID of a row in another table, linking them. Here, every classification, fix candidate, and decision has a `failure_id` pointing at its failure. The database enforces the link, and deleting a failure automatically deletes its linked rows.

**Frozen dataclass.** A Python class written with `@dataclass(frozen=True)`, which mainly holds data and cannot be changed after it is created. Python writes the constructor automatically from the listed fields. This project uses frozen dataclasses for results like `Decision` and `ApplyResult`, so nobody can alter a result after it was produced.

**GitHub Actions.** GitHub's built-in CI/CD system. A repository describes what to run in workflow files under `.github/workflows/`, and GitHub runs them on its own machines when code is pushed. Each execution is a workflow run with an ID, attempts, jobs, a log, and a final conclusion such as `success` or `failure`.

**GitHub App and personal access token (PAT).** Two ways to give a program access to GitHub. A personal access token is tied to one user account; a fine-grained one can be limited to specific repositories and permissions. A GitHub App is a separate identity installed on repositories. Either works for `GITHUB_TOKEN`; the automatic Actions token does not.

**HMAC.** A way to sign a message using a shared secret: the sender mixes the secret with the message through a hash function and sends the result. The receiver repeats the calculation and compares. A match proves the message came from someone with the secret and was not changed. GitHub signs webhooks this way.

**HTTP.** The protocol, meaning the agreed language, that web programs use to talk. One side sends a request with a method, path, headers, and optional body; the other sends a response with a status code, headers, and body. Every web page load and every API call in this project uses HTTP.

**HTTP status code.** A three-digit number in every HTTP response saying how it went. 2xx means success (200 OK, 202 Accepted). 4xx means the caller made a mistake (401 not authorised, 403 forbidden, 404 not found, 409 conflict, 413 too large, 422 bad data). 5xx means the server failed (502, 503).

**httpx.** The Python library this backend uses to send HTTP requests to other servers, namely GitHub and the AI models. It supports asynchronous calls, timeouts, redirects, and streaming replies, all of which this project needs. In tests, respx intercepts its requests so no real network is used.

**Idempotent.** An operation is idempotent if doing it twice has the same effect as doing it once. The secret redactor is idempotent: running it on already-redacted text changes nothing. This matters because text may pass through redaction at several points, and it must not get worse each time.

**Image (Docker).** A read-only, sealed bundle containing a small operating system, a language runtime, libraries, and the program, built from a Dockerfile. An image does nothing by itself; running it starts a container. You can copy an image to any machine or hosting platform and it behaves the same.

**Issue (GitHub).** A ticket on GitHub where people describe and discuss a problem, with a title, body, comments, and labels. When this backend escalates, it opens an issue whose body starts with `AI-generated, human review required.` and includes a marker so a later run can find and reuse it instead of opening a duplicate.

**Jaccard similarity.** A way to measure how similar two sets are: the number of items they share divided by the number of distinct items in both together. Two identical sets score 1 and two sets with nothing in common score 0. The agreement scorer applies it to the words in two diffs.

**JSON.** A plain-text format for structured data, made of objects in curly braces, lists in square brackets, strings, numbers, `true`, `false`, and `null`. Almost every language can read and write it. This backend receives JSON from GitHub, returns JSON to the dashboard, and requires the AI models to reply in JSON so answers can be checked automatically.

**JWT (JSON Web Token).** A common token format made of three base64-encoded parts separated by dots, often used for logins. Because JWTs frequently appear in logs and grant access, the redactor recognises them and replaces each one with `[REDACTED:JWT]` before any text reaches a model or GitHub.

**Kaggle.** A data-science website that offers free notebooks with GPUs for a limited number of hours per week. The team runs a Qwen model there and exposes it to the internet through a Cloudflare tunnel, so this backend can call it as an OpenAI-compatible model server.

**Linter.** A tool that reads code without running it and reports style problems and likely bugs, such as unused imports or missing documentation. This project uses ruff as its linter, configured in `pyproject.toml`, and `ruff check .` should report no problems before changes are shared.

**LLM (Large Language Model).** An AI program trained on a huge amount of text that reads instructions and writes replies, such as Qwen, Llama, or Gemini. LLMs are good at reading messy logs and spotting patterns but can be confidently wrong, which is why this backend checks every reply and lets fixed rules make the decisions.

**`LLMClient`.** This project's description of what any model client must be able to do: `complete(...)` to get a reply and `ping()` to check the model is reachable. It is a Protocol in `app/llm/base.py`. The fake `MockLLMClient` and the real `HttpLLMClient` both fit it, so providers can be swapped by settings alone.

**localhost and port.** `localhost`, or `127.0.0.1`, means "this same computer". A port is a numbered door on a computer that one program listens on, so many programs can run side by side. When run locally, this backend listens on port 8000, so it is reached at `http://127.0.0.1:8000`.

**Log.** The text a program prints while it runs. GitHub Actions saves a log for every job, often thousands of lines with timestamps and special `##[error]` and `##[group]` marker lines. This backend downloads the log, redacts secrets, and trims it to the lines around the first error before showing it to a model.

**Medoid.** The member of a group that is, on average, most similar to all the others: the most typical member. Unlike an average, it is always a real member of the group. The agreement scorer picks the medoid patch as the one to use in the pull request.

**Middleware.** Code that runs around every request and response, before and after the route itself. This backend uses one middleware, the CORS handler, which adds the headers a browser needs to allow the dashboard to call the API from a different address.

**Migration.** A script that changes an existing database's tables to match new code, such as adding a column, while keeping the data. This project has no migrations, so after a table change you must delete the SQLite file or create a fresh PostgreSQL database.

**Mock and fake.** Stand-ins for real services that behave predictably. `MockLLMClient` returns scripted replies instead of calling a real model, and `ReplayGitHub` returns a fixed log instead of downloading one. They make tests and demos fast, free, offline, and repeatable, and let you test rare situations on purpose.

**n8n.** A workflow automation tool the team uses to connect services together. It can forward CI failures to this backend at `POST /webhooks/n8n`, with its own shared token. The backend then reloads the run's details from GitHub instead of fully trusting the data n8n sends.

**OpenAI-compatible API.** A request and reply format for chat models, first published by OpenAI and now copied by many providers: you `POST` messages to `/v1/chat/completions` and read the reply from `choices[0].message.content`. Because Kaggle's llama-server, Groq, and Gemini all accept it, one client class works with all of them.

**Orchestrator.** The class in `app/services/orchestrator.py` that runs one failure through every step: claim, fetch, redact, classify, decide, generate and score patches, decide again, and act. It records every result in the database and handles timeouts and crashes, trying to escalate so a human always hears about a problem.

**ORM (Object-Relational Mapper).** A library that lets you work with database rows as normal objects in code, instead of writing SQL by hand. You define a class like `Failure`, and the ORM creates the table and writes the queries. This project uses SQLAlchemy as its ORM; most languages have similar libraries.

**Patch.** A proposed change to a file. In this project, a patch from the fixer is a set of SEARCH/REPLACE blocks for exactly one file. It goes through many checks in `app/core/patch_apply.py` before it can count, and only the most typical working patch is used in a pull request.

**pip.** Python's package installer. It reads a project's `pyproject.toml`, downloads the libraries it needs, and installs them, ideally into a virtual environment. `pip install -e ".[dev]"` installs this project in editable mode with its development tools; `pip install .` is the plain install used on servers and in Docker.

**PostgreSQL.** A full database server that many programs can share over a network, widely used in real deployments. The README recommends it for hosted deployments because their disks are temporary. This project's code is written to work with it, but `pyproject.toml` does not include the driver package it needs.

**Primary key.** A column whose value is unique for every row in a table, so each row can be identified exactly. In this project, every table's primary key is a number called `id`, assigned automatically by the database. Other tables refer to a row by storing its `id` as a foreign key.

**Prompt.** The text sent to an AI model, usually a list of messages with roles. The `system` message holds the fixed rules and the `user` message holds the request and data. This backend's prompts are written in `app/llm/prompts.py`, with all untrusted data wrapped in clearly marked fences.

**Prompt injection.** An attack where the data given to a model contains text pretending to be new instructions, such as a log line saying "ignore previous instructions". If the model obeys, an attacker can steer the system just by printing text. This backend fences untrusted text, checks replies strictly, and never lets the model choose the action.

**Protocol (Python typing).** A description of the methods an object must have, without requiring it to inherit from a particular class. Other languages call this an interface. `LLMClient` is a Protocol: any object with the right `complete` and `ping` methods counts, which is how the fake and real model clients are interchangeable.

**Pull request (PR).** A request on GitHub to merge one branch into another, showing exactly what changed so people can review, discuss, and approve it first. This backend opens pull requests from `ai-fix-*` branches with its proposed fix, but never merges them; a human always makes that decision.

**Pydantic.** A Python library that checks data has the right shape and types, and rejects it with a clear error otherwise. This project uses it for webhook bodies, API responses, settings, and especially the AI models' replies, which must match `ClassifierOutput` or `FixProposal` exactly, with no extra fields.

**pytest.** The tool that runs this project's automated tests. It finds functions whose names start with `test_` in the `tests/` folder, runs them, and reports which passed or failed. The project's 157 tests run in seconds with no network, because they use fakes and an in-memory database.

**Rate limit.** A cap a service places on how many requests a caller may send in a period of time. When GitHub's limit is reached, it answers with an error and headers saying when to try again. This backend's GitHub client reads those headers and waits before retrying instead of sending more requests.

**Redaction.** Replacing secret values in text with harmless placeholders, such as turning a GitHub token into `[REDACTED:GITHUB_TOKEN]`. This backend redacts logs, diffs, and files before any model sees them, and redacts again before any text is sent to GitHub, using format-specific rules in `app/services/secret_redactor.py`.

**`RedactedText`.** A special string type in `app/core/redacted.py` that means "this text is safe to send to a model". It cannot be created directly, only by the redactor or for fixed text written by the developers. The model message class refuses any other type, so unredacted text cannot reach a model by accident.

**Repository (git).** A project's files together with their full history, usually hosted on a site such as GitHub and named `owner/name`, for example `acme/demo`. This backend acts only on repositories listed in `ALLOWED_REPOS`, and only on `ai-fix-*` branches within them.

**Repository (code pattern).** A class whose only job is to read and write one kind of database record, so the rest of the code never writes queries itself. This project has `FailureRepository`, `ClassificationRepository`, `FixCandidateRepository`, `DecisionRepository`, and `StatsRepository` in `app/db/`. The name is unrelated to git repositories.

**respx.** A testing library that intercepts requests sent with httpx and answers them with prepared responses. This project's tests use it to pretend to be GitHub and model servers, so they can check exactly which requests are sent and how the code handles errors, without touching the real internet.

**REST API.** A common style of web API where URLs name things, such as `/failures/12`, and HTTP methods say what to do with them: GET to read, POST to create or trigger, PUT to replace. GitHub's API follows this style, and so do this backend's own endpoints.

**Retry.** In this backend, RETRY means asking GitHub to run only the failed jobs of a workflow run again, because the failure looked flaky. The decision stores the next expected `run_attempt`, so when the rerun finishes, its webhook can record `retry_success` or `retry_failure`.

**Router.** A group of related endpoints in FastAPI, defined in one file and plugged into the application. This backend has five routers, one per file in `app/api/`: health, webhooks, failures, decisions, and stats. The dashboard routers attach the API token check to all their routes at once.

**ruff.** A fast Python linter and import sorter, configured in `pyproject.toml` with rules for style, likely bugs, and documentation on every function in application code. Running `ruff check .` from inside `ci-heal-backend/` should print `All checks passed!` when the code is clean.

**`run_id` and `run_attempt`.** GitHub's identifiers for a workflow run. `run_id` names one run, and `run_attempt` counts how many times it has been run, starting at 1. Re-running increases `run_attempt` but keeps `run_id`. This backend uses the pair, plus the repository, to avoid duplicates and to match reruns to their RETRY decisions.

**Savepoint.** A mini-transaction inside a larger transaction that can be undone on its own without undoing everything else. `FailureRepository.get_or_create` uses one when inserting a failure, so if two identical webhooks arrive at the same moment, the losing insert is undone cleanly and the existing row is returned.

**Schema.** The expected shape of some data: which fields exist, their types, and their limits. In this project, schemas are Pydantic classes in `app/schemas/`, describing what is stored, what the API returns, and the exact JSON the models must reply with. The word is also used for the structure of database tables.

**SEARCH/REPLACE block.** The patch format the fixer must use: "find this exact text in the file, and put this other text in its place". The search text must appear exactly once, so the change is unambiguous. A patch may contain up to five blocks by default, all in one file.

**`SecretStr`.** A Pydantic type for secret values that prints as `**********` instead of the real value, so secrets do not appear in logs or error messages by accident. Code must call `.get_secret_value()` on purpose to read it. All tokens and keys in this project's settings use it.

**Self-consistency.** Asking a model the same question several times and voting on the answers, to get a more reliable result than one reply. In this backend, with several classifier samples, the stored confidence is the smaller of the average reported confidence and the winning vote share, so a split vote lowers confidence.

**Server.** A program that starts and then waits for other programs to send it requests, answering each one, until it is stopped. This backend is a server: Uvicorn runs it, it listens on a port, and it answers webhooks from GitHub and requests from the dashboard.

**Server-sent events (SSE) and streaming.** Streaming means a server sends its reply in small pieces as they are produced, instead of all at once at the end. Server-sent events are one standard format for this. The model client streams by default because the Cloudflare tunnel in front of Kaggle closes requests that stay silent for about two minutes.

**Session (SQLAlchemy).** One unit of database work. You make changes through a session, then commit them all together or roll them all back. This backend gives each request its own session, and the orchestrator opens a separate session for each failure it processes.

**SQL.** Structured Query Language, the language relational databases understand, with commands such as `SELECT`, `INSERT`, and `UPDATE`. Most of this backend's SQL is written by SQLAlchemy from Python code, but `app/db/stats.py` builds more advanced SQL so statistics are calculated inside the database.

**SQLAlchemy.** The Python database library and ORM this project uses. It turns table classes into real tables, writes queries, and manages connections and sessions. The same code works with SQLite and PostgreSQL, which is why switching databases is mainly a change of `DATABASE_URL`.

**SQLite.** A small database stored in a single file, with no separate server program to install or run. It is perfect for learning, tests, and demos. This backend uses `ci_heal.db` by default, and an in-memory SQLite database, which disappears when the program ends, for tests and the replay script.

**Status code.** See HTTP status code.

**Temperature.** A model setting that controls how random its replies are. At 0, the same input always gets the same reply. Higher values, such as 0.7, make replies vary. This backend classifies at 0 by default for stable answers and generates fixes at 0.7 so the candidate patches are independent.

**Threshold or gate.** The minimum value needed before an action is allowed, such as 0.75 classifier confidence before trying a fix or 0.6 agreement before opening a pull request. The thresholds live in the settings, are passed into the decision engine, and are saved with every decision so its reasoning can be checked later.

**Token (secret).** A secret string that proves identity to a service, like a password designed for programs. `GITHUB_TOKEN`, `API_TOKEN`, and model API keys are all tokens in this sense. They must never be committed or printed, which is why this project stores them as `SecretStr` and keeps them in `.env`.

**Token (model).** A small piece of text, often part of a word, that AI models count when reading and writing. A rough guide is three or four characters per token. Settings such as `CLASSIFIER_MAX_TOKENS` and `FIX_MAX_TOKENS` limit how long a reply may be, measured in these tokens.

**Transaction, commit, and rollback.** A transaction is a group of database changes that must all succeed or all fail together. Commit makes the changes permanent; rollback undoes everything since the last commit. This backend commits after each important step and rolls back on errors, so half-finished work never leaves broken data.

**Tunnel.** A service that gives a program running on a private machine a public internet address, by forwarding traffic through an outside server. The team uses a Cloudflare tunnel so this backend can reach the model running inside a Kaggle notebook. Tunnels can add their own limits, such as closing idle requests.

**Uvicorn.** The program that runs this FastAPI application as a web server. It opens the port, accepts connections, and passes each request to FastAPI. Start it with `python -m uvicorn app.main:app`, which works even where Windows blocks the separate `uvicorn.exe` file.

**Virtual environment (venv).** A private folder holding its own Python and its own installed libraries for one project, so different projects cannot interfere with each other's library versions. This project's is `ci-heal-backend/.venv`, created with `python -m venv .venv` and activated in each new terminal.

**WAL (Write-Ahead Logging).** A SQLite mode that writes changes to a separate log file first, which lets one program read the database while another writes to it. This backend turns it on for every SQLite file connection, so the dashboard can read while the pipeline saves results.

**Webhook.** An HTTP request one system sends to another automatically when something happens, so the receiver does not have to keep asking. It is like a doorbell. GitHub sends a webhook to `POST /webhooks/github` each time a workflow run completes, which is how this backend learns about failures.

**Workflow and workflow run.** A workflow is a recipe file in a repository's `.github/workflows/` folder that tells GitHub Actions what to do, such as install and test. A workflow run is one execution of that recipe, with an ID, attempts, jobs, logs, and a final conclusion. This backend reacts to completed workflow runs.

---

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 6, Status and running](06-status-and-running.md)
