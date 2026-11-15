# Chapter 6: Project status, running it, and common questions

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 5, Reference](05-reference.md) · Next: [Chapter 7, Glossary](07-glossary.md)

---

## 6.1 What was planned and what was built

The project synopsis set out a list of goals. The table shows each one, its current state, and where the work lives. "Done" means the code exists and is covered by tests. "Partly done" means some of it exists but it has not been proven end to end.

| Planned | Status | Where in the code |
| --- | --- | --- |
| Diagnose CI failures on GitHub Actions | Done | `app/api/webhooks.py`, `Orchestrator._fetch`, `app/core/log_trim.py` |
| Classify into dependency, configuration, test failure, flaky/network | Done | `app/services/classifier.py`, `app/llm/prompts.py`, `app/schemas/classification.py` |
| Decide retry, fix, or escalate | Done | `app/core/decision_engine.py`, `Orchestrator._pipeline` |
| Measure confidence with multiple candidates | Done | `app/services/fix_generator.py`, `app/core/agreement.py`, optional voting in `classifier.py` |
| Pull requests only, with human approval | Done | `app/services/pr_service.py`, `GitHubClient._refuse`; no merge code exists |
| Retry flaky failures | Done | `app/services/rerun_service.py` |
| Escalate to a human | Done | `app/services/issue_service.py` |
| Re-run to verify fixes | Partly done | `webhooks._intake` and `_verify_fix`; never run live |
| Store results for a dashboard | Partly done | `app/db/`, `app/api/`; the React dashboard is not connected |
| Evaluate on the Zheng et al. dataset | Not started | Groundwork: `failures.ground_truth_category` and `/stats/calibration` |
| Compare with "retry only" and "fix everything" baselines | Not started | No baseline code exists |

### Why verification is only "partly done"

The code that links a later CI run back to its RETRY or FIX exists and is tested with fake webhooks. It has never been seen working against real GitHub, because that needs `DRY_RUN=false` on a sandbox repository, which has not been done. It also depends on the target repository actually running CI on `ai-fix-*` branches, which is decided by that repository's own workflow files rather than by this backend.

### Extra things built that the synopsis did not mention

Several safety and reliability features were added along the way, because they turned out to be necessary for running an AI tool against real repositories safely. They are secret redaction with the `RedactedText` type, prompt-injection fences with strict output formats, safe patch application with path checks and size caps, rejection of test-weakening patches, self-consistency voting, calibration statistics with ECE, accuracy, precision, recall, and a confusion matrix, dry-run as the default, the `ai-fix-*` branch gate with fix-loop prevention, HMAC webhook checks with an allowlist and API token, crash recovery, separate classifier and fixer model roles, an n8n entry point, the offline replay script with sample logs, and a test that keeps `.env.example` in step with the settings.

---

## 6.2 What is not done yet

Each item explains why it matters, so you can judge what to do next.

**The Zheng et al. dataset evaluation harness.** This is how the project proves, on real recorded CI failures, how accurate the classifier is and whether its confidence numbers can be trusted. It is central to the project report, because without it the claims about "confidence-based" decisions have no numbers behind them. The database column `ground_truth_category` and the calibration endpoint are ready for it.

**The "retry only" and "fix everything" baselines.** A success rate means little on its own. Comparing it with a system that always retries and a system that always tries to fix shows whether the confidence-based approach is actually better. No code for these baselines exists yet.

**Connecting the React dashboard.** The API endpoints the dashboard needs are built and protected, but no user interface displays them yet. Until it is connected, results can only be seen through the `/docs` page, direct API calls, or the database itself.

**A run against real GitHub.** The HTTP model client was first tested only against fake servers. On 2026-11-15 the six fixtures were replayed through the real pipeline against the Qwen model on Kaggle, with GitHub mocked and `DRY_RUN` on ([BACKEND_ON_KAGGLE.md](../results/BACKEND_ON_KAGGLE.md)): every fixture was classified correctly and all 15 fix candidates applied. What still has no run is the backend with `DRY_RUN=false` against a real repository.

