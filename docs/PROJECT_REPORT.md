# Self-Healing CI/CD Pipeline: automatic fixes for failing builds with a self-hosted language model

Project report

November 2026

Repository: hr-agarwal/self_healing_CI-CD_Pipeline

## Introduction

### The problem

A team that pushes code several times a day also breaks its build several times a day. Most of those breaks are small: a missing `require`, a misspelled method name, a status code compared the wrong way round. Each one still costs a person the same routine. They notice the red mark on the commit, open the CI log, scroll to the first error, find the file, change a line, push again and wait for the pipeline to turn green. The fix is often shorter than the time spent finding it.

This project automates that routine. When a GitHub Actions run fails, a workflow collects the failing job's log and the source files involved, asks a language model what is wrong, and opens a pull request with the change. A person still reviews and merges the pull request. The system never pushes to the main branch.

### Goal and constraints

The goal was a pipeline that works end to end on a real repository and that we could measure honestly: how often the proposed fix is right, how long it takes from push to pull request, and what it does when it is wrong.

Three constraints shaped most of the decisions in the later chapters.

- **No paid model API.** The model runs on a free Kaggle notebook with two NVIDIA T4 GPUs. This limits us to a quantized model of roughly 27 billion parameters, a session that the notebook caps at 1.5 hours, and a network address that changes on every start.
- **The model must not be trusted.** Its reply is parsed, applied to the file by exact text matching, syntax-checked, and tested by CI on the pull request before anyone looks at it. A reply that fails any of these steps becomes a comment explaining why, not a pull request.
- **One healer per repository.** Two healers reacting to the same failed run would open two competing pull requests, so the design assumes exactly one is switched on.

### What was built

The repository holds four cooperating parts, described in Chapter 4.

1. A **GitHub Actions workflow** for a small Express demo application. When its tests fail with a code error, a notify job calls a webhook.
2. An **n8n workflow** of 26 nodes that receives that call, gathers evidence from the GitHub API, builds the prompt, calls the model, applies the reply and opens the pull request. If CI fails on its own pull request, it tries again with the model's reasoning mode switched on, up to a cap.
3. A **model endpoint** on Kaggle: Qwen3.8-27B served by llama.cpp with tensor split across both GPUs and multi-token prediction, published through a Cloudflare quick tunnel.
4. **ci-heal-backend**, a second healer written as a FastAPI service. It classifies failures first and then retries, fixes or escalates. It talks to the same model endpoint.

Around them sit a set of double-click launcher scripts, an evaluation harness with its measured results, and a test rig that runs the workflow's real code inside a real n8n engine.

### Summary of results

The numbers below are the headline results. Each is explained, with its method and its limits, in Chapter 9 and Chapter 10.

| question | result | source |
|---|---|---|
| Live heals on five real breakages (round 2) | 5 of 5 healed, 5 of 5 fix PRs passed CI | docs/results/GOLIVE_RESULTS.md |
| Time from push to fix PR, median | 42 s in round 2 (44 s in round 1) | docs/results/GOLIVE_RESULTS.md |
| Hard single-file bugs, first try, no thinking | 12 of 18 | docs/results/SHIPPED_QUALITY_EVAL.md |
| Same bugs with thinking on | 18 of 18 | docs/results/SHIPPED_QUALITY_EVAL.md |
| Multi-file bugs, first try / with retry loop | 9 of 18 / 15 of 18 | docs/results/MULTIFILE_EVAL.md |
| Model generation speed on 2 x T4 | about 22 to 27 tok/s | kaggle/logs/endpoint/ |

The multi-file result missed the bar that had been fixed before the data was collected (12 of 18), so the report does not call multi-file healing finished. Chapter 14 lists this and the other open problems.

### How to read this report

Chapters 2 and 3 give the background and the requirements. Chapters 4 to 8 describe the system part by part. Chapter 9 covers the evaluation, Chapter 10 a recorded live run, and Chapters 11 and 12 testing and security. Chapter 13 explains how to run everything. Chapters 14 and 15 are limits and future work. Short source notes in parentheses point to the file in the repository that holds the evidence, so any number can be checked.

## Background

This chapter explains the technology the rest of the report assumes. Readers who already use GitHub Actions and llama.cpp can skip it.

### Continuous integration

Continuous integration (CI) means that every change pushed to a shared repository is built and tested automatically on a clean machine. The point is to find out within minutes, and not at release time, that a change broke something. Continuous delivery goes one step further and ships every change that passes. This project only deals with the first half: a change fails CI, and we want to know why.

### GitHub Actions

GitHub Actions is the CI service built into GitHub. A **workflow** is a YAML file under `.github/workflows/`. It names the events that start it (a push, a pull request) and holds one or more **jobs**. Each job runs on a fresh **runner**, a virtual machine such as `ubuntu-latest`, and is a list of **steps**: either a shell command (`run:`) or a reusable action (`uses:`). One execution of a workflow is a **run**. A job that needs another job to finish first declares it with `needs:`, and an `if:` condition decides whether a job runs at all; `failure()` is true when an earlier job failed.

A step that exits with a non-zero status fails its job, and GitHub keeps the full text log of every step. The log is what a human reads to find the cause, and it is also what this project hands to the model. The job-log endpoint of the GitHub API answers with a redirect to a signed download address on a different host, which matters for how credentials are sent (Chapter 6).

Our demo workflow is `.github/workflows/demo-ci.yml`. Chapter 5 walks through it.

### Webhooks

A webhook is an HTTP request that one system sends to another when something happens, instead of the second system asking repeatedly. GitHub can send one for a finished workflow run. Our notify job sends its own, from inside the workflow, so that it can add fields GitHub does not include, such as whether the failure was a code error. A receiver should authenticate such a request; ours checks a shared secret in a header.

### n8n

n8n is a workflow automation tool. A workflow is a graph of nodes: a trigger node (here a webhook), HTTP request nodes, nodes that run JavaScript, and conditional nodes. Each node receives items from the previous one and passes items on. A node can be set to stop the whole run when it fails or to continue and hand the error on as data; the workflow relies on both behaviours. We used n8n 1.x. Version 2.x did not register our webhook (docs/guides/SETUP.md).

### Serving a language model

**llama.cpp** is a C and C++ program that runs language models from a single file in the GGUF format. Its `llama-server` exposes an HTTP API that follows the OpenAI chat-completions format, so any client written for that API can talk to it. The pieces that matter here:

- **Quantization.** The weights are stored with fewer bits than the 16 used in training. The model we use is `UD-Q4_K_XL` from the unsloth repository: roughly 4-bit weights, 17.6 GB on disk (kaggle/endpoint/models.json). Smaller files fit in GPU memory and read faster, at some cost in accuracy.
- **Tensor split.** With `-sm tensor` the work of each layer is divided over both GPUs, so they compute at the same time. Without it, the layers are divided between the GPUs and only one works at a time.
- **Multi-token prediction (MTP).** The model has a small extra head that guesses the next few tokens, and the full model checks the guesses in one pass. When the guesses are right, several tokens cost one step. The server reports how often they were accepted.
- **Streaming.** The reply can arrive as a stream of small events (server-sent events), so the client sees the first tokens at once instead of waiting for the whole answer.
- **Thinking mode.** Qwen3.8 can write out its reasoning before the answer. That usually gives better answers and always takes longer. A request can switch it on or off with `enable_thinking`.

### Kaggle and the tunnel

Kaggle offers notebooks with a choice of GPUs, including an accelerator named two T4s (2 x 16 GB). A notebook run is called a session, and there is no inbound network access to it. To reach the model from outside, the notebook starts `cloudflared` in "quick tunnel" mode, which makes the local port available at a random `https://<words>.trycloudflare.com` address. Two properties of this service drive design decisions later. The address is new on every start. And the tunnel closes a connection whose origin sends nothing for 120 seconds, which is why the workflow streams the model reply.

### SEARCH/REPLACE edits

Asking a model to return a whole corrected file wastes tokens and invites accidental changes elsewhere in the file. We ask instead for edits in a fixed block format:

```text
<<<<<<< SEARCH
(lines copied from the current file)
=======
(the replacement lines)
>>>>>>> REPLACE
```

The SEARCH text must occur exactly once in the file. The workflow applies the edit by plain text replacement and rejects the reply if the text is absent or ambiguous. Chapter 9 compares this with other formats.

## Requirements and method

### What the system has to do

The requirements below were written down as the pipeline grew, and each one is checked by a test or a recorded run listed in Chapter 11.

| id | requirement | checked by |
|---|---|---|
| F1 | A failing CI run on the main branch with a code error produces a fix pull request without human input. | live runs, docs/results/GOLIVE_RESULTS.md |
| F2 | A failure that is not a code error (runner or network problem) produces no model call and no pull request. | n8n rig probe `failure_kind=infra`, tools/n8n_rig/drive_full.py |
| F3 | The fix is a small edit applied by exact text match; a reply that cannot be applied produces a comment that states the reason. | tools/tests/test_extract_node.py, rig scenarios in drive_full.py |
| F4 | If CI fails on the healer's own pull request, the workflow tries again with the model's reasoning on, at most a fixed number of times. | docs/results/GOLIVE_RESULTS.md (round 3) |
| F5 | The healer never writes to the main branch. | a model-chosen branch named `main` or `master` is replaced by an `ai-fix-` name (tools/nodes/extract_ai_response.js); the backend refuses any ref that does not start with `ai-fix-` (ci-heal-backend/app/services/github_client.py, `_refuse`) |
| F6 | The model address can change without editing the workflow. | tools/tests/test_model_url.py |
| F7 | If the model call fails, the workflow can use a second model and says so when that fails too. | rig scenarios `524` and `fallback` |
| N1 | Secrets stay out of the repository. | secret scan in Chapter 12 |
| N2 | Re-running the workflow on the same commit does not open a second pull request. | `onError: continueRegularOutput` on branch and PR creation, tools/gen_n8n.py |
| N3 | A person can start and stop every component with one double-click script. | launcher/README.md |
| N4 | Every number in the documentation can be traced to a file in the repository. | source notes in each chapter |

### Constraints that came from the environment

The free GPU determined the model size and the serving settings (Chapter 7). The tunnel's 120-second silence limit determined the use of streaming (Chapter 6). The shared GPU quota determined a rule in `kaggle/tools/push.py`: it refuses to start a new session while any project kernel is running or queued, because two sessions at once would spend quota twice (kaggle/tools/kaggle_cli.py). The n8n Cloud trial lasts 14 days and disables the REST API, so the model address cannot be set through that API and a second webhook does it instead (docs/guides/cloud-n8n.md).

### Method

Four habits decided how the work was done, and they explain why some chapters read like lab reports.

**Measure before building.** Before the n8n workflow existed, the model was compared on a fixed set of bug cases to see whether it could fix them at all, and which quantization to use (docs/research/, docs/results/). Speed was measured on the real server flags, not on a benchmark of the model alone.

**Fix the decision rule before the data.** For the later studies (shipped-format quality, multi-file healing, the four-model comparison) the criterion for "ship" or "replace" was written down and, where possible, coded into the scorer before any GPU time was spent. When the multi-file study missed its bar of 12 of 18, the result was reported as a miss (docs/results/MULTIFILE_EVAL.md).

**Test the code without the GPU.** The Code nodes of the workflow are plain JavaScript files in `tools/nodes/`. A small harness runs each one under Node with a fake n8n context, so a logic error costs seconds and no GPU quota. Because the harness cannot reproduce n8n's own behaviour (for example how it hands data between nodes), a second rig runs the same node bodies inside a real n8n engine against a mock server (tools/n8n_rig/).

**Judge a feature by its effect.** A log line saying that a feature is on is not proof that it is on. The endpoint's thinking switch, for example, is checked by asking the model two questions and looking for reasoning in the reply (kaggle/endpoint/deploy_src.py, `thinking_check`).

## System Architecture

This chapter describes the whole system from above: which parts exist, what each one owns, how they are wired together, and why the main design choices were made. Chapter 5 covers the GitHub side in detail and Chapter 6 covers the n8n workflow.

### Components

The system has six parts. Two of them (the CI workflow and the n8n workflow) take part in every heal, one (the model endpoint) is shared by everything, and the rest are an alternative healer and support tools.

| Component | What it owns | Where it lives |
|---|---|---|
| GitHub Actions workflow | Runs the tests, decides whether a failure is a code bug, counts heal attempts, calls the healer | `.github/workflows/demo-ci.yml` |
| n8n workflow | Collects the evidence, asks the model, applies the edits, opens or updates the fix pull request, reports failures | `n8n/self_healing_pipeline.json` (26 nodes, generated by `tools/gen_n8n.py`) |
| Kaggle endpoint | Serves Qwen3.8-27B over an OpenAI-style chat API on two T4 GPUs, behind a Cloudflare quick tunnel | `kaggle/endpoint/deploy_src.py` |
| ci-heal-backend | The alternative healer: a FastAPI service that classifies a failed run, then retries it, opens a fix PR or opens an issue | `ci-heal-backend/` |
| Launcher scripts | Double-click start and stop scripts for the model, n8n and the backend | `launcher/` |
| Evaluation harness | Bug cases, scorers, unit tests and a real-n8n rig that produced the measurements used in this report | `eval/`, `tools/tests/`, `tools/n8n_rig/`, `run_tests.py` |

(source: README.md, "Repository layout"; docs/architecture/n8n-workflow.md; docs/architecture/kaggle-endpoint.md)

The JSON of the n8n workflow is never edited by hand. `tools/gen_n8n.py` writes it, and the bodies of its Code nodes are plain files in `tools/nodes/`. Running `python tools/gen_n8n.py --check` regenerates the file in memory and compares it byte for byte with the shipped copy (source: docs/architecture/n8n-workflow.md, checked against the node list in n8n/self_healing_pipeline.json).

The Kaggle endpoint runs as a script kernel. At boot it unpacks a prebuilt `llama-server`, downloads the GGUF model file, tries context sizes from 131072 downward until one boots, starts a Cloudflare quick tunnel and prints the public address (source: docs/architecture/kaggle-endpoint.md, describing kaggle/endpoint/deploy_src.py). The script keeps serving for a 1.5 hour window and then ends by itself.

The launcher is a convenience layer. `1_start_model_on_kaggle.bat` checks the kernel status, builds and pushes the kernel, watches the boot log until the tunnel address appears, and then posts that address to the n8n workflow (source: launcher/README.md).

The evaluation harness matters for the architecture because the Code nodes can run outside n8n. `run_tests.py` runs the node suites in `tools/tests/`, the eval scorer self-test and the generator check, with no GPU and no writes to GitHub. With `--rig` it also runs a real n8n 1.x engine against mock GitHub hosts (source: run_tests.py, docstring).

### Diagram

