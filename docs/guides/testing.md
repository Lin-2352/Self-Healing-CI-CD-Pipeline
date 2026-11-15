# Testing

The project has four kinds of checks: the `ci-heal-backend` test suite, the n8n node and eval suites run by `run_tests.py`, a rig that runs the workflow in a real n8n engine, and live runs against GitHub and the Kaggle model. The first three need no GPU and write nothing to GitHub.

## ci-heal-backend: pytest and ruff

Run from `ci-heal-backend/` with the virtual environment active (the Quick start in `ci-heal-backend/README.md` creates it):

```powershell
ruff check .
pytest
```

`pytest` and `ruff` are in the `dev` extra of `pyproject.toml`. The suite uses the mock model provider, which is the default, so it needs no key and no network. It has 128 test functions (counted by `def test_`) in these files under `ci-heal-backend/tests/`:

| File | Covers |
|---|---|
| `test_classifier.py`, `test_agreement.py` | Classifier replies, re-asks on malformed output, split votes, prompt injection; agreement scoring between candidate patches |
| `test_decision_engine.py` | The rule table that picks retry, fix or escalate, and the thresholds |
| `test_fix_generator.py`, `test_patch_apply.py` | Candidate fixes, and applying SEARCH/REPLACE edits (uniqueness, CRLF files, path limits, secrets in a replacement, syntax errors, size caps) |
| `test_llm.py` | The HTTP model client: streaming, think-block stripping, retry on 524, no retry on 401, one call at a time |
| `test_github.py` | The GitHub client: dry-run sends no writes, writes to `main` or a non-`ai-fix` branch are refused, rate limits, pagination, log unzip |
| `test_webhooks.py` | Both webhooks end to end with mocks: flaky retry, dependency fix as a dry-run PR, low agreement opens an issue, bad HMAC, missing secret |
| `test_secret_redactor.py`, `test_log_trim.py` | Redaction of known secret formats, and trimming of logs |
| `test_repositories.py`, `test_stats.py` | Database access, status transitions, stats and calibration |
| `test_config.py`, `test_env_example.py`, `test_health.py`, `test_replay.py` | Settings defaults, `.env.example` matching `Settings`, health endpoints, offline replay of the log fixtures |

No workflow in this repository runs these tests automatically. The only workflow is `.github/workflows/demo-ci.yml`, which covers `demo-app/`.

## run_tests.py

From the project root:

```powershell
python run_tests.py
```

Each check's full output is saved to `eval/runs/testall/<name>.txt`. The exit code is 0 only if every check passed. The first group runs every `tools/tests/test_*.py`, each executing the real Code-node bodies from `tools/nodes/*.js` under plain Node through `tools/heal_harness.py`.

| Suite | What it checks |
|---|---|
| `tools/tests/test_build_prompt.py` | `Build AI Prompt` against a real GitHub job log: the user message carries the trimmed error, and the system message equals the shipped prompt |
| `tools/tests/test_extract_node.py` | `Extract AI Response`: applying SEARCH/REPLACE edits, and each rejection with its specific error |
| `tools/tests/test_assemble_reply.py` | `Assemble Model Reply` (rebuilding a streamed reply) and `Explain Failure` |
| `tools/tests/test_heal_outcome.py` | `Heal Outcome` against the GitHub error bodies a retry can return |
| `tools/tests/test_model_url.py` | `Store Model URL`, `Get Model URL` (stored address first, `QWEN_BASE_URL` second) and `Normalize OpenAI Reply` |
| `tools/tests/test_multifile.py` | `Pick Candidate Files`, `Build AI Prompt` and `Extract AI Response` together on the multi-file cases, using real node output as the CI log |
| `tools/tests/test_workflow_structure.py` | The shipped workflow JSON: wiring, the `Set Model URL` webhook, the fallback node with no credential, unique node names, no token strings, `$vars.QWEN_BASE_URL` only in `Get Model URL` |

Then it runs the eval and generator checks:

| Check | What it checks |
|---|---|
| `eval/test_shipped_eval.py` | The scorer of the shipped-quality eval: golden files pass, broken cases fail, known good and bad model outputs score correctly |
| `eval/check_multifile_cases.py` | Each multi-file case is a real test: golden tree passes `npm test`, broken tree fails with exit 1 |
| `eval/test_multifile_eval.py` | The multi-file eval's scoring and the files shown to the model |
| `eval/test_bakeoff_eval.py` | The model bake-off kernel's functions, gates and decision rule |
| `tools/gen_n8n.py --check` | Regenerating the workflow from `tools/nodes/*.js` reproduces `n8n/self_healing_pipeline.json` byte for byte |

Those eval scripts and `tools/tests/test_multifile.py` build their test trees inside `app/` (the path is `ROOT/app` in `eval/check_multifile_cases.py`) and need `express` in `app/node_modules`. `app/` is listed in `.gitignore` as the separate demo repository clone, so a fresh checkout has to provide it.

`eval/test_qualityspeed.py` exists but `run_tests.py` does not call it.

## The real-engine rig

```powershell
python run_tests.py --rig
```

Run this from PowerShell, with n8n 1.x installed under `%LOCALAPPDATA%\selfheal-n8n\app` (install steps in [SETUP.md](SETUP.md)). The rig shells out through `cmd.exe` (see the docstring of `run_tests.py`).

The node suites run the Code nodes under plain Node, which does not reproduce how n8n's sandbox treats them. The rig imports the workflow into a real n8n and runs it against a local mock of GitHub and the model (`tools/n8n_rig/mock2.py` on port 5792), so no GitHub write and no GPU is involved. `Build AI Prompt` stays unmodified in the rig, because that node broke the first live heal.

With `--rig`, `run_tests.py` deletes `tools/n8n_rig/n8nhome2`, starts the mock, runs `build_wf.py` and `drive_full.py setup`, then `drive_full.py all`. The check passes when the output contains `PASS` and no line starts with `FAIL`. The modes are `happy`, `retry`, `multifile`, `extract`, `failure` and `probes`. `tools/n8n_rig/README.md` lists what each expects and the traps that cost time (the deprecated `execute --file`, pipes returning no output on success, PowerShell rewriting JSON).

## Live runs

These push to GitHub and use the model on Kaggle, so they are run by hand, not by `run_tests.py`.

| Script | What it does |
|---|---|
| `tools/golive/run_scenarios.py` | Pushes each broken `test.js` case to the demo repository, waits for CI and the new heal PR, then checks the PR's file parses and matches the golden file. Restores `main` between cases. |
| `tools/golive/run_retry.py` | Pushes a breakage, then follows its heal PR until CI goes green, the attempt cap is reached, or it stalls. Counts commits by their `Heal-Attempt` trailer. |
| `tools/golive/exec_stats.py` | Reads the local n8n database and prints per-execution status, whether the prompt carried a real CI log, and model timings. |

Both drivers run `git` inside `app/` and push to `origin main` there. Results go to `eval/runs/`. The measured outcomes are written up in `docs/results/GOLIVE_RESULTS.md`.

## Not covered by tests

1. `kaggle/endpoint/deploy_src.py` runs only on a Kaggle GPU. Its test is a real boot (`tools/n8n_rig/README.md`, Not covered here).
2. The GitHub write steps of the workflow, from `Create Branch` to `Open Pull Request`, run only live.
3. The `ci-heal-backend` tests use the mock model and mocked GitHub. The `--llm http` option of `scripts/replay_failure.py` calls a real model but still mocks GitHub. A backend run with `DRY_RUN=false` against real GitHub has no automated test.
4. The workflow on n8n Cloud has no automated test. The OpenAI fallback node is checked for wiring and for shipping with no credential, but not against a real OpenAI account.
5. `demo-ci.yml` is exercised only by pushing to GitHub. No local test runs the `Decide` and `Trigger n8n webhook` steps.
6. The launcher `.bat` files have no tests.