**A sandbox repository run with `DRY_RUN=false`.** No real branch, pull request, issue, or rerun has been created yet. This is the only way to test verification for real and to collect outcomes that the statistics can use.

**Setting the `escalated` and `error` outcomes.** These outcome values exist, and the statistics already expect `escalated`, but no code sets them. As a result, escalations stay `pending` forever and never appear in success statistics, which makes the numbers incomplete.

**Turning on `enable_thinking` for re-asks.** The model contract describes enabling the model's thinking mode when the classifier asks again after a bad reply. The HTTP client supports the flag, but no code passes it, so the feature exists only on paper.

**Schema migrations.** Without a migration tool, any change to the tables means deleting the database and losing its data. That is acceptable for development but not for a long-running deployment with history worth keeping.

**A separate job queue.** Pipeline work runs as background tasks inside the web server process. Many failures at once, or very long model calls, share that one process. A separate worker queue would isolate them, but it is more to set up and run.

**Multi-file fixes.** A fix proposal may edit exactly one file. Failures that need changes in two or more files will not produce agreeing single-file patches, so they will escalate.

**Setting `ground_truth_category` through the API.** Today only code, such as the replay script, can set the correct label on a failure. Labelling many failures for evaluation therefore needs a script.

**A PostgreSQL driver.** The README recommends PostgreSQL for hosted deployments, but `pyproject.toml` does not include a driver package for it, so one has to be added before a `postgresql://` URL will work.

---

## 6.3 How the Kaggle model connects

### The plug-and-socket idea

The backend does not care where the AI model runs. Everything talks to an `LLMClient` (defined in `app/llm/base.py`), and anything with `complete(...)` and `ping()` counts as one. Think of a wall socket: the lamp does not care which power station is at the other end of the wire, as long as the plug fits. This is why moving from the fake model to Kaggle, Groq, or Gemini is a settings change only.

### The two clients

**`MockLLMClient`** in `app/llm/mock.py` returns scripted replies and never touches the network. It is the default, so the server and tests run with no key. **`HttpLLMClient`** in `app/llm/http_client.py` sends a `POST` to `{base_url}/v1/chat/completions` in the OpenAI-compatible format, with `Authorization: Bearer <key>`, and streams the reply by default. It allows only one call at a time per client, because the Kaggle server handles one request at a time.

### The two roles

The **classifier** role uses `CLASSIFIER_LLM_PROVIDER`, `CLASSIFIER_LLM_BASE_URL`, `CLASSIFIER_LLM_API_KEY`, and `CLASSIFIER_LLM_MODEL`. The **fixer** role uses the matching `FIXER_LLM_*` settings. `app/llm/factory.py` reads them at startup: `mock` gives the fake client, `http` gives the real one, and an `http` role with an empty address or key stops the server from starting.

### Example values

These come from `ci-heal-backend/README.md` and `.env.example`.

