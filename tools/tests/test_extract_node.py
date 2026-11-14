"""Drive `Extract AI Response` (the real node body) without n8n and without a GPU.

This node now applies SEARCH/REPLACE edits to the live file, so its failure modes are the
dangerous kind: a patch that applies to the *wrong* place, or a patched file that does not
parse, becomes a PR that looks healthy and is not. Every rejection path is therefore asserted
on its SPECIFIC error, not merely on "it threw".

That distinction is not theoretical. An earlier version of this suite checked only
`ok == False` and reported 6 of 7 green while the node was completely broken - every case was
dying on the same mangled regex literal, and "it threw" was true for all of them.

Run:  python tools/tests/test_extract_node.py
"""
import base64, difflib, io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # tools/tests -> project root
sys.path.insert(0, os.path.dirname(HERE))          # tools/, for heal_harness
from heal_harness import run_node, first_json          # noqa: E402

GOLDEN = io.open(os.path.join(ROOT, "eval", "cases", "golden_test.js"), encoding="utf-8").read()
CASES = {c["name"]: c["code"] for c in
         json.load(io.open(os.path.join(ROOT, "eval", "cases", "eval_cases.json"),
                           encoding="utf-8"))["cases"]}

fails = 0


def ctx(edits, original):
    model = {"branch_name": "ai-fix-x", "file_path": "test.js", "edits": edits,
             "pr_title": "t", "pr_body": "b"}
    return {"input": {"choices": [{"message": {"content": json.dumps(model)}}]},
            "nodes": {"Build AI Prompt": {"repo": "demo/app", "commit": "abc1234def",
                                          "branch": "main", "file_path": "test.js"},
                      # raw text, because the node fetches with Accept: application/vnd.github.raw
                      "Fetch File Content": original}}


def sr(search, replace):
    return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (search, replace)


def minimal_edit(broken):
    """Derive a minimal, unique SEARCH/REPLACE straight from the real diff.

    Hand-writing these is how a fixture ends up testing the wrong thing: a first attempt
    inserted one blank line too many (the breakage removes a line but leaves its surrounding
    blanks), and the resulting 'failure' was the fixture, not the node.
    """
    sm = difflib.SequenceMatcher(None, broken, GOLDEN)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    if not ops:
        return None
    a1, a2, b1, b2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
    pad = 40
    s = broken[max(0, a1 - pad):min(len(broken), a2 + pad)]
    r = GOLDEN[max(0, b1 - pad):min(len(GOLDEN), b2 + pad)]
    return None if broken.count(s) != 1 else sr(s, r)


def happy(name, broken):
    global fails
    e = minimal_edit(broken)
    if e is None:
        print("  %-24s SKIP (no unique anchor)" % name); return
    res = run_node("extract_ai_response", ctx(e, broken))
    j = first_json(res)
    ok = bool(j) and base64.b64decode(j["base64_content"]).decode("utf-8") == GOLDEN
    fails += not ok
    print("  %-24s %s  blocks=%s" % (name, "PASS" if ok else "FAIL",
                                     (j or {}).get("edits_applied")))
    if not ok:
        print("       ", (res.get("error") or {}).get("message", "")[:140])


def reject(label, edits, must_match, original=None):
    """A rejection counts only if it throws for its OWN reason."""
    global fails
    res = run_node("extract_ai_response", ctx(edits, original or CASES["missing_app_init"]))
    msg = (res.get("error") or {}).get("message", "")
    ok = (not res["ok"]) and re.search(must_match, msg, re.I) is not None
    fails += not ok
    print("  %-38s %s  <- %s" % (label, "PASS" if ok else "FAIL", msg[:66]))


print("happy paths: every real breakage must yield BYTE-EXACT golden")
for nm, brk in CASES.items():
    happy(nm, brk)

