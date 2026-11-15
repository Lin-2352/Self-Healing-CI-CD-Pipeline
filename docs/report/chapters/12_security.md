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
