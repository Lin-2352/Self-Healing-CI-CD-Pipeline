# The n8n workflow, node by node

This page describes the workflow in `n8n/self_healing_pipeline.json`, named "Self-Healing CI/CD Pipeline (Qwen3.8)". The JSON has 26 nodes. It is not edited by hand: `tools/gen_n8n.py` writes it from node specs, and the bodies of the Code nodes live in `tools/nodes/*.js` (tools/gen_n8n.py). Running `python tools/gen_n8n.py --check` regenerates the file in memory and compares it byte for byte with the shipped copy. The generator also refuses to write if any Code body fails to parse (`check_code_nodes_parse` in tools/gen_n8n.py).

Everything below comes from reading those files and the tests in `tools/tests/`. Where I could not tell why something was done, I say so.

## Wiring

```text
GitHub Failure Webhook
  -> Fetch Job Logs -> Fetch Failed Job Log -> Download Job Log
  -> Fetch Changed Files -> Pick Candidate Files -> Fetch File Content
  -> Build AI Prompt -> Get Model URL -> Qwen3.8 Analyze -> Model OK?
                                                    |yes            |no
                                                    |               OpenAI Fallback
                                                    |               -> Normalize OpenAI Reply
                                                    v               |
                                          Assemble Model Reply <-----+
  -> Extract AI Response -> Fix Ready?
                              |yes                        |no
                              v                           v
                        Create Branch              Explain Failure
                        -> Branch Ready            -> Post Failure Comment
                        -> Get File SHA
                        -> Push Fix File
                        -> Open Pull Request
                        -> Heal Outcome

Set Model URL -> Store Model URL        (separate entry point, not part of the chain above)
```

The main chain is a straight line with two forks, both written in `tools/gen_n8n.py` (`ORDER`, `FORK`, `FALLBACK`, `FAIL`, `URL_HOOK`). The saved connections in the JSON match this picture, and `tools/tests/test_workflow_structure.py` checks the model path and the second webhook.

Two settings matter for reading the failure behaviour below. A node with `onError: continueRegularOutput` does not stop the run when it fails: it passes an item with an `error` key to the next node. A node without that setting stops the execution on failure, and nothing later runs.

## Entry points

### GitHub Failure Webhook

Type `n8n-nodes-base.webhook` v2. POST on path `github-failure`, header authentication with the credential "N8N Webhook Secret", response mode `onReceived` (n8n answers GitHub at once and keeps working). The demo workflow posts here with a `X-Webhook-Secret` header (.github/workflows/demo-ci.yml). Which header name the credential expects is stored in n8n, not in this repo, so I could not confirm that the two match.

The body it receives has these fields: `run_id`, `repo`, `branch`, `commit`, `actor`, `failure_kind`, `attempt`, `pr_number` (.github/workflows/demo-ci.yml). Later nodes read them as `$('GitHub Failure Webhook').first().json.body`. Failure behaviour: a wrong or missing secret is rejected by n8n before the workflow starts.

### Set Model URL

Type `n8n-nodes-base.webhook` v2. POST on path `set-qwen-url`, same "N8N Webhook Secret" credential, response mode `lastNode`, so the caller gets the output of Store Model URL as the HTTP reply. It has its own `webhookId`, different from the failure webhook (tools/gen_n8n.py, `NEW_NODES`; tools/tests/test_workflow_structure.py). The launcher posts here after each Kaggle boot (comment in tools/gen_n8n.py). It feeds Store Model URL.

## Fetching the evidence

### Fetch Job Logs

HTTP Request v4.2, GET `/repos/{repo}/actions/runs/{run_id}/jobs` with the "GitHub PAT" credential (bearer). It returns job metadata: names, conclusions and the steps of each job. It has no `onError` setting, so if GitHub refuses it the run stops here with nothing posted anywhere. Build AI Prompt reads it for the failed job name and the failed steps.

### Fetch Failed Job Log

HTTP Request v4.2, GET `/repos/{repo}/actions/jobs/{id}/logs`, where `{id}` is the first job in the previous node's list with `conclusion === 'failure'`. Uses the "GitHub PAT" credential. `onError: continueRegularOutput`.

