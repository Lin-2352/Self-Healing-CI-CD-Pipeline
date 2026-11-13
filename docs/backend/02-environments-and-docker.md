# Chapter 2: Environments, settings, and Docker

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 1, Basics](01-basics.md) · Next: [Chapter 3, How it works](03-how-it-works.md)

The word **environment** means two different things in this project, and people mix them up all the time. This chapter separates them, then answers the practical question: *which environment should I run the backend in, and how?*

1. A **Python environment** is the set of installed libraries the code runs with. Here that is a folder called `.venv`.
2. A **running environment** is the situation the backend runs in: your laptop for learning, the test suite, or a real server on the internet. The setting `APP_ENV` (`dev`, `test`, or `prod`) names it, and other settings like `DRY_RUN` and `DATABASE_URL` shape it.

---

## 2.1 Which environment should you use?

Use the stages below in order. Each one adds a bit of realism and a bit of risk, so do not skip ahead. You can stay at Stage 1 for as long as you are learning. You only need the later stages for a live demo or for collecting real results.

### Stage 1: your own computer, everything fake (start here)

This is the safest place to learn. You run the backend on your laptop with `APP_ENV=dev`, `DRY_RUN=true`, both models set to `mock`, and a SQLite file. Nothing touches GitHub or a real AI model, so nothing can break and nothing costs money. Use it to read the code, run the tests with `pytest`, run `scripts/replay_failure.py` on the sample logs, and click through the API at `http://127.0.0.1:8000/docs`.

One thing to expect: with the `mock` provider and no scripted replies, the fake model answers every question with the empty JSON object `{}`. That reply has no category, so it fails the format check, and a real failure sent to the running server ends up **escalated**. The replay script avoids this by scripting sensible fake replies from `fixtures/labels.json`. So use the replay script, not the running server, to watch a full "fix" path offline.

### Stage 2: your own computer, real AI model

The only change from Stage 1 is that you point one or both model roles at a real model: the team's Kaggle server through its tunnel, Groq, or Gemini. You set `CLASSIFIER_LLM_PROVIDER=http` (and/or `FIXER_LLM_PROVIDER=http`), plus the base URL, API key, and model name. Keep `APP_ENV=dev` and `DRY_RUN=true`. Run `python scripts/replay_failure.py --llm http fixtures/dependency.log` to see the real model's answer while GitHub is still faked.

This stage tells you whether the model actually classifies and fixes well. It costs nothing on GitHub because GitHub is still faked in the replay script. It may cost API credit, depending on the provider. If the Kaggle notebook is not running, the request fails, and `GET /health/llm` will report it as not reachable.

### Stage 3: automated tests (happens on its own)

You never set this one by hand. When you run `pytest`, the file `tests/conftest.py` builds its own settings with `APP_ENV="test"` and an in-memory SQLite database (`sqlite:///:memory:`), and it tells pydantic-settings to ignore your `.env` file, so your personal settings never leak into a test and nothing is written to disk. GitHub is faked with respx, and the models are `MockLLMClient` objects with scripted replies. That is why the 157 tests run in a few seconds with no internet. `test` also behaves like `prod` for security: it does not allow an empty API token.

### Stage 4: a real server, still in dry-run

This is the first time the backend runs on the internet, for example on Render or Railway. You set `APP_ENV=prod`, a strong `API_TOKEN`, a `GITHUB_WEBHOOK_SECRET`, `ALLOWED_REPOS` naming only your test repository, a GitHub token, and a hosted database. **Keep `DRY_RUN=true`.** Then you add a webhook in the test repository's GitHub settings pointing at `https://<your-server>/webhooks/github`. Failed runs then arrive for real, and the backend reads real logs, but every write is skipped.

The decisions it records are marked `dry_run=true` with a `dry-run://` link instead of a real PR or issue. This stage proves the delivery, signature, allowlist, and model wiring all work together, without any risk to code.

### Stage 5: a real server writing to a sandbox repository