| Provider | `*_LLM_BASE_URL` | `*_LLM_MODEL` |
| --- | --- | --- |
| Kaggle (through the team's Cloudflare tunnel) | `https://your-tunnel.trycloudflare.com` | `local` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| Gemini (OpenAI-compatible address) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.0-flash` |

A base address ending in `/v1` is accepted; the client removes it so it is not doubled. For the Kaggle server, the key is whatever bearer token that server was started with, and the README says the demo deployment uses `selfheal-e2e-key`. If the tunnel is started again with a new address, the `*_LLM_BASE_URL` settings must be updated and the backend restarted, because settings are read only once at startup. The full request and reply format is in [`ci-heal-backend/docs/LLM_CONTRACT.md`](../../ci-heal-backend/docs/LLM_CONTRACT.md).

### Limits to keep in mind

The README notes that a free Kaggle GPU session is on the order of 30 hours per week, that the Cloudflare tunnel drops requests that stay quiet for about two minutes, and that the model server answers one request at a time. Streaming is on to deal with the tunnel. When Kaggle is offline, requests fail, `GET /health/llm` reports `error`, and failures escalate or end in `error` as described in the FAQ below.

---

## 6.4 Running it yourself

[Chapter 2, section 2.7](02-environments-and-docker.md#27-running-it-on-your-own-computer-stage-1-step-by-step) gives the full setup, step by step. In short, from inside `ci-heal-backend/` in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
python -m pytest
python scripts/replay_failure.py fixtures/dependency.log
python -m uvicorn app.main:app --reload
```

`python -m pytest` should end with a summary like `157 passed`, and `ruff check .` should print `All checks passed!`. When the server is running, `http://127.0.0.1:8000/health` should show something like `{"status":"ok","app":"ci-heal-backend","version":"0.1.0","environment":"dev","dry_run":true}`, and `http://127.0.0.1:8000/docs` shows every endpoint with a "Try it out" button.

### The replay output, line by line

Running `python scripts/replay_failure.py fixtures/dependency.log` prints exactly this:

```text
fixture: dependency.log
ground_truth: dependency_error
database: sqlite:///:memory: (discarded)
dry_run: True
llm: mock
redaction_count: 0
classification: dependency_error confidence=0.950
sample: index=0 category=dependency_error confidence=0.95 parse=ok
stage1: action=candidate_fix reason=candidate_fix
candidates: 3
candidate: index=0 path=app.py applied_ok=True
candidate: index=1 path=app.py applied_ok=True
candidate: index=2 path=app.py applied_ok=True
agreement: 1.000
final: action=fix reason=fix outcome=pending dry_run=True
pr_url: dry-run://github/acme/demo/pull/ai-fix-dependency-error-abc1234
processing_status: done
```

The first five lines describe the setup. `fixture` is the log being replayed, `ground_truth` is its correct category from `fixtures/labels.json`, `database` confirms that an in-memory database was used and thrown away afterwards, `dry_run: True` confirms nothing could be written to GitHub, and `llm: mock` says the fake models were used. These lines let you confirm at a glance that the run was safe and offline.

`redaction_count: 0` means no secrets were found in this log. Try `fixtures/secrets.log` to see a number above zero; the secrets themselves are never printed. `classification` is the final verdict with its confidence, and each `sample` line is one individual answer from the classifier, with `parse=ok` meaning the reply had the right format. With the default of one sample, there is one such line.

`stage1: action=candidate_fix` is the decision engine's first answer: the category is fixable and confidence 0.95 is above the 0.75 gate, so it is worth generating patches. `candidates: 3` says three patches were requested, and each `candidate` line shows which file it edits and whether it passed every check in `apply_patch`. `agreement: 1.000` means the three patches were identical, the highest possible agreement.

`final: action=fix reason=fix` is the stage 2 decision, which is FIX because agreement 1.0 is above the 0.6 gate. `outcome=pending` and `dry_run=True` mean the result can never be verified, because nothing was pushed. `pr_url` is the fake link recorded instead of a real pull request, showing the branch name the backend would have used. `processing_status: done` means the pipeline finished without errors.

### Trying the other sample logs

`fixtures/flaky.log` ends in a network error and finishes with `action=retry`, because the flaky gate is met and no retries have been used. `fixtures/secrets.log` shows a `redaction_count` above zero, proving the redactor found its fake secrets, which are never printed. `fixtures/prompt_injection.log` still classifies as `test_failure`; note that with the fake model this is because the fake answers from `labels.json`, so the real test of the defence happens with `--llm http` and a real model.

To use a real model, set the `CLASSIFIER_LLM_*` and `FIXER_LLM_*` values in `.env` and add `--llm http`. GitHub stays fake and dry-run stays on, so this is safe to try.

---

## 6.5 Frequently asked questions

**Why not let the AI fix every failure?**
Many failures are not code problems at all. A network blip or a flaky test needs a rerun, not an edit, and any edit would be wrong by definition. A tool that always edits produces confident but wrong patches, and can even hide bugs by weakening tests. This backend only fixes when the category is fixable, the classifier is confident, and several independent patches agree with each other.

**Can it push code straight to `main`?**
No. Every branch or commit write goes through `GitHubClient._refuse`, which raises an error unless the branch starts with `ai-fix-` and is not `main`, `master`, or the configured default branch. The branch name is always generated by `make_fix_branch`, never chosen by the model. The backend only opens a pull request and has no code to merge one, so a human always makes the final decision.

**What happens if the Kaggle model is offline?**
The HTTP client retries timeouts and server errors until the role's deadline runs out, then gives up with `LLMError`. In the classifier, that error stops the pipeline: the failure is marked `error` with the error's type name and an escalation issue is attempted. In the fixer, each failed call becomes an invalid candidate, so agreement is "insufficient" and the engine escalates. `GET /health/llm` shows `error`, while `/health/ready` stays OK so the server is not taken offline.

**How do we know the confidence score means anything?**
By measuring it. `GET /stats/calibration` groups decisions by confidence and compares each group with its real success rate, and reports ECE. With labelled failures, it also measures how often the category itself was right. The thresholds should be tuned from those numbers. Today there is very little real data, because the evaluation harness and live runs are not done yet, so the defaults are reasoned choices rather than measured ones.

**What stops a secret from leaking to a model or to GitHub?**
Text must pass through `redact` to become `RedactedText`, and the message class sent to models refuses anything else, so forgetting to redact causes an immediate error. Pull request and issue text is redacted again just before it is sent. A patch whose replacement looks like a secret is rejected. And patches are applied to the original files while the model only ever saw redacted copies.

**What if a log tries to give the model orders?**
The log is fenced inside untrusted markers, and the system prompt tells the model to ignore instructions inside them. A fake closing marker in the log is broken up so it cannot end the fence early. The reply must fit a strict format with no extra fields. And the model never chooses the action; the fixed rules of the decision engine do, so even a fooled model can only change the category and confidence.

**Why do the statistics show nothing after my demo?**
Demos run in dry-run. Nothing is pushed, so GitHub never sends a verification run, and the outcome stays `pending`. The statistics leave out `pending` outcomes and, by default, dry-run decisions. Adding `include_dry_run=true` includes dry-run decisions, but pending ones are still left out, so a pure dry-run demo will always show empty success numbers.

**What if the server crashes in the middle of a job?**
The failure stays marked `processing`. On the next startup, `recover_stuck` finds rows that have been processing longer than `PROCESSING_STALE_SECONDS` and runs them again. After `MAX_RECOVERIES` attempts, the row is marked `error` so a failure that crashes the server cannot crash it forever. An operator can also call `POST /failures/{id}/reprocess` to retry it by hand.

**Why are there two models, a classifier and a fixer?**
Naming a category is a short, simple task that a small, fast hosted model can do quickly and cheaply. Writing a correct patch is much harder and benefits from a larger model such as the one on Kaggle. Each role has its own settings, so they can point at different providers or the same one, depending on cost and availability.

**Why does a duplicate webhook not run the pipeline twice?**
The `failures` table has a unique key on `repo`, `run_id`, and `run_attempt`. `get_or_create` returns the existing row instead of inserting a second one, and `_intake` replies `duplicate` unless that row ended in `error`. On top of that, `claim` guarantees only one worker can move a queued row to processing, so even a race between two deliveries processes the failure once.

**Why does a real webhook escalate when I run the server locally with default settings?**
With the default `mock` provider and no scripted replies, the fake model answers every question with `{}`. That reply has no category, so it fails the format check, the classifier falls back to `unknown` with confidence 0, and the decision engine escalates. This is expected. Use the replay script for an offline "fix" demo, or switch the classifier and fixer to `http` with a real model.

**Do I need Docker?**
No. Docker is one way to run the server with exactly the same setup everywhere, and the project includes a `Dockerfile` for that. You can run everything directly with Python on your laptop, and Render or Railway can run it with `pip install .` and the start command from the README. [Chapter 2, section 2.8](02-environments-and-docker.md#28-docker-from-zero) explains Docker from the beginning.

---

[Back to the start page](BACKEND_EXPLAINED.md) · Previous: [Chapter 5, Reference](05-reference.md) · Next: [Chapter 7, Glossary](07-glossary.md)
