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
