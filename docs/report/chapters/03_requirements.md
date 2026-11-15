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