Only when Stage 4 behaves well, set `DRY_RUN=false`, and only while `ALLOWED_REPOS` names a **sandbox** repository that nobody depends on. Now the backend really reruns jobs, really creates `ai-fix-*` branches and pull requests, and really opens issues. When a fix branch's own CI run finishes, GitHub sends another webhook, and the decision gets a real `verified_success` or `verified_failure` outcome, which is what the statistics need. This stage has not been tried yet in this project.

### Summary of the five stages

| Stage | Where | APP_ENV | DRY_RUN | Models | Database |
| --- | --- | --- | --- | --- | --- |
| 1 | Laptop | dev | true | mock | SQLite file |
| 2 | Laptop | dev | true | http | SQLite file |
| 3 | Laptop (pytest) | test | true | scripted mock | in-memory SQLite |
| 4 | Render or Railway | prod | true | http | hosted Postgres |
| 5 | Render or Railway | prod | false | http | hosted Postgres |

---

## 2.2 Python virtual environments (`.venv`)

### The problem a virtual environment solves

Every Python project needs libraries, and each project may need different versions. If you install everything into the one Python on your computer, projects start fighting: one needs SQLAlchemy 2, another needs version 1. A **virtual environment** is a private folder holding its own copy of Python and its own libraries for one project only. It is like giving each project its own toolbox instead of sharing one messy drawer. Other ecosystems have the same idea, such as a per-project dependency folder.

### Creating and activating one

`python -m venv .venv` creates the folder `.venv` inside `ci-heal-backend/`. `.\.venv\Scripts\Activate.ps1` (on Windows PowerShell) **activates** it. That means, for this terminal window only, the commands `python` and `pip` now point into `.venv`. Your prompt usually shows `(.venv)` as a reminder. If you open a new terminal, activate again. To leave, type `deactivate`. Deleting the `.venv` folder removes the environment completely without touching your system Python.

### Installing the project (`pip install -e ".[dev]"`)

**pip** is Python's package installer. It reads `pyproject.toml`, which lists what this project needs: FastAPI, Uvicorn, Pydantic, pydantic-settings, SQLAlchemy, and httpx. The `.` means "this folder". `[dev]` adds the extra tools for development: pytest, respx, and ruff. `-e` means **editable**: instead of copying the code, pip links to it, so when you edit a file the change is used immediately without reinstalling. On a server you use plain `pip install .` because nobody edits the code there.

### Which Python version

The file `.python-version` says `3.12`, and the README says to develop and deploy on Python 3.12. `pyproject.toml` allows 3.11 and newer. During this project the tests were also run on Python 3.14.6 and passed, but 3.12 is the version the project aims at, and it is the version the `Dockerfile` uses (`python:3.12-slim`). If you are choosing, choose 3.12.

---

## 2.3 Settings and environment variables

### What an environment variable is

An **environment variable** is a named piece of text that the operating system hands to every program it starts, like `DRY_RUN=true`. It is a standard way to configure a program without changing its code. Every operating system, every hosting platform, and every container tool supports it. It is like a note pinned on the door of the kitchen saying "today, do not deliver anything", which every cook reads when they arrive.

### How this backend reads its settings

`app/config.py` defines a class called `Settings`. Each attribute is one setting with a type and a default, such as `dry_run: bool = True`. The library pydantic-settings fills each attribute from the environment variable with the same name in capital letters, so `dry_run` reads `DRY_RUN`. Names are not case-sensitive. It converts text into the right type (`"true"` becomes `True`, `"0.75"` becomes `0.75`) and refuses to start if a value is invalid, for example `CLASSIFY_THRESHOLD=2`, because thresholds must be between 0 and 1.

### The `.env` file

Typing many environment variables every time is tiring, so pydantic-settings also reads a file called `.env`, one `NAME=value` per line. `app/config.py` sets `env_file=".env"`, which is looked up relative to the **folder you start the program from**. So start the server from inside `ci-heal-backend/`, or your `.env` will not be found and every setting falls back to its default. If the same name is set both as a real environment variable and in `.env`, the real environment variable wins.

