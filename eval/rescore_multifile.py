"""Re-score every stored raw from the multi-file kernel FROM SCRATCH (same functions, same node
bodies, local express) and require 100% agreement with the in-kernel verdicts.

Run:  python eval/rescore_multifile.py eval/runs/kout_multifile/results.json
"""
import io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import multifile_eval as E                          # noqa: E402
from heal_harness import RUNNER                     # noqa: E402

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
MF = json.loads(rd("eval", "cases", "multifile_cases.json")); CONTROL = json.loads(rd("eval", "cases", "eval_cases.json"))
G = MF["golden_tree"]
JS = {"pick": rd("tools", "nodes", "pick_candidate_files.js"), "build": rd("tools", "nodes", "build_ai_prompt.js"),
      "extract": rd("tools", "nodes", "extract_ai_response.js"), "runner": RUNNER}
CASES = {c["name"]: dict(c, set="multi") for c in MF["cases"]}
CASES.update({c["name"]: dict(name=c["name"], set="control", files={"test.js": c["code"]}, changed=["test.js"],
                              fixable_in=["test.js"]) for c in CONTROL["cases"]})
XDIR, WORK = os.path.join(ROOT, "app"), tempfile.mkdtemp()

R = json.load(io.open(sys.argv[1], encoding="utf-8"))
built, agree, bad_rows, skipped = {}, 0, [], 0
def retry_input(case, bad, pj, items, m_raw):
    """Rebuild what arm R saw: attempt 1 (arm M's raw) applied to the tree, candidates from its new log."""
    import base64
    ex = E.first_json(E.run_node(RUNNER, JS["extract"], {
        "input": {"choices": [{"message": {"content": m_raw}}]},
        "nodes": {"Build AI Prompt": {k: pj.get(k) for k in ("repo", "commit", "branch", "file_path")},
                  "Fetch File Content": items}}, WORK))
    tree1 = dict(bad); tree1[ex["file_path"]] = base64.b64decode(ex["base64_content"]).decode("utf-8")
    _, out1 = E.run_tree(tree1, XDIR)
    pick = E.run_node(RUNNER, JS["pick"], {"input": {}, "nodes": {
        "Download Job Log": {"data": E.github_log(out1)}, "Fetch Failed Job Log": {"statusCode": 302, "headers": {}, "body": ""},
        "Fetch Changed Files": {"files": [{"filename": ex["file_path"], "status": "modified"}]}}}, WORK)
    cands2 = [j["path"] for j in E.all_json(pick) if j]
    return tree1, cands2, [E.contents_item(p, tree1) for p in cands2]


M_RAW = {(r["case"], r["rep"]): r["raw"] for r in R["runs"] if r.get("arm") == "M" and "error" not in r}
for r in [x for x in R["runs"] if "error" not in x]:
    if r.get("raw_truncated"):
        skipped += 1; continue
    c = CASES[r["case"]]
    good, bad = E.case_trees(c, G)
    if r["case"] not in built:
        _, out = E.run_tree(bad, XDIR)
        built[r["case"]] = E.build_request(c, bad, E.github_log(out), JS, WORK)
    cands, items, pj, _ = built[r["case"]]
    if r["arm"] == "R":
        tree1, cands, items2 = retry_input(c, bad, pj, items, M_RAW[(r["case"], r["rep"])])
        s = E.score(r["raw"], c, tree1, good, pj, items2, JS, XDIR, WORK)
    else:
        s = E.score(r["raw"], c, bad, good, pj, items, JS, XDIR, WORK)
    keys = ("applied", "functional", "right_file", "file")
    if all(s[k] == r[k] for k in keys) and cands == r["candidates"]:
        agree += 1
    else:
        bad_rows.append((r["arm"], r["case"], r["rep"], {k: (r[k], s[k]) for k in keys}, cands, r["candidates"]))
print("re-scored %d | agree %d | disagree %d | skipped %d" % (agree + len(bad_rows), agree, len(bad_rows), skipped))
for b in bad_rows:
    print("  DISAGREE", b)
sys.exit(0 if not bad_rows and not skipped else 1)
