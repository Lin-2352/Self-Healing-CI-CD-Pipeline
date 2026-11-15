# LLM contract

This is the interface the Kaggle (or any OpenAI-compatible) server must satisfy. The backend has two roles, classifier and fixer. Each role has its own base URL, API key, and model name. They can point at the same server or at two different ones.

The client posts to `{root}/v1/chat/completions`. `root` is `CLASSIFIER_LLM_BASE_URL` or `FIXER_LLM_BASE_URL` with a trailing slash removed. A base URL that already ends in `/v1` is accepted and is not doubled.

## Auth

Every request sends:

```
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

The demo Kaggle deploy expects the bearer token it was started with (`selfheal-e2e-key` in that deploy script). Use whatever token the running server checks. Do not commit a new key into this repo.

Readiness is `GET {root}/v1/models` with the same bearer header. The backend treats any status below 400 as up. `GET /health/llm` calls that for both roles and always returns HTTP 200. Read `classifier_llm` and `fixer_llm` in the body (`ok` or `error`). A model outage does not fail `GET /health/ready`.

## Request

```json
{
  "model": "local",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0,
  "max_tokens": 512,
  "stream": true
}
```

- Classifier: `temperature` is `CLASSIFIER_TEMPERATURE` (default 0), `max_tokens` is `CLASSIFIER_MAX_TOKENS` (default 512). One sample by default. Set `CLASSIFIER_SAMPLES=3` and a temperature above 0 when the samples should differ.
- Fixer: `temperature` is `FIX_TEMPERATURE` (default 0.7), `max_tokens` is `FIX_MAX_TOKENS` (default 4096). The backend asks for `FIX_CANDIDATES` completions (default 3), one at a time.
- `stream` defaults to true. The Cloudflare tunnel in front of Kaggle closes a request that stays quiet for about two minutes, so a long completion has to stream.
- The HTTP client adds `"chat_template_kwargs": {"enable_thinking": true}` only when a caller passes `enable_thinking=True`. No caller does today (`app/services/classifier.py` never sets it), so every request leaves thinking to the server default. Other providers can ignore the field.
- Calls for one role are sequential. The Kaggle server is started with `--parallel 1`.

The user message wraps the log, the diff, and any file bodies in `<<<UNTRUSTED_...>>>` … `<<<END_UNTRUSTED_...>>>` markers. Text inside those markers is evidence. Instructions inside them must be ignored.

## Classifier response

Reply with one JSON object and no other text. Extra keys are rejected.

```json
{"category": "dependency_error", "confidence": 0.95, "reasoning": "unpinned package"}
```

`category` is exactly one of:

- `dependency_error`
- `configuration_error`
- `test_failure`
- `flaky_or_network`

`confidence` is a number from 0 to 1. `reasoning` is a non-empty string. Do not return `unknown`. The backend uses `unknown` with confidence 0 only after the reply fails this schema, and that escalates.

A reply may be wrapped in a ```json fence or a `<think>...</think>` block. The backend strips those before parsing. If the object still fails, the classifier asks again up to `CLASSIFIER_MAX_RETRIES` (default 2) and then escalates.

## Fixer response

Reply with one JSON object and no other text. Extra keys are rejected.

```json
{
  "branch_name": "ai-fix-ignored",
  "file_path": "app.py",
  "edits": "<<<<<<< SEARCH\nexact current text\n=======\nreplacement text\n>>>>>>> REPLACE\n",
  "pr_title": "Pin the dependency",
  "pr_body": "The install failed because the pin does not exist."
}
```

- `branch_name` is one path segment and must not be `main` or `master`. The backend does not use this name. It opens `ai-fix-<category>-<sha7>` from the failing commit.
- `file_path` must be one of the files in the user message.
- `edits` is one or more SEARCH/REPLACE blocks and nothing else. SEARCH is non-empty and must match the file exactly once. The replacement must change the file. At most `FIX_MAX_EDITS` blocks (default 5) and `FIX_MAX_CHANGED_LINES` lines (default 50).
- Patches that skip, xfail, delete a test, or remove assertions are rejected.
- `.github/workflows` stays blocked unless `FIX_ALLOW_WORKFLOWS` is set.

## Streaming

When `stream` is true the body is server-sent events:

```
data: {"choices":[{"delta":{"content":"{\"category\":"}}]}

data: {"choices":[{"delta":{"content":"\"dependency_error\"}"}}]}

data: [DONE]
```

The backend concatenates `delta.content` (or `message.content` if that is what a chunk sends). An empty delta is a keep-alive. A chunk that is not JSON, or a stream with no content, is a failed response and is not retried. HTTP 5xx, 524, and timeouts are retried within the role deadline (`CLASSIFIER_LLM_DEADLINE` default 180 seconds, `FIXER_LLM_DEADLINE` default 360). Each attempt also has its own timeout (`CLASSIFIER_LLM_TIMEOUT` default 60, `FIXER_LLM_TIMEOUT` default 120).

## curl smoke test

Replace the tunnel host and the token. `model` is `local` for the Kaggle llama-server.

```bash
curl -sS https://your-tunnel.trycloudflare.com/v1/models \
  -H "Authorization: Bearer $LLM_API_KEY"

curl -N https://your-tunnel.trycloudflare.com/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"local\",\"stream\":true,\"temperature\":0,\"max_tokens\":128,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the classifier JSON object only. category must be test_failure.\"}]}"
```

Then start the backend with both roles aimed at that URL and open `GET /health/llm`. Both fields should be `ok` when `/v1/models` answers. Chat itself is what the curl above checks.

## What the backend does with the reply

The classifier result and the agreement of the fixer candidates go through the decision engine. Confidence at or above 0.75 on a fixable category asks for patches. Agreement at or above 0.6 opens a dry-run or a real pull request. Anything else, including a schema failure, escalates. The model never chooses the git branch, and it never receives unredacted logs.