### `.env.example` versus `.env`

`.env.example` is a template that lists every setting with safe example values. It is meant to be shared, and a test fails if it and `Settings` drift apart. `.env` is your personal copy, where you put real secrets. `.gitignore` lists `.env` so it is never committed. Never put a real token into `.env.example`. The usual first step is `copy .env.example .env` and then edit `.env`.

### One surprise after copying `.env.example`

`.env.example` contains `API_TOKEN=replace-me`. Once you copy it to `.env`, the API token is no longer empty, so even in `dev` the protected routes (`/failures`, `/decisions`, `/stats`, and reprocess) now expect the header `Authorization: Bearer replace-me`. If those routes return `401`, that is why. Either send that header (the `/docs` page has an **Authorize** button) or set `API_TOKEN=` to empty in `.env` for local learning in `dev` only.

### Settings are read once

`get_settings()` in `app/config.py` is marked `@lru_cache`, so the settings are read the first time they are needed and then reused for the whole life of the process. If you edit `.env` while the server is running, nothing changes. Stop the server and start it again. With `--reload`, Uvicorn restarts when Python files change. Unclear from the code: whether `--reload` also notices a change to `.env`; to be safe, restart by hand.

### Secret settings (`SecretStr`)

Settings that hold secrets, like `GITHUB_TOKEN` and the model API keys, use the type `SecretStr`. If someone prints or logs the settings object, a `SecretStr` shows as `**********` instead of the real value. The code has to call `.get_secret_value()` on purpose to read it. It is a small guard against the most common leak: a debug print that ends up in a public log.

---

## 2.4 What `APP_ENV` actually changes

`APP_ENV` can only be `dev`, `test`, or `prod`. Any other value stops the app at startup. Searching the code, it changes exactly three things. Everything else behaves the same in all three modes.

### The API token rule

In `app/deps.py`, if `APP_ENV` is `dev` **and** `API_TOKEN` is empty, the protected routes are open without a token, which is handy on your own laptop. In `test` and `prod`, every request to those routes must carry `Authorization: Bearer <API_TOKEN>`. If `API_TOKEN` is set, it is required in every mode, including `dev`. If you forget to set it in `prod`, the routes do not open up; `app/security.py` treats an empty secret as never matching, so every request is refused with `401`. This protects failure data and stats once the server is on the internet.

### The GitHub webhook signature rule

In `app/api/webhooks.py`, if `GITHUB_WEBHOOK_SECRET` is empty, a webhook is accepted only when `APP_ENV` is `dev`. In `test` and `prod`, a missing secret means every GitHub delivery is rejected with `401`. When a secret is set, the signature is checked in every mode. The n8n webhook has no dev shortcut: `N8N_SHARED_TOKEN` is always required.

### What `/health` reports

`GET /health` includes an `environment` field showing the current `APP_ENV`, plus the `dry_run` value. That lets you open one URL on a deployed server and confirm, without reading any files, that it is really running in `prod` and whether it is allowed to write to GitHub. The startup log line also prints both values.

---

## 2.5 What `DRY_RUN` actually changes

### The idea

**Dry run** means "rehearse everything, but do not touch the real world". It is like a fire drill: everyone walks the route, nobody calls the fire brigade. With `DRY_RUN=true`, which is the default, the backend still downloads logs, still asks the model, still scores the candidate fixes, and still writes a decision to its own database. It just stops right before any action that would change GitHub.

### Where it stops

In `app/services/github_client.py`, every **write** method returns early when dry-run is on: creating a branch, saving a file (a `PUT` request), opening a pull request, opening an issue, and rerunning failed jobs. **Read** methods still run, so with a real token and a real webhook, logs and files are really downloaded. The replay script fakes even the reads. The recorded decision gets `dry_run=true` and a link starting with `dry-run://` instead of a real GitHub link.

