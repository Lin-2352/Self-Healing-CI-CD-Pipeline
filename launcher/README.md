# Launcher

Double-click scripts to start and stop every part of the project. `start/` brings things up and `end/`
takes them down. Each script opens its own window, prints what it is doing, and waits for a key
before closing, so you can read the result.

All of them need Python, Node and the local n8n install described in `docs/guides/SETUP.md`. They find the
project root on their own, so they work from wherever the repository is checked out.

## Which order

| goal | run, in this order | stop with |
|---|---|---|
| Show the n8n workspace (no model, nothing public) | `start/start_n8n_local_only.bat` | `end/stop_n8n.bat` |
| Live heal with the n8n workflow | `start/1_start_model_on_kaggle.bat`, `start/2a_load_workflow_into_n8n.bat`, `start/3a_start_n8n_live.bat` | `end/stop_everything.bat` |
| Run ci-heal-backend against the model | `start/1_start_model_on_kaggle.bat`, `start/4_start_backend.bat` | `end/stop_backend.bat`, then `end/stop_model_on_kaggle.bat` |

The model URL changes on every Kaggle boot. `1_start_model_on_kaggle.bat` sends it to the cloud n8n by itself:
the workflow's `Set Model URL` webhook stores it and `Get Model URL` reads it before each model call, with the
`QWEN_BASE_URL` variable as a fallback. The local n8n takes the URL when `2a_load_workflow_into_n8n.bat`
imports the workflow. `show_model_info.bat` prints the URL and the endpoint key at any time.

Run only one healer per repository at a time (see the root `README.md`): the n8n workflow and the
backend both react to the same failed run.

The number in front of each script is its step. `1` starts the model. `2a` and `3a` are the local n8n path; `2b` is the web n8n path, for when the local n8n does not work (do not run `3a` after it, because `3a` points the GitHub secret at your PC). `4` starts the backend.

## Start scripts

| script | what it runs | takes | notes |
|---|---|---|---|
| `start_n8n_local_only.bat` | `tools/golive/up.py --local`, then opens http://localhost:5678 | ~25 s | n8n on this PC only. No tunnel, no change on GitHub. Log in with the owner account you created the first time. |
| `1_start_model_on_kaggle.bat` | `kaggle/tools/status.py`, `build_endpoint.py`, `push.py endpoint`, `watch_deploy.py`, then `tools/golive/set_cloud_qwen_url.py` | ~4 min | One click: starts Qwen3.8-27B on a Kaggle 2×T4 session, saves its URL to `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt`, and posts it to the cloud n8n workflow's `Set Model URL` webhook, using the secret in `secrets.json`, so nothing has to be pasted. Uses Kaggle GPU quota until you stop it. `push.py` refuses to start a second session. |
| `2b_cloud_n8n_connect_model.bat` | `show_model_info.py`, `set_cloud_qwen_url.py`, then opens the web workflow | ~10 s | Use it when the local n8n does not work: tells the web n8n (self-heal.app.n8n.cloud) where the model is and opens its workflow, which is already loaded and published there. Credentials cannot be loaded by a script on the cloud trial. |
| `show_model_info.bat` | `tools/golive/show_model_info.py` | 5 s | Prints the current model URL (n8n `QWEN_BASE_URL`), the endpoint API key (the token for the `Qwen3.8 Endpoint Key` credential), and whether the model answers. Copies the URL to the clipboard. |
| `2a_load_workflow_into_n8n.bat` | `tools/golive/down.py`, then `tools/golive/n8n_import.py` | ~1 min | Stops n8n, then imports the workflow and credentials with the new model URL and activates it. Run it after every new Kaggle session, because the URL changes each time. |
| `3a_start_n8n_live.bat` | `tools/golive/up.py` | ~1 min | Starts n8n and a public Cloudflare tunnel, then points this repo's `N8N_WEBHOOK_URL` secret at it. Needs `gh auth login` done once. After this, a failing push that changes `demo-app/` triggers a heal. |
| `4_start_backend.bat` | `kaggle/tools/backend_env.py --check`, then `uvicorn app.main:app --port 8000` in `ci-heal-backend/.venv` | ~10 s | Writes the model URL and key into `ci-heal-backend/.env` and runs the backend in that window. Opens http://127.0.0.1:8000/docs. `DRY_RUN=true` by default, so it writes nothing to GitHub. |

## End scripts

| script | what it runs | notes |
|---|---|---|
| `stop_n8n.bat` | `tools/golive/down.py` | Stops n8n and the tunnel. The repo secret keeps the dead tunnel URL; the CI notify job then only warns. |
| `stop_backend.bat` | stops whatever listens on port 8000 | Closing the backend's window also stops it. |
| `stop_model_on_kaggle.bat` | opens the notebook on kaggle.com, then `status.py` and `fetch_logs.py endpoint` | The Kaggle command line cannot stop a session. On the page: View Active Events, the `…` menu on the session, Stop Session. Then press a key in the window to check it stopped and save the logs. |
| `stop_everything.bat` | the three above, in that order | |

## Live demo checklist

1. `1_start_model_on_kaggle.bat` and wait for the URL line.
2. `2a_load_workflow_into_n8n.bat`.
3. `3a_start_n8n_live.bat`.
4. Push a commit to `main` that breaks a test in `demo-app/` (for example in `demo-app/src/format.js`). Actions turns red, the notify job
   calls n8n, and an `ai-fix-...` pull request should appear within about a minute.
5. `stop_everything.bat`.

Rehearse the whole run once before showing it to anyone.
