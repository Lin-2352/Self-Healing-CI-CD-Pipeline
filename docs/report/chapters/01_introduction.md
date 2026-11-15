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
