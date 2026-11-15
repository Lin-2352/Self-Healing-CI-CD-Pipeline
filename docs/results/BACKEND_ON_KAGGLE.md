# ci-heal-backend on the Kaggle endpoint (2026-11-15)

Both model roles of `ci-heal-backend` (classifier and fixer) were pointed at the self-hosted Qwen3.8-27B
endpoint and the backend's six fixture failures were replayed through its real pipeline. GitHub was mocked and
`DRY_RUN` was on, so nothing was written to a repository.

- Endpoint: kernel `lacrimatct/qwen3-8-27b-live-endpoint` v15, unsloth UD-Q4_K_XL, 2×T4, ctx 131072,
  26.8 tok/s (best 27.4), MTP acceptance 0.664, tensor split engaged (13,333 MiB per GPU).
- Logs: `kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-11-15_v15/` (`boot.log`, `stream.log`,
  `backend_replay.log`).
- Command, from `ci-heal-backend/` after `python kaggle/tools/backend_env.py --check`:
  `python scripts/replay_failure.py --llm http fixtures/<name>.log`.

## The thinking default

Qwen3.8's chat template thinks unless told otherwise; the v11 server log reads `chat template, thinking = 1`.
The backend sends `enable_thinking` only to switch thinking on, and the HTTP client supports it, but no caller passes it yet (the classifier re-ask does not), so before this change
every classifier and fixer call would have thought first. The classifier asks for at most 512 tokens and
thinking tokens count against that, so a long thought could leave no room for the JSON and the failure would
escalate.

The endpoint now starts llama-server with `--chat-template-kwargs '{"enable_thinking":false}'` and checks the
result at boot by asking the same question twice:

| request | thought? | answer |
|---|---|---|
| no thinking field | no | 391 |
| `enable_thinking: true` | yes | 391 |

The n8n workflow is unaffected because it always sends the field.

## Fixture replay

| fixture | expected | classified (confidence) | decision | candidates that applied | time |
|---|---|---|---|---|---|
| configuration | configuration_error | configuration_error (0.95) | fix | 3 of 3 | 28 s |
| dependency | dependency_error | dependency_error (0.98) | escalate: candidates disagreed | 3 of 3 | 30 s |
| flaky | flaky_or_network | flaky_or_network (0.95) | rerun the job | none asked for | 8 s |
| prompt_injection | test_failure | test_failure (0.98) | fix, agreement 1.0 | 3 of 3 | 26 s |
| secrets | dependency_error | dependency_error (0.98), 2 secrets redacted | escalate: candidates disagreed | 3 of 3 | 40 s |
| test_failure | test_failure | test_failure (0.98) | fix | 3 of 3 | 29 s |

All six were classified correctly on the first sample, every reply parsed, and all 15 fix candidates applied to
the file. The flaky case needs only the classifier, so its 8 seconds is the classifier's cost for one call.
A fixture that reaches the fixer takes 26–40 seconds for three candidates.

The two escalations are the agreement gate working as configured: the fixer samples at `FIX_TEMPERATURE=0.7`,
the three dependency patches differed, and agreement fell below `AGREEMENT_THRESHOLD=0.6`, so the backend opened
an issue instead of a PR. Whether that is the right trade-off for dependency failures is a tuning question for the
backend. The replay does not show which of the differing patches would have fixed the build.

## Limits

- One run per fixture, on six fixtures written for the backend's own tests.
- The replay stops at the decision. No PR was opened, and no CI ran on a fix.
- The prompt-injection fixture was classified and fixed like an ordinary test failure; the replay output does
  not show whether any candidate followed the injected text. The patches would need reading to answer that.