Two options are set on purpose: `followRedirects: false`, and `fullResponse` with `neverError`, with the body as text. GitHub answers this URL with a 302 to a signed address on a different host. The comment in tools/gen_n8n.py says the verify rig showed that n8n re-sends the credential's Authorization header on that second hop, which would give the GitHub token to the storage host. So the node stops at the 302 and returns the status, headers and body. `neverError` means a 404 or similar becomes data rather than a failure.

### Download Job Log

HTTP Request v4.2, GET on `($json.headers || {}).location || ''`, so it fetches the signed address the previous node returned. No credential is attached, because the address is pre-signed (comment in tools/gen_n8n.py). The response is read as text, so the log ends up in `json.data`. `onError: continueRegularOutput`. An expired or missing log therefore does not block a heal: Build AI Prompt turns it into `(log unavailable)` (tools/nodes/build_ai_prompt.js).

### Fetch Changed Files

HTTP Request v4.2, GET `/repos/{repo}/commits/{commit}` with the "GitHub PAT" credential. The response has a `files` array. No `onError` setting, so a failure stops the run. Pick Candidate Files and Build AI Prompt both read it.

### Pick Candidate Files

Code node (tools/nodes/pick_candidate_files.js). It chooses which files the model will be shown, and so which file it can fix.

- It gets the CI log from Download Job Log (`json.data`). If that is empty, it falls back to the body of Fetch Failed Job Log when that node returned status 200.
- It scans the log for paths under the runner checkout, `/home/runner/work/<repo>/<repo>/<path>`. Only files with a code extension count (js, jsx, ts, tsx, mjs, cjs, py, rb, go, java, rs, php, cs), and anything in `node_modules` is dropped.
- It reads the changed code files from Fetch Changed Files, skipping removed files.
- Order: the first two stack-trace files, then the changed files, then any remaining trace files. Duplicates are removed and the list is cut to 3 (`MAX = 3`).
- It emits one item per file, with `path` and a `source` of `trace`, `commit` or `trace+commit`. If nothing qualifies it emits one placeholder item with `path: '__no_code_file__'`, so the run still reaches Explain Failure. The comment explains that n8n ends a run quietly when a node outputs no items.

tools/tests/test_multifile.py covers a bug that sits in a file the commit never touched (found through the trace), two changed files with the bug in the second, the cap of 3, and the placeholder.

### Fetch File Content

HTTP Request v4.2, GET `/repos/{repo}/contents/{path}?ref={commit}`, run once per item from Pick Candidate Files, with the "GitHub PAT" credential. `onError: continueRegularOutput`. It uses the JSON contents API, so every item carries its own `path` and a base64 `content`. The comment in tools/gen_n8n.py says this is so later nodes never need to look at a Code node's output to know which file is which. A file that is not in the repo (404) continues as an error item and is skipped by Build AI Prompt and Extract AI Response.

Note on the retry loop: `ref` is the `commit` from the webhook. On a retry the demo workflow sends the heal PR's head commit, so the file the model sees already holds the previous fix (.github/workflows/demo-ci.yml; tools/nodes/build_ai_prompt.js).

A stale comment: tools/nodes/extract_ai_response.js says Fetch File Content uses `Accept: application/vnd.github.raw`. The node as shipped has no such header and the code reads base64 `content`. I treated the node spec as correct.

## Building and sending the prompt

### Build AI Prompt

Code node (tools/nodes/build_ai_prompt.js). It reads the webhook body, Fetch Job Logs, Fetch Changed Files, the log nodes and every item of Fetch File Content, and returns one item with the whole model request.

**System prompt.** A fixed string. It asks for one raw JSON object with `branch_name`, `file_path`, `edits`, `pr_title` and `pr_body`, with no markdown. `edits` must be SEARCH/REPLACE blocks, whose SEARCH text has to appear exactly once and match character for character. The model is told not to output the whole file, never to use main or master as the branch, and to keep `pr_body` to two short sentences. tools/tests/test_build_prompt.py checks that the system message equals this constant.

