"""Run the Phase 6 eval against an ALREADY-RUNNING live endpoint instead of a new Kaggle kernel.

Same model, same production server flags (the live endpoint is kaggle/endpoint/deploy_src.py), same real
node code and scoring as eval/multifile_eval.py - only the transport differs: requests are STREAMED
through the Cloudflare tunnel exactly as production sends them, and the SSE is reassembled here.
Saves after every call, fast arms first, so a closing serving window loses as little as possible.

Usage: python eval/run_multifile_eval_remote.py [arm ...]      (default: M B MT)
Output: eval/runs/kout_multifile/results.json   (re-score with eval/rescore_multifile.py)
"""
import io, json, os, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "tools"))
import multifile_eval as E                          # noqa: E402
from heal_harness import RUNNER                     # noqa: E402

URL = open(os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n", "qwen_url.txt")).read().strip()
KEY = "selfheal-e2e-key"
ARMS = sys.argv[1:] or ["M", "B", "MT"]
OUT = os.path.join(ROOT, "eval", "runs", "kout_multifile", "results.json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

rd = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")
MF = json.loads(rd("eval", "cases", "multifile_cases.json")); CONTROL = json.loads(rd("eval", "cases", "eval_cases.json"))
G = MF["golden_tree"]
JS = {"pick": rd("tools", "nodes", "pick_candidate_files.js"), "build": rd("tools", "nodes", "build_ai_prompt.js"),
      "extract": rd("tools", "nodes", "extract_ai_response.js"), "runner": RUNNER}
CASES = [dict(c, set="multi") for c in MF["cases"]] + \
        [dict(name=c["name"], set="control", files={"test.js": c["code"]}, changed=["test.js"], fixable_in=["test.js"])
         for c in CONTROL["cases"]]
XDIR, WORK = os.path.join(ROOT, "app"), tempfile.mkdtemp()


def ask_streamed(body):
    body = dict(body, stream=True)
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    t = time.time()
    raw = urllib.request.urlopen(req, timeout=900).read().decode("utf-8", "replace")
    content, finish, timings, usage = "", None, {}, {}
    for line in raw.splitlines():
        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
            continue
        ev = json.loads(line[5:])
        c = (ev.get("choices") or [{}])[0]
        content += (c.get("delta") or {}).get("content") or ""
        finish = c.get("finish_reason") or finish
        timings = ev.get("timings") or timings
        usage = ev.get("usage") or usage
    return content, finish, timings, usage, time.time() - t


R = json.load(io.open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"runs": [], "endpoint": "live (streamed)"}
done = {(r["arm"], r["case"], r["rep"]) for r in R["runs"] if "error" not in r}
built = {}
for c in CASES:
    good, bad = E.case_trees(c, G)
    _, out = E.run_tree(bad, XDIR)
    built[c["name"]] = E.build_request(c, bad, E.github_log(out), JS, WORK)[:3]
R["candidates"] = {k: v[0] for k, v in built.items()}

if __name__ == "__main__":
    for arm in ARMS:
        for rep in range(E.REPS):
            for c in CASES:
                if (arm in ("M", "MT")) != (c["set"] == "multi") or (arm, c["name"], rep) in done:
                    continue
                cands, items, pj = built[c["name"]]
                good, bad = E.case_trees(c, G)
                body = json.loads(json.dumps(pj["request_body"]))
                if arm == "MT":
                    body["chat_template_kwargs"] = {"enable_thinking": True}
                try:
                    content, finish, tim, usage, wall = ask_streamed(body)
                except Exception as ex:
                    R["runs"].append({"arm": arm, "case": c["name"], "set": c["set"], "rep": rep, "error": str(ex)[:200]})
                    io.open(OUT, "w", encoding="utf-8").write(json.dumps(R, indent=1)); print("ERROR", ex, flush=True); continue
                s = E.score(content, c, bad, good, pj, items, JS, XDIR, WORK)
                rec = {"arm": arm, "case": c["name"], "set": c["set"], "rep": rep, "candidates": cands,
                       "completion_tokens": (usage or {}).get("completion_tokens") or tim.get("predicted_n"),
                       "finish_reason": finish, "wall_s": round(wall, 1), "tps": round(tim.get("predicted_per_second") or 0, 2),
                       "raw": content[:8000], "raw_truncated": len(content) > 8000}
                rec.update(s)
                R["runs"].append(rec)
                io.open(OUT, "w", encoding="utf-8").write(json.dumps(R, indent=1))
                print("r%d %-2s %-30s func=%d file=%-18s right=%d tok=%-5s %5.1fs %s" % (
                    rep, arm, c["name"], s["functional"], s["file"], s["right_file"], rec["completion_tokens"], wall,
                    (s["gate"] or "")[:50]), flush=True)

    for arm in ARMS:
        rs = [r for r in R["runs"] if r["arm"] == arm and "error" not in r]
        if rs:
            print("ARM %-2s healed %d/%d  right file %d/%d" % (arm, sum(r["functional"] for r in rs), len(rs),
                                                             sum(r["right_file"] for r in rs), len(rs)))
