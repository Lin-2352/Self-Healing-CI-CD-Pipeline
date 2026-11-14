"""Validate the eval's scoring and control logic before any GPU is spent.

Everything tested here is a failure that would NOT crash - it would produce a table that looks
fine and ranks the packings wrongly. Those are the ones worth catching in advance:

  * a byte-exact scorer that says True for a file that is not the golden one
  * a normaliser so loose it forgives a real code change
  * a ladder whose tensor rungs pass quantized KV (tensor mode forbids it - the server refuses)
  * a probe that greps one guessed .so and reads "unsupported" (this already happened twice)
  * a summary that hides error rows or truncation

Functions and constants are pulled out of the real file by AST, so the code under test is the
code that ships. The hard cases are validated against the REAL golden file on disk, not a
hand-written fixture.
"""
import ast, io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = io.open(os.path.join(HERE, "quality_speed_eval.py"), encoding="utf-8").read()
tree = ast.parse(src)

WANT_FN = {"norm", "parse_envelope"}
WANT_AS = {"LADDER", "PROOF", "PACKINGS", "NL", "CTX", "REPS"}
keep = [n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in WANT_FN)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in WANT_AS for t in n.targets))]
ns = {"re": re, "json": json}
exec(compile(ast.Module(body=keep, type_ignores=[]), "<t>", "exec"), ns)
norm, parse_envelope = ns["norm"], ns["parse_envelope"]
LADDER, PROOF, PACKINGS = ns["LADDER"], ns["PROOF"], ns["PACKINGS"]
print("pulled LADDER=%d PACKINGS=%d PROOF=%s" % (len(LADDER), len(PACKINGS), sorted(PROOF)))

GOLDEN = io.open(os.path.join(HERE, "cases", "golden_test.js"), encoding="utf-8").read()
HARD = json.load(io.open(os.path.join(HERE, "cases", "hard_cases.json"), encoding="utf-8"))
CTRL = json.load(io.open(os.path.join(HERE, "cases", "eval_cases.json"), encoding="utf-8"))

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("  %-56s %s" % (label, "PASS" if ok else "FAIL got=%r want=%r" % (got, want)))


# ---- 1. byte-exact must be exact, and the normaliser must not forgive real changes ----
print("\nbyte-exact scoring")
check("golden itself is exact", GOLDEN == GOLDEN, True)
check("trailing newline breaks STRICT", (GOLDEN + "\n") == GOLDEN, False)
check("trailing newline forgiven by norm", norm(GOLDEN + "\n") == norm(GOLDEN), True)
check("CRLF forgiven by norm", norm(GOLDEN.replace("\n", "\r\n")) == norm(GOLDEN), True)
# NOTE: this must append spaces at END of a line. A first cut wrote "const http" ->
# "const http   ", which inserts spaces BEFORE the "=" - an internal change, which norm
# correctly rejects. The test was wrong, not the normaliser.
check("trailing spaces forgiven by norm",
      norm(GOLDEN.replace('const http = require("http");',
                          'const http = require("http");   ')) == norm(GOLDEN), True)
check("norm REJECTS mid-line whitespace change",
      norm(GOLDEN.replace("const http =", "const http   =", 1)) == norm(GOLDEN), False)
# the important half: normalisation must NOT forgive anything semantic
for label, bad in [
        ("a real one-char change", GOLDEN.replace("=== 200", "=== 201", 1)),
        ("a deleted line", GOLDEN.replace('const http = require("http");\n', "", 1)),
        ("an inserted statement", GOLDEN.replace("const app = express();",
                                                 "const app = express();\nprocess.exit(0);", 1)),
        ("leading-whitespace change (indentation matters in JS blocks)",
         GOLDEN.replace("  const hint =", "      const hint =", 1)),
        ("empty output", ""),
]:
    check("norm REJECTS %s" % label, norm(bad) == norm(GOLDEN), False)

# every hard case's broken source must itself be non-golden, or the case is vacuous
print("\nhard cases are real")
for c in HARD["cases"]:
    check("%-24s differs from golden" % c["name"], c["code"] == GOLDEN, False)