```text
 developer pushes to main
          |
          v
 +---------------------------+
 | GitHub Actions            |   failure_kind = code
 |  build-and-test           |   (exit code other than 0 and 75)
 +---------------------------+------------------------+
          ^                                            v
          |                                  +-----------------------+
          | CI runs again on the             | notify-n8n-on-failure |
          | fix PR (pull_request)            | decide: heal, retry,  |
          |                                  | or give up            |
          |                                  +-----------------------+
          |                                            | POST JSON with
          |                                            | X-Webhook-Secret
          v                                            v
 +---------------------------+            +-----------------------+
 | GitHub repository         |<-----------| n8n workflow          |
 |  ai-fix-* branch and PR   | GitHub API | logs, files, prompt,  |
 +---------------------------+ (PAT)      | apply edits, open PR  |
                                          +-----------------------+
                                                     | POST /v1/chat/completions
                                                     | (streamed, bearer key)
                                                     v
                                          +-----------------------+
                                          | Cloudflare quick      |
                                          | tunnel, llama-server  |
                                          | Qwen3.8-27B, 2x T4    |
                                          +-----------------------+

 Alternative healer (use only one per repository):
 GitHub workflow_run webhook --> ci-heal-backend --> same model endpoint
```

(source: README.md, "How the parts connect"; .github/workflows/demo-ci.yml; docs/architecture/n8n-workflow.md)

### Data flow of one heal

The steps below follow a first-time heal from the push to the open pull request.

1. A developer pushes a commit that changes `demo-app/` to `main`, and the workflow starts (source: .github/workflows/demo-ci.yml, `on.push`).
2. The `build-and-test` job runs `npm test` and turns the exit code into `failure_kind`: `none` for 0, `infra` for 75 and `code` for anything else (source: .github/workflows/demo-ci.yml, step "Run tests").
3. The `notify-n8n-on-failure` job runs only when `failure_kind` is `code` and the event is a push or the branch starts with `ai-fix-`. On a push it sets `attempt=0` and `action=heal`.
4. The notify job POSTs a JSON body to the n8n webhook. The fields are in the table below.
5. n8n answers GitHub at once (response mode `onReceived`) and keeps working (source: n8n/self_healing_pipeline.json, node "GitHub Failure Webhook").
6. n8n lists the run's jobs, finds the failed one, downloads its log, lists the commit's changed files and picks up to 3 candidate files (source: tools/nodes/pick_candidate_files.js, `MAX = 3`, as described in docs/architecture/n8n-workflow.md).
7. n8n builds one prompt from the failure details, the trimmed log and the source of each candidate file. For a first heal it sets `enable_thinking` to false.
8. n8n sends the request to the model address it has stored. The reply comes back as a stream of server-sent events.
9. n8n rebuilds the reply, reads the JSON object, applies the SEARCH/REPLACE blocks to the file the model named and checks that the result parses.
10. n8n creates the `ai-fix-...` branch from the failing commit, pushes the patched file with a commit message that ends in `Heal-Attempt: 1`, and opens a pull request into the branch that failed (source: tools/nodes/extract_ai_response.js; n8n/self_healing_pipeline.json, node "Open Pull Request").
11. The workflow also triggers on pull requests into `main`, so the fix PR gets its own CI run. If that run fails with a code failure, the notify job runs again with `attempt=1` and the loop continues (Chapters 5 and 6).

The webhook body has these fields (source: .github/workflows/demo-ci.yml, step "Trigger n8n webhook").

| Field | Value | Used for |
|---|---|---|
| `run_id` | `github.run_id` | Listing the jobs of the failed run |
| `repo` | `owner/name` | Every GitHub API URL |
| `branch` | The pushed branch, or the head branch of the fix PR | Base of the new PR; retry detection by the `ai-fix-` prefix |
| `commit` | The pushed commit on a push, the PR head commit on a pull request | Source of file contents; base of the new branch |
| `actor` | `github.actor` | Shown in the prompt |
| `failure_kind` | `code` in practice | A second check inside n8n |
| `attempt` | `0` on a push, the `Heal-Attempt` number on a fix PR | Turns thinking on; numbers the next commit |
| `pr_number` | The PR number on a pull request event | Where a failure comment is posted |

All values are sent as strings inside the JSON. On a push event the expression for `pr_number` has nothing to read, so it should arrive empty. This is inferred from the expression; we did not observe it. `tools/nodes/explain_failure.js` treats anything that is not all digits as "no PR".

### Trust boundaries and secrets

Four boundaries are crossed during one heal, and each has its own protection.

| Boundary | What protects it |
|---|---|
| GitHub runner to n8n webhook | A shared secret in the header `X-Webhook-Secret`, checked by n8n's header authentication. A wrong or missing secret is rejected before the workflow starts (source: docs/architecture/n8n-workflow.md) |
| n8n to the GitHub API | A bearer token held in the n8n credential "GitHub PAT", used by nine request nodes (source: docs/guides/cloud-n8n.md) |
| n8n to the model | A bearer key held in the n8n credential "Qwen3.8 Endpoint Key", checked by `llama-server` through `--api-key` |
| n8n to the log storage host | No credential at all, on purpose (see the design decision on redirects) |

The secrets live in these places. No value is printed here.

