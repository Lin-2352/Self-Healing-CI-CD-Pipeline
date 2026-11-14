"""Phase 6 eval: can production heal bugs that span files? (Kaggle 2xT4, production server flags)

Everything production-facing is the REAL node code, run under the heal-harness runner:
  Pick Candidate Files  <- the case's real CI output, re-rooted at GitHub's runner checkout path
  Fetch File Content    <- emulated: GitHub contents-API items (path + base64) for each candidate
  Build AI Prompt       -> the model request, verbatim except stream=false (localhost, no tunnel)
  Extract AI Response   -> applies the edits to the file the model names (must be one it was shown)

Scoring is the real test suite on the patched TREE: `node test.js && node test/api.test.js`, exit 0
= healed. Also recorded: whether the model picked a file that CAN fix it (case["fixable_in"]).

Arms:
  M   multi-file cases, thinking off  (a first heal in production)
  MT  multi-file cases, thinking on   (a retry in production)
  B   the 5 single-file control cases, thinking off - regression gate, must stay 15/15

Decision rule (fixed before the data): multi-file healing ships if M heals >= 12/18 and B stays 15/15.

TEMPLATE: eval/build_multifile_eval.py fills the __*_B64__ placeholders. The helper functions are
importable for the local self-test (eval/test_multifile_eval.py) and re-score (eval/rescore_multifile.py).
"""
import base64, datetime, glob, json, os, re, shutil, subprocess, tempfile, time, urllib.request

NL = chr(10)
PORT = 8000
API_KEY = "multifile-eval-key"
CTX = 8192
REPS = 3
THINK_BUDGET = 1024
RUNNER_ROOT = "/home/runner/work/self-healing-cicd-demo/self-healing-cicd-demo/"
TS = "2026-09-28T00:00:00.0000000Z "
_port = [5700]


def sh(cmd, timeout=None, cwd=None):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def run_node(runner_js, body_js, ctx, workdir):
    rp, bp, cp = (os.path.join(workdir, n) for n in ("_runner.js", "_body.js", "_ctx.json"))
    open(rp, "w", encoding="utf-8", newline=NL).write(runner_js)
    open(bp, "w", encoding="utf-8", newline=NL).write(body_js)
    open(cp, "w", encoding="utf-8", newline=NL).write(json.dumps(ctx))
    p = subprocess.run(["node", rp, bp, cp], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "error": {"message": "runner: " + (p.stdout or p.stderr)[:300]}, "output": None}


def first_json(res):
    out = res.get("output")
    return out[0].get("json") if isinstance(out, list) and out and isinstance(out[0], dict) else None


def all_json(res):
    out = res.get("output")
    return [o.get("json") for o in out if isinstance(o, dict)] if isinstance(out, list) else []


def reroot(out, tree_dir, app_dir):
    """Local paths -> GitHub runner checkout paths, so the log looks exactly like CI's."""
    out = out.replace("\\\\", "\\")                     # node prints paths in error objects JSON-style
    for base in (tree_dir, app_dir):                     # tree dir first: it sits inside app_dir
        parts = [p for p in re.split(r"[\\/]", os.path.normpath(base)) if p]
        pat = ("/" if base.startswith("/") else "") + r"[\\/]".join(re.escape(p) for p in parts)
        out = re.sub(pat + r"[\\/]([^\s()'\":]+)", lambda m: RUNNER_ROOT + m.group(1).replace("\\", "/"), out)
    return out


