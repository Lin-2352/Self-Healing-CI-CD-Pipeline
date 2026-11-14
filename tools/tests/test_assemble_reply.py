"""Drive `Assemble Model Reply` and `Explain Failure` (the real node bodies) without n8n or a GPU.

Assemble turns llama-server's STREAMED reply (SSE) back into the completion object the rest of the
workflow reads. Streaming exists because a non-streamed thinking retry died at 125.9 s on the quick
tunnel's 120 s origin timeout. Explain Failure turns any heal that ended without a fix into a
comment, instead of a silent red PR.

The SSE fixture follows llama-server's chunk shape: `data: {...}` events with `choices[0].delta`
(content / reasoning_content), a final chunk with finish_reason + timings, then `data: [DONE]`.

Run:  python tools/tests/test_assemble_reply.py
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # tools/, for heal_harness
from heal_harness import run_node, first_json          # noqa: E402

fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-60s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:140]))


def sse(content_parts, reasoning_parts=(), done=True, finish="stop"):
    ev = lambda d, **k: "data: " + json.dumps(dict({"choices": [dict({"index": 0, "delta": d}, **k)],
                                                    "object": "chat.completion.chunk"})) + "\n\n"
    out = "".join(ev({"reasoning_content": r}, finish_reason=None) for r in reasoning_parts)
    out += "".join(ev({"content": c}, finish_reason=None) for c in content_parts)
    if finish:
        last = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "timings": {"predicted_n": 42, "predicted_per_second": 25.5, "draft_n": 30, "draft_n_accepted": 27},
                "usage": {"completion_tokens": 42}}
        out += "data: " + json.dumps(last) + "\n\n"
    if done:
        out += "data: [DONE]\n\n"
    return out


def assemble(item):
    res = run_node("assemble_model_reply", {"input": item, "nodes": {}})
    return res, first_json(res)


print("Assemble Model Reply")
reply = '{"branch_name": "ai-fix-x", "edits": "<<<<<<< SEARCH\\na\\n=======\\nb\\n>>>>>>> REPLACE"}'
parts = [reply[i:i + 7] for i in range(0, len(reply), 7)]
res, j = assemble({"data": sse(parts, reasoning_parts=["Let me ", "think."])})
check("SSE -> content reassembled byte-for-byte", bool(j) and j["choices"][0]["message"]["content"] == reply,
      res.get("error"))
check("reasoning kept separate from content", bool(j) and j["choices"][0]["message"]["reasoning_content"] == "Let me think.")
check("finish_reason + timings + usage carried over (exec stats keep working)",
      bool(j) and j["choices"][0]["finish_reason"] == "stop" and j["timings"]["predicted_n"] == 42
      and j["usage"]["completion_tokens"] == 42)
res, j = assemble({"data": sse(parts).replace("\n\n", "\r\n\r\n")})
check("CRLF line endings tolerated", bool(j) and j["choices"][0]["message"]["content"] == reply, res.get("error"))
plain = {"choices": [{"message": {"content": reply}, "finish_reason": "stop"}], "timings": {"predicted_n": 1}}
res, j = assemble(plain)
check("plain (non-stream) JSON passes through unchanged", j == plain, res.get("error"))
res, j = assemble({"error": {"message": "The service failed to process your request",
                             "description": "The origin web server did not return a complete response within the 120-second Proxy Read Timeout window."}})
m = (res.get("error") or {}).get("message", "")
check("524 error item -> rethrown with its original text",
      not res["ok"] and m.startswith("Model call failed:") and "120-second" in m, m)
res, j = assemble({"data": sse(parts, done=False, finish=None)})
m = (res.get("error") or {}).get("message", "")
check("stream cut off (no finish, no [DONE]) -> refused, not a half patch", not res["ok"] and "ended early" in m, m)
res, j = assemble({"data": ""})
check("empty body -> refused", not res["ok"] and "empty body" in (res.get("error") or {}).get("message", ""))
res, j = assemble({"data": 'data: {"error": {"code": 500, "message": "boom"}}\n\n'})
check("server error event mid-stream -> refused",
      not res["ok"] and "mid-stream" in (res.get("error") or {}).get("message", ""))

print("\nExtract AI Response passes an upstream failure through verbatim")
res = run_node("extract_ai_response", {"input": {"error": "Model call failed: 524 - origin timeout"},
                                       "nodes": {"Build AI Prompt": {}, "Fetch File Content": "x"}})
check("upstream error rethrown unchanged",
      not res["ok"] and (res.get("error") or {}).get("message") == "Model call failed: 524 - origin timeout", res.get("error"))

print("\nExplain Failure")
def explain(err, **hook):
    h = {"repo": "demo/app", "commit": "abc1234def", "branch": "main", "attempt": "0", "pr_number": ""}
    h.update(hook)
    res = run_node("explain_failure", {"input": {"error": err}, "nodes": {"GitHub Failure Webhook": {"body": h}}})
    return res, first_json(res)
res, j = explain("Model call failed: 524 - origin timeout", branch="ai-fix-x", attempt="1", pr_number="37")
check("retry failure -> comment targets the PR, names attempt 2 and the 524",
      bool(j) and j["target"] == "pr" and j["pr_number"] == "37" and "attempt 2" in j["body"] and "524" in j["body"],
      res.get("error") or j)
res, j = explain("SEARCH text of block 1 is not in the file: \"const x\"")
check("first-heal failure (push to main) -> comment targets the commit",
      bool(j) and j["target"] == "commit" and j["commit"] == "abc1234def" and "No heal PR was opened" in j["body"],
      res.get("error") or j)
res, j = explain({"message": "object-shaped error"}, pr_number="12", attempt="2", branch="ai-fix-y")
check("object error unwrapped", bool(j) and "object-shaped error" in j["body"], j)
res, j = explain("Model call failed: getaddrinfo ENOTFOUND x.trycloudflare.com [line 18] [line 10]")
check("n8n's '[line N]' suffixes stripped (seen in the live comment)",
      bool(j) and "[line" not in j["body"] and "ENOTFOUND x.trycloudflare.com" in j["body"], j and j["body"])
res, j = explain(None)
check("missing error text still yields a useful comment", bool(j) and "no error message was recorded" in j["body"], j)

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
