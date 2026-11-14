"""Retry arm (R) of the Phase 6 eval: what production's CI-feedback retry actually sees.

Arm MT re-asks the ORIGINAL input with thinking on; a real retry sees more. For every fast-pass
attempt (arm M) that applied a fix but still failed the tests, this rebuilds the retry exactly:
  tree        the broken tree with attempt 1's patch applied   (what the heal branch holds)
  CI log      that tree's real test output, as GitHub shows it  ("expected X, got Y")
  changed     the file attempt 1 edited                          (the heal commit)
  hook        attempt=1 on an ai-fix-* branch -> the REAL Build AI Prompt switches thinking on
and scores attempt 2 with the real test suite on the whole tree.

Usage: python eval/run_multifile_retry_remote.py        (after run_multifile_eval_remote.py)
Output: appended to eval/runs/kout_multifile/results.json as arm "R"
"""
import base64, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import multifile_eval as E                          # noqa: E402
import run_multifile_eval_remote as RM              # noqa: E402  (loads cases + builds; runs no arms with argv R)

OUT = RM.OUT
R = json.load(io.open(OUT, encoding="utf-8"))
done = {(r["case"], r["rep"]) for r in R["runs"] if r["arm"] == "R" and "error" not in r}
cases = {c["name"]: c for c in RM.CASES}
hook = {"run_id": "2", "repo": "demo/app", "branch": "ai-fix-greet-abc1234", "commit": "def5678abc1234",
        "actor": "dev", "failure_kind": "code", "attempt": "1", "pr_number": "7"}

for m in [r for r in R["runs"] if r["arm"] == "M" and "error" not in r and r["applied"] and not r["functional"]]:
    if (m["case"], m["rep"]) in done:
        continue
    c = cases[m["case"]]
    good, bad = E.case_trees(c, RM.G)
    cands, items, pj = [x for x in E.build_request(c, bad, E.github_log(E.run_tree(bad, RM.XDIR)[1]), RM.JS, RM.WORK)[:3]]
    ex = E.first_json(E.run_node(RM.JS["runner"], RM.JS["extract"], {
        "input": {"choices": [{"message": {"content": m["raw"]}}]},
        "nodes": {"Build AI Prompt": {k: pj.get(k) for k in ("repo", "commit", "branch", "file_path")},
                  "Fetch File Content": items}}, RM.WORK))
    tree1 = dict(bad); tree1[ex["file_path"]] = base64.b64decode(ex["base64_content"]).decode("utf-8")
    rc1, out1 = E.run_tree(tree1, RM.XDIR)
    log1 = E.github_log(out1)
    retry_case = dict(c, changed=[ex["file_path"]])
    # the real front half, with the retry hook (Build AI Prompt reads attempt/branch from it)
    changed = {"files": [{"filename": ex["file_path"], "status": "modified"}]}
    pick = E.run_node(RM.JS["runner"], RM.JS["pick"], {"input": {}, "nodes": {
        "Download Job Log": {"data": log1}, "Fetch Failed Job Log": {"statusCode": 302, "headers": {}, "body": ""},
        "Fetch Changed Files": changed}}, RM.WORK)
    cands2 = [j["path"] for j in E.all_json(pick) if j]
    items2 = [E.contents_item(p, tree1) for p in cands2]
    b = E.run_node(RM.JS["runner"], RM.JS["build"], {"input": {}, "nodes": {
        "GitHub Failure Webhook": {"body": hook},
        "Fetch Job Logs": {"jobs": [{"id": 2, "name": "build-and-test", "conclusion": "failure", "status": "completed",
                                     "steps": [{"name": "Run tests", "conclusion": "failure"}]}]},
        "Fetch Failed Job Log": {"statusCode": 302, "headers": {}, "body": ""},
        "Download Job Log": {"data": log1}, "Fetch Changed Files": changed, "Fetch File Content": items2}}, RM.WORK)
    pj2 = E.first_json(b)
    assert pj2 and pj2["is_retry"] and pj2["request_body"]["chat_template_kwargs"] == {"enable_thinking": True}, b.get("error")
    try:
        content, finish, tim, usage, wall = RM.ask_streamed(pj2["request_body"])
    except Exception as e:
        R["runs"].append({"arm": "R", "case": c["name"], "set": "multi", "rep": m["rep"], "error": str(e)[:200]})
        io.open(OUT, "w", encoding="utf-8").write(json.dumps(R, indent=1)); print("ERROR", e, flush=True); continue
    s = E.score(content, c, tree1, good, pj2, items2, RM.JS, RM.XDIR, RM.WORK)
    rec = {"arm": "R", "case": c["name"], "set": "multi", "rep": m["rep"], "candidates": cands2,
           "attempt1_file": ex["file_path"], "attempt1_test_rc": rc1,
           "completion_tokens": (usage or {}).get("completion_tokens") or tim.get("predicted_n"),
           "finish_reason": finish, "wall_s": round(wall, 1), "tps": round(tim.get("predicted_per_second") or 0, 2),
           "raw": content[:8000], "raw_truncated": len(content) > 8000}
    rec.update(s)
    R["runs"].append(rec)
    io.open(OUT, "w", encoding="utf-8").write(json.dumps(R, indent=1))
    print("r%d R  %-30s func=%d file=%-18s cands=%s tok=%-5s %5.1fs %s" % (
        m["rep"], c["name"], s["functional"], s["file"], cands2, rec["completion_tokens"], wall, (s["gate"] or "")[:50]), flush=True)

rs = [r for r in R["runs"] if r["arm"] == "R" and "error" not in r]
print("ARM R  retry heals %d/%d of the applied-but-failed first attempts" % (sum(r["functional"] for r in rs), len(rs)))
