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
