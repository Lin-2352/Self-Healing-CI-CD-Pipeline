"""Drive the three Code nodes that deal with the model address and the fallback reply.

  Store Model URL          the "Set Model URL" webhook calls it after each Kaggle boot; it keeps the
                           new trycloudflare.com address in the workflow's static data
  Get Model URL            runs before every model call; stored address first, QWEN_BASE_URL second
  Normalize OpenAI Reply   turns the OpenAI node's answer into the completion shape the rest reads

The harness gives the nodes `$getWorkflowStaticData` (ctx["static"]) and `$vars` (ctx["vars"]).

Run:  python tools/tests/test_model_url.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # tools/, for heal_harness
from heal_harness import run_node, first_json          # noqa: E402

fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-66s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:140]))


def store(body, static=None):
    res = run_node("store_model_url", {"input": {"body": body}, "static": static or {}})
    return res, first_json(res)


def get(item, static=None, vars_=None):
    res = run_node("get_model_url", {"input": item, "static": static or {}, "vars": vars_ or {}})
    return res, first_json(res)


def norm(item, qwen=None):
    res = run_node("normalize_openai_reply", {"input": item, "nodes": {"Qwen3.8 Analyze": qwen} if qwen else {}})
    return res, first_json(res)


URL = "https://hats-conducted-committees-hollywood.trycloudflare.com"

print("Store Model URL")
res, j = store({"url": URL})
check("valid address is stored and echoed", bool(j) and j["ok"] and j["url"] == URL, res.get("error"))
check("static data now holds it", res.get("static_after", {}).get("qwenUrl") == URL, res.get("static_after"))
check("storage time is recorded", bool(res.get("static_after", {}).get("qwenUrlSetAt")))
res, j = store({"url": URL + "///"})
check("trailing slashes are stripped", bool(j) and j["url"] == URL, res.get("error"))
for bad in ["https://evil.example.com", "http://abc.trycloudflare.com", URL + "/v1", "",
            "https://abc.trycloudflare.com.evil.com", "https://abc.trycloudflare.com@evil.com"]:
    res, j = store({"url": bad}, static={"qwenUrl": "https://old.trycloudflare.com"})
    check("rejects %r" % bad, not res["ok"] and "trycloudflare.com" in (res["error"] or {}).get("message", ""), res)
    check("  and keeps the old address", res.get("static_after", {}).get("qwenUrl") == "https://old.trycloudflare.com")
res, j = store({})
check("missing url is rejected", not res["ok"])

print("Get Model URL")
res, j = get({"request_body": {"x": 1}}, static={"qwenUrl": URL}, vars_={"QWEN_BASE_URL": "https://var.trycloudflare.com"})
check("stored address wins over the variable", bool(j) and j["model_base_url"] == URL, res.get("error"))
check("the incoming item is passed through", bool(j) and j["request_body"] == {"x": 1})
res, j = get({"a": 1}, vars_={"QWEN_BASE_URL": "https://var.trycloudflare.com/"})
check("variable is the fallback, slash stripped", bool(j) and j["model_base_url"] == "https://var.trycloudflare.com", res.get("error"))
res, j = get({"a": 1})
check("neither set -> clear error", not res["ok"] and "No model URL" in (res["error"] or {}).get("message", ""), res)

print("Normalize OpenAI Reply")
res, j = norm({"output": [{"content": [{"type": "output_text", "text": "hello {\"a\":1}"}]}]})
check("Responses-API shape -> completion with the text", bool(j) and j["choices"][0]["message"]["content"] == 'hello {"a":1}', res.get("error"))
check("marked as coming from the fallback", bool(j) and j["provider"] == "openai-fallback")
res, j = norm({"message": {"content": "plain text"}})
check("chat shape -> completion", bool(j) and j["choices"][0]["message"]["content"] == "plain text", res.get("error"))
res, j = norm({"text": "just text"})
check("bare text field -> completion", bool(j) and j["choices"][0]["message"]["content"] == "just text", res.get("error"))
res, j = norm({"error": {"message": "Forbidden - perhaps check your credentials?"}})
check("error item -> throws naming both failures",
      not res["ok"] and "OpenAI fallback failed too" in res["error"]["message"] and "Forbidden" in res["error"]["message"], res)
res, j = norm({"error": {"message": "Forbidden - perhaps check your credentials?"}},
              qwen={"error": {"message": "The origin web server did not return a complete response within the 120-second Proxy Read Timeout window."}})
check("failure comment keeps Qwen's own error too",
      not res["ok"] and "120-second" in res["error"]["message"] and "Forbidden" in res["error"]["message"], res)
res, j = norm({"error": "boom"}, qwen={"data": "data: {partial"})
check("cut stream (no Qwen error) is named as such",
      not res["ok"] and "incomplete" in res["error"]["message"], res)
res, j = norm({"output": []})
check("no text -> throws", not res["ok"] and "no text" in res["error"]["message"], res)

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