print("\nbranch + commit trailer (the retry loop's counter)")
e = minimal_edit(CASES["missing_require"])
c = ctx(e, CASES["missing_require"])
j = first_json(run_node("extract_ai_response", c))
ok = bool(j) and j["branch_name"] == "ai-fix-x-abc1234" and j["commit_message"] == "t\n\nHeal-Attempt: 1" \
    and j["attempt"] == 1
fails += not ok
print("  %-38s %s  <- %s" % ("first heal: sha-suffixed branch, trailer 1", "PASS" if ok else "FAIL",
                              j and (j["branch_name"], j["commit_message"])))
c["nodes"]["Build AI Prompt"].update({"branch": "ai-fix-http-abc1234", "is_retry": True, "attempt": 1})
j = first_json(run_node("extract_ai_response", c))
ok = bool(j) and j["branch_name"] == "ai-fix-http-abc1234" and j["commit_message"].endswith("Heal-Attempt: 2") \
    and j["attempt"] == 2
fails += not ok
print("  %-38s %s  <- %s" % ("retry: SAME branch, trailer 2", "PASS" if ok else "FAIL",
                              j and (j["branch_name"], j["commit_message"])))

print("\nrejections, each matched on its specific error")
GET = 'app.get("/", (req, res) => res.send("ok"));'
reject("SEARCH text absent", sr("const nope = 1;", "const yes = 2;"), r"not in the file")
reject("no blocks at all (prose)", "here is your fix!", r"no well-formed")
reject("empty SEARCH", "<<<<<<< SEARCH\n\n=======\nx\n>>>>>>> REPLACE", r"empty SEARCH|not unique")
reject("patched file does not parse", sr(GET, "const app = express(   \n" + GET), r"does not parse")
reject("edit is a no-op", sr(GET, GET), r"unchanged")
reject("missing edits field",
       "", r"no edits field|no well-formed")
# a SEARCH that legitimately matches twice must be refused rather than patched arbitrarily
dup = "const dup = 1;\nconst dup = 1;\n" + CASES["missing_app_init"]
reject("SEARCH matches more than once", sr("const dup = 1;", "const dup = 2;"),
       r"not unique", original=dup)

# The cases above are hand-built: they prove the applier's LOGIC, not that it survives what a
# model actually emits. Below are the 15 REAL search_replace responses captured by
# DIFF_FORMAT_EVAL.md, replayed through this node. Without them the suite is green only on
# fixtures written by the same person who wrote the applier.
DIFF = os.path.join(ROOT, "eval", "runs", "kout_diff", "results.json")
if os.path.exists(DIFF):
    def envelope(t):
        c = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
        c = re.sub(r"^```(?:json)?|```$", "", c, flags=re.M).strip()
        try:
            return json.loads(c)
        except Exception:
            a = c.find(chr(123)); b = c.rfind(chr(125))   # { } by index: no regex escapes
            if a >= 0 and b > a:
                try: return json.loads(c[a:b + 1])
                except Exception: return None
        return None
    runs = [r for r in json.load(io.open(DIFF, encoding="utf-8"))["runs"]
            if r.get("format") == "search_replace" and r.get("raw")]
    print("\nREAL model-produced edits (%d captured responses)" % len(runs))
    exact = 0
    for r in runs:
        edits = (envelope(r["raw"]) or {}).get("edits")
        res = run_node("extract_ai_response", ctx(edits, CASES[r["case"]]))
        j = first_json(res)
        be = bool(j) and base64.b64decode(j["base64_content"]).decode("utf-8") == GOLDEN
        exact += be
        if not be:
            print("  %-22s r%d FAIL %s" % (r["case"], r["rep"],
                  (res.get("error") or {}).get("message", "")[:70]))
    fails += (exact != len(runs))
    print("  byte-exact on real model output: %d/%d %s"
          % (exact, len(runs), "PASS" if exact == len(runs) else "FAIL"))
else:
    print("  SKIP real-model replay: run kaggle kernels output "
          "lacrimatct/qwen3-8-diff-format-eval -p eval/runs/kout_diff")

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
