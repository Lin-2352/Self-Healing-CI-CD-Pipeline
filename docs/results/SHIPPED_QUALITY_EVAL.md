# Shipped-format quality: hard bugs, with and without the CI log, with and without thinking

**Result: production (arm B) heals 12/18 hard calls and 15/15 control. The CI log did not change
the heal rate (12 → 12), but it made fixes closer to golden (6 → 9 byte-exact). Thinking with a
1024-token budget healed 18/18 hard calls, including the two bugs every earlier config failed, at
~4× the time.** The decision rules were fixed before the data.

Date: 2026-09-23. Kernel `lacrimatct/qwen3-8-shipped-quality-eval` v1, 2×T4, Qwen3.8-27B UD-Q4_K_XL,
production server flags (`-sm tensor`, f16 KV, MTP n=2; VRAM symmetric [9017, 9017]).
84 calls, 0 errors, 0 truncations.

## What makes this production, not a proxy

- **Prompt:** built by the real `tools/nodes/build_ai_prompt.js` under the heal-harness runner. The
  real `trimCiLog` cuts each case's node output out of a GitHub-style job-log envelope.
- **Request:** the node's own `request_body`, verbatim.
- **Patch application:** the real `tools/nodes/extract_ai_response.js`, so the exactly-once and parse
  gates count.
- **Score:** functional first. The patched file runs as the real smoke test (`node test.js` with
  express): exit 0 means healed. Preflight: golden exits 0 and all 11 broken files exit 1.
- **Scorer self-check:** `eval/rescore_shipped.py` re-scored every raw locally from scratch.
  **84/84 agree.** The scorer itself was proven before any GPU time (`eval/test_shipped_eval.py`,
  including 15/15 real captured model responses).

Arms: **A** no log (`(log unavailable)`, i.e. production before round 2) · **B** with log
(production now) · **C** with log + `enable_thinking` + `--reasoning-budget 1024` (hard cases only;
the server accepted a positive budget).

## Per case (3 reps; `1` = healed)

| case | set | A func | B func | C func | A exact | B exact | C exact |
|---|---|---|---|---|---|---|---|
| missing_app_init | control | 111 | 111 | – | 111 | 111 | – |
| typo_method | control | 111 | 111 | – | 111 | 111 | – |
| missing_require | control | 111 | 111 | – | 111 | 111 | – |
| syntax_missing_brace | control | 111 | 111 | – | 111 | 010 | – |
| wrong_status_check | control | 111 | 111 | – | 111 | 111 | – |
| https_protocol_mismatch | hard | 111 | 111 | 111 | 111 | 111 | 111 |
| **callback_arity** | hard | **000** | **000** | **111** | 000 | 000 | 111 |
| uninterpolated_url | hard | 111 | 111 | 111 | 111 | 111 | 111 |
| inverted_status_check | hard | 111 | 111 | 111 | 000 | 111 | 000 |
| route_wrong_status | hard | 111 | 111 | 111 | 000 | 000 | 000 |
| **multi_hunk** | hard | **000** | **000** | **111** | 000 | 000 | 000 |

| arm | control healed | hard healed | hard exact | median wall | median completion tok | MTP accept |
|---|---|---|---|---|---|---|
| A no log | 15/15 | 12/18 | 6/18 | 9.2 s | 156 | 0.904 |
| B with log | 15/15 | 12/18 | 9/18 | 9.1 s | 158 | 0.883 |
| C log + thinking | – | **18/18** | 9/18 | **36.8 s** | 735 | 0.756 |

## Decisions, by the rules set in advance

- **Log: a wash on heal rate** (B − A = 0, rule needed ≥ 3). It stays. It is correct input, costs
  ~3 s, and shifted fixes toward the canonical one (`inverted_status_check` 000 → 111 exact).
  `syntax_missing_brace` with the log used a smaller anchor (110 vs 361 tokens) and made a different
  valid fix. That is why exact dropped 3 → 1 there while it healed 3/3.
- **Thinking: adopted** (C − B = **+6/18**, rule needed ≥ 3, no failures). It is the only config that has
  ever fixed `callback_arity` or `multi_hunk`. **Cost:** median 36.8 s, worst 77.9 s, vs 9.1 s.
  Thinking fills ~1–1.3k tokens on the hard ones.
- **How it ships: thinking only on RETRIES.**
  - The first pass stays fast: all 5 control cases and 4/6 hard cases heal in ~9 s.
  - When the heal PR's own CI fails, the retry (the CI-feedback loop) turns thinking on. The two
    cases B fails are exactly where C was measured.
  - Always-on thinking would add ~28 s to every heal to fix 2 cases in 11, and control cases with
    thinking were not measured.
- **Retry-loop targets:** `callback_arity`, `multi_hunk`.

## Caveats

- **Cloudflare quick tunnels time out at 120 s** (corrected from 100 s after a real 524). The slowest eval thinking call was 77.9 s; a live retry on 2026-09-28 ran 125.9 s and was cut off. A slower
  call would surface as a 524 and a failed retry, which leaves no new commit and so no further retry.
  The live retry run must watch for this.
- **All 6 production (B) failures applied cleanly and failed only the smoke test**, so each
  would open a red heal PR, and the retry loop can reach it. Only 1 of all 12 A/B failures was
  rejected by the parse gate (`callback_arity` A rep 1: "Identifier 'server' has already been
  declared"). A parse-gate rejection opens **no PR, so it gets no retry**, which is a known limit of the loop.
- Arm C saw the ORIGINAL broken file. A live retry sees a file that already contains the model's
  wrong first fix, plus a new log. So C's 18/18 is **not** a measurement of retries; the live retry run is.
- `cheat` (a `mustnot` regex hit) occurred only on non-healing answers (A 5, B 6), never on a healed one.
- 3 reps per cell, one session. The consistency strings are all `000` or `111` except one byte-exact
  cell, so the verdicts are not noise-sensitive. The magnitudes (seconds) are one-session numbers.

Data: `eval/runs/kout_shipped/results.json` (raws included). Logs:
`kaggle/logs/evals/qwen3-8-shipped-quality-eval/2026-09-23_v1/`. Scripts:
`eval/shipped_quality_eval.py` (template), `eval/build_shipped_eval.py`, `eval/test_shipped_eval.py`,
`eval/rescore_shipped.py`.
