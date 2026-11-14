"""Local self-test of the bake-off, before any GPU time: the kernel's own functions and node bodies.

Proves:
  - all 17 cases (5 control, 6 hard, 6 multi) behave: golden passes, broken fails with exit 1
  - production's real Pick -> fetch -> Build shows the model a file that can fix each one
  - a correct fix (derived from the real diff) scores healed on every case; a prose reply does not
  - thinking_leak catches reasoning_content and <think> blocks, and passes a clean reply
  - summarize + decide apply the fixed rule (gates, ranking, the +3 heals / 95% speed switch condition)
  - the generated kernel parses, has no placeholders left, and lists the baseline first

Run:  python eval/test_bakeoff_eval.py    (needs express in app/node_modules)
"""
import base64, difflib, io, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import bakeoff_eval as B                            # noqa: E402  (main() does not run off-Kaggle)
from heal_harness import RUNNER                     # noqa: E402

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
MF = json.loads(rd("eval", "cases", "multifile_cases.json"))
CONTROL = json.loads(rd("eval", "cases", "eval_cases.json"))
HARD = json.loads(rd("eval", "cases", "hard_cases.json"))
G = MF["golden_tree"]
JS = {"pick": rd("tools", "nodes", "pick_candidate_files.js"), "build": rd("tools", "nodes", "build_ai_prompt.js"),
      "extract": rd("tools", "nodes", "extract_ai_response.js"), "runner": RUNNER}
XDIR, WORK = os.path.join(ROOT, "app"), tempfile.mkdtemp()
fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-74s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:160]))


def fix_edit(broken_text, golden_text):
    sm = difflib.SequenceMatcher(None, broken_text, golden_text)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    a1, a2, b1, b2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
    for pad in (40, 80, 160, 400, 2000):
        s = broken_text[max(0, a1 - pad):a2 + pad]
        if broken_text.count(s) == 1:
            return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (s, golden_text[max(0, b1 - pad):b2 + pad])


CASES = B.bakeoff_cases(MF, CONTROL, HARD)
check("17 cases: 5 control, 6 hard, 6 multi",
      [sum(c["set"] == s for c in CASES) for s in ("control", "hard", "multi")] == [5, 6, 6])
check("arms per rep: 5 control F + 12 hard/multi x (F, T) = 29 calls", sum(len(B.arms_for(c)) for c in CASES) == 29)
for c in CASES:
    good, bad = B.case_trees(c, G)
    g, _ = B.run_tree(good, XDIR)
    b, out = B.run_tree(bad, XDIR)
    cands, items, pj, res = B.build_request(c, bad, B.github_log(out), JS, WORK)
    target = next((p for p in c["fixable_in"] if bad.get(p) != good.get(p)), c["fixable_in"][0])
    env = json.dumps({"branch_name": "ai-fix-x", "file_path": target, "edits": fix_edit(bad[target], good[target]),
                      "pr_title": "t", "pr_body": "b"})
    s = B.score(env, c, bad, good, pj, items, JS, XDIR, WORK)
    prose = B.score("I think the bug is on line 3.", c, bad, good, pj, items, JS, XDIR, WORK)
    ok = (g == 0 and b == 1 and bool(pj) and any(p in cands for p in c["fixable_in"]) and s["functional"]
          and not prose["applied"])
    check("%-8s %-30s" % (c["set"], c["name"]), ok, dict(golden=g, broken=b, cands=cands, score=s, err=res.get("error")))
    if pj:
        check("  first pass asks for no thinking", (pj["request_body"].get("chat_template_kwargs") or {}) == {"enable_thinking": False},
              pj["request_body"].get("chat_template_kwargs"))

check("thinking_leak: clean reply = 0", B.thinking_leak({"content": "{\"a\":1}", "reasoning_content": ""}) == 0)
check("thinking_leak: reasoning_content counted", B.thinking_leak({"content": "x", "reasoning_content": "hmm"}) == 3)
check("thinking_leak: inline <think> counted", B.thinking_leak({"content": "<think>abc</think>{}"}) > 0)
check("thinking_leak: unclosed <think> counted", B.thinking_leak({"content": "<think>abc"}) > 0)