### Why dry-run results never get an outcome

The final outcome of a fix (`verified_success` or `verified_failure`) arrives only when GitHub runs CI on the new `ai-fix-*` branch and sends a webhook. In dry-run nothing is pushed, so that webhook never comes, and the outcome stays `pending` forever. For the same reason, the statistics endpoints leave out dry-run decisions unless you add `include_dry_run=true`. That is why a local demo never moves the success rate.

---

## 2.6 Databases per environment

### SQLite for learning

The default `DATABASE_URL=sqlite:///./ci_heal.db` means "a SQLite file called `ci_heal.db` in the folder I was started from". It appears the first time the server starts, because tables are created at startup. `app/db/session.py` turns on three SQLite settings for every connection: foreign keys (so links between tables are enforced), WAL mode (so a reader and a writer can share the file), and a 5-second wait when the file is busy.

### In-memory SQLite for tests and replays

`sqlite://` or `sqlite:///:memory:` means a database that lives only in memory and disappears when the program ends. The tests and the replay script use it, so they never leave a file behind. `app/db/session.py` keeps exactly one shared connection for this case, because otherwise each new connection would see its own empty database and the tables would seem to vanish.

### PostgreSQL for a real server

On Render or Railway the free disk is **ephemeral**: it is wiped on every restart or redeploy, so a SQLite file there loses all history. For anything you want to keep, the README says to put a hosted Postgres URL in `DATABASE_URL`. SQLAlchemy needs a separate **driver** package to talk to Postgres. `pyproject.toml` does not list one (such as `psycopg`), so a Postgres URL needs that extra package installed, and possibly a matching URL prefix such as `postgresql+psycopg://`. Unclear from the code: which driver the team intends to use; none has been tested here.

### No migrations

A **migration** is a script that changes an existing database's tables when the code's models change, for example adding a column. This project has none. At startup, `create_all` creates tables that are missing, but it never changes a table that already exists. So after a change to `app/db/models.py`, stop the app and delete `ci_heal.db` (or create a fresh Postgres database). Otherwise the old file keeps the old shape and the app can fail when it uses the new column.

---

## 2.7 Running it on your own computer (Stage 1, step by step)

All commands are for Windows PowerShell, run from inside the `ci-heal-backend` folder.

1. **Create the virtual environment.** `python -m venv .venv` makes a private Python for this project in a new `.venv` folder. You do this once.
2. **Activate it.** `.\.venv\Scripts\Activate.ps1`. Do this in every new terminal. If PowerShell refuses with a message about running scripts being disabled, that is a Windows policy on script files; Unclear from the code: the exact policy on your machine, so ask whoever manages it, or run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` if you are allowed to.
3. **Install the project and the dev tools.** `pip install -e ".[dev]"`. This downloads the libraries listed in `pyproject.toml` into `.venv`. You redo it only when `pyproject.toml` changes.
4. **Create your settings file.** `copy .env.example .env`. Then open `.env` and read it. For pure learning in `dev`, you may set `API_TOKEN=` to empty so the protected routes open without a header.
5. **Check everything works.** `pytest` should report all tests passed, and `ruff check .` should report no problems. If tests fail before you changed anything, the installation is the first suspect.
6. **Try the offline demo.** `python scripts/replay_failure.py fixtures/dependency.log` runs one sample failure through the whole pipeline with a fake model and fake GitHub, and prints each step. [Chapter 6](06-status-and-running.md) explains every line it prints.
7. **Start the server.** `python -m uvicorn app.main:app --reload`. Use `python -m uvicorn` rather than `uvicorn`, because on this machine Windows Application Control can block `uvicorn.exe`. `--reload` restarts the server when you save a Python file.
8. **Look at it.** Open `http://127.0.0.1:8000/health` for a quick status and `http://127.0.0.1:8000/docs` for a page where you can try every endpoint. Press `Ctrl+C` in the terminal to stop the server.

