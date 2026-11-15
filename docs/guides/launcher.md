# Launcher

`launcher/` holds Windows `.bat` files that start and stop each part of the project. Each one changes into the project root, runs one or more Python scripts, prints what it is doing, and waits for a key at the end so you can read the output. The full tables of what each script runs and how long it takes are in [launcher/README.md](../../launcher/README.md). This page says which scripts to use for which goal.

All of them need Python, Node and the local n8n install from [SETUP.md](SETUP.md). The scripts that talk to GitHub need `gh auth login` done once, and the Kaggle ones need the Kaggle command line signed in.

## Three goals

### Look at the n8n workspace

1. Run `start\start_n8n_local_only.bat`. It runs `tools/golive/up.py --local` and opens http://localhost:5678. No tunnel starts and nothing changes on GitHub.
2. Stop it with `end\stop_n8n.bat`.

Use this to show or edit the workflow without a model.

### Heal live with the n8n workflow

1. Run `start\1_start_model_on_kaggle.bat`. It builds and pushes the endpoint notebook, watches the boot for about 4 minutes, saves the URL to `%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt`, and posts the URL to the cloud n8n's `Set Model URL` webhook. `push.py` refuses to start a second session while one is running.
2. Run `start\2a_load_workflow_into_n8n.bat`. It stops n8n, imports the workflow and credentials with the new model URL, and activates it. Repeat it after every Kaggle boot, because the URL changes.
3. Run `start\3a_start_n8n_live.bat`. It starts n8n and a public tunnel and points the repository's `N8N_WEBHOOK_URL` secret at it.
4. Push a breaking change under `demo-app/` to `main`. A heal PR should appear in about a minute.
5. Stop everything with `end\stop_everything.bat`.

If you use n8n Cloud instead of the local n8n, step 1 is enough for the model, and steps 2 and 3 do not apply. See [cloud-n8n.md](cloud-n8n.md).

### Run ci-heal-backend against the model

1. Run `start\1_start_model_on_kaggle.bat`.
2. Run `start\4_start_backend.bat`. It runs `kaggle/tools/backend_env.py --check`, which writes the model URL and key into `ci-heal-backend\.env` and checks that the model answers, then starts `uvicorn` from `ci-heal-backend\.venv` on port 8000 and opens http://127.0.0.1:8000/docs. It stops with a message if `.venv` does not exist; create it with the Quick start in `ci-heal-backend/README.md`. `DRY_RUN` is `true` by default, so nothing is written to GitHub.
3. Stop with `end\stop_backend.bat`, then `end\stop_model_on_kaggle.bat`.

Run only one healer per repository at a time.

## Other scripts

| Script | When to use it |
|---|---|
| `start\show_model_info.bat` | To read the current model URL and endpoint key, check that the model answers, and copy the URL to the clipboard. |
| `end\stop_model_on_kaggle.bat` | When you finish. The Kaggle command line cannot stop a session, so it opens the notebook page where you stop it by hand, then waits for a key and runs `status.py` and `fetch_logs.py endpoint` to check and save the logs. A running session keeps using GPU quota. |
| `end\stop_n8n.bat` | To stop n8n and the tunnel. The repository secret keeps the dead tunnel address, and the CI notify job only warns until the next start. |
| `end\stop_backend.bat` | To stop whatever listens on port 8000. Closing the backend's window does the same. |
| `end\stop_everything.bat` | Runs the backend, n8n and Kaggle stop scripts in that order. |
