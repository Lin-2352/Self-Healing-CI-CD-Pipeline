"""Re-score every stored raw from the shipped-quality kernel FROM SCRATCH and require 100% agreement.

The in-kernel scorer decided each verdict once; this recomputes applied / functional / byte_exact /
cheat locally (same functions, same node bodies, local express) from nothing but the raw model
content. Any disagreement means a verdict depended on the kernel environment, not the answer.

Run:  python eval/rescore_shipped.py eval/runs/kout_shipped/results.json
"""
import io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import shipped_quality_eval as E                    # noqa: E402
from heal_harness import RUNNER                     # noqa: E402

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
CONTROL = json.loads(rd("eval", "cases", "eval_cases.json")); HARD = json.loads(rd("eval", "cases", "hard_cases.json"))
GOLDEN = CONTROL["golden"]
CASES = {c["name"]: c for c in CONTROL["cases"] + HARD["cases"]}
BUILD, EXTRACT = rd("tools", "nodes", "build_ai_prompt.js"), rd("tools", "nodes", "extract_ai_response.js")
XDIR, WORK = os.path.join(ROOT, "app"), tempfile.mkdtemp()

R = json.load(io.open(sys.argv[1], encoding="utf-8"))
runs = [r for r in R["runs"] if "error" not in r]
agree, bad, skipped = 0, [], 0
for i, r in enumerate(runs):
    if r.get("raw_truncated"):
        skipped += 1; continue
    c = CASES[r["case"]]
    pj = E.first_json(E.run_node(RUNNER, BUILD, E.prompt_ctx(c, r["arm"] != "A"), WORK))
    s = E.score(r["raw"], c, pj, GOLDEN, RUNNER, EXTRACT, XDIR, WORK, 6000 + i)
    keys = ("applied", "functional", "byte_exact", "cheat")
    if all(bool(s[k]) == bool(r[k]) for k in keys):
        agree += 1
    else:
        bad.append((r["arm"], r["case"], r["rep"], {k: (r[k], s[k]) for k in keys}))
print("re-scored %d | agree %d | disagree %d | skipped (truncated raw) %d"
      % (len(runs) - skipped, agree, len(bad), skipped))
for b in bad:
    print("  DISAGREE", b)
sys.exit(0 if not bad and not skipped else 1)