check("hard set has 6 cases", len(HARD["cases"]), 6)
check("control set has 5 cases", len(CTRL["cases"]), 5)
check("both sets share one golden", HARD["golden"] == CTRL["golden"], True)
check("golden on disk matches the case files", GOLDEN == HARD["golden"], True)
# a log that says nothing makes the case unfair rather than hard
for c in HARD["cases"]:
    body = re.sub(chr(92).join([]) + r"\nError: Process completed.*$", "", c["log"]).strip()
    check("%-24s log has diagnostic content" % c["name"],
          bool(re.fullmatch(r"(CODE_FAILURE|INFRA_FAILURE):\s*", body)), False)

# ---- 2. the fallback ladder ----
print("\nfallback ladder")
names = [n for n, _ in LADDER]
check("ordered best-first", names, ["tensor+mtp", "tensor", "layer+mtp", "layer"])
by = dict(LADDER)
for n in ("tensor+mtp", "tensor"):
    check("%-11s uses f16 KV (tensor mode FORBIDS quantized)" % n,
          ("f16" in by[n], "q8_0" in by[n]), (True, False))
for n in ("layer+mtp", "layer"):
    check("%-11s uses q8_0 KV (production parity)" % n, "q8_0" in by[n], True)
check("mtp rungs pin --parallel 1",
      all("--parallel 1" in by[n] for n in ("tensor+mtp", "layer+mtp")), True)
check("every rung pins --parallel 1", all("--parallel 1" in v for v in by.values()), True)
check("non-mtp rungs carry no spec flag",
      any("spec-type" in by[n] for n in ("tensor", "layer")), False)

# ---- 3. engagement proof strings ----
print("\nengagement proof")
check("tensor needle is the one real logs carry", PROOF["tensor"], "SPLIT_MODE_TENSOR")
check("mtp needle", PROOF["mtp"], "draft-mtp")
logs = os.path.join(os.path.dirname(HERE), "kaggle", "logs", "evals", "qwen3-8-speed-levers-tensor-split-mtp",
                    "2026-09-15", "server_logs")
if os.path.isdir(logs):
    want = {"A_baseline": (False, False), "B_tensor": (True, False),
            "C_mtp": (False, True), "D_both": (True, True)}
    for nm, (wt, wm) in want.items():
        fp = os.path.join(logs, nm + ".log")
        if not os.path.exists(fp):
            check("fixture %s.log present" % nm, False, True); continue
        t = io.open(fp, encoding="utf-8", errors="replace").read()
        check("%-12s tensor=%-5s mtp=%s" % (nm, wt, wm),
              (PROOF["tensor"] in t, PROOF["mtp"] in t), (wt, wm))
else:
    check("kout_speed log fixtures present", False, True)

# ---- 4. packings: all four must be Qwen3.8-27B, and Bonsai must be flagged fork-only ----
print("\npackings")
check("four packings", len(PACKINGS), 4)
fork_only = {n for n, _, _, needs in PACKINGS if needs}
check("both Bonsai packings require the fork", fork_only, {"PQ2_0", "PTQ1_0"})
check("Q4/Ridge not marked fork-only",
      {n for n, _, _, needs in PACKINGS if not needs}, {"Q4_K_XL", "Ridge"})
for n, repo, pat, _ in PACKINGS:
    check("%-8s repo is a Qwen3.8-27B source" % n,
          bool(re.search(r"Qwen3\.8-27B|Bonsai-2-27B", repo, re.I)), True)

# ---- 5. envelope parsing ----
print("\nenvelope parsing")
good = json.dumps({"fixed_code": GOLDEN, "branch_name": "ai-fix-x"})
check("plain JSON", (parse_envelope(good) or {}).get("fixed_code") == GOLDEN, True)
check("fenced JSON", (parse_envelope("```json\n" + good + "\n```") or {}).get("fixed_code") == GOLDEN, True)
check("with <think> preamble",
      (parse_envelope("<think>hmm</think>" + good) or {}).get("fixed_code") == GOLDEN, True)
check("prose, not JSON", parse_envelope("here is your fix!"), None)
check("empty", parse_envelope(""), None)

# ---- 6. summary must expose the ways a cell can be quietly wrong ----
print("\nsummary exposes failure modes")
for field in ('"n": len(rs)', '"truncated": sum(', '"control_exact"', '"hard_exact"',
              '"byte_exact_norm"', '"raw": txt[:4000]', '"raw_truncated"'):
    check("eval records %s" % field[:34], field in src, True)
check("summary line prints n", "n=" in src or '%5d' in src, True)

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
