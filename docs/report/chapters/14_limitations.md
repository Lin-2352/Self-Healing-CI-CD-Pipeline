## Limitations and failed experiments

### What the results do not show

- **One demo application.** All heal measurements use a small Express app with one to three source files. Bugs in a large code base, in other languages or in build configuration are not covered.
- **Small case sets.** The quality studies use 11 to 17 cases with two or three repetitions each (Chapter 9). A difference of one case is within noise, and the report treats it that way.
- **First-try quality is partial.** With thinking off, the production setting for the first attempt, 12 of 18 hard single-file calls were healed. Thinking raised that to 18 of 18 at about four times the time, which is why retries use it (docs/results/SHIPPED_QUALITY_EVAL.md).
- **Multi-file healing is below its own bar.** The fast pass healed 9 of 18 against a bar of 12, and the retry loop reached 15 of 18 in replay. The model-comparison study recorded the same weakness as a "caller gap": bugs that need a change in a file other than the one the stack trace names (docs/results/MULTIFILE_EVAL.md, MODEL_BAKEOFF.md).
- **A passing fix can still be wrong.** The gates check that the edit applies, parses and passes the repository's tests. The recorded run of Chapter 10 produced such a fix: it removed the error and dropped a string conversion.
- **The OpenAI fallback has never produced a fix.** Its routing is tested, but on the n8n Cloud trial the free credits answered `Forbidden` to a webhook-triggered run, and the cause was not established.

### Operational limits

- **The model address changes on every Kaggle boot.** The Set Model URL webhook removes the manual step but not the dependency: a stopped session means a dead address, and the workflow then reaches its failure comment or the fallback.
- **GPU availability.** A session can wait in the Kaggle queue without limit; one attempt for this report waited about 90 minutes without a GPU (Chapter 10).
- **A cap of 1.5 hours per session** in our notebook, set to bound quota use, and no way to stop a session from the command line.
- **The n8n Cloud instance is a 14-day trial** whose REST API is disabled.
- **The attempt cap lives in the notify job**, not in n8n. As far as the workflow's nodes show, a call to the webhook made by hand with a high `attempt` value is not stopped inside n8n.
- **Failures that are not test failures are not healed.** A failed `npm install` or a build step that fails after the tests passed does not set `failure_kind=code` and is ignored (Chapter 5).
- **Seven workflow nodes have no error setting**, and 17 backend audit findings are open (Chapters 11 and 8).
- **One healer per repository.** Running the n8n workflow and the backend on the same repository would open competing pull requests.

### Experiments that did not work

The failures below were useful: each changed a decision.

| experiment | what happened | consequence |
|---|---|---|
| A quantization tuned for the model's DeltaNet layers (Ridge) | 5 of 18 against 9 of 18 for the generic Q4 build (docs/results/SPEED_QUALITY_EVAL.md) | the generic unsloth UD-Q4_K_XL file stayed |
| A ternary-weight model (Bonsai) | tied the Q4 build on quality at about a third of the size, but could not use tensor split or MTP | not used |
| The claim that 30 tok/s was unreachable on two T4s | refuted by measurement with tensor split and MTP (22 to 27 tok/s) | the analysis moved to docs/old/ |
| Non-streamed thinking retry | died at 125.9 s on the tunnel's 120 s limit (HTTP 524) | model calls are streamed |
| Following the log redirect with the n8n HTTP node | the GitHub token would have been sent to the storage host | the redirect is handled by two nodes (Chapter 12) |
| Judging a feature by a log line | wording changes in logs twice made a disabled feature look enabled | features are judged by effect, as in the thinking self-check |
| Asking the model what it is | the answers about its own identity were unreliable | the endpoint's `/v1/models` is the reference |
| n8n 2.x | the webhook was never registered | n8n 1.x only |
| Reading `$vars` in a self-hosted n8n | variables are a licensed feature and did not resolve | the local import substitutes the literal address |

Sources for the rows: docs/research/QWEN38_IDENTITY_AND_RIDGE.md, docs/results/SPEED_QUALITY_EVAL.md, docs/results/SPEED_LEVERS_EVAL.md, docs/results/GOLIVE_RESULTS.md, tools/golive/n8n_import.py, and tools/n8n_rig/README.md.

### Inconsistencies found in the older documentation

The review of the existing documents for this report found, and the rewritten documents fix, statements that no longer matched the code: the old setup guide named a different demo layout and a 20-node workflow; two documents said the backend turns thinking on for classifier re-asks when no code does; the speed analysis in `docs/old/` was refuted by the later speed study; and one endpoint note counted a crashed kernel version and its successor as two sessions. The raw data of several early experiments (the first sweep, the first end-to-end baseline) was not kept, so those numbers can be quoted but not recomputed.
