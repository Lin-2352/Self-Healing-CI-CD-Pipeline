# Running on n8n Cloud

The workflow can run on an n8n Cloud instance instead of a local n8n. The project used a Cloud trial for this. The Qwen3.8 model still runs on Kaggle, and GitHub still calls a webhook, so the Cloud instance replaces only the local n8n and its Cloudflare tunnel.

Read [SETUP.md](SETUP.md) first for the repository, the Kaggle model and the GitHub secrets. This page covers what differs on Cloud.

## Trial limits

1. The trial lasts 14 days.
2. The n8n REST API is switched off on trial instances, so nothing can create credentials or import workflows by API key. This is why the model URL is sent to a webhook inside the workflow and not set through the API (`tools/golive/set_cloud_qwen_url.py`). The local `n8n_import.py` route does not apply to Cloud. You import and configure in the browser.

## Import the workflow

1. In the Cloud interface, create a workflow and use the menu to import from file. Choose `n8n/self_healing_pipeline.json`. It has 26 nodes.
2. Do not rely on the credential ids inside the file. Create the credentials below and attach them to the nodes that show a missing credential.

## Credentials

Create three credentials, with exactly these names, so they match the names stored in the workflow.

| Name | Type | Value |
|---|---|---|
| `N8N Webhook Secret` | Header Auth. Header name `X-Webhook-Secret` | The shared webhook secret |
| `GitHub PAT` | Bearer Auth | A GitHub token with access to the repository's Actions logs, contents, branches and pull requests |
| `Qwen3.8 Endpoint Key` | Bearer Auth | The endpoint key that `launcher\start\show_model_info.bat` prints |

The shared webhook secret is `webhook_secret` in `%LOCALAPPDATA%\selfheal-n8n\secrets.json`. Use the same value for the credential, for the GitHub secret `N8N_WEBHOOK_SECRET`, and for the script that updates the model URL. Do not paste the value into the repository or this page.

Both webhook nodes, `GitHub Failure Webhook` and `Set Model URL`, use `N8N Webhook Secret`. `GitHub PAT` is used by the nine GitHub request nodes. `Qwen3.8 Endpoint Key` is used by `Qwen3.8 Analyze`.

## The OpenAI Fallback node

`OpenAI Fallback` ships with no credential (`tools/tests/test_workflow_structure.py` checks this). It is the node on the `no` branch of `Model OK?`: when the Qwen request fails or returns no usable stream, the same two messages go to `gpt-4.1`, and `Normalize OpenAI Reply` converts the answer for the rest of the workflow.

On Cloud the node can be bound to the free OpenAI credits that come with the trial. In our test, with that binding, the node answered `Forbidden` on runs started by the webhook. We did not find out why. Do not count on the fallback for webhook-triggered runs on Cloud. The node has `onError` set to continue, so its error reaches `Normalize OpenAI Reply`, which stops with a message that names both the Qwen failure and the fallback failure (`tools/nodes/normalize_openai_reply.js`). That message is what you will see in the execution.

## Activate and find the webhook URL

1. Activate the workflow. The production URL works only while the workflow is active.
2. The failure webhook's production URL has this form: `https://<instance>.app.n8n.cloud/webhook/github-failure`. The path `github-failure` comes from the `GitHub Failure Webhook` node. `<instance>` is the name of your Cloud instance.
3. Set it as the repository secret from your own PC: `gh secret set N8N_WEBHOOK_URL --repo <owner>/<repo>`, and paste the URL when asked. Also set `N8N_WEBHOOK_SECRET` the same way. `launcher\start\3a_start_n8n_live.bat` is for a local n8n and would overwrite `N8N_WEBHOOK_URL` with a tunnel address, so do not run it while you use Cloud.

## Send the model URL to Cloud

Each Kaggle boot gives the model a new `trycloudflare.com` address. The `Set Model URL` webhook, at `https://<instance>.app.n8n.cloud/webhook/set-qwen-url`, stores it in the workflow. `Get Model URL` reads the stored address before every model call. The `QWEN_BASE_URL` variable is used only when nothing has been stored (`tools/nodes/get_model_url.js`).

1. Run `launcher\start\1_start_model_on_kaggle.bat`. Its last step runs `python tools\golive\set_cloud_qwen_url.py`.
2. The script reads `qwen_url.txt` and `secrets.json` from `%LOCALAPPDATA%\selfheal-n8n` and posts `{"url": ...}` with the header `X-Webhook-Secret`. It never prints the secret.
3. The script's default address is `https://self-heal.app.n8n.cloud`. To use another instance, set the environment variable `N8N_CLOUD_URL` to its base URL before running the script.
4. `Store Model URL` accepts only an `https://...trycloudflare.com` address and rejects anything else. On success the reply contains the stored URL and a time, and the script prints `cloud n8n now uses the model at ...`.

Exit code 0 means stored, 2 means there was no saved URL or secret yet, and 1 means the request failed. An HTTP 401 or 403 usually means the secret differs from the one in the `N8N Webhook Secret` credential. To run the update by hand after a boot, run `python tools\golive\set_cloud_qwen_url.py` from the project root.

## Check it

1. Run `python tools\golive\show_model_info.py`. It reports whether the model answers.
2. Push a breaking change to `demo-app/` on `main`, as in the first test in [SETUP.md](SETUP.md).
3. Open the Executions list on the Cloud instance and follow the run. An `ai-fix-...` pull request should open.
