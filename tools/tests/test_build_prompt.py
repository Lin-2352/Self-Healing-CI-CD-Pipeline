"""Drive `Build AI Prompt` (the real node body) without n8n and without a GPU.

This node shipped with a string literal split across 10 lines and killed the first two live heals,
and for its whole life it sent the model NO CI log. So the suite asserts on what the model will
actually receive: the user message must carry the real error from a real GitHub job log, trimmed,
and the system message must be byte-equal to SYSTEM_PROMPT.

Fixture: eval/cases/fixtures/job_log_failure.txt is the unmodified log of a real failing job
(Lin-2352/self-healing-cicd-demo job 106924016599, missing_require). Secrets appear as ***.

Run:  python tools/tests/test_build_prompt.py
"""
import io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # tools/tests -> project root
sys.path.insert(0, os.path.dirname(HERE))          # tools/, for heal_harness
from heal_harness import run_node, first_json          # noqa: E402

LOG = io.open(os.path.join(ROOT, "eval", "cases", "fixtures", "job_log_failure.txt"),
              encoding="utf-8", newline="").read()
SRC = io.open(os.path.join(ROOT, "eval", "cases", "golden_test.js"), encoding="utf-8").read()
BODY = io.open(os.path.join(ROOT, "tools", "nodes", "build_ai_prompt.js"), encoding="utf-8").read()
SYSTEM_PROMPT = json.loads(re.match(r"const SYSTEM_PROMPT = (.*);\r?\n", BODY).group(1))

fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-52s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else detail[:120]))


REDIRECT = {"statusCode": 302, "headers": {"location": "https://blob.example/log?sig=x"}, "body": ""}


def ctx(log_item, kind="code", api=REDIRECT):
    return {"input": {},
            "nodes": {"GitHub Failure Webhook": {"body": {
                          "run_id": 1, "repo": "demo/app", "branch": "main", "commit": "abc1234",
                          "actor": "dev", "failure_kind": kind}},
                      "Fetch Job Logs": {"jobs": [{"id": 106924016599, "name": "build-and-test",
                                                   "conclusion": "failure", "steps": [
                                                       {"name": "Run tests", "conclusion": "failure"}]}]},
                      "Fetch Failed Job Log": api,
                      "Download Job Log": log_item,
                      "Fetch Changed Files": {"files": [{"filename": "test.js", "status": "modified"}]},
                      "Fetch File Content": SRC}}


def build(log_item, kind="code", api=REDIRECT):
    res = run_node("build_ai_prompt", ctx(log_item, kind, api))
    return res, first_json(res)


print("real job log -> trimmed failure region")
res, j = build({"data": LOG})
check("node ran", res["ok"], str(res.get("error")))
if j:
    user = j["request_body"]["messages"][1]["content"]
    log = j["ci_log"]
    check("user message has the real error", "ReferenceError: http is not defined" in user)
    check("user message has the failing source line", "test.js:29" in user)
    check("CI log section present", "--- CI failure log ---\n" in user)
    check("no runner timestamps left", not re.search(r"\d{4}-\d\d-\d\dT\d\d:", log), log[:80])
    check("no ANSI codes left", "\x1b" not in log)
    check("no ##[ control lines left", "##[" not in log, log)
    check("no checkout-path noise", "/home/runner/work" not in log)
    check("post-job cleanup excluded", "Post job cleanup" not in log and "safe.directory" not in log)
    check("step script (before endgroup) excluded", "kind=code" not in log.split("failure_kind=code")[0])
    check("within 40 lines / 3000 chars", log.count("\n") < 40 and len(log) <= 3000,
          "%d lines %d chars" % (log.count("\n") + 1, len(log)))
    check("system message byte-equal to SYSTEM_PROMPT",
          j["request_body"]["messages"][0]["content"] == SYSTEM_PROMPT)
    check("pr_body cap is in the system prompt", "pr_body: at most 2 short sentences." in SYSTEM_PROMPT)
    check("cache_prompt on", j["request_body"].get("cache_prompt") is True)
    check("model call is streamed (no 120 s tunnel timeout)", j["request_body"].get("stream") is True)
    print("\n  trimmed log (%d lines, %d chars):\n    %s\n" % (
        log.count("\n") + 1, len(log), log.replace("\n", "\n    ")))

print("degraded inputs -> '(log unavailable)', prompt still builds")
for label, item in [("log fetch 404 (error item)", {"error": {"message": "404 Not Found"}}),
                    ("empty log", {"data": ""}),
                    ("log with no ##[error] marker", {"data": "2026-09-22T20:27:01.7541238Z hello\n"})]:
    res, j = build(item)
    ok = res["ok"] and j and "--- CI failure log ---\n(log unavailable)" in j["prompt"]
    check(label, bool(ok), str(res.get("error")))

res, j = build({"error": {"message": "Invalid URL"}}, api={"statusCode": 404, "headers": {}, "body": "Not Found"})
check("API 404, no Location (download errors)", bool(j) and "(log unavailable)" in j["prompt"], str(res.get("error")))
res, j = build({"error": {"message": "Invalid URL"}}, api={"statusCode": 200, "headers": {}, "body": LOG})
check("API answers 200 directly -> its body is the log",
      bool(j) and "ReferenceError: http is not defined" in j["ci_log"], str(res.get("error")))

print("\nlong log -> tail kept within caps")
big = "".join("2026-09-22T20:27:11.9957635Z line %d %s\n" % (i, "x" * 120) for i in range(300))
res, j = build({"data": "2026-09-22T20:27:11.7690900Z ##[endgroup]\n" + big +
                        "2026-09-22T20:27:12.0066162Z ##[error]Process completed with exit code 1.\n"})
log = (j or {}).get("ci_log", "")
check("tail kept (last line present)", "Process completed with exit code 1." in log)
check("caps hold", log.count("\n") < 40 and len(log) <= 3000, "%d chars" % len(log))

print("\nretry (CI failed on the healer's own PR)")
def build_hook(**over):
    c = ctx({"data": LOG})
    c["nodes"]["GitHub Failure Webhook"]["body"].update(over)
    res = run_node("build_ai_prompt", c)
    return res, first_json(res)
res, j = build_hook(branch="ai-fix-http-require-abc1234", attempt="1")
check("attempt 1 on ai-fix branch -> is_retry, attempt=1", bool(j) and j["is_retry"] is True and j["attempt"] == 1,
      str(res.get("error")))
check("prompt tells the model it is attempt 2 and the file has the previous fix",
      bool(j) and "Heal attempt: 2." in j["prompt"] and "ALREADY contains the previous automated fix" in j["prompt"])
check("retry turns thinking ON", bool(j) and j["request_body"]["chat_template_kwargs"] == {"enable_thinking": True})
res, j = build_hook(branch="main")
check("first heal (no attempt field) -> not a retry, no attempt line",
      bool(j) and j["is_retry"] is False and j["attempt"] == 0 and "Heal attempt" not in j["prompt"])
check("first heal keeps thinking OFF", bool(j) and j["request_body"]["chat_template_kwargs"] == {"enable_thinking": False})
res, j = build_hook(branch="feature-x", attempt="1")
check("attempt on a NON ai-fix branch -> not treated as a retry", bool(j) and j["is_retry"] is False)

print("\ninfra failure still refused before any model call")
res, j = build({"data": LOG}, kind="infra")
check("infra refused", (not res["ok"]) and "not a code bug" in (res.get("error") or {}).get("message", ""))

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
