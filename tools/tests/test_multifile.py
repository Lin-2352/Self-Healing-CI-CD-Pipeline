"""Phase 6: multi-file healing - Pick Candidate Files, Build AI Prompt and Extract AI Response together.

The CI logs here are REAL: each broken tree from eval/cases/multifile_cases.json is run with node, and
its output is re-rooted at GitHub's runner checkout path (/home/runner/work/<repo>/<repo>/...) with the
job log's timestamp prefix - the exact shape "Pick Candidate Files" parses in production.

Run:  python tools/tests/test_multifile.py
"""
import base64, io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))                  # tools/, for heal_harness
sys.path.insert(0, os.path.join(ROOT, "eval"))              # check_multifile_cases.tree_test
from heal_harness import run_node, first_json              # noqa: E402
from check_multifile_cases import tree_test, trees, as_github_log   # noqa: E402

D = json.load(io.open(os.path.join(ROOT, "eval", "cases", "multifile_cases.json"), encoding="utf-8"))
CASES = {c["name"]: c for c in D["cases"]}
fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-66s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:150]))


LOGS = {}
for name, c in CASES.items():
    _, bad, _ = trees(c, D["golden_tree"])
    rc, out = tree_test(bad)
    LOGS[name] = as_github_log(out)


def pick(name, changed=None, log=None):
    c = CASES[name]
    files = [{"filename": p, "status": "modified"} for p in (changed if changed is not None else c["changed"])]
    res = run_node("pick_candidate_files", {"input": {}, "nodes": {
        "Download Job Log": {"data": LOGS[name] if log is None else log},
        "Fetch Failed Job Log": {"statusCode": 302, "headers": {}, "body": ""},
        "Fetch Changed Files": {"files": files}}})
    out = res.get("output") or []
    return res, [o["json"]["path"] for o in out if isinstance(o, dict)]


print("Pick Candidate Files on real CI output")
for name, want_first in [("helper_throws", "src/format.js"), ("test_exposes_latent_bug", "src/format.js"),
                         ("wrong_require_path", "test/api.test.js"), ("readme_first_then_code", "src/app.js"),
                         ("signature_changed_caller_not", "src/format.js")]:
    res, got = pick(name)
    check("%-28s first candidate %-18s got %s" % (name, want_first, got), res.get("ok") and got and got[0] == want_first,
          res.get("error"))
res, got = pick("test_exposes_latent_bug")
check("latent bug: buggy src/format.js reached via the TRACE although the commit changed only the test",
      "src/format.js" in got and CASES["test_exposes_latent_bug"]["changed"] == ["test/api.test.js"], got)
res, got = pick("second_changed_file_buggy")
check("two changed files, bug in the SECOND: both are candidates", got == ["src/app.js", "src/format.js"], got)
check("never a node_modules path, never more than 3",
      all(not any("node_modules" in p for p in pick(n)[1]) and len(pick(n)[1]) <= 3 for n in CASES))
res, got = pick("readme_first_then_code", log="")
check("no CI log -> changed code files only (README.md skipped)", got == ["src/app.js"], got)
res, got = pick("readme_first_then_code", changed=["README.md"], log="")
check("nothing qualifies -> one placeholder item, so the run reaches the failure comment",
      got == ["__no_code_file__"], got)


