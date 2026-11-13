# ci-heal-backend, explained from the beginning

This is the start page of the guide to the `ci-heal-backend/` folder, the backend of the project "Confidence-Based Self-Healing CI/CD Pipeline Using AI". The guide is written so that someone from any programming background, or someone quite new to programming, can follow it. Ideas that appear in every language, such as servers, databases, and environment variables, are explained from zero before the project-specific parts.

Every statement was checked against the code in `ci-heal-backend/`. Where the code does not answer a question, the text says **"Unclear from the code:"** instead of guessing.

---

## What this backend does, in one paragraph

When a GitHub Actions CI run fails, GitHub sends a message (a webhook) to this backend. The backend downloads the log, removes any secrets, and asks an AI model which of four kinds of failure it is: a dependency problem, a configuration problem, a real test failure, or a flaky or network blip. Fixed rules, not the AI, then choose one of three actions. **Retry** the run if it looks flaky. **Fix** it by opening a pull request on a separate `ai-fix-*` branch, but only when the model is confident and several independent patch attempts agree. **Escalate** it to a human by opening a GitHub issue otherwise. A human always reviews and merges any fix.

---

## Which environment should you use?

Short answer: **start on your own computer with everything fake, and only move to a real server after that works.** The five stages are explained in detail in [Chapter 2](02-environments-and-docker.md#21-which-environment-should-you-use).

| Stage | Where | `APP_ENV` | `DRY_RUN` | Models | Database | Use it for |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Your laptop | `dev` | `true` | `mock` | SQLite file | Learning, tests, offline demo |
| 2 | Your laptop | `dev` | `true` | `http` (Kaggle, Groq, or Gemini) | SQLite file | Checking a real model's answers |
| 3 | Your laptop, inside `pytest` | `test` (automatic) | `true` | scripted fakes | in-memory SQLite | Automated tests |
| 4 | Render or Railway | `prod` | `true` | `http` | hosted PostgreSQL | First real webhooks, no writes |
| 5 | Render or Railway | `prod` | `false` | `http` | hosted PostgreSQL | Real PRs and issues on a sandbox repository only |

Use Python 3.12 (the version in `.python-version` and the `Dockerfile`) inside a virtual environment. Docker is optional. Two things to know before Stage 4: free hosting disks are wiped on restart, so use PostgreSQL there, and `pyproject.toml` does not yet include a PostgreSQL driver package, so one must be added.

---

## Reading order

Read the chapters in order the first time. Afterwards, jump straight to whichever one you need.

1. **[Chapter 1: The basic ideas you need first](01-basics.md)**
   Servers, HTTP, JSON, APIs, webhooks, databases, git and pull requests, CI/CD, AI language models, the Python features used in the code, and the libraries this project depends on. Each idea is explained without assuming any particular language.

2. **[Chapter 2: Environments, settings, and Docker](02-environments-and-docker.md)**
   Which environment to use and why, virtual environments, environment variables and `.env`, what `APP_ENV` and `DRY_RUN` really change, SQLite versus PostgreSQL, step-by-step local setup on Windows, Docker explained from zero with every line of the project's `Dockerfile`, and deploying to Render or Railway.

3. **[Chapter 3: How the backend works](03-how-it-works.md)**
   The problem, the big-picture flow diagram, one real failure followed through all twelve steps from webhook to verification, and every safety feature with what it prevents, how it works, and where it lives in the code.

4. **[Chapter 4: A tour of the code, file by file](04-code-tour.md)**
   Every one of the 51 Python files in `app/` and `scripts/`, with each function or class explained, plus one paragraph for each test file. Use it as a map while reading the code.

5. **[Chapter 5: Reference — settings, endpoints, and database](05-reference.md)**
   Every setting with its default, what it changes, and whether to change it before going live. Every endpoint with who calls it and what it returns. Every database table and what one row means. Also what calibration and ECE mean.

6. **[Chapter 6: Project status, running it, and common questions](06-status-and-running.md)**
   What was planned versus what was built, what is not done yet and why it matters, how the Kaggle model connects, the offline replay output explained line by line, and answers to frequent questions.

7. **[Chapter 7: Glossary](07-glossary.md)**
   Every special word in the guide, in alphabetical order, each with a full explanation.

---

## The quickest possible start

From inside the `ci-heal-backend` folder in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest
python scripts/replay_failure.py fixtures/dependency.log
```

The tests should report `157 passed`, and the replay should end with `final: action=fix` and `processing_status: done`. Nothing in these commands contacts GitHub or a real AI model. [Chapter 6](06-status-and-running.md#64-running-it-yourself) explains what each line of the replay output means.

---

## Other documents

- [`ci-heal-backend/README.md`](../../ci-heal-backend/README.md): the short technical reference, with the architecture diagram, settings table, endpoints, and deployment notes.
- [`ci-heal-backend/docs/LLM_CONTRACT.md`](../../ci-heal-backend/docs/LLM_CONTRACT.md): exactly what a model server must accept and return, for whoever runs the Kaggle server.