def runs(model, ctl, f, t, wall=20.0):
    out = [dict(model=model, arm="F", set="control", case="c%d" % i, rep=0, functional=i < ctl, wall_s=wall, think_leak=0)
           for i in range(10)]
    out += [dict(model=model, arm="F", set="hard", case="h%d" % i, rep=0, functional=i < f, wall_s=wall, think_leak=0)
            for i in range(24)]
    out += [dict(model=model, arm="T", set="hard", case="h%d" % i, rep=0, functional=i < t, wall_s=60.0) for i in range(24)]
    return out


bench = lambda tps, acc=0.6, split=True: {"avg": tps, "accept_rate": acc, "tensor_split_engaged": split, "ctx": 131072}
R = runs("base", 10, 12, 20) + runs("plus3", 10, 15, 18) + runs("plus2", 10, 14, 24) + runs("slow", 10, 20, 20) + \
    runs("ctlfail", 9, 23, 23)
S = B.summarize(R, {"base": bench(25.0), "plus3": bench(24.0), "plus2": bench(30.0), "slow": bench(20.0),
                    "ctlfail": bench(25.0)})
rank, verdict, pick = B.decide(S, "base")
check("summary counts 24 hard calls, 10 controls", S["base"]["f_calls"] == 24 and S["base"]["control"] == "10/10")
check("control 9/10 is ineligible", not verdict["ctlfail"]["eligible"])
check("ranking by F heals", rank[:3] == ["slow", "plus3", "plus2"], rank)
check("'slow' (+8 heals, 80% speed) does NOT switch; plus3 (+3, 96%) does", pick == "plus3", pick)
S2 = B.summarize(runs("base", 10, 12, 20) + runs("plus2", 10, 14, 24), {"base": bench(25.0), "plus2": bench(30.0)})
check("+2 heals is not enough -> keep baseline", B.decide(S2, "base")[2] == "base")
S3 = B.summarize(runs("base", 10, 12, 20) + runs("c", 10, 16, 20), {"base": bench(25.0), "c": bench(25.0, acc=0.3)})
check("MTP accept 0.3 is ineligible -> keep baseline",
      B.decide(S3, "base")[2] == "base" and not B.decide(S3, "base")[1]["c"]["eligible"])
leak = runs("c", 10, 20, 20); leak[12]["think_leak"] = 500
S4 = B.summarize(runs("base", 10, 12, 20) + leak, {"base": bench(25.0), "c": bench(25.0)})
check("thinking leaking into fast replies is ineligible", not B.decide(S4, "base")[1]["c"]["eligible"])
S5 = B.summarize(runs("base", 10, 12, 20), {"base": bench(25.0), "c": {"error": "download: 404"}})
check("a model that never booted is reported ineligible, not dropped",
      set(S5) == {"base", "c"} and not B.decide(S5, "base")[1]["c"]["eligible"] and B.decide(S5, "base")[2] == "base")

p = subprocess.run([sys.executable, os.path.join(HERE, "build_bakeoff_eval.py")], capture_output=True, text=True)
check("builder ran", p.returncode == 0, p.stderr[-300:])
k = rd("kaggle", "evals", "bakeoff", "push", "qwen3-8-model-bakeoff.py")
check("kernel: no placeholders, multifile helpers inlined",
      not re.search(r'"__[A-Z_]+_B64__"', k) and "from multifile_eval import" not in k and "def run_tree(" in k)
models = json.loads(base64.b64decode(re.search(r'MODELS = json.loads\(base64.b64decode\("([^"]+)"', k).group(1)))
check("kernel models: baseline first, 4 total",
      models[0][0] == json.loads(rd("kaggle", "endpoint", "models.json"))["default"] and len(models) == 4, models)
meta = json.loads(rd("kaggle", "evals", "bakeoff", "push", "kernel-metadata.json"))
check("metadata: T4 x2, builder attached, internet", meta["machine_shape"] == "NvidiaTeslaT4" and meta["enable_internet"]
      and meta["kernel_sources"] == ["linsxav/llama-server-builder"])
print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