| Secret | Where it lives |
|---|---|
| Webhook shared secret | GitHub repository secret `N8N_WEBHOOK_SECRET`; the n8n credential "N8N Webhook Secret"; the field `webhook_secret` in `%LOCALAPPDATA%\selfheal-n8n\secrets.json` (source: docs/guides/cloud-n8n.md) |
| Webhook address | GitHub repository secret `N8N_WEBHOOK_URL` (source: .github/workflows/demo-ci.yml) |
| GitHub PAT | The n8n credential "GitHub PAT". For a local n8n, `tools/golive/n8n_import.py` takes the token from `gh auth token`, writes it into n8n's credential store and deletes the plaintext import file. On n8n Cloud it is created by hand in the browser (source: docs/guides/cloud-n8n.md) |
| n8n encryption key | The field `encryption_key` in `secrets.json`, used by the local n8n (source: tools/golive/n8n_import.py) |
| Endpoint API key | A constant `API_KEY` in `kaggle/endpoint/deploy_src.py`, and the n8n credential "Qwen3.8 Endpoint Key" (source: docs/architecture/kaggle-endpoint.md, "API key") |
| Current model address | `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt` on the PC, and the workflow's static data in n8n (source: tools/nodes/store_model_url.js) |
| Backend secrets | `ci-heal-backend/.env` (`GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `N8N_SHARED_TOKEN` and others). `.gitignore` lists `*.env` and `secrets.json` |

The folder `%LOCALAPPDATA%\selfheal-n8n` is outside the repository on purpose. It also holds the local n8n install and database, the tunnel logs and the process ids (source: tools/golive/up.py, docstring; tools/golive/exec_stats.py).

Two weak points should be stated plainly. The endpoint key is a fixed string inside a file that is committed to the repository, so anyone with repository access can read it (source: docs/architecture/kaggle-endpoint.md). And the quick tunnel makes the model server reachable from the internet, so that key is the only thing between a stranger and the GPU.

The JSON of the workflow holds no secrets. `tools/tests/test_workflow_structure.py` searches it for token prefixes, and the OpenAI node ships with no credential bound (source: docs/architecture/n8n-workflow.md, "Credentials and variables").

### One healer per repository

The n8n workflow and ci-heal-backend are two separate healers, and a repository should use only one of them. GitHub tells both about the same failed run: n8n through the notify job, the backend through a `workflow_run` webhook. With both active, each would open its own fix PR for the same bug and the two would compete (source: README.md, "How the parts connect"; launcher/README.md).

They also decide differently. The n8n workflow acts only on failures the CI job marked as `code`, and then always tries a fix. The backend classifies the failure first and then reruns a flaky job, opens a fix PR or opens an issue, depending on its confidence (source: ci-heal-backend/README.md). Their attempt counters are separate too: n8n uses the `Heal-Attempt` trailer, while the backend counts fix decisions in its own database (source: ci-heal-backend/README.md, Architecture). Neither counter knows about the other.

The backend has an endpoint `POST /webhooks/n8n` for failures forwarded from n8n (source: ci-heal-backend/README.md). We found no node in the shipped workflow that calls it, so that route is not part of the n8n healer described in this report.

### Design decisions

#### SEARCH/REPLACE edits instead of whole files

The model answers with a JSON object whose `edits` field holds SEARCH/REPLACE blocks. The first version asked for the whole fixed file, which made the model copy nearly all of its input back out. Output tokens cost time on this hardware (the baseline run measured 12.29 tok/s), so three formats were measured on 45 calls (source: docs/results/DIFF_FORMAT_EVAL.md).

| Format | Median completion tokens | Wall time | Applied and parsed |
|---|---|---|---|
| Whole file | 655 | 56.4 s | 15/15 |
| Unified diff | 233 | 23.3 s | 12/15 |
| SEARCH/REPLACE | 171 | 17.0 s | 15/15 |

(source: docs/results/DIFF_FORMAT_EVAL.md, "Results")

The unified diff lost on correctness as well as speed. In one scenario the model wrote a patch whose header claimed an added line while the only added line was empty, and a tolerant `--recount` retry applied it as a no-op that still did not parse (source: same file). SEARCH/REPLACE avoids this because every block must match the current file exactly once, or the whole fix is rejected. The rule is enforced in `tools/nodes/extract_ai_response.js`. In the first live round the median model phase fell from about 57 s to 11.4 s (source: docs/results/GOLIVE_RESULTS.md).

#### Streamed model calls

The model sits behind a Cloudflare quick tunnel, which cuts a request when the origin stays silent for 120 seconds. A thinking retry once died that way, with HTTP 524 at 125.9 s. A proof run sent the same long request through the same tunnel twice: without streaming it failed with 524 at 125.9 s, and with streaming it finished with HTTP 200 after 166.5 s (source: docs/results/GOLIVE_RESULTS.md, Round 5). So the request sets `stream: true`, and a Code node called "Assemble Model Reply" rebuilds the ordinary completion from the event stream. A stream that ends with neither `[DONE]` nor a finish reason counts as cut off and as a failed call (source: tools/nodes/assemble_model_reply.js, as described in docs/architecture/n8n-workflow.md).

#### Thinking off first, on for retries

With thinking off, the first pass healed 15/15 control cases and 12/18 hard cases, with a median wall time of 9.1 s. With thinking on and a 1024-token budget it healed 18/18 hard cases, including two that no earlier configuration had fixed, but the median rose to 36.8 s (source: docs/results/SHIPPED_QUALITY_EVAL.md, arms B and C). Always thinking would add about 28 s to every heal, so the workflow turns it on only after CI has rejected the fast fix. `Build AI Prompt` sets `enable_thinking` to the value of `isRetry` (source: tools/nodes/build_ai_prompt.js). The server itself defaults to thinking off, because the backend can only switch it on per request (source: docs/architecture/kaggle-endpoint.md, "Why thinking is off by default").

The price is that a hard bug needs one more CI cycle. In the live retry rounds, `multi_hunk` turned green on attempt 2 in 143 to 161 s, and `callback_arity`, which failed in earlier rounds, turned green on attempt 3 in 249 s (source: docs/results/GOLIVE_RESULTS.md, Rounds 3 and 5).

#### The failure_kind gate

An infrastructure problem, such as a port that is already in use, has nothing to fix in the source. If the model were asked anyway, it would invent a patch. The demo tests therefore exit with 75 for environment problems and with 1 for code failures, and the workflow maps these to `infra` and `code` (source: demo-app/test.js, header comment). We checked that `npm test` exits with 75 when port 5000 is already taken. The gate exists twice: the notify job runs only for `code`, and `Build AI Prompt` throws `Refusing to run` for any other non-empty `failure_kind`, because the webhook is a public address that anyone could post to (source: tools/nodes/build_ai_prompt.js).

#### Log redirect handling

GitHub answers a job-log request with a 302 redirect to a signed address on another host. The first design let one HTTP node follow the redirect, assuming the client drops the `Authorization` header when the host changes. The real-n8n rig showed that its mock storage host received the bearer token. The fix uses two nodes. "Fetch Failed Job Log" does not follow redirects and returns the `Location`, and "Download Job Log" fetches that address with no credential. The rig now asserts that the token reached the API host and did not reach the storage host (source: docs/results/GOLIVE_RESULTS.md, Round 2, "Two defects the verification caught"; n8n/self_healing_pipeline.json).

#### Model address stored through a webhook

The quick tunnel gets a new address on every Kaggle boot, so the address has to be delivered again each time. Earlier the update went through the n8n API (source: git commit 4833c44, whose message says it switched from the API to a webhook). On an n8n Cloud trial the REST API is switched off (source: docs/guides/cloud-n8n.md, "Trial limits"), so the workflow got a second webhook, "Set Model URL", protected by the same shared secret, and the launcher posts the new address to it. The node "Store Model URL" accepts only an address of the form `https://<name>.trycloudflare.com` and saves it in the workflow's static data, so a mistyped or hostile value is refused (source: tools/nodes/store_model_url.js). "Get Model URL" reads the stored address before each model call and falls back to the variable `QWEN_BASE_URL` (source: tools/nodes/get_model_url.js).

#### The attempt counter lives in the commit

The cap on retries has to survive a re-run of the workflow and a restart of n8n. For that reason the count is a `Heal-Attempt: N` line in the message of every commit the healer pushes, and the notify job reads it from the head commit of the fix PR (source: .github/workflows/demo-ci.yml, comment on `MAX_ATTEMPTS`). The cap is enforced in the notify job only. The n8n workflow does not check it (source: docs/architecture/n8n-workflow.md, "The retry loop").

## The GitHub Actions Side

Everything on the GitHub side is one workflow file, `.github/workflows/demo-ci.yml`, named "Demo CI", plus the small Node application it tests in `demo-app/`. The workflow does two jobs: it tests the code, and when the tests fail because of a code bug it wakes the healer. This chapter goes through the file from top to bottom.

### Triggers

```yaml
on:
  push:
    branches:
      - main
    paths: ["demo-app/**"]
  pull_request:
    branches:
      - main
    paths: ["demo-app/**"]
```

(source: .github/workflows/demo-ci.yml)

The workflow starts in two cases. A push to `main` starts it, and so does a pull request that targets `main`. In both cases the changed files must include something under `demo-app/`. The path filter keeps documentation commits, report files and the rest of the repository from running the tests.

The path filter has a side effect. A change that touches only files outside `demo-app/` does not start CI at all, so a failing test could not be reported for it. That does not matter for the demo, whose bugs live in the app, but it would matter for a real repository, where the filter would have to cover every folder that can break the build.

The `pull_request` trigger was added so that the fix pull requests opened by the healer are tested too. The reason is in the last section of this chapter.

### Jobs

There are two jobs, `build-and-test` and `notify-n8n-on-failure`. The second one names the first in `needs`, so it waits for it and can read its outputs. Both run on `ubuntu-latest`.

### The build-and-test job

The job's default working directory is `demo-app`. Its steps are as follows (source: .github/workflows/demo-ci.yml).

1. Check out the code with `actions/checkout@v4`.
2. Install Node.js 20 with `actions/setup-node@v4`.
3. Run `npm install`.
4. Run the tests (step id `tests`) and classify the result.
5. Run `npm run build --if-present`.

The fourth step is the one that matters for healing. It runs `npm test` with `set +e`, so the script survives a failure and can inspect the exit code:

```yaml
- name: Run tests
  id: tests
  run: |
    set +e
    npm test
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then
      kind=none
    elif [ "$code" -eq 75 ]; then
      kind=infra
    else
      kind=code
    fi
    echo "failure_kind=$kind" >> "$GITHUB_OUTPUT"
    exit "$code"
```

(shortened; source: .github/workflows/demo-ci.yml, step "Run tests")

The result is published as a job output, `failure_kind`, which the second job reads. The step then exits with the original code, so the job still turns red when the tests fail. The exit code 75 is `EX_TEMPFAIL`, the conventional "try again later" code (source: demo-app/test.js, header comment).

Two situations leave `failure_kind` without the value `code`, and both are consequences of how the step is written, not of anything the workflow says. If `npm install` fails, the tests step never runs, so the output is empty. If the tests pass and only the build step fails, the output is `none`. Either way the notify job's condition is false and the healer is not called. The first case is intended, since a failed install is an environment matter. The second is a gap: a build failure after passing tests is not healed. In this demo the build script only runs `node index.js`, which prints one line (source: demo-app/package.json, demo-app/index.js).

### The notify job

#### When it runs

```yaml
if: >-
  failure() && needs.build-and-test.outputs.failure_kind == 'code' &&
  (github.event_name == 'push' || startsWith(github.head_ref, 'ai-fix-'))
permissions:
  contents: read
  pull-requests: write
  issues: write
env:
  MAX_ATTEMPTS: 3
```

(source: .github/workflows/demo-ci.yml)

The condition has three parts. `failure()` makes the job run only when an earlier job failed. The `failure_kind` check stops it for infrastructure problems: the file's own comment says that `failure()` alone would also fire for a bound port, DNS or a full runner disk, and that the model would then invent a patch for a problem that is not in the source. The third part limits the event. A push to `main` always qualifies. A pull request qualifies only when its head branch starts with `ai-fix-`, which is the prefix the healer uses. A human's pull request therefore never wakes the healer.

The job asks for `pull-requests: write` and `issues: write`. The file does not say why, but its last step comments on a pull request, which is the likely reason.

`MAX_ATTEMPTS` is 3. That is the total number of model attempts per heal PR: the first heal plus two retries (source: comment in .github/workflows/demo-ci.yml).

#### Step 1: decide

The step with id `decide` produces four outputs: `branch`, `commit`, `attempt` and `action`.

On a push the answer is fixed: the branch is `GITHUB_REF_NAME`, the commit is `GITHUB_SHA`, `attempt` is 0 and `action` is `heal`.

On a pull request the step looks at the head commit of the PR, not at `github.sha`. For a pull request event, `github.sha` points at a merge commit that GitHub makes, and that commit does not carry the healer's message. The step fetches the head commit's message through the API and reads the trailer:

```bash
attempt=$(printf '%s\n' "$msg" | sed -n 's/^Heal-Attempt: *\([0-9][0-9]*\) *$/\1/p' | tail -n 1)
```

(source: .github/workflows/demo-ci.yml, step "Decide")

The `Heal-Attempt: N` line is added by the healer to every commit it pushes, and the first heal is numbered 1 (source: tools/nodes/extract_ai_response.js). The step then picks one of three actions:

| Trailer found | Action | Meaning |
|---|---|---|
| None | `none` | A human pushed to the heal branch and owns it now. The step prints a notice and nothing else happens |
| N is at least `MAX_ATTEMPTS` | `giveup` | The cap is reached |
| Otherwise | `heal` | Call the healer again with `attempt=N` |

With the cap at 3, the commit numbering works out as follows. The first heal commit carries `Heal-Attempt: 1`. When CI fails on it, the notify job reads 1, which is below 3, and calls the healer, which pushes a commit marked 2. A second failure reads 2 and produces a commit marked 3. A third failure reads 3 and gives up. That is three model attempts in total.

The counter sits in the commit message, so re-running the workflow cannot reset it (source: comment in .github/workflows/demo-ci.yml).

#### Step 2: trigger the webhook

This step runs when `action` is `heal`. It posts to the address in the repository secret `N8N_WEBHOOK_URL`, with the shared secret in the header `X-Webhook-Secret`:

```yaml
curl -sS --fail --max-time 20 -X POST "${{ secrets.N8N_WEBHOOK_URL }}" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: ${{ secrets.N8N_WEBHOOK_SECRET }}" \
  -d '{ "run_id": "...", "repo": "...", "branch": "...", "commit": "...",
        "actor": "...", "failure_kind": "...", "attempt": "...",
        "pr_number": "..." }' \
  || echo "::warning::n8n webhook unreachable - heal not triggered"
```

(shortened; each value is a `${{ }}` expression in the real file; source: .github/workflows/demo-ci.yml)

The fields and what n8n does with them are listed in Chapter 4. Three details stand out. The request has a 20 second limit (`--max-time 20`). `--fail` makes curl report an HTTP error as a failure. And the trailing `|| echo "::warning::..."` turns any failure of the call into a warning annotation, so the step itself still succeeds.

The warning exists because the webhook address is a tunnel that is only up while the healer is deployed. Without the fallback, a stopped healer would turn every broken commit red a second time, for a reason unrelated to the commit. The go-live notes record a test with the healer down: the build job failed and the notify job ended as success, with the warning shown in the annotations (source: docs/results/GOLIVE_RESULTS.md, Round 2).

#### Step 3: give up at the cap

When `action` is `giveup`, the last step posts a comment on the pull request with `gh pr comment`. The text says how many attempts were made, that the pipeline has stopped and that the case needs a human (source: .github/workflows/demo-ci.yml). This is the only place where the cap is enforced. The n8n workflow does not check it (source: docs/architecture/n8n-workflow.md).

### The demo application

The application is small on purpose. It stands in for a real project, and the pipeline would work the same way on one (source: demo-app/index.js, comment).

| File | Role |
|---|---|
| `demo-app/index.js` | Entry point. It prints one line and nothing else |
| `demo-app/src/app.js` | An Express app with three routes: `GET /health`, `GET /greet/:name` and `GET /sum?a=&b=` |
| `demo-app/src/format.js` | Two helpers, `formatGreeting` and `formatSum`, used by the routes |
| `demo-app/test.js` | The smoke test: starts its own Express server on `PORT` (default 5000), requests `/` and expects status 200 |
| `demo-app/test/api.test.js` | The API test: loads `src/app.js`, listens on `API_PORT` (default 5055) and checks four requests |
| `demo-app/package.json` | The one dependency is `express` `^4.19.2`. The `test` script is `node test.js && node test/api.test.js` |

(source: the files listed)

The four API checks are `/health` returning `{"status":"ok"}`, `/greet/Ada` returning `Hello, Ada!`, `/sum?a=2&b=3` returning a sum of 5, and `/sum?a=x&b=3` returning status 400 with an error message (source: demo-app/test/api.test.js).

Both tests follow the same exit-code contract. A wrong status or body prints a line starting with `CODE_FAILURE` and exits with 1. If the server cannot start, the `error` event on the server prints a line starting with `INFRA_FAILURE` and exits with 75. For the smoke test, a failed `listen` such as `EADDRINUSE` goes to the infrastructure path, while a server that listens but does not answer itself counts as a code failure (source: demo-app/test.js, comments). We ran `npm test` with port 5000 already occupied and got exit code 75, which is the value the workflow turns into `infra`.

Because the script joins the two tests with `&&`, a failing smoke test stops the run and the API test never starts. The exit code of the whole `npm test` is the one from the failing command.

The log lines matter beyond the exit code. When the healer later downloads the job log, the first `##[error]` line and the text above it are what the model reads, so a message that starts with `CODE_FAILURE:` and names the route or the stack trace gives it something concrete (source: tools/nodes/build_ai_prompt.js, `trimCiLog`).

### Why the heal PR gets its own CI run

In the first live round the workflow ran only on pushes to `main`. The fix pull requests were not tested at all, and the team checked them by running the file by hand (source: docs/results/GOLIVE_RESULTS.md, "Still not true"). That left two holes. A fix that parsed but was wrong would sit in an open PR with no red mark. And there was no signal that could start a retry.

Adding the `pull_request` trigger closed both holes. After the change, 5 of 5 heal PRs ran CI and all 5 were green (source: docs/results/GOLIVE_RESULTS.md, Round 2). A red result on a heal PR is now the event that wakes the healer again, and the notify job's condition and the `Heal-Attempt` trailer keep that loop under control.

The `ai-fix-` prefix test has a second purpose, which is to stop the healer from feeding on its own mistakes without limit. A scratch pull request with a broken `test.js` was used to test this gate: the build job failed and the notify job was skipped, because the branch did not start with `ai-fix-` (source: docs/results/GOLIVE_RESULTS.md, Round 2, run 35802293458). Separate scratch runs with trailers 3, 1 and none produced the give-up comment, a webhook call and nothing, in that order (source: docs/results/GOLIVE_RESULTS.md, Round 3, "Gate tests without GPU").

## The n8n Healing Workflow

The workflow "Self-Healing CI/CD Pipeline (Qwen3.8)" has 26 nodes (source: n8n/self_healing_pipeline.json). Most of the time it is a straight line of nodes with two forks. This chapter follows one run through seven stages and then explains the retry and re-run behaviour. It does not repeat every node setting. The node-by-node reference, with each node's type, endpoint, error setting and tests, is in `docs/architecture/n8n-workflow.md`. We checked the points below against the workflow JSON, `tools/gen_n8n.py` and `tools/nodes/*.js`.

Two facts help when reading the stages. The JSON is generated by `tools/gen_n8n.py` from node specifications, and the Code node bodies are separate files in `tools/nodes/`. And a node marked `onError: continueRegularOutput` does not stop the run when it fails. It passes an item with an `error` key to the next node, which is how failures are routed to the reporting stage.

### Stage 1: receive

The entry point is a POST webhook on the path `github-failure`. It uses header authentication with the credential "N8N Webhook Secret", and its response mode is `onReceived`, so GitHub gets its answer at once and n8n keeps working in the background (source: n8n/self_healing_pipeline.json). The body is the JSON the notify job sends: `run_id`, `repo`, `branch`, `commit`, `actor`, `failure_kind`, `attempt` and `pr_number` (Chapter 4 lists them).

There is a second, separate webhook on the path `set-qwen-url`. It handles the model address and takes no part in a heal. Stage 4 covers it.

### Stage 2: gather evidence

Six nodes collect what the model will be shown.

1. **Fetch Job Logs** lists the jobs of the failed run and their steps. It has no error setting, so if GitHub refuses it the run stops here.
2. **Fetch Failed Job Log** asks for the log of the first job whose conclusion is `failure`. It is set not to follow redirects, because GitHub answers with a 302 to a signed address on another host.
3. **Download Job Log** fetches that signed address with no credential, so the GitHub token is never sent to the storage host (Chapter 4 explains why this was split in two). If the log is missing or expired, the run goes on and the prompt says `(log unavailable)` (source: tools/nodes/build_ai_prompt.js).
4. **Fetch Changed Files** reads the commit's file list.
5. **Pick Candidate Files** and **Fetch File Content** choose and download the source.

The choice of files is a small piece of logic in `tools/nodes/pick_candidate_files.js`. It looks for file paths under the runner's checkout folder in the log, keeps only code files outside `node_modules`, and adds the code files the commit changed. The order is the first two files from the stack trace, then the changed files, then any other traced files, with duplicates removed and a limit of 3 files (source: tools/nodes/pick_candidate_files.js, `MAX = 3`). Using the trace matters because the file that crashes is not always the file that was edited. If nothing qualifies, the node emits one placeholder item so that the run still reaches the failure report, since n8n ends a run quietly when a node outputs nothing.

File contents are fetched at the `commit` from the webhook. On a retry that is the head commit of the fix PR, so the model sees the file with its own earlier fix already applied.

### Stage 3: build the prompt

`Build AI Prompt` (tools/nodes/build_ai_prompt.js) assembles the whole model request in one Code node, so n8n never has to splice a multi-line string into a JSON body.

First it applies the failure gate: if `failure_kind` is set and is not `code`, it throws and the run ends with no model call. Then it works out whether this is a retry, which is true only when `attempt` is above 0 and the branch starts with `ai-fix-`.

The CI log is trimmed before it goes in. The node strips timestamps and colour codes, finds the first `##[error]` line, starts after the closest `##[endgroup]` above it, and keeps the last 40 lines and at most the last 3000 characters (source: tools/nodes/build_ai_prompt.js, `LOG_MAX_LINES` and `LOG_MAX_CHARS`). Presumably this keeps the prompt short, since the model only needs the part of the log that explains the failure.

The system prompt asks for one raw JSON object with `branch_name`, `file_path`, `edits`, `pr_title` and `pr_body`, where `edits` holds SEARCH/REPLACE blocks. The request body uses the model name `local`, temperature 0.1, `max_tokens` 4096, `stream: true`, and `enable_thinking` set to the retry flag. On a retry the prompt also states that the file already contains the previous automated fix and that CI still fails.

### Stage 4: call the model, with a fallback

**Get Model URL** builds the request address from the value stored in the workflow's static data, and falls back to the variable `QWEN_BASE_URL` (source: tools/nodes/get_model_url.js). The stored value comes from the second webhook. After each Kaggle boot the launcher posts the new tunnel address to `set-qwen-url`, and **Store Model URL** saves it only if it matches `https://<name>.trycloudflare.com`.

**Qwen3.8 Analyze** posts to `/v1/chat/completions` with the endpoint key and a timeout of 240 seconds (source: n8n/self_healing_pipeline.json). It reads the response as text, so the stream arrives as one string. The 240 s limit is there so that a stuck endpoint reaches the fallback while the run is still useful (source: comment in tools/gen_n8n.py, as reported in docs/architecture/n8n-workflow.md).

The node **Model OK?** checks the result. The answer counts as good when there is no error and the text contains `[DONE]` or a `finish_reason`. Anything else, such as a refused connection, a timeout or a stream cut short, goes to **OpenAI Fallback**, which sends the same two messages to `gpt-4.1`. **Normalize OpenAI Reply** converts that answer to the shape the next node expects, or throws one message that names both failures.

Both paths meet at **Assemble Model Reply**, which rebuilds a streamed answer into the usual completion object. It rejects a stream that ended early, an empty one, and one with an error event.

The fallback has a documented weakness. In the one test on n8n Cloud, using the free OpenAI credits that come with the trial, it answered `Forbidden` for runs started by the webhook (source: docs/guides/cloud-n8n.md). So we treat it as untested until a real OpenAI key is attached.

### Stage 5: extract the fix

`Extract AI Response` (tools/nodes/extract_ai_response.js) turns the reply into a patched file. It is deliberately strict, because a quiet mistake here becomes a pull request.

1. It removes any `<think>` block and stray code fences, then parses the JSON.
2. It selects the file named in `file_path`, and only accepts a file that was among those shown to the model.
3. It applies each SEARCH/REPLACE block in order. Every SEARCH text must be non-empty and must occur exactly once in the file as patched so far. A block that is not found, matches twice or is malformed rejects the whole answer.
4. It rejects the answer if the result equals the original file.
5. It parses the patched text with `new Function(...)`, which checks the syntax without running the code. If the parse fails, no pull request is opened.

The parse check has a known limit, which its comment states plainly: a fix that parses but is wrong still goes through, and the pull request's own CI is what catches it. We could not confirm how the check behaves for a non-JavaScript file, since it uses a JavaScript parser; the demo app is Node, so this never came up.

The node then chooses the branch name. If the model gave none, or `main` or `master`, the node uses `ai-fix-` plus a timestamp. It appends the first 7 characters of the commit. On a retry it replaces the name with the branch from the webhook. The commit message is the PR title, a blank line and `Heal-Attempt: <attempt + 1>`.

**Fix Ready?** is the fork. If there is no error and the file content exists, the run continues to the pull request. Otherwise it goes to the failure report (Stage 7).

### Stage 6: write the pull request

Six nodes act on GitHub in this order.

1. **Create Branch** posts a new ref from the failing commit. A 422 "Reference already exists" is allowed to pass as data.
2. **Branch Ready** accepts that case and stops on any other error, such as a bad token or a rate limit, instead of reporting a success it did not reach.
3. **Get File SHA** reads the file on the heal branch, because updating an existing file needs its blob SHA.
4. **Push Fix File** writes the patched file with the commit message above.
5. **Open Pull Request** opens a PR from the heal branch into the branch that failed.
6. **Heal Outcome** reads the answer and reports `pr_opened`, `retry_pushed` or `pr_already_open`. Anything else throws, so a real failure is not recorded as a success.

The four HTTP nodes among them use the "GitHub PAT" credential. Create Branch and Open Pull Request continue on error. Get File SHA and Push Fix File stop the run when they fail.

### Stage 7: report failures

When no fix is produced, **Explain Failure** builds a comment and **Post Failure Comment** posts it. The comment goes on the heal PR if `pr_number` is all digits, and on the failing commit otherwise. It starts with "Self-healing pipeline: no fix produced" and quotes the reason, cut to 600 characters (source: tools/nodes/explain_failure.js).

This stage exists because of a failure found in testing. Before it, a retry that died at the tunnel's time limit left a red pull request with no explanation (source: docs/results/GOLIVE_RESULTS.md, Round 3, "Known limits"). In the later round, the stage was proved with the model endpoint switched off: a comment appeared on the failing commit, and a comment appeared on the PR for a retry (source: docs/results/GOLIVE_RESULTS.md, Round 5, Fix 2).

The stage has gaps. Several nodes have no error setting (Fetch Job Logs, Fetch Changed Files, Build AI Prompt, Get Model URL, Get File SHA, Push Fix File and Post Failure Comment). If one of them fails, n8n records an execution error but nothing is written to GitHub (source: docs/architecture/n8n-workflow.md, "Known limits"). The Round 5 notes also say that a failure after the fix is pushed, between Create Branch and Open Pull Request, is not covered by a comment. We did not re-check that sentence against later changes.

### Retry behaviour

The retry loop is driven by GitHub Actions, not by anything inside n8n. When CI fails on a heal PR, the notify job sends the PR's head branch, its head commit and the trailer number as `attempt`. In n8n this has four effects. The prompt carries the "Heal attempt N+1" line. Thinking is switched on. The file is fetched at the PR head, so it holds the previous fix. And the new commit goes onto the same branch with the next trailer number. The cap is checked only in the notify job. With `MAX_ATTEMPTS` set to 3, the first heal and two retries make three attempts, and then the notify job comments that a human is needed.

One detail of the retry shows up at the last step. The new commit is on the PR's own branch, which is also the base that Open Pull Request is given, so GitHub answers 422 "No commits between X and X". Heal Outcome treats this as normal for attempt 2 or later and reports `retry_pushed`. The need for this was found live: all three retries of one round had pushed correctly and were still marked as failed (source: tools/nodes/heal_outcome.js, comment; docs/results/GOLIVE_RESULTS.md, Round 3).

### Idempotent re-runs

The same failed commit can reach the workflow twice, for example when someone re-runs the CI job. The workflow is built so that the second run updates the first one's work instead of breaking. The branch name ends in the first 7 characters of the commit, so the same commit gives the same name when the model repeats its choice. A repeat of the same commit lands on the same branch (a measured case, according to a comment in tools/nodes/extract_ai_response.js). Create Branch tolerates the 422, Get File SHA reads the file's current blob on that branch, Push Fix File updates it, and if the PR already exists, Heal Outcome reports `pr_already_open` and leaves it alone.

This holds only when the model proposes the same branch name. Because the name comes from the model, a different name for the same commit would open a second branch and a second PR. We did not test that case.

## The Model Endpoint on Kaggle

### Role of the service

The healer needs a language model that n8n and the backend can call over HTTPS. We could not pay for a hosted API, so the model runs on a free Kaggle notebook. A Python script starts a `llama-server` process from the llama.cpp project, loads Qwen3.8-27B into it, and puts a Cloudflare tunnel in front of port 8000. The result is an OpenAI-style chat API (`/v1/chat/completions`) at a temporary web address.

Everything that runs on Kaggle is one file, `kaggle/endpoint/deploy_src.py`. A small build script pastes the chosen model entry from `kaggle/endpoint/models.json` into it and writes the file that is uploaded (`kaggle/tools/build_endpoint.py`). Boot logs from each session are kept under `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/<run>/`. In this chapter "boot log v16" means the `boot.log` in the run folder whose name ends in `v16`.

This chapter was checked against the code and the saved logs.

### Hardware

The kernel is private, has the GPU and internet switched on, and sets `machine_shape` to `NvidiaTeslaT4` (source: `kaggle/endpoint/push/kernel-metadata.json`). That setting matters: with only `enable_gpu` Kaggle hands out a single P100 instead of two T4 cards, and the notebook's accelerator menu is overwritten by a command-line push (source: `docs/research/KAGGLE_ENV_MEASURED.md`). The same note measured 4 CPU cores and 31 GB of RAM on the machine.

The boot logs show two Tesla T4 cards with 15360 MiB each, 30720 MiB in total (source: boot log v16). The script adds up the memory that `nvidia-smi` reports. If the sum is below 24000 MiB it prints `ABORT: need 2xT4` and stops before it downloads 17 GB for nothing (source: `deploy_src.py`).

### The server binary

Compiling llama.cpp with CUDA on the four slow cores of a Kaggle machine took about 40 minutes (source: module docstring of `deploy_src.py`). The project therefore builds the server once, in a separate kernel called `linsxav/llama-server-builder`, and attaches that kernel as a source (`kernel_sources` in the metadata). At boot the script finds `llama-server-cuda.zip` under `/kaggle/input`, unpacks it into `/kaggle/working/bin`, sets `LD_LIBRARY_PATH` and asserts that `llama-server --help` runs. Unpacking took 3.5 to 7.0 seconds in the kernel logs we kept (source: `kernel.log` of v11 and v14). If the zip is missing, the script lists the input folder and stops. It has no fallback to compiling.

The build in production is a stock llama.cpp build, which `docs/results/GOLIVE_RESULTS.md` calls b9690. The quality study in Chapter 9 used a newer fork, b10709, which measured about 4 percent faster on the same settings (source: `docs/results/SPEED_QUALITY_EVAL.md`).

### Model file and quantization

The model is `Qwen3.8-27B-UD-Q4_K_XL.gguf` from the Hugging Face repository `unsloth/Qwen3.8-27B-GGUF`, 17.6 GB (source: `kaggle/endpoint/models.json`). GGUF is the single-file format llama.cpp reads: it holds the weights, the tokenizer and the chat template together. A name like Q4_K_XL says how the weights were compressed: roughly 4-bit blocks of the "K" kind, with the "XL" mix keeping some tensors at higher precision, and the "UD" prefix marks Unsloth's dynamic variant (this reading of the name is general knowledge about the format, not something the project measured). For scale, 17.6 GB over about 27 billion weights is around 5 bits per weight on average (our arithmetic from those two figures).

`fetch()` in the script lists the `.gguf` files in the repository, skips anything with `mmproj` in its name, and downloads the first file that matches the registry pattern. If that fails it tries `UD-Q4_K_M` and then `UD-Q6_K` from the same repository. Downloads took 65 s (v8), 363 s (v9), 92 s (v10), 84 s (v11), 91 s (v12), 89 s and 76 s (the two attempts logged under v14), and 82 s (v16) (source: boot logs of those runs). Only v9 was slow, and the logs do not say why.

The default stayed because of the four-model bake-off: it tied the others on first-try heals (14 of 24), healed 21 of 24 with thinking on against 15 to 17 for the others, and was the fastest (source: `docs/results/MODEL_BAKEOFF.md`; Chapter 9).

### Server flags

`launch()` kills any old server, waits 4 seconds and starts a new one in the background. The flags are these (source: `deploy_src.py`, `launch()`).

| Flag | Effect |
|---|---|
| `-m <file>` | The downloaded GGUF file. |
| `--n-gpu-layers 999` | Put every layer on the GPUs. |
| `--ctx-size <n>` | Context length, taken from the ladder below. |
| `--flash-attn on` | Flash attention. It was already on before the speed work, and tensor split needs it (source: `docs/results/SPEED_LEVERS_EVAL.md`). |
| `-sm tensor` | Split every tensor across both cards so both read weights at the same time. The default splits by layer, and the cards then work one after the other. |
| `--cache-type-k f16 --cache-type-v f16` | Unquantized attention cache. Tensor mode forbids a quantized cache, so these flags and `-sm tensor` change together (source: `docs/results/SPEED_LEVERS_EVAL.md`). |
| `--spec-type draft-mtp --spec-draft-n-max 2` | Speculative decoding with the model's own multi-token-prediction head: it drafts up to 2 tokens and the full model checks them in one pass. |
| `--parallel 1` and `-np 1` | One request slot. These are the same option written twice. Heal requests arrive one at a time. |
| `-b 2048 -ub 512` | Batch and micro-batch size for reading the prompt. |
| `-t 4 -tb 4` | Four CPU threads for generation and four for prompt batches. |
| `--reasoning-budget 1024` | Cap on thinking tokens when thinking is on. The code has no comment on it. |
| `--host 0.0.0.0 --port 8000` | Listen on port 8000 on all interfaces. |
| `--jinja` | Use the chat template stored in the model file. |
| `--api-key selfheal-e2e-key` | Require `Authorization: Bearer <key>`. |
| `--chat-template-kwargs '{"enable_thinking":false}'` | Default thinking to off. It is added only if the binary's help text lists this option. |

The speed work behind `-sm tensor` and MTP is in the last section of this chapter and in Chapter 9. Before it, the production flags used a quantized `q8_0` cache and layer split (source: `docs/results/SPEED_LEVERS_EVAL.md`, "What shipping looks like").

### Context ladder

The script tries context sizes of 131072, 98304, 65536, 32768, 16384 and 8192 tokens, largest first. Each try gets 300 seconds for `/health` to answer 200, and the wait ends early if the server process has died. A rung that boots must also answer three benchmark requests. The first rung that does is kept, and the script asserts if none does (source: `deploy_src.py`, `LADDER`).

The ladder used to start higher. In the first live round, sizes of 262144 and 196608 each hung for the full 15-minute health timeout, about 30 minutes of GPU time, before 131072 booted in under a minute. With tensor split, llama.cpp cannot fit the context to memory by itself, so a rung that is too large does not fail quickly (source: `docs/results/GOLIVE_RESULTS.md`, "Three defects found only by going live"; the server log of v11 has the warning `llama_params_fit is not implemented for SPLIT_MODE_TENSOR`). The model was trained for 262144 tokens, and the log of v11 says the 131072 setting leaves part of that unused.

Every boot log we kept shows the first rung, 131072, booting (source: boot logs v8 to v16). Memory in use afterwards was 26566 MiB of 30720 MiB in v8 to v12 and 26662 MiB in v15 and v16 (source: the same logs). The research notes explain why this fits: only 16 of the model's 64 layers keep a cache that grows with the context (source: `docs/research/QWEN38_HARDWARE_FACTS.md`, a secondary source, not measured by us).

### Thinking default

Qwen3.8's chat template makes the model think before it answers unless a request says otherwise. The v11 server log reads `chat template, thinking = 1` (source: `server.log` of v11). The n8n workflow always sends `enable_thinking` with each request: false on the first heal and true on a retry (source: `tools/nodes/build_ai_prompt.js`, line 155). The backend sends it only to turn thinking on, so before the change every backend call would have thought first. The classifier asks for at most 512 tokens, and thinking tokens count against that limit, so a long thought could leave no room for the JSON answer (source: `docs/results/BACKEND_ON_KAGGLE.md`).

The kernel therefore starts the server with thinking off, and a request that sets `enable_thinking: true` still turns it on. Thinking is expensive: in the shipped-quality study the median call took 36.8 s with it and 9.1 s without (source: `docs/results/SHIPPED_QUALITY_EVAL.md`). The policy that follows is thinking off for the first attempt and on for a retry, which the n8n chapter describes.

### Boot-time self-check

After a rung boots, the script does not trust its own flags. `bench()` sends three requests of up to 256 tokens at temperature 0.7 and reads the server's own `timings` field, not the log text. From that it works out three facts:

- **Tensor split engaged.** True when the two cards report memory use within 64 MiB of each other. Layer split leaves them uneven, tensor split makes them equal.
- **MTP engaged.** True when at least one request reported draft tokens.
- **Accept rate.** Accepted draft tokens divided by drafted tokens.

If either lever is missing the script prints a warning that the endpoint will be slower than measured, and serves anyway. This design came from two earlier mistakes in which a log-wording check reported a working lever as missing (source: `docs/results/SPEED_LEVERS_EVAL.md` and `docs/results/SPEED_QUALITY_EVAL.md`).

`thinking_check()` then asks "What is 17 * 23?" twice, once plain and once with thinking requested. A reply counts as thinking if it has `reasoning_content` or a `<think>` block. The expected result is false then true. Boot logs v14, v15 and v16 all show `thinking by default : False` and `thinking when asked : True` with the answer 391 both times (source: `stream.log` of v14 and v15, `boot.log` of v16). The older runs v8 to v12 predate this check.

The boot benchmark of each session that reached the end was this (source: the `SELECTED` lines of the boot logs).

| Session | Date | Average tok/s | Best tok/s | MTP accept rate |
|---|---|---|---|---|
| v8 | 2026-09-23 | 26.4 | 26.6 | 0.671 |
| v9 | 2026-09-23 | 26.6 | 27.5 | 0.633 |
| v10 | 2026-09-28 | 24.0 | 25.0 | 0.64 |
| v11 | 2026-09-28 | 23.9 | 24.4 | 0.613 |
| v12 | 2026-10-30 | 23.8 | 24.4 | 0.64 |
| v15 | 2026-11-15 | 26.8 | 27.4 | 0.664 |
| v16 | 2026-11-15 | 22.4 | 23.7 | 0.719 |

All seven had the tensor split engaged, with equal memory on both cards (source: the same logs). Version 14 and version 15 look like two sessions in the log folders, but the v14 kernel log shows a `TypeError` in `launch()` at the first rung, so v14 never served. Its `stream.log` continues with the output of v15, which was pushed about 3 minutes later (source: `kernel.log` of v14 and `history.jsonl`), and the two runs report the same tunnel address. This chapter counts them as one session.

The accept rates here are far below the 0.95 to 0.96 measured on real code fixes (source: `docs/results/SPEED_LEVERS_EVAL.md`). The boot benchmark asks for a general explanation at temperature 0.7, and that text is harder to guess than a code edit. Speed also varied between sessions from 22.4 to 26.8 tok/s with the same flags. The documents give no reason.

### Tunnel and changing address

When the benchmark is done, the script downloads `cloudflared` and starts `cloudflared tunnel --url http://localhost:8000`. It reads `tunnel.log` every 4 seconds, up to 45 times, until a `https://<words>.trycloudflare.com` address appears. This is a quick tunnel: it needs no Cloudflare account, and every start gets a new random name. The saved boot logs show seven different addresses for the seven sessions, for example `pilot-fixed-cooler-hello` in v8 and `hats-conducted-committees-hollywood` in v16.

A quick tunnel also cuts a request that stays silent for 120 seconds. An unstreamed request through it failed with HTTP 524 at 125.9 s, while the same request streamed succeeded after 166.5 s with 3600 tokens (source: `eval/runs/golive5/golive5_stream_proof.json`). Both the n8n workflow and the backend therefore ask for streamed replies. Only port 8000 is exposed, and the server still checks the API key. The key is a constant in the repository, so anyone who can read the repository can read it.

### Session window

After the tunnel step the script writes `endpoint.json` and sets a deadline of `HOURS = 1.5` from that moment. The comment gives the reason: the Kaggle command line cannot reliably cancel a running kernel, so the script ends itself. Once a minute the loop checks for a `llama-server` process and restarts it at the chosen context if none is found, and the tunnel stays up, so the address does not change. Every ten minutes it prints the full "ENDPOINT LIVE" block again and on other minutes a one-line status.

The log of v11 matches this. It reached the URL line at 223 s (3.5 s unpacking, 84 s download, about 90 s to boot, 34 s benchmark, 8 s tunnel) and its script output ends at about 94 minutes, which is 223 s plus 90 minutes (source: `kernel.log` of v11). The first live round used a window of 1.0 hours and the third round moved it to 1.5 (source: `docs/results/GOLIVE_RESULTS.md`). The launcher script quotes about 4 minutes from push to a usable address (source: `launcher/README.md`).

### How the address reaches n8n and the backend

`kaggle/tools/watch_deploy.py` follows the kernel log, and when it sees the `URL :` line it writes the address to `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt`, outside the repository. Two scripts read that file.

- **n8n.** `tools/golive/set_cloud_qwen_url.py` posts the address to the cloud workflow's `set-qwen-url` webhook, with a secret header. The node `Store Model URL` accepts only addresses that match `https://*.trycloudflare.com` and saves the address in the workflow's static data. The node `Get Model URL` reads it before each model call and falls back to the n8n variable `QWEN_BASE_URL` (source: `tools/nodes/store_model_url.js`, `tools/nodes/get_model_url.js`). A webhook is used because the n8n API is switched off on cloud trials.
- **Backend.** `kaggle/tools/backend_env.py` rewrites the `CLASSIFIER_` and `FIXER_` lines for provider, base URL, key and model name in `ci-heal-backend/.env`, and with `--check` calls `GET /v1/models` through the tunnel (source: `kaggle/tools/backend_env.py`). It has to run again after every boot.

The one-click start file `launcher/start/1_start_model_on_kaggle.bat` runs the status check, build, push, watch and webhook steps in order. A failed webhook step only prints a message, so the model can be running while n8n still holds the old address. In that case the Qwen call fails and the workflow goes to its OpenAI fallback and then to a failure comment.

### Quota and stopping

A Kaggle quota dialog read on 2026-09-07 showed 30 hours of GPU per week (source: `docs/research/KAGGLE_ENV_MEASURED.md`). The figure is from one dialog on one day, and Kaggle may change it. At 1.5 hours per window and ignoring boot time, 30 hours is at most 20 windows a week (our arithmetic).

A push does not replace a running kernel. Kaggle starts a second session, which would use quota twice, so `push.py` refuses to push while any of the four project kernels is `RUNNING` or `QUEUED` unless `--force` is given. The Kaggle README adds that GPU kernels can wait in the queue for hours (source: `kaggle/README.md`).

The command line cannot stop a session. The helper `launcher/end/stop_model_on_kaggle.bat` opens the kernel page and prints the steps: View Active Events, the session menu, Stop Session. `history.jsonl` records two manual stops through the website, for v12 and v15, and v12 ended with status `CANCEL_ACKNOWLEDGED` (source: `history.jsonl`). A session nobody stops runs out its 1.5 hours and uses quota the whole time. This is a real limit of the setup, because it depends on a person remembering to stop it, and the endpoint is a development service, not a durable host (source: `kaggle/README.md`).

### Measured speed

Speed was measured in four settings, and the figures do not belong in one column, so the table names the setting of each.

| Setting | Median tok/s | Source |
|---|---|---|
| First 9-config sweep, layer split, no MTP | 8.8 for the chosen file | `docs/old/QWEN38_SWEEP_RESULTS.md` |
| First live round trip, old flags (`q8_0` cache, layer split) | 12.11 mean | `docs/old/SELFHEAL_E2E_RESULTS.md` |
| Speed rig A, same old flags, ctx 8192 | 12.35 | `docs/results/SPEED_LEVERS_EVAL.md` |
| Rig B, `-sm tensor` with f16 cache | 15.49 (+25%) | `docs/results/SPEED_LEVERS_EVAL.md` |
| Rig C, MTP only | 19.99 (+62%) | `docs/results/SPEED_LEVERS_EVAL.md` |
| Rig D, both levers | 26.83 (+117%) | `docs/results/SPEED_LEVERS_EVAL.md` |
| Fork build b10709, both levers | 28.02 | `docs/results/SPEED_QUALITY_EVAL.md` |
| Boot benchmark, seven sessions, ctx 131072 | 22.4 to 26.8 average | boot logs v8 to v16 |
| Live decode, round 1 | 23.10 | `docs/results/GOLIVE_RESULTS.md` |
| Live decode, round 2 | 27.73 | `docs/results/GOLIVE_RESULTS.md` |
| Bake-off baseline, ctx 131072 | 23.4 | `docs/results/MODEL_BAKEOFF.md` |

We recomputed rigs A to D from `eval/runs/kout_speed/results.json`: 60 calls, no errors, and the same medians. In rig D the MTP head accepted drafts at a median rate of 0.96, and the per-card memory was 9051 MiB on each card, against uneven pairs such as 7761 and 9371 MiB in rig A (source: the same file). The tensor-split gain is smaller than the +47 to +68 percent reported for Ampere machines. The study traces this to PCIe: the two T4 cards have no NVLink, and it estimates the cost of combining results at about 24 ms per token (source: `docs/results/SPEED_LEVERS_EVAL.md`).

The live figures are lower than the rig because the inputs differ. Round 1 had an MTP accept rate of about 0.89 against 0.965 in the rig, since a search-and-replace answer is less predictable than a whole file that mostly copies the input (source: `docs/results/GOLIVE_RESULTS.md`). The same document ran with a context of 131072 against 8192 in the rig. Round 2 decoded faster at the same settings, and the document says the reason is not proven.

Prompt reading ran at 274 to 323 tok/s in round 1, which is about 2.2 s for a prompt of roughly 700 tokens. A model call through the tunnel carried 5 to 6 s of fixed overhead on top of generation (source: `docs/results/GOLIVE_RESULTS.md`). The study that set `--spec-draft-n-max` named values 3 and 4 as the next thing to test, and we found no result for them in the documents, so 2 is the value in use but not a tuned one.

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

## Evaluation

### How to read this chapter

The project ran nine studies, each on the Kaggle machine or against the live pipeline. This chapter gives them in the order they answer questions about the healer: first which model file to load, then how to make it fast, then what output format to ask for, then how often the fix is right, and last how the whole pipeline behaves live. Each section states the question, the method, the decision rule when the results document names one, the headline numbers and the conclusion.

Every number comes from a results document in `docs/results/` or `docs/old/`, with the file named after it in brackets. Where saved data exists under `eval/runs/`, we recomputed the headline numbers from it. A section says so when the data confirmed the document, and says so when it did not or when the data was not kept. Disagreements between documents are named where they occur and collected in the last table.

### The evaluation harness

The `eval/` folder holds the test cases, the scorers, the scripts that build Kaggle kernels from them, and the saved results in `eval/runs/`.

**Cases.** There are 5 control cases (`eval/cases/eval_cases.json`), 6 hard single-file cases (`eval/cases/hard_cases.json`) and 6 multi-file cases (`eval/cases/multifile_cases.json`). The control cases are the five breakages the live runner pushes to the demo repository: a missing `app` line, a misspelled method, a missing `require`, a missing closing brace and a wrong status check. The hard cases come from the same file, but the CI log no longer points at the bug. The multi-file cases use the demo app's `src/app.js`, `src/format.js` and `test/api.test.js` (source: `docs/results/SHIPPED_QUALITY_EVAL.md`, `docs/results/MULTIFILE_EVAL.md`). We counted the cases in the three JSON files.

**Same code as production.** The later studies do not paraphrase the pipeline. They run the real node code from `tools/nodes/` (`build_ai_prompt.js`, `extract_ai_response.js`, and for multi-file cases `Pick Candidate Files`) under `node`, send the request body unchanged to the model, and apply the reply with the real extraction node, so its exactly-once and syntax gates count (source: `docs/results/SHIPPED_QUALITY_EVAL.md`, `docs/results/MODEL_BAKEOFF.md`).

**What "healed" means.** Live, a case counts as healed when three things hold: the file on the pull request branch parses, it is byte-identical to the known-good file, and the real smoke test passes (source: `docs/results/GOLIVE_RESULTS.md`). In the offline studies the test result comes first. The patched file is run as the real smoke test (`node test.js` with express), and exit code 0 means healed; for multi-file cases the full `npm test` must pass. Byte equality with the golden file is kept as a second, stricter column, and a `mustnot` pattern flags answers that cheat (source: `eval/shipped_quality_eval.py`, function `score`). Before any GPU time, a preflight proves that the golden file passes and that every broken file fails with exit code 1.

**Why test first, byte-exact second.** The alternative-quant study found a case, `route_wrong_status`, that scored 0 of 12 byte-exact and 12 of 12 functionally correct, because the models wrote `res.status(200).send("ok")` where the golden file has `res.send("ok")` (source: `docs/results/SPEED_QUALITY_EVAL.md`). A byte comparison alone would have marked a valid fix as a failure. The earliest studies scored with fix patterns and `node --check`, the alternative-quant study used byte equality as its main score, and the later ones run the test.

**Scorer self-tests.** Each scorer is tested locally before it is trusted. `eval/test_shipped_eval.py`, `eval/test_bakeoff_eval.py`, `eval/test_multifile_eval.py` and `eval/check_multifile_cases.py` feed known-good and known-bad outputs through it, and the saved outputs in `eval/runs/testall/` report `FAILURES: 0` for each. The shipped-quality test includes 15 real captured model replies, all of which score as functional heals. The self-test of the speed-levers study is a warning: it passed every assertion while the check it protected gave a false answer, because its fixture came from a dump of the binary and not from a real log (source: `docs/results/SPEED_LEVERS_EVAL.md`). After that the engagement tests ran against the four captured server logs.

**Re-scoring.** Every result file stores the raw model reply and a flag that says whether it was cut short. `eval/rescore_shipped.py`, `eval/rescore_multifile.py` and `eval/rescore_bakeoff.py` recompute every verdict from the raw text alone, with the same node code and a local express. We ran all three. The shipped-quality file re-scored 84 of 84 calls with no disagreement, the multi-file file 57 of 57, and the bake-off file 232 of 232, and the bake-off run also reproduced its decision (source: output of the three scripts, run for this chapter; the same counts are in `docs/results/SHIPPED_QUALITY_EVAL.md`, `MULTIFILE_EVAL.md` and `MODEL_BAKEOFF.md`). The speed-levers run did not save raw output, so its correctness column cannot be re-scored.

**Rig corrections.** Two studies each found that a check on log wording gave a false negative, and one found a recount option that hid a bad patch. The project's rule since then is to judge a feature by its measured effect, such as the accept rate or equal memory on both cards, and not by a log string (source: `docs/results/SPEED_LEVERS_EVAL.md`, `docs/results/SPEED_QUALITY_EVAL.md`, `docs/results/DIFF_FORMAT_EVAL.md`).

### Model choice sweep

**Question.** Which quantized Qwen3.8-27B file gives a good fix on two T4 cards?

**Method.** One kernel ran 9 configurations in a batch of 252 minutes on two T4 cards. Quality was a 5-case CI-failure set scored 3 points per case (valid JSON, `node --check`, correct fix). Speed came from the server's own `timings` field (source: `docs/old/QWEN38_SWEEP_RESULTS.md`). The document names no decision rule in advance. A second note covers three sweeps, 24 configurations in all (source: `docs/old/QWEN38_SPEED_CEILING.md`).

**Numbers.** The raw `results.json` of these sweeps was not kept in the repository, so we could not recompute them and the figures below rest on the documents alone.

| Config | Size (GB) | Quality | Generation tok/s |
|---|---|---|---|
| UD-Q4_K_XL (unsloth) | 17.6 | 15/15 | 8.8 |
| IQ2_S (ISTA-DASLab) | 9.6 | 14/15 | 8.4 |
| Q8_0 (barozp) | 29.0 | 14/15 | 5.9 |
| Q4_K_M with feed-forward tensors on CPU | 16.8 | 14/15 | 1.1 |

(source: `docs/old/QWEN38_SWEEP_RESULTS.md`)

**Conclusion.** Four of the five cases scored 3 of 3 for all nine configurations, so the set could not rank them. The one case any file missed needed semantic reading of the test. Speed was the only usable axis, and UD-Q4_K_XL was fastest at 8.8 tok/s with a perfect score, so it was chosen. Q8_0 did load in 30.7 GB of split memory but was the slowest file that ran on the GPUs, and moving feed-forward tensors to CPU memory cut one file from 7.2 to 1.1 tok/s.

Two later results weaken parts of this study. The 8.8 tok/s figure turned out to be low: the first live run with different batch flags measured 12.1 tok/s on the same file (source: `docs/old/SELFHEAL_E2E_RESULTS.md`), and the speed study below reached 26.83. The case that every config failed offline, `wrong_status_check`, was healed first try live with the real log and full file, so the sweep's synthetic snippet hid information the case needed (same source). The sweep's quality ranking therefore carries little weight, and the file choice was confirmed later by the bake-off.

### Speed levers

**Question.** The first sweeps said 30 tok/s was out of reach. Is that true if the server splits each tensor across both cards (`-sm tensor`) and uses the model's own multi-token-prediction head (MTP) for speculative decoding?

**Method.** Four configurations (A baseline, B tensor split, C MTP, D both) on 5 scenarios with 3 repetitions each, 60 calls, temperature 0.1, context 8192, with the same production file (source: `docs/results/SPEED_LEVERS_EVAL.md`). The document names no decision rule in advance, and states the verdict afterwards: ship both.

**Numbers.** We recomputed the medians from the 60 saved calls, which had no errors (checked in `eval/runs/kout_speed/results.json`).

| Config | Median tok/s | Gain over A | Median wall time | Correct fixes | Draft accept rate |
|---|---|---|---|---|---|
| A baseline | 12.35 | none | 56.7 s | 15/15 | none |
| B tensor split with f16 cache | 15.49 | +25% | 45.3 s | 15/15 | none |
| C MTP only | 19.99 | +62% | 36.8 s | 15/15 | 0.953 |
| D both | 26.83 | +117% | 27.8 s | 15/15 | 0.960 |

(source: `docs/results/SPEED_LEVERS_EVAL.md`, matched by `eval/runs/kout_speed/results.json`)

Both levers were shown to have run by their effect. In configs B and D the two cards held equal memory (8721 and 8721 MiB, and 9051 and 9051 MiB), while A and C were uneven (for example 7761 and 9371 MiB in A) (checked in `eval/runs/kout_speed/results.json`).

**Conclusion.** The two levers together made the model 2.17 times faster with no loss on this fixture, and the older argument that 30 tok/s was physically impossible rested on two premises that no longer hold (one card's bandwidth at a time, one token per weight read). The document's own caveats matter. Config B is not a clean single change, because tensor mode forbids a quantized cache and the two changed together. The context was 8192 here against 32768 in production at the time, the correctness column rests on in-kernel patterns and cannot be re-scored, and the projected cycle of about 75 s was a projection from medians, not a measurement.

### Speed and quality of alternative quantizations

**Question.** The Bonsai ternary files (5.95 and 7.21 GB) and the Ridge file (12.6 GB, built for this model's layer mix) are much smaller than UD-Q4_K_XL. Do they give the same fixes, and how fast do they run?

**Method.** 4 packings, 11 cases (5 control and 6 hard) and 3 repetitions: 132 calls, no errors and no truncations, one runtime (a fork build) for all four. Each packing tried four settings from tensor split plus MTP down to layer split and kept the best that worked (source: `docs/results/SPEED_QUALITY_EVAL.md`). The document says a decision rule was fixed before the data and gives its effect: smaller size is not a reason to switch. It does not quote the rule.

**Numbers.** (Checked in `eval/runs/kout_qs/results.json`: all 132 calls, counts and medians match.)

| Packing | Size (GB) | Setting reached | Control | Hard, byte-exact | Median tok/s |
|---|---|---|---|---|---|
| UD-Q4_K_XL | 17.56 | tensor split and MTP | 15/15 | 9/18 | 28.02 |
| Ridge 3.7 bpw | 12.60 | tensor split and MTP | 15/15 | 5/18 | 28.52 |
| Bonsai PQ2_0 | 7.21 | layer split | 15/15 | 9/18 | 21.02 |
| Bonsai PTQ1_0 | 5.95 | layer split | 15/15 | 9/18 | 17.09 |

(source: `docs/results/SPEED_QUALITY_EVAL.md`)

The Bonsai speeds are not like for like, since those files fell back to layer split. The server aborted on tensor split for them and found no MTP tensors in the converted files (source: same document).

**Conclusion.** UD-Q4_K_XL stays. Ridge is measurably worse: it fails `inverted_status_check` in all three repetitions while the others pass all three, and it gains 1.8 percent speed. The Bonsai files show no detectable quality difference but run 25 and 39 percent slower. The document chooses the phrase "no detectable difference" over "equal", because the three packings other than Ridge were identical on all six hard cases, so the set can only rule out a large gap. Only 2 of 6 hard cases separated anything. One of the three cases that every packing failed, `route_wrong_status`, was a scorer error as described in the harness section. The data also shows a detail the document leaves out: Ridge scored 6 of 18 on the hard set by the fix pattern but 5 of 18 byte-exact (checked in `eval/runs/kout_qs/results.json`), and the document reports the byte-exact figure.

### Diff format comparison

**Question.** The model rewrites the whole file to fix one line, and generation took about 55 percent of the push-to-PR time. Is a patch format cheaper without making fixes worse?

**Method.** Three output formats (whole file, unified diff, search and replace) on the five control scenarios with 3 repetitions: 45 calls, none truncated, context 32768, temperature 0.1. The score is applied, parses with `node --check`, and matches the fix patterns; byte-exact against golden is a stricter column (source: `docs/results/DIFF_FORMAT_EVAL.md`).

**Decision rule, fixed before the data.** "Ship diffs if a format holds `fix_correct` at or above whole-file's and `applied_ok` is 100%, at materially fewer tokens. Don't ship if either correctness or apply-rate drops." The document adds that the plan had also listed `node --check` on the applied result as a scored metric, so using it was not a rule added after the data.

**Numbers.** (Checked in `eval/runs/kout_diff/results.json`: medians, apply counts and parse counts all match.)

| Format | Prompt tokens | Generated tokens | Wall time | Applied | Parses | Byte-exact |
|---|---|---|---|---|---|---|
| Whole file | 794 | 655 | 56.4 s | 15/15 | 15/15 | 15/15 |
| Unified diff | 1150 | 233 | 23.3 s | 15/15 | 12/15 | not scored |
| Search and replace | 861 | 171 | 17.0 s | 15/15 | 15/15 | 15/15 |

(source: `docs/results/DIFF_FORMAT_EVAL.md`)

**Conclusion.** Search and replace ships: the same correctness as whole file with 3.8 times fewer generated tokens and 39 seconds saved per heal. Unified diff does not ship. All three failures were the missing-brace scenario, where the patch added only an empty line. A strict `git apply` rejected it, a `--recount` retry then accepted it, and the fix pattern matched a different `});` elsewhere in the file, so the apply and fix columns read 15/15 although 3 files did not parse. The stored summary in the data file still shows 1.0 for both columns for that reason. Only the syntax check caught it. The document projects a cycle of about 65 s and says this was not measured, and that the fallback rate on a larger file was unknown. The two source lengths that appear do not conflict: the document's 1862 characters is the length of the known-good `test.js` (1862 bytes), and the data file's `source_chars` of 1839 is the length of the broken file the model was shown, which is 23 characters shorter.

### Shipped-format quality, with and without the CI log and thinking

**Question.** On the format that shipped, does putting the real CI log in the prompt raise the heal rate, and does thinking help the hard cases?

**Method.** 11 cases (5 control, 6 hard) with 3 repetitions in three arms: A without the log, B with the log, C with the log and thinking (budget 1024 tokens, hard cases only). That is 84 calls with no errors and no truncation, on the production server flags (source: `docs/results/SHIPPED_QUALITY_EVAL.md`).

**Decision rule, fixed before the data.** The log is judged by whether it adds at least 3 healed hard calls (B minus A), and thinking by whether it adds at least 3 (C minus B) without failures (source: same document, "Decisions, by the rules set in advance").

**Numbers.** (Checked in `eval/runs/kout_shipped/results.json`.)

| Arm | Control healed | Hard healed | Hard byte-exact | Median wall time on hard cases | MTP accept |
|---|---|---|---|---|---|
| A no log | 15/15 | 12/18 | 6/18 | 9.2 s | 0.904 |
| B with log | 15/15 | 12/18 | 9/18 | 9.1 s | 0.883 |
| C log and thinking | not run | 18/18 | 9/18 | 36.8 s | 0.756 |

(source: `docs/results/SHIPPED_QUALITY_EVAL.md`)

The hard-case times and the byte-exact counts matched the data. The accept rates match the medians over all calls of each arm, and the data gave 0.894 and 0.878 for the hard calls alone, so the table is slightly mixed in what it averages. Case by case, `callback_arity` and `multi_hunk` were 0 of 3 in arms A and B and 3 of 3 in arm C, and nothing else changed.

**Conclusion.** The log did not change the heal rate (12 to 12, short of the 3 needed), but it moved fixes closer to the golden file (6 to 9 byte-exact), costs about 3 seconds of extra log-fetch time in the workflow (the model call itself took 9.1 s against 9.2 s) and stays in the prompt. Thinking passes its rule (+6 of 18) and is the only setting that ever fixed those two cases, at about four times the time: a median of 36.8 s and a worst case of 77.9 s. That led to the shipped policy of thinking on retries only. The document's caveats are that arm C saw the original broken file, not a file with a wrong first fix, so it does not measure retries, and that 3 repetitions in one session fix the magnitudes only for that session.

The data folder also holds a second run of the same kernel, `eval/runs/kout_shipped_v2/results.json`, started on 2026-09-27, which no results document describes. We recomputed it. Arm A healed 15 of 15 controls and 10 of 18 hard cases, arm B 15 of 15 and 12 of 18, and arm C 18 of 18, with a median of 45.75 s and a worst case of 58.5 s on arm C. The B and C heal counts repeat the first run, and arm A was 2 lower. The documents do not say what changed between the runs.

### Multi-file healing

**Question.** Can the pipeline heal bugs whose cause is in a different file from the one that changed?

**Method.** Six multi-file cases, each made from the golden tree by an exact mutation, with 3 repetitions on the live endpoint through the tunnel, streamed as in production. Arms: M the fast first pass, MT thinking on the first pass, R the production retry on the failures of M, and B the single-file controls. A heal counts only if the full `npm test` passes (source: `docs/results/MULTIFILE_EVAL.md`).

**Decision rule, fixed before the data.** Multi-file healing is declared shipped only if the fast first pass heals at least 12 of 18.

**Numbers.** (Checked in `eval/runs/kout_multifile/results.json`. The file has 59 rows, because two calls hit a connection reset and were re-run; counting each of the 57 final calls once gives the figures below.)

| Arm | Healed | Fix landed in a file that could fix it | Median time |
|---|---|---|---|
| M fast first pass | 9/18 | 15/18 | 8.2 s |
| MT thinking first pass | 16/18 | 18/18 | 39.6 s |
| R retry after M | 6/6 | 6/6 | 45.9 s |
| B single-file controls | 15/15 | 15/15 | 7.6 s |

(source: `docs/results/MULTIFILE_EVAL.md`)

**Conclusion.** By the rule, multi-file healing is not declared shipped (9 is below 12). The candidate-selection nodes ship anyway, since they leave single-file prompts byte-identical (controls 15 of 15) and are the only way two of the six cases can be reached. The fast pass fails in one recognisable way: it stops the crash but misses what the test expects. The retry, which sees the new log, fixed all 6 such cases. The projection of 15 of 18 with the retry loop is from replay, assumes no connection resets, and had not been confirmed live. The remaining misses are the caller gap: `signature_changed_caller_not` needs a fix in `src/app.js`, which the pipeline does not show the model.

### Four-model bake-off

**Question.** Does another Qwen3.8-27B build of similar size beat the current file on our cases?

**Method.** Four Q4-class builds, all with an MTP head, on one 2xT4 session with the production flags and context 131072. There were 17 cases (5 control, 6 hard, 6 multi-file) with 2 repetitions: 232 calls. F means thinking off, as on the first heal, and T means thinking on, as on a retry (source: `docs/results/MODEL_BAKEOFF.md`).

**Decision rule, fixed before the data.** A model must pass the controls (10 of 10), reach an MTP accept rate of at least 0.5, show the tensor split, and not think in F replies. Eligible models are ranked by first-try heals on the 24 hard and multi-file calls, and a challenger replaces the baseline only with at least 3 more first-try heals at 95 percent or more of the baseline's tok/s.

**Numbers.** (Checked in `eval/runs/kout_bakeoff/results.json`.)

| Build | Controls | F heals of 24 | T heals of 24 | tok/s | Speed vs baseline | T median / max time |
|---|---|---|---|---|---|---|
| UD-Q4_K_XL, baseline | 10/10 | 14 | 21 | 23.4 | 100% | 50.5 s / 110 s |
| Fable Cold Fusion 735 | 10/10 | 14 | 15 | 17.6 | 75% | 14.4 s / 65 s |
| Jumbo Fusion 747 | 8/10, ineligible | 14 | 16 | 19.3 | 82% | 12.6 s / 62 s |
| Coder390 EfficientThink | 10/10 | 14 | 17 | 16.9 | 72% | 32.4 s / 78 s |

(source: `docs/results/MODEL_BAKEOFF.md`)

The heal counts, speeds and control results match the data exactly. The time column matches our recomputation from the raw calls (50.55 s, 14.4 s, 12.55 s and 32.35 s medians). The `summary` block inside the data file gives different values for three of the builds (56.5 s, 12.9 s and 34.7 s), and we did not find how that field is computed, so we used the raw calls.

**Conclusion.** The baseline is kept. All four heal the same 7 of the 12 hard and multi-file cases on the first try and miss the same 5, so another build at this size cannot raise first-try accuracy. What differs is thinking: with thinking on, the baseline writes a median of 991 tokens and the "efficient thinking" builds 176 to 557, and the baseline heals more on retry. The Q4_K_M builds were also slower, and the document says the cause was not isolated. `signature_changed_caller_not` failed on every build and arm, 0 of 16 for the same pipeline reason as before. The document's caveats are 2 repetitions, so a one-heal gap is noise, challengers run at temperature 0.1 without tuning, and self-made cases on one demo repository.

### Live end-to-end rounds

**Question.** Does the whole pipeline, from a pushed failure to a green pull request, work on a real repository, and how long does it take?

**Method.** A runner pushes a breakage to the demo repository `Lin-2352/self-healing-cicd-demo`, waits for the pull request, and scores the branch. Timings come from GitHub's own timestamps and from n8n's stored executions (source: `docs/results/GOLIVE_RESULTS.md`). The documents state no accept or reject rule for these rounds, except that the first baseline was judged against a bar of under about 6 minutes (source: `docs/old/SELFHEAL_E2E_RESULTS.md`).

**Baseline before the live rounds.** The first end-to-end measurement ran the shipped Code nodes under a harness that stood in for n8n itself, on a 13-node flow, with whole-file output and 12.1 tok/s. It healed 5 of 5 with a median of 104 s from push to pull request and a worst case of 116 s. The model phase was 56 to 60 s, about 55 percent of the time. Running the real workflow inside real n8n then found four bugs the harness could not (source: `docs/old/SELFHEAL_E2E_RESULTS.md`). The raw files of that run were not kept in the repository, so these numbers rest on the document. The current workflow has 26 nodes (source: `docs/architecture/n8n-workflow.md`).

**Rounds.** The table gives the headline of each round. We checked the push-to-PR times and outcomes against the files in `eval/runs/golive1` to `golive5`. The per-node times and the model decode speeds come from n8n's database, which is not in the repository, so we could not recheck those.

| Round | Date | Result | Push to PR or green (median) |
|---|---|---|---|
| 1, new format and levers | 2026-09-22 | 5/5 healed, byte-exact, smoke test passes | 44 s to PR |
| 2, CI log in prompt, PR CI | 2026-09-23 | 5/5 healed, 5/5 heal PRs green on their own CI | 42 s to PR |
| 3, retry loop | 2026-09-23 | 5 controls green on attempt 1; `multi_hunk` green via retry twice; `callback_arity` gave up at 3 attempts | 59 to 103 s to green for controls; 161 s and 151 s for `multi_hunk` |
| 4, full test run | 2026-09-28 | in the data: 5 controls green on attempt 1, `multi_hunk` green in 173 s, `callback_arity` stalled after attempt 1 | 60 to 70 s to green for controls |
| 5, streaming and failure comments | 2026-09-28 | `callback_arity` green on attempt 3 in 249 s, `multi_hunk` green in 143 s, `missing_require` green in 54 s | not reported as a median |

(source: `docs/results/GOLIVE_RESULTS.md` for rounds 1 to 3 and 5; `eval/runs/golive4/golive4_retry_results.json` for round 4, which the results document mentions only in passing)

**What the data shows that the document explains.** The stored file for round 1 has 7 rows and marks only 2 of them as healed (#13 and #14, both with the golden file). Two rows ended with no pull request (the broken prompt-building node of the first runs) and three (#10 to #12) scored `file_is_golden: false`. The document says the runner stripped the file's trailing newline and so judged identical files as different, and that all pull requests were re-scored from their branches. We could confirm the two later rows (#13 and #14) as golden in the data and the 44 s median over the five pull requests, but the re-scoring of #10 to #12 is not in the stored file. The round 2 file has 5 of 5 golden, median 42 s, as the document says.

**Findings that changed the system.** Round 1 found that the model never saw the CI log, that a string split over ten lines broke the prompt node, and that a ladder size too large hung. Round 2 fixed those and found that GitHub's redirect would have sent the access token to the log host. Round 3 showed the retry loop works but that retry prompts anchor on a wrong first fix. Round 5 fixed the 120 s tunnel cut-off by streaming. A streamed request answered after 166.5 s, and the same unstreamed request failed with HTTP 524 at 125.9 s (checked in `eval/runs/golive5/golive5_stream_proof.json`).

**Speed in production.** The median decode speed from n8n's stored timings was 23.10 tok/s in round 1 and 27.73 tok/s in round 2, with a model call of a median 11.4 s and then 9.3 s (source: `docs/results/GOLIVE_RESULTS.md`; not rechecked). Thinking retries took 51.6 to 67.2 s in round 3, and one retry in round 4 took 125.9 s.

**Conclusion.** Once the round 1 defects were fixed, every control case that was run healed on the first attempt (rounds 2 to 5). Push to pull request went from a median of 104 s for the baseline to 44 s and 42 s after the format and speed changes. The hard cases need the retry loop, and one of them, `callback_arity`, needed three attempts.

### The backend on the Kaggle endpoint

**Question.** Can `ci-heal-backend`, the second healer, use the same endpoint for both its classifier and its fixer?

**Method.** Both model roles were pointed at the endpoint (kernel v15) and the backend's six fixture failures were replayed through its real pipeline, with GitHub mocked and `DRY_RUN` on (source: `docs/results/BACKEND_ON_KAGGLE.md`). The document states no decision rule.

**Numbers.** (The replay output is in `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-11-15_v15/backend_replay.log`; we checked the first fixture there and the rest are from the document.)

| Fixture | Expected class | Classified, with confidence | Decision | Time |
|---|---|---|---|---|
| configuration | configuration_error | configuration_error (0.95) | fix, 3 of 3 candidates applied | 28 s |
| dependency | dependency_error | dependency_error (0.98) | escalate, candidates disagreed | 30 s |
| flaky | flaky_or_network | flaky_or_network (0.95) | rerun the job | 8 s |
| prompt_injection | test_failure | test_failure (0.98) | fix, agreement 1.0 | 26 s |
| secrets | dependency_error | dependency_error (0.98) | escalate, candidates disagreed | 40 s |
| test_failure | test_failure | test_failure (0.98) | fix | 29 s |

(source: `docs/results/BACKEND_ON_KAGGLE.md`)

**Conclusion.** All six fixtures were classified correctly on the first sample, every reply parsed, and all 15 fix candidates applied. The two escalations are the agreement gate working as set: the three dependency patches differed and agreement fell below 0.6. The study also found the thinking default that Chapter 7 describes. The limits are one run per fixture, fixtures written for the backend's own tests, no pull request opened and no CI run on a fix. The prompt-injection case was fixed like an ordinary failure, and the replay does not show whether any candidate followed the injected text.

### Limits of the evaluation

The table lists only limits that the project documents themselves state or that we found in the saved data.

| Limit | Evidence | Source |
|---|---|---|
| Small case sets: 5 control, 6 hard and 6 multi-file cases | Only 2 of 6 hard cases separated any packing, and one of the 5 original cases was the only one any model missed | `docs/results/SPEED_QUALITY_EVAL.md`, `docs/old/QWEN38_SWEEP_RESULTS.md` |
| Control set is saturated | All four packings scored 15 of 15 on the controls, so the controls rank nothing | `docs/results/SPEED_QUALITY_EVAL.md` |
| One demo app, with self-made cases | The bake-off names this itself; the diff study used a 45-line file with five mechanical bugs, and the fallback rate on larger files is unmeasured | `docs/results/MODEL_BAKEOFF.md`, `docs/results/DIFF_FORMAT_EVAL.md` |
| Few repetitions, one session each | 3 repetitions in one session (shipped quality), 2 in the bake-off where a 1-heal gap is noise; the first sweep was a single run | `docs/results/SHIPPED_QUALITY_EVAL.md`, `docs/results/MODEL_BAKEOFF.md`, `docs/old/QWEN38_SWEEP_RESULTS.md` |
| Speed differs between sessions | Boot benchmark ranged from 22.4 to 26.8 tok/s with the same flags, and the documents give no reason | boot logs v8 to v16 |
| Speed numbers come from different settings | Rig medians (ctx 8192), fork build, boot benchmark and live decode do not form one series | `docs/results/SPEED_LEVERS_EVAL.md`, `docs/results/GOLIVE_RESULTS.md` |
| Caller gap | `signature_changed_caller_not` healed 0 of 16 in the bake-off, and the fix belongs in a file the pipeline does not show | `docs/results/MODEL_BAKEOFF.md` |
| Byte-exact scoring is too strict | `route_wrong_status` was 12 of 12 working and 0 of 12 byte-exact, so exact counts understate every packing by the same amount | `docs/results/SPEED_QUALITY_EVAL.md` |
| Thinking arm did not measure retries | Arm C saw the original file, not a file carrying a wrong first fix | `docs/results/SHIPPED_QUALITY_EVAL.md` |
| Offline evals skip the tunnel | They run on localhost with `stream: false`, while live calls add 5 to 6 s of overhead | `docs/results/GOLIVE_RESULTS.md` |
| Multi-file projection not confirmed | 15 of 18 is from replay, assumes no connection resets, and the write path for a file other than `test.js` had never run live; the kernel version of that eval had never been run | `docs/results/MULTIFILE_EVAL.md` |
| Live rounds are five cases each | Round times include GitHub Actions queueing, for example a 73 s and an 84 s outlier | `docs/results/GOLIVE_RESULTS.md` |
| Some raw data is not in the repository | Sweep results, the first end-to-end files, the speed-levers scripts and n8n's execution database | `docs/old/SELFHEAL_E2E_RESULTS.md`, `docs/old/QWEN38_SPEED_CEILING.md`, `docs/results/SPEED_LEVERS_EVAL.md` |
| Speed-levers correctness cannot be re-scored | The run saved no raw output | `docs/results/SPEED_LEVERS_EVAL.md` |
| Backend replay stops at the decision | One run per fixture, six fixtures, no pull request and no CI | `docs/results/BACKEND_ON_KAGGLE.md` |
| Documents differ from the data in places | Bake-off time summary against raw calls; a second shipped-quality run not described; round 1 stored as 2 of 7 healed before re-scoring | `eval/runs/kout_diff/results.json`, `eval/runs/kout_bakeoff/results.json`, `eval/runs/kout_shipped_v2/results.json`, `eval/runs/golive1/golive_results.json` |

## A recorded live run

Chapter 9 reports the measured live rounds of September 2026. This chapter follows one complete run from 2026-11-15, recorded with screenshots of the GitHub and n8n pages, from a broken commit to a passing fix pull request. The screenshots are in `docs/media/shots/` and are joined with captions into `docs/media/demo.mp4`, which is built by `tools/media/build_demo_video.py` from `docs/media/demo_spec.json`.

### Setup of the run

- **Repository:** `Lin-2352/Self-Healing-CI-CD-Pipeline`, a throwaway public repository that holds a copy of the demo app. Its workflow file is named `main.yml` (workflow name "CI Pipeline"); the main repository of this project carries the same logic as `.github/workflows/demo-ci.yml`.
- **Healer:** the n8n Cloud instance, workflow "Self-Healing CI/CD Pipeline (Qwen3.8)", reached through the production webhook `/webhook/github-failure`.
- **Model:** Qwen3.8-27B on Kaggle, address stored in the workflow through the Set Model URL webhook.
- **The break:** commit `4da7e41`, message "break: remove the clean variable in formatGreeting". It deletes the line `const clean = String(name).trim();` from `src/format.js` and leaves `${clean}` in the template string on the next line (screenshot 01).

### What happened

| step | observed | evidence |
|---|---|---|
| 1 | The push to `main` started the workflow run "CI Pipeline #2". | docs/media/shots/02_actions_runs.jpg |
| 2 | `build-and-test` failed after 9 s; the run took 16 s in total. The `notify-n8n-on-failure` job ran and passed after 3 s. | 03_failed_run.jpg |
| 3 | The step log shows `ReferenceError: clean is not defined` at `src/format.js:4:20`, then an API test failure, then the line `npm test exited 1 -> failure_kind=code`. | 04_failed_job_log.jpg |
| 4 | n8n execution 2 started at 20:45:04, ran 18.699 s and finished with every node green, including all of the branch, push and pull-request nodes. | 05_n8n_execution.jpg, 06_n8n_execution_zoom.png |
| 5 | Pull request #1 "Fix ReferenceError in formatGreeting" opened from the branch `ai-fix-format-greeting-4da7e41` (commit `f3e8f4c`). | 07_pr_conversation.jpg |
| 6 | The diff changes one line: `${clean}` becomes `${name}`. | 08_pr_diff.jpg |
| 7 | CI on the pull request ran 12 s and passed. | 09_pr_checks.jpg, run "CI Pipeline #3" |

The breaking commit was pushed at 8:44 PM and the fix pull request's own CI run is listed at 8:45 PM, so the whole chain from push to a passing fix pull request took about a minute on GitHub's minute-resolution timestamps. The two intervals that the pages report to the second are the n8n execution (18.7 s) and the CI run of the fix (12 s).

### What the run does not show

The fix is not the original code. The deleted line also converted the value to a string and trimmed it; the model's edit replaces the variable with the raw parameter, so the tests pass while the trimming behaviour is gone. The pipeline's own gates (the SEARCH text matches once, the file parses, CI passes) cannot detect this, which is why the result is a pull request for a person to review, not a merge.

The run used the n8n workflow as it stood before the fallback nodes were added (the execution canvas shows 20 nodes). Later runs on the same repository the same evening used the 26-node version: a run with the model address deliberately set to a dead host went through the OpenAI fallback node, which answered `Forbidden` (n8n's included free credits did not work for that webhook-triggered run; the cause was not established), and a run after the model address was stored through the Set Model URL webhook healed through Qwen and opened pull request #2 (execution 6, 17.886 s). The fallback routing is verified by the real-engine tests in Chapter 11; a fix produced by OpenAI has never been observed.

### A run that could not be repeated

A second live run for this report was started on 2026-11-15: version 18 of the Kaggle endpoint notebook was pushed and then stayed in the Kaggle queue for about an hour and a half without receiving a GPU. It was cancelled, and the recording above was built from the earlier run, whose pages are still on GitHub and in n8n. Availability of the free GPU is a real operating constraint (Chapter 13).

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

The generator check regenerates `n8n/self_healing_pipeline.json` from `tools/gen_n8n.py` and the node files and requires a byte-identical result, so the file in the repository cannot drift from its source. In the last full run every check passed: 12 without the rig and 13 with it.

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

## Security

A pipeline that reads logs written by anyone who can push code, sends them to a model, and then writes to a repository has three things to protect: its credentials, the repository, and its own decisions. This chapter lists what is protected and how, and ends with the weaknesses that are known and open.

### Credentials

| secret | where it lives | who can read it |
|---|---|---|
| Webhook shared secret | the n8n credential "N8N Webhook Secret" (header auth, header `X-Webhook-Secret`), the GitHub repository secret `N8N_WEBHOOK_SECRET`, and `secrets.json` on the operator's PC | the workflow, the notify job and the launcher scripts |
| GitHub token | the n8n credential "GitHub PAT" (bearer); locally taken from the GitHub CLI by `tools/golive/n8n_import.py` | the n8n HTTP nodes that call the GitHub API |
| Model endpoint key | the n8n credential "Qwen3.8 Endpoint Key" and the constant `API_KEY` in `kaggle/endpoint/deploy_src.py` | anyone who can read the repository |
| Webhook address | the repository secret `N8N_WEBHOOK_URL` | the notify job |

Runtime secrets (`secrets.json`, the saved model address) live under `%LOCALAPPDATA%\selfheal-n8n\`, outside the repository. The backend's `.env` is ignored by git. The repository contains no token strings; this is checked by `tools/tests/test_workflow_structure.py` for the workflow JSON, and by a search of the whole tree before each push (see below). Credentials are imported into a local n8n as a plaintext file that the import script deletes immediately afterwards (tools/golive/n8n_import.py).

### The webhooks

Both n8n webhooks require the shared secret as a header and n8n rejects a request without it before the workflow starts: a wrong secret gave HTTP 403 in a test against the cloud instance, and the right one started the workflow. The second webhook, which stores the model address, accepts only an address that matches `https://<letters, digits, hyphens>.trycloudflare.com` and keeps the old address otherwise (tools/nodes/store_model_url.js, tested in tools/tests/test_model_url.py with look-alike hosts such as `https://abc.trycloudflare.com.evil.com`). A stolen secret could therefore still point the workflow at an attacker's tunnel, which is one reason to keep the secret long and to rotate it.

`ci-heal-backend` verifies GitHub's HMAC-SHA256 signature on the raw body with a constant-time comparison, applies a repository allowlist that denies by default, protects its operator API with a token outside development, and now ignores runs from fork pull requests and refuses to start outside `dev` with an empty or placeholder secret (docs/results/BACKEND_AUDIT.md).

### The log redirect

The job-log endpoint of the GitHub API answers with a redirect to a signed address on another host. n8n re-sends the credential's authorization header when it follows a redirect, which would hand the GitHub token to the storage host. The workflow therefore does not follow the redirect: one node reads the address from the 302 answer, and a second node without any credential downloads it. The real-engine rig checks this: the token reaches the API host and does not reach the blob host (tools/n8n_rig/drive_full.py, eval/runs/testall/n8n_rig.txt).

### Treating the model as untrusted

The prompt contains text written by whoever caused the failure: log lines, file contents. The design assumes the model may be steered by that text or may simply be wrong, so every step after the reply is a gate.

- Only failures marked `failure_kind=code` reach the model, and a payload with any other kind is refused inside the workflow as well (tools/nodes/build_ai_prompt.js).
- The reply must be a JSON object with SEARCH/REPLACE edits. Each SEARCH text must occur in the file exactly once, and the result must parse as JavaScript (tools/nodes/extract_ai_response.js).
- The file the edit names must be one of the files the model was shown.
- A branch named `main` or `master` is replaced by an `ai-fix-` name, and the fix is a pull request, never a push to the main branch.
- CI runs on the pull request, and a person merges it.
- The backend adds secret redaction before any text reaches a model, caps on edit size, a check against weakened tests, and an `ai-fix-` prefix rule enforced in the GitHub client (ci-heal-backend/app/services/github_client.py, `_refuse`).

### Known weaknesses

- **The endpoint key is public.** It is a constant in a file in the repository. Anyone who reads the repository and learns the tunnel address can use the GPU session. The address changes on every boot and is not published, which limits the exposure but is not authentication.
- **The shared secret in use is short.** It was chosen for convenience and should be replaced by a long random value before the repository or its secrets are shared.
- **A misled fix is still a candidate.** A log line that argues for a particular edit can influence the model. The gates above stop a malformed or unrelated edit, not a plausible wrong one that happens to pass the tests. The review of the pull request is the control.
- **Pull-request CI runs the proposed code.** The fix pull request is built by the repository's own workflow, with whatever that workflow can access. The audit of the backend noted that only workflow files, `.git` and `.env` files are blocked from editing, so a build entry point such as `package.json` can be changed by a fix.
- **Seven workflow nodes have no error setting**, so a failure there ends the run silently (Chapter 11). Seventeen backend findings are open (docs/results/BACKEND_AUDIT.md).
- **The backend reads a webhook body in full** before it checks the size limit and the signature (finding 11 in docs/results/BACKEND_AUDIT.md).

### Checks before publishing

Before each push the tree is searched for the token prefixes `ghp_` and `github_pat_`, for `hf_`, for the webhook secret value, and for `.env` and `secrets.json` files. A match in a test that deliberately contains a fake token is accepted after reading it.

## Running the system

The launcher folder holds double-click scripts for every step, so nobody has to remember the command lines (launcher/README.md). This chapter lists what runs where, the order to start things in, and what each step costs.

### What runs where

| part | runs on | started by | stopped by |
|---|---|---|---|
| Qwen3.8-27B endpoint | Kaggle, 2 x T4 | `launcher/start/1_start_model_on_kaggle.bat` | Stop Session in the Kaggle page (the Kaggle command line cannot stop a session) |
| n8n workflow, cloud copy | n8n Cloud trial, always on | already published | unpublish in the editor |
| n8n workflow, local copy | this PC, `localhost:5678` | `start/start_n8n_local_only.bat`, or `2_` and `3_` for the live setup | `end/stop_n8n.bat` |
| ci-heal-backend | this PC, port 8000 | `start/4_start_backend.bat` | `end/stop_backend.bat` |
| demo repository workflow | GitHub Actions | a push to `main` that changes `demo-app/` | not applicable |

### The live demo in order

1. `1_start_model_on_kaggle.bat`. It prints the status of the project's kernels, builds the endpoint notebook from `kaggle/endpoint/deploy_src.py`, pushes it, follows the boot log for about four minutes, saves the tunnel address to `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt` and posts it to the cloud workflow's Set Model URL webhook.
2. Push a commit that breaks a test in `demo-app/`. The Actions run fails, the notify job posts to the n8n webhook, and a pull request on an `ai-fix-...` branch appears about a minute later.
3. Stop the Kaggle session when finished, either with `end/stop_model_on_kaggle.bat` (it opens the notebook page and then checks that the session stopped) or directly in the page: View Active Events, the three-dot menu on the session, Stop Session.

For the local copy of n8n instead of the cloud one, run `2a_load_workflow_into_n8n.bat` and `3a_start_n8n_live.bat` after step 1. The second imports the workflow and credentials with the current model address, and the third starts n8n with a public Cloudflare tunnel and points the repository's `N8N_WEBHOOK_URL` secret at it. If the local n8n does not start, `2b_cloud_n8n_connect_model.bat` replaces both: it tells the web n8n where the model is and opens its workflow, which is already loaded and published there (the local import once failed because n8n 1.123 cannot activate the OpenAI node version used by the cloud copy, and the import now substitutes an HTTP call). The break step of the demo can be done with `5_break_demo_app_and_push.bat`, which changes the sum in `demo-app/src/format.js`, commits and pushes to the test repository.

### Cost and limits

The Kaggle session uses GPU quota for as long as it runs. The kernel stops itself after 1.5 hours (kaggle/endpoint/deploy_src.py, `HOURS`), and `push.py` refuses to start a second session while one is running or queued. A boot took about four minutes in the logged runs, with the model download taking 76 to 91 seconds (kaggle/logs/endpoint/). A pushed version that is stopped before it finishes does not become the notebook's default view on the Kaggle page; pinning it as the default version in the version history does (observed on the Kaggle page, 2026-11-15).

The n8n Cloud instance is a 14-day trial. After it ends the same workflow can be imported into a local n8n 1.x, which is what the launcher's local scripts do.

### Troubleshooting

| symptom | cause found in practice | action |
|---|---|---|
| The workflow's model call fails right after a Kaggle restart | The tunnel address changed and the workflow still holds the old one | run `launcher\start\show_model_info.bat`; run `python tools\golive\set_cloud_qwen_url.py` to post the new address |
| Kaggle status stays `QUEUED` for several minutes | the notebook is waiting for a GPU | wait; `push.py` will refuse a second push in the meantime |
| `watch_deploy.py` prints `500 Server Error` from the Kaggle log stream | the Kaggle log API failed once | it reconnects by itself; check the status with `kaggle/tools/status.py` |
| Local n8n starts but the webhook does nothing | n8n 2.x does not register the webhook | install n8n 1.x (docs/guides/SETUP.md) |
| A fix pull request appears but CI on it is red | the model's fix parsed but was wrong | the retry loop tries again with thinking, up to the attempt cap; after that a comment says a person has to look |

## Limitations and failed experiments

### What the results do not show

- **One demo application.** All heal measurements use a small Express app with one to three source files. Bugs in a large code base, in other languages or in build configuration are not covered.
- **Small case sets.** The quality studies use 11 to 17 cases with two or three repetitions each (Chapter 9). A difference of one case is within noise, and the report treats it that way.
- **First-try quality is partial.** With thinking off, the production setting for the first attempt, 12 of 18 hard single-file calls were healed. Thinking raised that to 18 of 18 at about four times the time, which is why retries use it (docs/results/SHIPPED_QUALITY_EVAL.md).
- **Multi-file healing is below its own bar.** The fast pass healed 9 of 18 against a bar of 12, and the retry loop reached 15 of 18 in replay. The model-comparison study recorded the same weakness as a "caller gap": bugs that need a change in a file other than the one the stack trace names (docs/results/MULTIFILE_EVAL.md, MODEL_BAKEOFF.md).
- **A passing fix can still be wrong.** The gates check that the edit applies, parses and passes the repository's tests. The recorded run of Chapter 10 produced such a fix: it removed the error and dropped a string conversion.
- **The OpenAI fallback has never produced a fix.** Its routing is tested, but on the n8n Cloud trial the free credits answered `Forbidden` to a webhook-triggered run, and the cause was not established.

### Operational limits

- **The model address changes on every Kaggle boot.** The Set Model URL webhook removes the manual step but not the dependency: a stopped session means a dead address, and the workflow then reaches its failure comment or the fallback.
- **GPU availability.** A session can wait in the Kaggle queue without limit; one attempt for this report waited about 90 minutes without a GPU (Chapter 10).
- **A cap of 1.5 hours per session** in our notebook, set to bound quota use, and no way to stop a session from the command line.
- **The n8n Cloud instance is a 14-day trial** whose REST API is disabled.
- **The attempt cap lives in the notify job**, not in n8n. As far as the workflow's nodes show, a call to the webhook made by hand with a high `attempt` value is not stopped inside n8n.
- **Failures that are not test failures are not healed.** A failed `npm install` or a build step that fails after the tests passed does not set `failure_kind=code` and is ignored (Chapter 5).
- **Seven workflow nodes have no error setting**, and 17 backend audit findings are open (Chapters 11 and 8).
- **One healer per repository.** Running the n8n workflow and the backend on the same repository would open competing pull requests.

### Experiments that did not work

The failures below were useful: each changed a decision.

| experiment | what happened | consequence |
|---|---|---|
| A quantization tuned for the model's DeltaNet layers (Ridge) | 5 of 18 against 9 of 18 for the generic Q4 build (docs/results/SPEED_QUALITY_EVAL.md) | the generic unsloth UD-Q4_K_XL file stayed |
| A ternary-weight model (Bonsai) | tied the Q4 build on quality at about a third of the size, but could not use tensor split or MTP | not used |
| The claim that 30 tok/s was unreachable on two T4s | refuted by measurement with tensor split and MTP (22 to 27 tok/s) | the analysis moved to docs/old/ |
| Non-streamed thinking retry | died at 125.9 s on the tunnel's 120 s limit (HTTP 524) | model calls are streamed |
| Following the log redirect with the n8n HTTP node | the GitHub token would have been sent to the storage host | the redirect is handled by two nodes (Chapter 12) |
| Judging a feature by a log line | wording changes in logs twice made a disabled feature look enabled | features are judged by effect, as in the thinking self-check |
| Asking the model what it is | the answers about its own identity were unreliable | the endpoint's `/v1/models` is the reference |
| n8n 2.x | the webhook was never registered | n8n 1.x only |
| Reading `$vars` in a self-hosted n8n | variables are a licensed feature and did not resolve | the local import substitutes the literal address |

Sources for the rows: docs/research/QWEN38_IDENTITY_AND_RIDGE.md, docs/results/SPEED_QUALITY_EVAL.md, docs/results/SPEED_LEVERS_EVAL.md, docs/results/GOLIVE_RESULTS.md, tools/golive/n8n_import.py, and tools/n8n_rig/README.md.

### Inconsistencies found in the older documentation

The review of the existing documents for this report found, and the rewritten documents fix, statements that no longer matched the code: the old setup guide named a different demo layout and a 20-node workflow; two documents said the backend turns thinking on for classifier re-asks when no code does; the speed analysis in `docs/old/` was refuted by the later speed study; and one endpoint note counted a crashed kernel version and its successor as two sessions. The raw data of several early experiments (the first sweep, the first end-to-end baseline) was not kept, so those numbers can be quoted but not recomputed.

## Future work

The items below follow from the limits in Chapter 14, in the order we would do them.

1. **Close the open backend findings** that can lose a delivery or corrupt state: the SQLite lock held across awaits (move intake off the event loop, or use Postgres), the stuck `processing` rows, the rollback that loses a pull-request record and the unbounded rate-limit sleeps (docs/results/BACKEND_AUDIT.md).
2. **Give every workflow node an error path.** The seven nodes without one should continue as data into the existing failure comment, so that a GitHub refusal produces a message and not a silent red run. Add the attempt cap inside n8n as well, so that it does not depend on the notify job alone.
3. **A real fallback model.** Bind the OpenAI node to a credential that works for webhook-triggered runs, or replace it with a second self-hosted endpoint, and then record a heal that the fallback actually produced. Until then the fallback is wiring, not capability.
4. **Make the endpoint key random per boot.** The notebook could generate a key at start and post it to the Set Model URL webhook together with the address, so that no secret is committed and a stale address is useless.
5. **Multi-file bugs.** The caller gap needs a retrieval step that is more than the stack trace and the changed files: for example the files that import or call the changed function. The evaluation harness already has the cases for it (eval/cases/multifile_cases.json).
6. **Judge the fix, not only the tests.** A second model call or a simple check could compare the proposed edit with the deleted or changed lines in the breaking commit and flag a fix that removes behaviour the commit did not touch, as in the recorded run.
7. **More than one repository and language.** Run the same measurements on a second demo application and on a Python project, to see which of the numbers in Chapter 9 depend on the demo.
8. **Cover build and install failures.** Extend the notify job's classification beyond failing tests, with the same refusal for infrastructure problems.
9. **Merge the work into the main branch** of the repository once the open items above are decided. The branches `harsh/kaggle-backend-integration` and `harsh/launcher-and-demo-app` hold everything described in this report.

## Repository map and reproduction

### Layout

| path | contents |
|---|---|
| `README.md` | overview, layout and the shortest way to start |
| `launcher/` | double-click scripts: `start/` brings parts up, `end/` stops them (launcher/README.md) |
| `ci-heal-backend/` | the FastAPI healer, with its own tests, `.env.example`, Dockerfile and `docs/LLM_CONTRACT.md` |
| `kaggle/endpoint/` | the endpoint kernel source (`deploy_src.py`), the model registry (`models.json`) and the generated push folder |
| `kaggle/tools/` | status, push, build, watch and log tools for the Kaggle kernels |
| `kaggle/evals/` | the generated kernels of the three evaluation studies that ran on Kaggle |
| `kaggle/notebooks/` | a snapshot of the notebook as it is on Kaggle |
| `kaggle/logs/` | boot and run logs of every kernel version, by date |
| `n8n/` | `self_healing_pipeline.json`, the workflow (generated) |
| `tools/` | the workflow generator, the node bodies in `nodes/`, the node test suites in `tests/`, the real-engine rig in `n8n_rig/`, the live-run and setup scripts in `golive/`, the demo video builder in `media/` |
| `eval/` | bug cases, eval drivers, scorers and the stored results in `runs/` |
| `demo-app/` | the Express app that CI runs on, with `.github/workflows/demo-ci.yml` at the repository root |
| `docs/` | everything below |
| `run_tests.py` | one command for all local checks |

### Documentation

| path | contents |
|---|---|
| `docs/PROJECT_REPORT.md` | this report, assembled from `docs/report/chapters/` (the PDF is built locally with `docs/report/build.py` and is not tracked) |
| `docs/report/` | chapter sources, the Markdown to LaTeX converter, the build script and the LaTeX output |
| `docs/architecture/` | the n8n workflow node by node, and the model endpoint |
| `docs/guides/` | setup, cloud n8n, launcher, testing |
| `docs/backend/` | the seven-chapter walkthrough of `ci-heal-backend` |
| `docs/results/` | one write-up per measurement and the backend audit |
| `docs/research/` | notes on the Kaggle environment and the model |
| `docs/media/` | screenshots and the demo video |
| `docs/old/` | superseded documents, with the reason for each |

### Reproducing the checks

From the repository root, in PowerShell:

```text
python run_tests.py                 node suites, eval scorers, generator check (13 checks with --rig)
python run_tests.py --rig           adds the real-n8n engine rig
cd ci-heal-backend ; pytest ; ruff check .
python docs/report/build.py         rebuilds this report and its PDF
python tools/media/build_demo_video.py docs/media/demo_spec.json
```

The eval scripts and some tests build trees in `app/`, a separate clone of the demo repository that is not part of this repository (it is listed in `.gitignore`), and need `express` installed there. Starting the model, the workflow and the backend is described in Chapter 13.

### Evidence index

Every number in the report carries a source note. The files that hold the raw evidence are:

| evidence | location |
|---|---|
| test outputs of the last full run | `eval/runs/testall/` |
| measured model data of each study | `eval/runs/` (`kout_*`, `golive*`) |
| Kaggle boot logs and speed numbers | `kaggle/logs/endpoint/` |
| live-run screenshots | `docs/media/shots/` |
| backend audit and its fixes | `docs/results/BACKEND_AUDIT.md`, commit `4bfd37d` |
