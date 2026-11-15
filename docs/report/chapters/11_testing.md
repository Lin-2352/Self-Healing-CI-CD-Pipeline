## Testing and verification

The project has four layers of checks, from fast and local to slow and live. The first three need no GPU and write nothing to GitHub. A fuller guide with commands is in docs/guides/testing.md.

### Layer 1: backend unit tests

`ci-heal-backend` has a pytest suite that uses a mock model and a mocked GitHub client, so it needs no key and no network. It collected 157 tests before the audit fixes described in Chapter 8 and 205 after, all passing, with `ruff check .` and `ruff format --check .` clean (run on 2026-11-15, output recorded in docs/results/BACKEND_AUDIT.md). The suite covers the classifier, the decision rules, patch application, the HTTP model client, the GitHub client, both webhooks, secret redaction, log trimming, the database layer and the settings.

### Layer 2: node suites and eval scorers

`python run_tests.py` runs every suite in `tools/tests/`, the scorer self-tests in `eval/` and a generator check. Each Code node of the n8n workflow is a plain JavaScript file, so `tools/heal_harness.py` can run it under Node with a fake n8n context. The table gives the number of passing assertions each suite printed in its last run (eval/runs/testall/).

| suite | passing assertions | what it checks |
|---|---|---|
| test_build_prompt | 29 | the prompt carries the trimmed CI error and the shipped system prompt |
| test_extract_node | 15 | SEARCH/REPLACE application and every rejection reason |
| test_assemble_reply | 15 | rebuilding a streamed reply, and the failure explanation |
| test_heal_outcome | 6 | the retry outcome against GitHub error bodies |
| test_model_url | 30 | storing and reading the model address, and the fallback reply |
| test_multifile | 19 | candidate file choice and prompt for multi-file bugs |
| test_workflow_structure | 22 | wiring, webhooks and credentials of the shipped workflow JSON |
| test_shipped_eval | 49 | the scorer of the quality study, including 15 of 15 real captured replies |
| check_multifile_cases | 7 | every multi-file case is a real test: golden passes, broken fails |
| test_multifile_eval | 13 | scoring of the multi-file study |
| test_bakeoff_eval | 52 | the bake-off kernel's functions and decision rule |

The generator check regenerates `n8n/self_healing_pipeline.json` from `tools/gen_n8n.py` and the node files and requires a byte-identical result, so the file in the repository cannot drift from its source. In the last full run all 13 checks passed (12 without the rig, 13 with it).

The harness has a known blind spot, stated in its own docstring: it resolves `$('Node')` lookups that n8n's sandbox does not resolve for Code nodes. A passing harness run therefore does not prove that an accessor works in n8n. This is the reason for layer 3.

### Layer 3: a real n8n engine

`python run_tests.py --rig` imports the workflow nodes into a real n8n 1.x and runs them against a local mock of GitHub and the model (tools/n8n_rig/). The nodes under test are the shipped ones, copied from the workflow JSON; only host names are changed and credentials removed. The last run printed 18 passing scenarios and no failures (eval/runs/testall/n8n_rig.txt). They include:

- the CI log reaches the prompt and the PAT is sent to the GitHub API host but not to the blob host the log redirect points to;
- the same fix is produced on a first heal, on a retry (thinking on, same branch, trailer `Heal-Attempt: 2`) and on a trace-only multi-file bug;
- each rejection path of the extract node posts a comment on the commit that quotes its reason;
- a model call that times out with HTTP 524 and a fallback that also fails ends in a pull-request comment that names both causes;
- a model call that times out and a fallback that answers still produces the correct fix and no comment;
- a job log that returns 404 still heals with the text `(log unavailable)`;
- a payload with hostile characters still builds a valid request;
- a payload with `failure_kind=infra` is refused before any model call.

### Layer 4: live runs

Live runs push a deliberately broken commit to a GitHub repository and watch the whole chain, with the real model on Kaggle. They are run by hand because they write to GitHub and use GPU quota. Chapter 9 and Chapter 10 report them.

### Audit

A read-only security and reliability review of `ci-heal-backend` listed 21 findings. Four were fixed with tests that failed first, and 17 are open (docs/results/BACKEND_AUDIT.md). The n8n workflow was reviewed by reading its nodes against the tests: seven nodes (Fetch Job Logs, Fetch Changed Files, Build AI Prompt, Get Model URL, Get File SHA, Push Fix File and Post Failure Comment) have no error setting, so a failure there ends the execution without a comment on GitHub (docs/architecture/n8n-workflow.md).

### What is not tested

- `kaggle/endpoint/deploy_src.py` runs only on a Kaggle GPU; its test is a real boot.
- The GitHub write steps of the workflow, from branch creation to the pull request, run only in live runs.
- The backend with `DRY_RUN=false` against real GitHub has no automated test.
- The OpenAI fallback node is tested for wiring, for shipping without a credential and for its error handling, but never against a real OpenAI account. On the n8n Cloud trial the included free credits answered `Forbidden` to a webhook-triggered run in our test (docs/guides/cloud-n8n.md).
- `demo-ci.yml` is exercised only by pushing to GitHub.
- The launcher scripts have no tests.
