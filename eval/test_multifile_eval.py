"""Local self-test of the Phase 6 eval, before any GPU time: same functions and node bodies as the kernel.

Proves, per case (6 multi-file + 5 single-file controls):
  - the trees behave: golden passes, broken fails with exit 1
  - production's real Pick -> fetch -> Build puts a file that CAN fix the bug in front of the model
  - the scorer marks a correct single-file fix (derived from the real diff) as healed, right file
  - the scorer rejects a fix aimed at a file the model was not shown

Run:  python eval/test_multifile_eval.py    (needs express in app/node_modules)
"""
import difflib, io, json, os, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import multifile_eval as E                          # noqa: E402  (main() does not run off-Kaggle)
from heal_harness import RUNNER                     # noqa: E402

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
MF = json.loads(rd("eval", "cases", "multifile_cases.json"))
CONTROL = json.loads(rd("eval", "cases", "eval_cases.json"))
G = MF["golden_tree"]
JS = {"pick": rd("tools", "nodes", "pick_candidate_files.js"), "build": rd("tools", "nodes", "build_ai_prompt.js"),
      "extract": rd("tools", "nodes", "extract_ai_response.js"), "runner": RUNNER}
XDIR, WORK = os.path.join(ROOT, "app"), tempfile.mkdtemp()
CASES = [dict(c, set="multi") for c in MF["cases"]] + \
        [dict(name=c["name"], set="control", files={"test.js": c["code"]}, changed=["test.js"], fixable_in=["test.js"])
         for c in CONTROL["cases"]]
fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-72s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:140]))


def fix_edit(broken_text, golden_text):
    sm = difflib.SequenceMatcher(None, broken_text, golden_text)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    a1, a2, b1, b2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
    for pad in (40, 80, 160, 400, 2000):
        s = broken_text[max(0, a1 - pad):a2 + pad]
        if broken_text.count(s) == 1:
            return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (s, golden_text[max(0, b1 - pad):b2 + pad])


for c in CASES:
    good, bad = E.case_trees(c, G)
    g, _ = E.run_tree(good, XDIR)
    b, out = E.run_tree(bad, XDIR)
    log = E.github_log(out)
    cands, items, pj, res = E.build_request(c, bad, log, JS, WORK)
    shown = [p for p in c["fixable_in"] if p in cands]
    target = next(p for p in c["fixable_in"] if bad.get(p) != good.get(p)) if any(bad.get(p) != good.get(p) for p in c["fixable_in"]) else c["fixable_in"][0]
    env = json.dumps({"branch_name": "ai-fix-x", "file_path": target, "edits": fix_edit(bad[target], good[target]),
                      "pr_title": "t", "pr_body": "b"})
    s = E.score(env, c, bad, good, pj, items, JS, XDIR, WORK)
    ok = g == 0 and b == 1 and bool(pj) and bool(shown) and s["functional"] and s["right_file"] and not re.search(r"[A-Za-z]:\\", log)
    check("%-8s %-30s cands=%s" % (c["set"], c["name"], cands), ok,
          dict(golden=g, broken=b, shown=shown, score=s, err=res.get("error")))

c = CASES[0]; good, bad = E.case_trees(c, G)
_, out = E.run_tree(bad, XDIR)
cands, items, pj, _ = E.build_request(c, bad, E.github_log(out), JS, WORK)
s = E.score(json.dumps({"branch_name": "x", "file_path": "src/elsewhere.js", "edits": "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE",
                        "pr_title": "t", "pr_body": "b"}), c, bad, good, pj, items, JS, XDIR, WORK)
check("fix aimed at a file the model was not shown -> not applied, gate says why",
      not s["applied"] and "is not one of the files shown" in (s["gate"] or ""), s)
s = E.score("no json here", c, bad, good, pj, items, JS, XDIR, WORK)
check("prose reply -> not applied", not s["applied"], s)

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
