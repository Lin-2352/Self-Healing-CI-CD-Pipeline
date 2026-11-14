"""Re-score every stored bake-off answer locally, from the raw model content alone, and re-apply the rule.

The kernel's verdicts are only trusted if this agrees with every one of them (functional + file), and the
summary/decision recomputed from the RE-SCORED runs matches the kernel's.

    python eval/rescore_bakeoff.py [eval/runs/kout_bakeoff/results.json]    (needs express in app/node_modules)
"""
import io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import bakeoff_eval as B                            # noqa: E402
from heal_harness import RUNNER                     # noqa: E402

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "eval", "runs", "kout_bakeoff", "results.json")
R = json.load(io.open(path, encoding="utf-8"))
MF = json.loads(rd("eval", "cases", "multifile_cases.json"))
CASES = {c["name"]: c for c in B.bakeoff_cases(MF, json.loads(rd("eval", "cases", "eval_cases.json")),
                                                json.loads(rd("eval", "cases", "hard_cases.json")))}
G = MF["golden_tree"]
JS = {"pick": rd("tools", "nodes", "pick_candidate_files.js"), "build": rd("tools", "nodes", "build_ai_prompt.js"),
      "extract": rd("tools", "nodes", "extract_ai_response.js"), "runner": RUNNER}
XDIR, WORK = os.path.join(ROOT, "app"), tempfile.mkdtemp()

built = {}
for name, c in CASES.items():
    good, bad = B.case_trees(c, G)
    _, out = B.run_tree(bad, XDIR)
    cands, items, pj, _ = B.build_request(c, bad, B.github_log(out), JS, WORK)
    assert cands == R["candidates"][name], "candidates differ from the kernel's for %s: %s vs %s" % (
        name, cands, R["candidates"][name])
    built[name] = (good, bad, items, pj)

agree = disagree = truncated = 0
rescored = []
for r in R["runs"]:
    if "error" in r:
        rescored.append(r); continue
    truncated += bool(r.get("raw_truncated"))
    good, bad, items, pj = built[r["case"]]
    s = B.score(r["raw"], CASES[r["case"]], bad, good, pj, items, JS, XDIR, WORK)
    same = s["functional"] == r["functional"] and s["file"] == r["file"]
    agree += same; disagree += not same
    if not same:
        print("DISAGREE %s %s %s r%d: kernel func=%s file=%s | local func=%s file=%s gate=%s" % (
            r["model"], r["case"], r["arm"], r["rep"], r["functional"], r["file"], s["functional"], s["file"], s["gate"]))
    rescored.append(dict(r, **{k: s[k] for k in ("functional", "file", "applied", "gate")}))

S = B.summarize(rescored, R["bench"])
rank, verdict, pick = B.decide(S, R["models"][0])
same_decision = rank == R["ranking"] and pick == R["recommended"] and all(
    S[k][f] == R["summary"][k][f] for k in S for f in ("f_heals", "t_heals", "control"))
print("runs %d | errors %d | truncated raws %d | agree %d / %d (%.1f%%) | decision reproduced: %s" % (
    len(R["runs"]), sum("error" in r for r in R["runs"]), truncated, agree, agree + disagree,
    100.0 * agree / max(1, agree + disagree), same_decision))
print("ranking %s | recommended %s" % (rank, pick))
sys.exit(0 if disagree == 0 and same_decision and truncated == 0 else 1)