def contents(path, text):
    return {"path": path, "type": "file", "encoding": "base64",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def build_ctx(files, changed):
    return {"input": {}, "nodes": {
        "GitHub Failure Webhook": {"body": {"repo": "demo/app", "branch": "main", "commit": "abc1234def",
                                            "actor": "dev", "failure_kind": "code"}},
        "Fetch Job Logs": {"jobs": [{"id": 1, "name": "build-and-test", "conclusion": "failure", "steps": []}]},
        "Fetch Failed Job Log": {"statusCode": 302, "headers": {}, "body": ""},
        "Download Job Log": {"data": LOGS["helper_throws"]},
        "Fetch Changed Files": {"files": [{"filename": p, "status": "modified"} for p in changed]},
        "Fetch File Content": [contents(p, t) for p, t in files]}}


print("\nBuild AI Prompt with several candidate files")
_, bad, _ = trees(CASES["helper_throws"], D["golden_tree"])
three = [(p, bad[p]) for p in ("src/format.js", "src/app.js", "test/api.test.js")]
res = run_node("build_ai_prompt", build_ctx(three, ["src/format.js"]))
j = first_json(res)
u = (j or {}).get("prompt", "")
check("all 3 files in the prompt, each labelled",
      bool(j) and all(("File: %s\n" % p) in u for p, _ in three), res.get("error"))
check("multi-file header tells the model to fix exactly ONE file and name it",
      "3 candidates; fix exactly ONE file, and set file_path to that file" in u)
check("default file_path = first candidate; candidate list recorded",
      bool(j) and j["file_path"] == "src/format.js" and j["candidate_files"] == "src/format.js,src/app.js,test/api.test.js")
check("the stack trace in the prompt points at src/format.js", "src/format.js:5" in j["ci_log"], j and j["ci_log"][:300])
# one file through the JSON contents API renders EXACTLY like the legacy raw single file
one = run_node("build_ai_prompt", build_ctx([("src/format.js", bad["src/format.js"])], ["src/format.js"]))
legacy_ctx = build_ctx([], ["src/format.js"]); legacy_ctx["nodes"]["Fetch File Content"] = bad["src/format.js"]
legacy = run_node("build_ai_prompt", legacy_ctx)
check("single file: prompt byte-identical to the legacy single-file shape (earlier evals still apply)",
      first_json(one)["prompt"] == first_json(legacy)["prompt"] and "--- changed source files ---" in first_json(one)["prompt"])


def extract(files, model):
    ctx = {"input": {"choices": [{"message": {"content": json.dumps(model)}}]},
           "nodes": {"Build AI Prompt": {"repo": "demo/app", "commit": "abc1234def", "branch": "main",
                                         "file_path": files[0][0]},
                     "Fetch File Content": [contents(p, t) for p, t in files]}}
    res = run_node("extract_ai_response", ctx)
    return res, first_json(res)


print("\nExtract AI Response picks the file the model named")
fix = "<<<<<<< SEARCH\n  return `Hello, ${clean.toUppercase()}!`;\n=======\n  return `Hello, ${clean}!`;\n>>>>>>> REPLACE"
res, j = extract([("src/app.js", bad["src/app.js"]), ("src/format.js", bad["src/format.js"])],
                 {"branch_name": "ai-fix-greet", "file_path": "src/format.js", "edits": fix, "pr_title": "t", "pr_body": "b"})
patched = base64.b64decode(j["base64_content"]).decode("utf-8") if j else ""
check("edits applied to the NAMED file (2nd candidate), result == golden src/format.js",
      bool(j) and j["file_path"] == "src/format.js" and patched == D["golden_tree"]["src/format.js"], res.get("error"))
tree = dict(bad); tree["src/format.js"] = patched
check("the healed tree passes the real npm test", tree_test(tree)[0] == 0)
res, j = extract([("src/app.js", bad["src/app.js"])],
                 {"branch_name": "x", "file_path": "src/format.js", "edits": fix, "pr_title": "t", "pr_body": "b"})
m = (res.get("error") or {}).get("message", "")
check("a file the model was NOT shown -> rejected with the list of shown files",
      not res["ok"] and "is not one of the files shown" in m and "src/app.js" in m, m)
res, j = extract([("./src/format.js", bad["src/format.js"])][:0] + [("src/format.js", bad["src/format.js"])],
                 {"branch_name": "x", "file_path": "./src/format.js", "edits": fix, "pr_title": "t", "pr_body": "b"})
check("a leading './' in file_path is tolerated", bool(j) and j["file_path"] == "src/format.js", res.get("error"))

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
