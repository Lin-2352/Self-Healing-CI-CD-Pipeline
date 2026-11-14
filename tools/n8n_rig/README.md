# n8n verify rig

`tools/heal_harness.py` runs each Code node's body under plain Node. That checks the logic but not how n8n's
sandbox behaves (isolated-vm, the `$()` accessor, `new Function`), so this rig runs the workflow inside a
real n8n engine against a local mock of GitHub and the model. Nothing is written to GitHub and no GPU is used.

The quickest way to run it is from the project root in PowerShell:

```powershell
python run_tests.py --rig
```

That runs every node suite, the generator check and this rig, and writes the outputs to `eval/runs/testall/`.
The rest of this page is for running the pieces by hand.

## Setup

1. Install n8n 1.x from PowerShell: `npm install n8n@^1` in `%LOCALAPPDATA%\selfheal-n8n\app` (about 6 minutes;
   1.123.81 was the last version used). Version 2.x never registers webhooks. Installs started from Git Bash
   fail in npm's postinstall step on this machine. The scripts also look in `%TEMP%\n8n_verify`, but Windows
   clears that folder, so use the `%LOCALAPPDATA%` path.
2. Start the mock: `python tools/n8n_rig/mock2.py 5792`. It serves the GitHub shapes the workflow reads
   (`/hook`, `/jobs`, `/files`, `/file`, `/repos/.../contents/<path>`). The job log at
   `/repos/.../actions/jobs/<id>/logs` answers with a 302 to a second host (127.0.0.2), the way GitHub
   redirects to blob storage, and the mock records `log_hit.json` and `blob_auth.json`. `POST /model` records
   the request in `last_request.json` and answers with server-sent events when `stream` is true. Everything
   reads `scenario.json`; `"log": null` makes the log endpoint return 404.
3. Build the workflow: `python tools/n8n_rig/build_wf.py` writes `wf_full.json` from the shipped
   `n8n/self_healing_pipeline.json`. Build AI Prompt, Extract AI Response, Pick Candidate Files and Download
   Job Log are copied unchanged. Fetch Failed Job Log, Fetch File Content and Qwen3.8 Analyze change only
   their host, URL or credential. `drive_full.py setup` imports the workflow and a dummy `github-pat`
   credential, which the log node needs.

   Keep Build AI Prompt unstubbed. The first version of this rig (`mock.py`, `wf_template.json`, kept for
   history) replaced it, and that node is the one that broke on the first live heal: a string literal split
   across two lines.

## Running by hand

```powershell
$env:N8N_USER_FOLDER = "<rig>\n8nhome"
node <n8n>\bin\n8n import:workflow --input=wf.json        # the JSON needs an "id"
cmd /c "node <n8n>\bin\n8n execute --id=<id> --rawOutput > out.json 2>&1"
```

`python tools/n8n_rig/drive_full.py all` runs every group. The modes `setup`, `happy`, `retry`, `multifile`,
`extract`, `failure` and `probes` each run one.

| mode | expects |
|---|---|
| `happy` | Real model `edits` from `eval/runs/kout_diff/results.json` give `success` and a file equal to `eval/cases/golden_test.js`. The recorded request carries the shipped system prompt and the trimmed CI error without timestamps. The token reaches the API host but not the blob host. |
| `multifile` | A latent bug reachable only through the stack trace is fetched and fixed in `src/format.js`. A fix aimed at a file the model was not shown is rejected with a comment. |
| `extract` | Each rejection (SEARCH missing, SEARCH not unique, patch does not parse, the old `fixed_code` schema) posts a commit comment quoting its reason. |
| `failure` | A model 524 on a retry posts a PR comment (`/issues/37/comments`) quoting the timeout; a streamed happy path keeps its timings. |
| `probes` | Hostile characters in the webhook payload round-trip. `failure_kind=infra` is refused before any model call. A missing job log (404) still heals, and the prompt says `(log unavailable)`. |

After each run the rig exports the workflow and compares it with the shipped JSON: Code node bodies must
be byte-identical, and the HTTP nodes may differ only in the host, URL or credential the rig changed.
`last_request.json` shows exactly what production sends the model.

## Known traps

- In n8n 1.123, `execute --file` is deprecated and does nothing: it exits 0 without running. Import the
  workflow and run it by `--id`.
- Reading n8n's output through a pipe (Python `capture_output`) returns 0 bytes when the run succeeds,
  because n8n exits before Node flushes the pipe on Windows. Failed runs do print, so it looks as if only
  successes break. Redirect to a file with `cmd /c ... > out.json`.
- PowerShell's `ConvertTo-Json` rewrites the file. Before trusting a run, export the workflow and check the
  imported node bodies are still byte-identical.

## Not covered here

The endpoint script (`kaggle/endpoint/deploy_src.py`) only runs on a Kaggle GPU, so its test is a real boot.
The GitHub write steps (Create Branch through Open PR) only run live; see `tools/golive/`.