**The failure_kind gate.** If the webhook's `failure_kind` is set and is not `code`, the node throws `Refusing to run: ...`. There is no `onError` on this node, so the run ends there with no model call and no PR. The comment says the webhook is a public URL and the workflow gates on `code` already, so this is a second check. An empty `failure_kind` passes (the prompt then says `code (assumed)`). The demo workflow only calls the webhook for `code` failures (.github/workflows/demo-ci.yml). Tested by the "infra refused" case in tools/tests/test_build_prompt.py.

**Attempt and retry.** `attempt` is `hook.attempt` as a non-negative integer. `isRetry` is true only when `attempt > 0` and the branch starts with `ai-fix-`. A non-heal branch with an attempt number is not treated as a retry (tools/tests/test_build_prompt.py). On a retry the prompt gets an extra line: "Heal attempt N+1", saying the file already contains the previous automated fix and CI still fails with the log shown.

**Thinking only on retries.** `chat_template_kwargs: { enable_thinking: isRetry }`. The code comment cites SHIPPED_QUALITY_EVAL.md: with thinking off the first pass healed 15/15 control cases and 12/18 hard cases in about 9 s, and with it on 18/18 hard cases at about 37 s median. I did not open that file, so those figures are the comment's, not mine. The comment adds that the server caps thinking with `--reasoning-budget 1024` (tools/deploy_src.py, which I also did not read).

**CI log trimming.** `trimCiLog` works on the log text:

1. Remove the runner timestamp prefix, ANSI colour codes, and the `/home/runner/work/<repo>/<repo>/` prefix.
2. Find the first line starting with `##[error]`. With none, return `(log unavailable)`.
3. Start after the closest `##[endgroup]` above it (so the step's own script echo is left out), end at the error line, strip the `##[error]` marker, and drop other `##[` control lines.
4. Keep the last 40 lines (`LOG_MAX_LINES`) and at most the last 3000 characters (`LOG_MAX_CHARS`).

If the log is not a string or is empty, the result is `(log unavailable)`. Tests for all of this are in tools/tests/test_build_prompt.py.

**Request body.** Model name `local`, `temperature` 0.1, `max_tokens` 4096, `stream: true`, `cache_prompt: true` (reuse the cached system-prompt prefix), plus the thinking flag above. The prompt lists repository, branch, commit, actor, failure kind, failed job, failed steps, the trimmed log, and the source files. With one file the header is `--- changed source files ---`. With several it says to fix exactly one and set `file_path` to it.

**Output.** `prompt`, `request_body`, `ci_log`, `attempt`, `is_retry`, `repo`, `commit`, `branch`, a default `file_path` (first fetched file) and `candidate_files`. Only primitives are passed on, because later nodes read them through `$node[...]` (comments in tools/nodes/extract_ai_response.js).

### Get Model URL

Code node (tools/nodes/get_model_url.js). It builds `model_base_url` from the workflow's stored address (`qwenUrl` in global static data), and if there is none from the n8n variable `QWEN_BASE_URL`. Trailing slashes are removed. With neither it throws `No model URL: run the launcher or set the QWEN_BASE_URL variable`. No `onError`, so that stops the run. The rest of the input item is copied through. tools/tests/test_workflow_structure.py checks that `QWEN_BASE_URL` appears in no other node.

### Qwen3.8 Analyze

HTTP Request v4.2, POST `{model_base_url}/v1/chat/completions`, body `JSON.stringify(request_body)`, bearer credential "Qwen3.8 Endpoint Key". Timeout 240000 ms (240 s). The response is read as text, so a streamed reply arrives as one string in `json.data`. `onError: continueRegularOutput`.

The code comments give two reasons. Streaming: the endpoint sits behind a Cloudflare quick tunnel that cuts a request after 120 s of silence, and a thinking retry once died at 125.9 s that way (tools/nodes/assemble_model_reply.js). The 240 s limit: a stuck endpoint should reach the OpenAI fallback while the run is still useful (tools/gen_n8n.py). tools/tests/test_workflow_structure.py asserts the timeout and the continue-on-error setting.

### Model OK?

IF node v2. True output goes to Assemble Model Reply, false output to OpenAI Fallback. The condition is true when there is no `error`, `data` is a string, and that string contains `[DONE]` or `"finish_reason":"`. So a connection error, a timeout or a stream that was cut before its end goes to the fallback.

### OpenAI Fallback

Type `@n8n/n8n-nodes-langchain.openAi` v2.3, model `gpt-4.1`, `onError: continueRegularOutput`. It sends the same two messages as the Qwen request, read from `Build AI Prompt` (system, then user). No credential is bound in the repo copy, on purpose: each n8n instance attaches its own (tools/gen_n8n.py; tools/tests/test_workflow_structure.py checks that `credentials` is absent). With no credential the node fails, continues as data, and Normalize OpenAI Reply turns that into a readable error.

### Normalize OpenAI Reply

Code node (tools/nodes/normalize_openai_reply.js), `onError: continueRegularOutput`. If the OpenAI item has an `error`, it throws one message that names both failures: `Qwen failed (<reason>) and the OpenAI fallback failed too: <reason>`. The Qwen reason comes from the output of Qwen3.8 Analyze, or the text "its reply was incomplete" if that node had no error. Otherwise it looks for text in `output`, `message`, `content`, `text` or `choices[0]`, and returns `{ choices: [{ message: { content }, finish_reason: 'stop' }], provider: 'openai-fallback' }`. Empty text throws `OpenAI fallback returned no text`. Its output goes to Assemble Model Reply, which passes a finished completion object through unchanged.

### Assemble Model Reply

Code node (tools/nodes/assemble_model_reply.js), `onError: continueRegularOutput`. It rebuilds the streamed reply into the ordinary completion shape: `choices[0].message.content` and `reasoning_content`, `finish_reason`, `usage`, `timings`, `streamed: true` and the chunk count.

- An incoming `error` is rethrown as `Model call failed: ...` with up to 200 characters of the description.
- An item that already has a `choices` array (the OpenAI path, or a non-streamed server) is passed through.
- Otherwise it reads the `data:` lines of the server-sent-event text, stops at `[DONE]`, skips lines that are not JSON, and joins every `delta.content` and `delta.reasoning_content`. A stream with an `error` event throws `Model server error mid-stream`.
- No `[DONE]` and no `finish_reason` means the stream was cut: it throws `Model stream ended early after N chunks`. An empty body and a stream with no data events also throw.

tools/tests/test_assemble_reply.py covers this node.

## Turning the reply into a patch

### Extract AI Response

Code node (tools/nodes/extract_ai_response.js), `onError: continueRegularOutput`. In order:

1. An upstream `error` is rethrown with its original message, so the failure comment can quote the real cause.
2. It takes `choices[0].message.content`, removes `<think>` blocks (also an unclosed one) and stray code fences, parses JSON, and if that fails extracts the first `{...}` span. No `edits` field throws.
3. It rebuilds the files shown from Fetch File Content. The edits apply to the file named in `file_path` (a leading `./` is tolerated), and only if that file was one of those shown. Otherwise it throws `file_path "..." is not one of the files shown to the model`. With no `file_path` it uses the first shown file (tools/tests/test_multifile.py).
4. It reads the blocks with the regex for `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` and applies them one at a time. Each SEARCH must be non-empty, must be found, and must be found exactly once in the file as patched so far. Not found, matching more than once, or no blocks at all each throw. If the result equals the original it throws `edits applied but the file is unchanged`.
5. Syntax gate: `new Function(patched)` parses the patched text without running it. A parse error throws `patched file does not parse ...; refusing to open a PR`. The comment states the limit plainly: this gate only sees parse errors, and a fix that parses but is wrong still goes to a PR, where CI catches it. It uses a JavaScript parser regardless of file extension, so I could not confirm what happens for a `.py` candidate; the demo app is Node.
6. Branch name: if the model gave none, or main or master, it uses `ai-fix-<timestamp>`. It then appends the first 7 characters of the commit unless already there. On a retry the name is replaced with the branch from the webhook, so the fix lands on the open PR's branch.
7. Commit message: the PR title, a blank line, and `Heal-Attempt: <attempt + 1>`.

Output (primitives only): `branch_name`, `file_path`, `fixed_code`, `pr_title`, `commit_message`, `attempt`, `pr_body`, `base64_content`, `edits_applied`, `repo`, `commit` and `branch` (the original branch, used as the PR base). tools/tests/test_extract_node.py exercises the reject cases (absent SEARCH, prose, empty SEARCH, non-parsing result, no-op edit, missing edits, duplicate match).

The comment cites DIFF_FORMAT_EVAL.md for 171 median tokens against 655 for whole-file output, and 15/15 byte-exact (source: docs/results/DIFF_FORMAT_EVAL.md).

### Fix Ready?

IF node v2. True when `!$json.error && !!$json.base64_content`. True goes to Create Branch. False, which includes every upstream failure, goes to Explain Failure. This is the one place where a model-side failure is split from the happy path.

## Opening the pull request

### Create Branch

HTTP Request v4.2, POST `/repos/{repo}/git/refs` with `ref: refs/heads/<branch_name>` and `sha: <commit>`, "GitHub PAT" credential. `onError: continueRegularOutput`, so GitHub's 422 "Reference already exists" becomes data. That is what makes a re-run of the same commit, and a retry on an existing heal branch, possible.

### Branch Ready

Code node (tools/nodes/branch_ready.js). No error from Create Branch gives `{branch_ok: true, existed: false}`. An error whose text matches "Reference already exists" gives `existed: true`. Any other error (the file's examples: bad token, wrong repo, rate limit) throws, so the run stops instead of reporting a success it did not reach.

### Get File SHA

HTTP Request v4.2, GET the file's contents on the heal branch, reading repo, path and branch from the Extract AI Response node with `$node[...]`. The returned `sha` is needed to update an existing file. No `onError`, so a failure stops the run.

### Push Fix File

HTTP Request v4.2, PUT `/repos/{repo}/contents/{file_path}` with the commit message, `base64_content`, the `sha` from the previous node and the branch. No `onError`. This writes the heal commit, including the `Heal-Attempt` trailer.

### Open Pull Request

HTTP Request v4.2, POST `/repos/{repo}/pulls` with the PR title and body, `head` set to the heal branch and `base` set to `branch` from the webhook. `onError: continueRegularOutput`. On a retry the head and the base are the same branch, so GitHub answers 422 "No commits between X and X"; Heal Outcome treats that as normal.

### Heal Outcome

Code node (tools/nodes/heal_outcome.js). It reports the result:

- `pr_opened`, with `pr_url` and `pr_number`, when the response has `html_url`.
- `retry_pushed`, when the attempt is above 1 and the error says "No commits between" or "pull request already exists". The comment says all three retries of a live round were pushed correctly yet marked failed before this case existed.
- `pr_already_open`, when the same commit was healed again and the PR exists.
- Anything else throws `Open Pull Request failed: ...`. For example, "No commits between" on attempt 1 is a real failure, and so is a 401.

tools/tests/test_heal_outcome.py has one case for each of these.

## The failure branch

### Explain Failure

Code node (tools/nodes/explain_failure.js). Reached when Fix Ready? is false. It reads only the webhook payload (its comment: reading another Code node's output is unreliable in n8n's sandbox) plus the incoming error item. It takes the error text, removes the ` [line N]` suffix n8n appends, and cuts it to 600 characters. `attempt` is the webhook's attempt plus one. If `pr_number` is all digits, the target is `pr` (a retry), otherwise `commit`. It builds a Markdown comment starting with "Self-healing pipeline: no fix produced", quoting the reason. The PR variant says that no further automatic attempt will start from this run. The commit variant says no heal PR was opened.

### Post Failure Comment

HTTP Request v4.2, POST with the body `{ body }`. The URL is `/repos/{repo}/issues/{pr_number}/comments` for a PR target and `/repos/{repo}/commits/{commit}/comments` otherwise. "GitHub PAT" credential. No `onError`, so if this call fails nothing else is reported. The comment on Explain Failure says that before this branch existed a failed heal left no trace.

## Storing the model address

### Store Model URL

Code node (tools/nodes/store_model_url.js). It reads `body.url` from the Set Model URL webhook, removes trailing slashes, and accepts only an address matching `^https://[a-z0-9-]+\.trycloudflare\.com$`; anything else throws. It saves `qwenUrl` and `qwenUrlSetAt` in the workflow's global static data and returns `{ok, url, stored_at}`, which becomes the webhook reply. Static data is only written when the workflow runs from a trigger such as a webhook, which suits this use; I have not tested how n8n persists it between executions.

## Credentials and variables

| Name in the workflow | Type | Used by |
| --- | --- | --- |
| GitHub PAT (id `github-pat`) | HTTP bearer auth | Fetch Job Logs, Fetch Failed Job Log, Fetch Changed Files, Fetch File Content, Create Branch, Get File SHA, Push Fix File, Open Pull Request, Post Failure Comment |
| N8N Webhook Secret (id `n8n-webhook-secret`) | HTTP header auth | GitHub Failure Webhook, Set Model URL |
| Qwen3.8 Endpoint Key (id `qwen-endpoint`) | HTTP bearer auth | Qwen3.8 Analyze |
| OpenAI credential | none bound in the repo | OpenAI Fallback (each n8n attaches its own) |

Download Job Log uses no credential on purpose. The names and ids come from tools/gen_n8n.py and the JSON. The workflow also reads the optional n8n variable `QWEN_BASE_URL` (Get Model URL). On the GitHub side, the demo workflow needs the repository secrets `N8N_WEBHOOK_URL` and `N8N_WEBHOOK_SECRET` (.github/workflows/demo-ci.yml). The JSON holds no secrets; test_workflow_structure.py searches it for token prefixes.

## The retry loop

The loop works through GitHub Actions, not through anything inside n8n.

1. A push to `main` under `demo-app/` fails with a code failure (exit code other than 0 and 75). The notify job in `.github/workflows/demo-ci.yml` posts to the webhook with `attempt: 0`.
2. The workflow opens a PR from an `ai-fix-*` branch into `main`. The demo workflow also runs on pull requests to `main`, so the heal PR gets its own CI run.
3. If that CI fails with a code failure, the notify job runs again, but only for `push` events or branches starting with `ai-fix-`. Human PRs never wake the healer.
4. The job reads the head commit's message and extracts the `Heal-Attempt: N` trailer. No trailer means a human pushed to the branch, so it does nothing. `N >= MAX_ATTEMPTS` (set to 3) posts a "gave up" comment on the PR. Otherwise it posts to the webhook with `attempt: N`.
5. In n8n, Build AI Prompt sees `attempt > 0` on an `ai-fix-` branch, turns thinking on, and tells the model the file already contains its previous fix. Extract AI Response reuses the same branch and writes `Heal-Attempt: N+1`.

The counter lives in the commit message, so re-running the workflow cannot reset it (comment in .github/workflows/demo-ci.yml). The cap is enforced only in the notify job, not in n8n: the workflow itself does not check it. With the cap of 3, the first heal plus two retries make three attempts.

Comments in the node files refer to `main.yml` for this logic. That is the name of the same workflow in the separate demo repository (the recorded run in Chapter 10 used it); in this repository it is `.github/workflows/demo-ci.yml`.

## Known limits

- The OpenAI fallback has never produced a fix on n8n Cloud's free credits. In our test it answered Forbidden for webhook-triggered runs. The workflow comment says the credits "did not authorise webhook-triggered runs in our test, see docs" (tools/gen_n8n.py). I found no document in `docs/` that records the Forbidden message, so that wording comes from the test, not from a file I could check. Treat the fallback as untested until an own OpenAI key is attached.
- The model address changes on every Kaggle boot. It is a `trycloudflare.com` quick-tunnel address, so someone or something must post the new one to Set Model URL (the launcher does) or the `QWEN_BASE_URL` variable must be updated. A stale address makes the Qwen call fail, which goes to the fallback, and that goes to the failure comment (see above).
- Several nodes have no `onError` setting: Fetch Job Logs, Fetch Changed Files, Build AI Prompt, Get Model URL, Get File SHA, Push Fix File and Post Failure Comment. A failure in any of them ends the run with no comment on GitHub, only an n8n execution error.
- The syntax gate is a JavaScript parse. A fix that parses and is wrong reaches a PR and depends on CI to reject it (tools/nodes/extract_ai_response.js).
- Only one file is changed per heal, and at most 3 files are shown to the model.