A note on webhooks: GitHub's servers cannot reach `127.0.0.1` on your laptop, because that address only means "this computer". To receive real webhooks locally you would need a tunnel tool or a deployed server. This project has no tunnel setup for the backend, so use the replay script locally and save real webhooks for Stage 4.

---

## 2.8 Docker from zero

### The problem Docker solves

"It works on my computer" is the oldest problem in software. Your laptop has one Python version, certain libraries, Windows paths, and certain settings. A server has different ones. **Docker** packs the program together with the exact operating system files, the exact Python, and the exact libraries it needs, into one bundle that runs the same way everywhere Docker is installed. It is like shipping a meal in a sealed lunchbox with its own plate and cutlery, instead of hoping the destination has the right kitchen.

### Image

A Docker **image** is that sealed bundle: a read-only snapshot of a small Linux system with Python, the libraries, and our code already installed. You build it once from instructions and can copy it to any machine or hosting platform. An image does nothing on its own; it is like a recipe card plus all the ingredients in the box, or like an installer that has not been run yet.

### Container

A **container** is a running copy of an image. When you "run" an image, Docker starts a container: an isolated process with its own files and its own network, but sharing your computer's operating system core, so it starts in about a second. You can run many containers from one image. When a container is removed, anything it wrote inside itself is gone, unless you attached outside storage. That matters for SQLite, as explained below.

### Dockerfile

A **Dockerfile** is the text file of instructions Docker follows to build an image, one step per line. Each step adds a layer on top of the previous one. Docker remembers layers that did not change, so rebuilding after a small code change is fast. This project has one at `ci-heal-backend/Dockerfile`. It is short, so here is every line explained.

### The project's Dockerfile, line by line

`FROM python:3.12-slim`
Start from an official, ready-made image that already contains a minimal Linux system and Python 3.12. "slim" means many extra system tools are left out, so the image is smaller and has less to attack. Every Dockerfile starts from some base image like this, so you never install an operating system yourself.

`WORKDIR /app`
Inside the image, create the folder `/app` if it does not exist and make it the current folder for all the following steps and for the running program. It is the equivalent of typing `cd /app` before everything else. This `/app` is a folder inside the container and has nothing to do with the `app/` package folder in the project, even though the names match.

`COPY pyproject.toml README.md ./`
Copy two files from your project folder into `/app` inside the image. `pyproject.toml` lists the dependencies pip must install. `README.md` is copied because `pyproject.toml` names it as the package's readme, and pip expects it to exist when it builds the package. Only these two files are copied here.

`COPY app ./app`
Copy the `app/` package, which is all of the backend's code, into `/app/app` inside the image. Notice what is **not** copied: `tests/`, `scripts/`, `fixtures/`, `docs/`, `.venv/`, and, importantly, `.env`. So your secrets never get baked into the image, and the image only contains what the server needs to run. The project has no `.dockerignore` file, but these explicit `COPY` lines already keep the image small.

`RUN pip install --no-cache-dir .`
Run a command while building the image: install the package and its dependencies into the image's Python. `--no-cache-dir` tells pip not to keep its download cache, which would only waste space in the final image. This is a plain install, not editable, because nobody edits code inside a container. The dev tools (pytest, respx, ruff) are not installed, because `[dev]` is not requested.

`ENV APP_ENV=prod DRY_RUN=true PYTHONUNBUFFERED=1`
Set default environment variables inside the image. `APP_ENV=prod` means the image assumes it runs as a real server, so it requires an API token and a webhook secret. `DRY_RUN=true` keeps it safe by default. `PYTHONUNBUFFERED=1` makes Python print log lines immediately instead of holding them in memory, so you see logs live. Any of these can be overridden when you run the container.

`EXPOSE 8000`
Document that the program inside listens on port 8000. This line does not open anything by itself; it is a label for humans and tools. The actual connection from your computer's port to the container's port is made with `-p` when you run the container.

`CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`
The command that runs when a container starts. It is the same `python -m uvicorn app.main:app` as on your laptop, with two additions. `--host 0.0.0.0` means "accept connections from outside this container"; the default `127.0.0.1` would only accept connections from inside the container, so nobody could reach it. `--port 8000` matches the `EXPOSE` line.

### Building the image

`docker build -t ci-heal-backend .` reads the Dockerfile in the current folder (that is what the final `.` means) and produces an image named `ci-heal-backend` (`-t` stands for "tag", which is a name). Run it from inside `ci-heal-backend/`. You need Docker Desktop (on Windows) or Docker Engine installed and running first. These commands are listed in the README; they were not run during the writing of this guide.

### Running a container

`docker run --env-file .env -p 8000:8000 ci-heal-backend` starts a container from that image. `--env-file .env` reads your `.env` file on your computer and passes each line in as an environment variable, which is how secrets reach the container without being inside the image. Values from `--env-file` override the `ENV` defaults from the Dockerfile. `-p 8000:8000` connects port 8000 on your computer to port 8000 inside the container, so `http://127.0.0.1:8000/health` works. Press `Ctrl+C` to stop it.

### Things to know before using Docker here

- **The image assumes prod.** Because the Dockerfile sets `APP_ENV=prod`, the protected routes need `API_TOKEN` and GitHub webhooks need `GITHUB_WEBHOOK_SECRET`. If your `.env` still says `APP_ENV=dev`, `--env-file` overrides it back to `dev`.
- **SQLite inside a container is temporary.** With the default `DATABASE_URL`, the database file is written inside the container at `/app/ci_heal.db` and is lost when the container is removed. Use a Docker volume or a Postgres URL for anything you want to keep.
- **Hosting platforms choose the port.** Many platforms tell the program which port to use through a `PORT` variable. The Dockerfile hardcodes 8000, so the README says to override the command with `--port $PORT` on such platforms.
- **Docker is optional.** Render and Railway can run this project directly with `pip install .` and the start command below, without Docker. Docker is useful when you want the exact same setup everywhere, or when a platform only accepts containers.

---

## 2.9 Deploying to Render or Railway

### What these platforms are

**Render** and **Railway** are hosting platforms. You connect a code repository, tell them how to install and start the program, and they run it on their servers with a public web address. They also offer hosted Postgres databases. They take care of the machine, the operating system, and HTTPS certificates, so you only manage your program and its settings. Both have free or trial tiers with limits.

### The settings the README gives

- **Runtime:** Python 3.12.
- **Build command:** `pip install .`. The platform runs this once per deploy to install the project.
- **Start command:** `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`. `$PORT` is replaced with the port the platform chooses.
- **Health check path:** `/health/ready`. The platform calls this URL to decide whether the server is alive. It returns `503` if the database cannot be reached, so the platform will not send traffic to a broken instance.
- **Environment variables:** set `APP_ENV=prod`, `API_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `ALLOWED_REPOS`, and `GITHUB_TOKEN` in the platform's settings page, never in a file you commit. Leave `DRY_RUN=true` until a sandbox repository is ready.

### Which GitHub token to use

Use a **fine-grained personal access token** or a **GitHub App installation token** for `GITHUB_TOKEN`. Do not use the automatic token GitHub Actions gives to workflows: pushes made with it do not start new workflow runs, so the verification webhook for an `ai-fix-*` branch would never arrive. On the target repository the token needs Contents, Pull requests, Issues, and Actions read/write, plus Metadata read. Editing `.github/workflows` files additionally needs the Workflows permission and `FIX_ALLOW_WORKFLOWS=true`.

### What one process means here

The server runs as one process, and the pipeline work runs inside it as FastAPI background tasks, not in a separate worker or queue. If the platform restarts the process in the middle of a job, that job is picked up again at the next startup (up to `MAX_RECOVERIES` times). The Kaggle model server also handles one request at a time, so many failures arriving together will queue up and be slow.

---

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 1, Basics](01-basics.md) · Next: [Chapter 3, How it works](03-how-it-works.md)