def run_tree(files, xdir):
    """`npm test` of the demo on a full file tree, in a temp dir under xdir (which has express).
    Returns (exit_code, output with runner paths)."""
    d = tempfile.mkdtemp(dir=xdir, prefix="_tree_")
    try:
        for p, c in files.items():
            fp = os.path.join(d, *p.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w", encoding="utf-8", newline=NL).write(c)
        _port[0] += 2
        env = dict(os.environ, PORT=str(_port[0]), API_PORT=str(_port[0] + 1))
        out = ""
        for script in ("test.js", os.path.join("test", "api.test.js")):
            try:
                p = subprocess.run(["node", script], cwd=d, env=env, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                return -1, reroot(out + "\n%s: timeout" % script, d, xdir)
            out += p.stdout + p.stderr
            if p.returncode != 0:
                return p.returncode, reroot(out, d, xdir)
        return 0, reroot(out, d, xdir)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def github_log(output):
    lines = ["##[group]Run npm test", "npm test", "shell: /usr/bin/bash -e {0}", "##[endgroup]",
             "> self-healing-cicd-demo@1.0.0 test", "> node test.js && node test/api.test.js", ""] + \
        output.rstrip(NL).split(NL) + \
        ["npm test exited 1 -> failure_kind=code", "##[error]Process completed with exit code 1.",
         "Post job cleanup."]
    return "".join(TS + l + NL for l in lines)


def contents_item(path, tree):
    if path not in tree:
        return {"error": {"message": "404 - Not Found", "name": "NodeApiError"}}
    return {"path": path, "type": "file", "encoding": "base64",
            "content": base64.b64encode(tree[path].encode("utf-8")).decode("ascii")}


def build_request(case, broken, log, js, workdir):
    """Production's front half on one case: Pick -> fetch each candidate -> Build. Returns
    (candidates, fetched items, Build AI Prompt output)."""
    hook = {"run_id": "1", "repo": "demo/app", "branch": "main", "commit": "abc1234def5678",
            "actor": "dev", "failure_kind": "code"}
    changed = {"files": [{"filename": p, "status": "modified"} for p in case["changed"]]}
    pick = run_node(js["runner"], js["pick"], {"input": {}, "nodes": {
        "Download Job Log": {"data": log}, "Fetch Failed Job Log": {"statusCode": 302, "headers": {}, "body": ""},
        "Fetch Changed Files": changed}}, workdir)
    cands = [j["path"] for j in all_json(pick) if j]
    items = [contents_item(p, broken) for p in cands]
    res = run_node(js["runner"], js["build"], {"input": {}, "nodes": {
        "GitHub Failure Webhook": {"body": hook},
        "Fetch Job Logs": {"jobs": [{"id": 1, "name": "build-and-test", "conclusion": "failure", "status": "completed",
                                     "steps": [{"name": "Run tests", "conclusion": "failure",
                                                "started_at": "2026-09-28T00:00:00Z", "completed_at": "2026-09-28T00:00:05Z"}]}]},
        "Fetch Failed Job Log": {"statusCode": 302, "headers": {"location": "https://blob/x"}, "body": ""},
        "Download Job Log": {"data": log}, "Fetch Changed Files": changed,
        "Fetch File Content": items}}, workdir)
    return cands, items, first_json(res), res


def score(content, case, broken, golden, pj, items, js, xdir, workdir):
    """Everything that decides pass/fail, from the raw model content alone (so a local re-score must agree)."""
    res = run_node(js["runner"], js["extract"], {
        "input": {"choices": [{"message": {"content": content}}]},
        "nodes": {"Build AI Prompt": {k: pj.get(k) for k in ("repo", "commit", "branch", "file_path")},
                  "Fetch File Content": items}}, workdir)
    j = first_json(res)
    if not (res.get("ok") and j):
        return {"applied": False, "gate": ((res.get("error") or {}).get("message", ""))[:200], "functional": False,
                "file": None, "right_file": False, "exact": False, "test_rc": None}
    patched = base64.b64decode(j["base64_content"]).decode("utf-8")
    tree = dict(broken); tree[j["file_path"]] = patched
    rc, out = run_tree(tree, xdir)
    return {"applied": True, "gate": None, "functional": rc == 0, "test_rc": rc, "test_tail": out[-300:],
            "file": j["file_path"], "right_file": j["file_path"] in case.get("fixable_in", [j["file_path"]]),
            "exact": patched == golden.get(j["file_path"])}


def case_trees(case, golden_tree):
    good = dict(golden_tree); good.update(case.get("golden", {}))
    bad = dict(golden_tree); bad.update(case["files"])
    return good, bad


def main():
    OUT, TMP = "/kaggle/working", "/kaggle/tmp"
    MF = json.loads(base64.b64decode("__MULTI_B64__").decode("utf-8"))
    CONTROL = json.loads(base64.b64decode("__CONTROL_B64__").decode("utf-8"))
    js = {k: base64.b64decode(v).decode("utf-8") for k, v in {
        "pick": "__PICK_JS_B64__", "build": "__BUILD_JS_B64__", "extract": "__EXTRACT_JS_B64__",
        "runner": "__RUNNER_B64__"}.items()}
    G = MF["golden_tree"]
    # control cases live in test.js of the same tree
    cases = [dict(c, set="multi") for c in MF["cases"]] + \
            [dict(name=c["name"], set="control", files={"test.js": c["code"]}, changed=["test.js"],
                  fixable_in=["test.js"]) for c in CONTROL["cases"]]
    os.makedirs(TMP, exist_ok=True)
    work = tempfile.mkdtemp(dir=TMP)

    def banner(m):
        print(NL + "=" * 78 + NL + m + NL + "=" * 78, flush=True)

    _, gpu, _ = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader")
    vram = sum(int(r.split(",")[1].split()[0]) for r in gpu.strip().split(NL) if "," in r)
    print("GPU:", gpu.strip().replace(NL, " | "), "| total", vram, "MiB", flush=True)
    if vram < 24000:
        raise SystemExit("ABORT: need 2xT4 - check machine_shape")

    banner("PREFLIGHT: express + every tree")
    xdir = os.path.join(TMP, "fx"); os.makedirs(xdir, exist_ok=True)
    rc, o, e = sh("npm init -y >/dev/null && npm install express@4 --no-audit --no-fund", cwd=xdir, timeout=300)
    print("npm install express rc=%d" % rc, flush=True)
    pre = {"golden": run_tree(G, xdir)[0], "cases": {}}
    logs = {}
    for c in cases:
        good, bad = case_trees(c, G)
        g = run_tree(good, xdir)[0]
        b, bout = run_tree(bad, xdir)
        pre["cases"][c["name"]] = {"golden": g, "broken": b}
        logs[c["name"]] = github_log(bout)
    print(json.dumps(pre), flush=True)
    if pre["golden"] != 0 or any(v["golden"] != 0 or v["broken"] != 1 for v in pre["cases"].values()):
        raise SystemExit("ABORT: preflight failed - scores would be meaningless")

    banner("SERVER: production flags")
    binp = OUT + "/bin"
    shutil.rmtree(binp, ignore_errors=True); os.makedirs(binp)
    cands = glob.glob("/kaggle/input/**/llama-server-cuda.zip", recursive=True)
    assert cands, "attach kernel_sources = linsxav/llama-server-builder"
    shutil.unpack_archive(cands[0], binp)
    for f in glob.glob(binp + "/*"):
        os.chmod(f, 0o755)
    env = "LD_LIBRARY_PATH=%s:$LD_LIBRARY_PATH " % binp
    from huggingface_hub import HfApi, hf_hub_download
    files = [f for f in HfApi().list_repo_files("unsloth/Qwen3.8-27B-GGUF") if re.search(r"UD-Q4_K_XL\.gguf$", f)]
    model = hf_hub_download("unsloth/Qwen3.8-27B-GGUF", sorted(files)[0], local_dir=TMP + "/models")
    os.system(env + "nohup " + binp + "/llama-server -m '%s' --ctx-size %d --n-gpu-layers 999 --flash-attn on "
              "-sm tensor --cache-type-k f16 --cache-type-v f16 --spec-type draft-mtp --spec-draft-n-max 2 "
              "--parallel 1 -b 2048 -ub 512 -t 4 -tb 4 -np 1 --jinja --reasoning-budget %d --host 0.0.0.0 "
              "--port %d --api-key %s > %s/server.log 2>&1 &" % (model, CTX, THINK_BUDGET, PORT, API_KEY, OUT))
    t0 = time.time()
    while time.time() - t0 < 600:
        try:
            if urllib.request.urlopen("http://localhost:%d/health" % PORT, timeout=5).status == 200:
                break
        except Exception:
            time.sleep(5)
    else:
        raise SystemExit("ABORT: server never healthy: " + open(OUT + "/server.log").read()[-1500:])
    print("up in %.0fs" % (time.time() - t0), flush=True)

    def ask(body):
        req = urllib.request.Request("http://localhost:%d/v1/chat/completions" % PORT, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
        t = time.time()
        return json.loads(urllib.request.urlopen(req, timeout=1800).read()), time.time() - t

    banner("PROMPTS: real Pick -> fetch -> Build per case")
    built = {}
    for c in cases:
        _, bad = case_trees(c, G)
        cands, items, pj, res = build_request(c, bad, logs[c["name"]], js, work)
        assert pj, "Build AI Prompt failed on %s: %s" % (c["name"], res.get("error"))
        built[c["name"]] = (cands, items, pj)
        print("%-30s candidates=%s  fixable shown=%s" % (c["name"], cands,
              any(p in cands for p in c.get("fixable_in", []))), flush=True)

    R = {"started": datetime.datetime.now(datetime.timezone.utc).isoformat(), "reps": REPS, "preflight": pre,
         "candidates": {k: v[0] for k, v in built.items()}, "runs": []}

    def save():
        json.dump(R, open(OUT + "/results.json", "w"), indent=1)

    banner("RUN: rep > case > arm")
    for rep in range(REPS):
        for c in cases:
            arms = ["M", "MT"] if c["set"] == "multi" else ["B"]
            for arm in arms:
                cands, items, pj = built[c["name"]]
                good, bad = case_trees(c, G)
                body = json.loads(json.dumps(pj["request_body"]))
                body["stream"] = False
                if arm == "MT":
                    body["chat_template_kwargs"] = {"enable_thinking": True}
                try:
                    j, wall = ask(body)
                except Exception as ex:
                    R["runs"].append({"arm": arm, "case": c["name"], "set": c["set"], "rep": rep, "error": str(ex)[:200]})
                    save(); continue
                msg = j["choices"][0]["message"]; content = msg.get("content") or ""
                s = score(content, c, bad, good, pj, items, js, xdir, work)
                tim = j.get("timings") or {}
                rec = {"arm": arm, "case": c["name"], "set": c["set"], "rep": rep, "candidates": cands,
                       "completion_tokens": (j.get("usage") or {}).get("completion_tokens"),
                       "prompt_tokens": (j.get("usage") or {}).get("prompt_tokens"),
                       "finish_reason": j["choices"][0].get("finish_reason"), "wall_s": round(wall, 1),
                       "tps": round(tim.get("predicted_per_second") or 0, 2),
                       "raw": content[:8000], "raw_truncated": len(content) > 8000}
                rec.update(s)
                R["runs"].append(rec); save()
                print("r%d %-7s %-30s %-2s func=%d file=%-18s right=%d tok=%-5s %5.1fs %s"
                      % (rep, c["set"], c["name"], arm, s["functional"], s["file"], s["right_file"],
                         rec["completion_tokens"], wall, (s["gate"] or "")[:60]), flush=True)

    banner("SUMMARY")
    summary = {}
    for c in cases:
        for arm in (["M", "MT"] if c["set"] == "multi" else ["B"]):
            rs = sorted([r for r in R["runs"] if r["case"] == c["name"] and r["arm"] == arm and "error" not in r],
                        key=lambda r: r["rep"])
            summary["%s|%s" % (c["name"], arm)] = {"set": c["set"], "functional": "".join(str(int(r["functional"])) for r in rs),
                                                   "right_file": "".join(str(int(r["right_file"])) for r in rs),
                                                   "files": [r["file"] for r in rs], "gates": [r["gate"] for r in rs if r["gate"]]}
            print("%-36s func=%s right_file=%s files=%s" % ("%s|%s" % (c["name"], arm), summary["%s|%s" % (c["name"], arm)]["functional"],
                  summary["%s|%s" % (c["name"], arm)]["right_file"], summary["%s|%s" % (c["name"], arm)]["files"]), flush=True)
    for arm in ("M", "MT", "B"):
        rs = [r for r in R["runs"] if r["arm"] == arm and "error" not in r]
        if rs:
            print("ARM %-2s healed %d/%d  right file %d/%d  median %.1fs" % (
                arm, sum(r["functional"] for r in rs), len(rs), sum(r["right_file"] for r in rs), len(rs),
                sorted(r["wall_s"] for r in rs)[len(rs) // 2]), flush=True)
    R["summary"] = summary; save()
    print(NL + "===RESULTS_JSON_BEGIN===" + NL + json.dumps({"summary": summary, "preflight": pre}) + NL +
          "===RESULTS_JSON_END===", flush=True)
    sh("pkill -f llama-server")


if __name__ == "__main__" and os.path.isdir("/kaggle"):
    main()
