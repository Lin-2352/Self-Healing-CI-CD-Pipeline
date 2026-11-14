"""Local self-test of the shipped-quality eval's scorer, BEFORE spending GPU.

The eval's verdicts are only as good as its scorer, so every piece is exercised here with the same
functions the kernel uses: the smoke-test preflight (golden passes, every broken case fails as a
code bug), prompts built by the real node for both arms, and scoring of known-good and known-bad
model outputs (real captured model responses included).

Run:  python eval/test_shipped_eval.py      (needs express in app/node_modules)
"""
import difflib, io, json, os, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import shipped_quality_eval as E                    # noqa: E402  (main() does not run off-Kaggle)
from heal_harness import RUNNER                     # noqa: E402

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
CONTROL = json.loads(rd("eval", "cases", "eval_cases.json")); HARD = json.loads(rd("eval", "cases", "hard_cases.json"))
GOLDEN = CONTROL["golden"]
CASES = [dict(c, set="control") for c in CONTROL["cases"]] + [dict(c, set="hard") for c in HARD["cases"]]
BUILD, EXTRACT = rd("tools", "nodes", "build_ai_prompt.js"), rd("tools", "nodes", "extract_ai_response.js")
XDIR = os.path.join(ROOT, "app")                    # has node_modules/express
WORK = tempfile.mkdtemp()
fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-58s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:140]))


def envelope(edits):
    return json.dumps({"branch_name": "ai-fix-x", "file_path": "test.js", "edits": edits,
                       "pr_title": "t", "pr_body": "b"})


def golden_edit(broken):
    """Minimal unique SEARCH/REPLACE derived from the real diff (same idea as test_extract_node)."""
    sm = difflib.SequenceMatcher(None, broken, GOLDEN)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    a1, a2, b1, b2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
    for pad in (40, 80, 160, 400):
        s = broken[max(0, a1 - pad):min(len(broken), a2 + pad)]
        if broken.count(s) == 1:
            r = GOLDEN[max(0, b1 - pad):min(len(GOLDEN), b2 + pad)]
            return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (s, r)
    return None


print("preflight: the smoke test must separate golden from every broken case")
rc, out = E.smoke(GOLDEN, XDIR, 5601)
check("golden -> exit 0", rc == 0, out)
for i, c in enumerate(CASES):
    rc, out = E.smoke(c["code"], XDIR, 5602 + i)
    check("broken %-22s -> exit 1 (code bug, not infra)" % c["name"], rc == 1, "rc=%s %s" % (rc, out))

print("\nprompts built by the REAL node, both arms")
prompts = {}
for c in CASES:
    for wl in (False, True):
        r = E.run_node(RUNNER, BUILD, E.prompt_ctx(c, wl), WORK)
        prompts[(c["name"], wl)] = E.first_json(r)
        if not (r.get("ok") and prompts[(c["name"], wl)]):
            check("build %s log=%s" % (c["name"], wl), False, r.get("error"))
for c in CASES:
    a, b = prompts[(c["name"], False)], prompts[(c["name"], True)]
    first_err = next((l.strip() for l in c["log"].split("\n") if re.search(r"Error|CODE_FAILURE", l)), "")
    check("%-22s A says (log unavailable), B carries the error" % c["name"],
          "(log unavailable)" in a["prompt"] and bool(first_err) and first_err in b["ci_log"]
          and not re.search(r"\d{4}-\d\d-\d\dT\d\d:", b["ci_log"]) and "/home/runner" not in b["ci_log"],
          b["ci_log"][:200])
    check("%-22s request_body is production's (temp 0.1, thinking off)" % c["name"],
          b["request_body"]["temperature"] == 0.1 and b["request_body"]["chat_template_kwargs"] == {"enable_thinking": False})

print("\nscoring: known-good edits must score functional, known-bad must not")
for i, c in enumerate(CASES):
    e = golden_edit(c["code"])
    s = E.score(envelope(e), c, prompts[(c["name"], True)], GOLDEN, RUNNER, EXTRACT, XDIR, WORK, 5700 + i)
    check("%-22s golden edit -> functional + exact, no cheat" % c["name"],
          s["functional"] and s["byte_exact"] and not s["cheat"], s)
c = CASES[0]
s = E.score(envelope("<<<<<<< SEARCH\nconst nope = 1;\n=======\nx\n>>>>>>> REPLACE"), c,
            prompts[(c["name"], True)], GOLDEN, RUNNER, EXTRACT, XDIR, WORK, 5790)
check("absent SEARCH -> not applied, gate names the reason", not s["applied"] and "not in the file" in s["gate"], s)
s = E.score("sorry, cannot help", c, prompts[(c["name"], True)], GOLDEN, RUNNER, EXTRACT, XDIR, WORK, 5791)
check("prose reply -> not applied", not s["applied"], s)
# an edit that parses but leaves the bug: applied, NOT functional
hc = next(x for x in CASES if x["name"] == "multi_hunk")
s = E.score(envelope("<<<<<<< SEARCH\nconst PORT\n=======\nconst  PORT\n>>>>>>> REPLACE"), hc,
            prompts[(hc["name"], True)], GOLDEN, RUNNER, EXTRACT, XDIR, WORK, 5792)
check("parsing-but-wrong edit -> applied, functional=False", s["applied"] and not s["functional"], s)

diff = json.load(io.open(os.path.join(ROOT, "eval", "runs", "kout_diff", "results.json"), encoding="utf-8"))["runs"]
real = [r for r in diff if r.get("format") == "search_replace" and r.get("raw")]
byname = {x["name"]: x for x in CASES}
ok = 0
for i, r in enumerate(real):
    raw = r["raw"]; a, b = raw.find("{"), raw.rfind("}")
    s = E.score(raw[a:b + 1], byname[r["case"]], prompts[(r["case"], True)], GOLDEN, RUNNER, EXTRACT, XDIR, WORK, 5800 + i)
    ok += s["functional"]
check("REAL captured model responses score functional: %d/%d" % (ok, len(real)), ok == len(real))

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
