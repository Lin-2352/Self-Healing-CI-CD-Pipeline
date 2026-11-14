"""Shipped-format quality: does production heal the HARD bugs, and does the CI log help?

Every hard-case number so far came from the old whole-file prompt with a hand-written log section.
Production now differs end to end, so this eval runs PRODUCTION ITSELF wherever it can:

  prompt     built by the real tools/nodes/build_ai_prompt.js (run under the heal harness RUNNER),
             including the shipped trimCiLog over a GitHub-style log envelope
  request    the node's own request_body, verbatim (temperature, max_tokens, cache_prompt, ...)
  applier    the real tools/nodes/extract_ai_response.js - exactly-once and parse gates included
  server     production flags from kaggle/endpoint/deploy_src.py (-sm tensor, f16 KV, MTP n=2, parallel 1)

Arms (per case, per rep):
  A  no log      Download Job Log errored -> "(log unavailable)"  (production before round 2)
  B  with log    the case's real node output inside a GitHub log envelope   (production now)
  C  with log + thinking on (hard cases only), budget-capped if the server supports it

Scoring is FUNCTIONAL first: the patched file is run as the real smoke test (`node test.js` with
express installed). Exit 0 = healed. Byte-exact vs golden is a tiebreaker; must/mustnot regexes
flag fixes that pass by weakening the test.

This file is a TEMPLATE: eval/build_shipped_eval.py substitutes the __*_B64__ placeholders and
writes the kernel. The scoring functions are importable (eval/rescore_shipped.py) so every stored
raw can be re-scored locally from scratch.
"""
import base64, datetime, glob, json, os, re, shutil, subprocess, tempfile, time, urllib.request

NL = chr(10)
PORT = 8000
API_KEY = "shipped-eval-key"
CTX = 8192
REPS = 3
THINK_BUDGET = 1024
TS = "2026-09-23T00:00:00.0000000Z "      # runner timestamp prefix; trimCiLog strips it


def sh(cmd, timeout=None, cwd=None):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------------ harness (shared with local)
def run_node(runner_js, body_js, ctx, workdir):
    """Execute a real node body under the heal harness RUNNER. Same contract as heal_harness.run_node."""
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


def github_log(node_output):
    """Wrap a case's real node output the way GitHub's job log presents it, so the SHIPPED trimCiLog
    (not this script) decides what the model sees."""
    lines = (["##[group]Run set +e", "set +e", "npm test", "shell: /usr/bin/bash -e {0}", "##[endgroup]",
              "", "> self-healing-cicd-demo@1.0.0 test", "> node test.js", ""]
             + node_output.rstrip(NL).split(NL)
             + ["", "npm test exited 1 -> failure_kind=code", "##[error]Process completed with exit code 1.",
                "Post job cleanup.", "[command]/usr/bin/git version"])
    return NL.join(TS + l for l in lines) + NL


def prompt_ctx(case, with_log):
    return {"input": {}, "nodes": {
        "GitHub Failure Webhook": {"body": {"run_id": "1", "repo": "demo/app", "branch": "main",
                                            "commit": "abc1234def5678", "actor": "dev", "failure_kind": "code"}},
        "Fetch Job Logs": {"jobs": [{"id": 1, "name": "build-and-test", "conclusion": "failure",
                                     "status": "completed",
                                     "steps": [{"name": "Run tests", "conclusion": "failure",
                                                "started_at": "2026-09-23T00:00:00Z",
                                                "completed_at": "2026-09-23T00:00:05Z"}]}]},
        "Fetch Failed Job Log": {"statusCode": 302, "headers": {"location": "https://blob/x"}, "body": ""},
        "Download Job Log": ({"data": github_log(case["log"])} if with_log
                             else {"error": {"message": "404 - log unavailable"}}),
        "Fetch Changed Files": {"files": [{"filename": "test.js", "status": "modified"}]},
        "Fetch File Content": case["code"]}}


def extract_ctx(content, case, prompt_json):
    return {"input": {"choices": [{"message": {"content": content}}]},
            "nodes": {"Build AI Prompt": {k: prompt_json.get(k) for k in ("repo", "commit", "branch", "file_path")},
                      "Fetch File Content": case["code"]}}


def smoke(js, express_dir, port):
    """The REAL smoke test: run the patched file. 0 = healthy, 1 = code failure, 75 = infra."""
    d = tempfile.mkdtemp(dir=express_dir)
    try:
        open(os.path.join(d, "test.js"), "w", encoding="utf-8", newline=NL).write(js)
        env = dict(os.environ, PORT=str(port), NODE_PATH=os.path.join(express_dir, "node_modules"))
        try:
            p = subprocess.run(["node", "test.js"], cwd=d, env=env, capture_output=True, text=True, timeout=30)
            return p.returncode, (p.stdout + p.stderr)[-300:]
        except subprocess.TimeoutExpired:
            return -1, "timeout (server never exited)"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def score(content, case, prompt_json, golden, runner_js, extract_js, express_dir, workdir, port):
    """Everything that decides pass/fail, from the raw model content alone - so a local re-score
    from stored raws must agree 100% with the in-kernel score."""
    res = run_node(runner_js, extract_js, extract_ctx(content, case, prompt_json), workdir)
    j = first_json(res)
    if not (res.get("ok") and j):
        msg = (res.get("error") or {}).get("message", "")
        return {"applied": False, "gate": msg[:160], "functional": False, "byte_exact": False,
                "must_ok": False, "cheat": False, "smoke_rc": None}
    patched = base64.b64decode(j["base64_content"]).decode("utf-8")
    rc, out = smoke(patched, express_dir, port)
    must_ok = all(re.search(p, patched, re.M) for p in case.get("must", []))
    cheat = any(re.search(p, patched, re.M) for p in case.get("mustnot", []))
    return {"applied": True, "gate": None, "functional": rc == 0, "smoke_rc": rc, "smoke_tail": out,
            "byte_exact": patched == golden, "must_ok": must_ok, "cheat": cheat,
            "edits_applied": j.get("edits_applied")}


# ------------------------------------------------------------------ kernel
def main():
    OUT = "/kaggle/working"
    TMP = "/kaggle/tmp"
    CONTROL = json.loads(base64.b64decode("__CONTROL_B64__").decode("utf-8"))
    HARD = json.loads(base64.b64decode("__HARD_B64__").decode("utf-8"))
    BUILD_JS = base64.b64decode("__BUILD_JS_B64__").decode("utf-8")
    EXTRACT_JS = base64.b64decode("__EXTRACT_JS_B64__").decode("utf-8")
    RUNNER_JS = base64.b64decode("__RUNNER_B64__").decode("utf-8")
    GOLDEN = CONTROL["golden"]
    assert HARD["golden"] == GOLDEN
    CASES = [dict(c, set="control") for c in CONTROL["cases"]] + [dict(c, set="hard") for c in HARD["cases"]]
    os.makedirs(TMP, exist_ok=True)
    work = tempfile.mkdtemp(dir=TMP)

    def banner(m):
        print(NL + "=" * 78 + NL + m + NL + "=" * 78, flush=True)

    # ---- env
    _, gpu, _ = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader")
    vram = sum(int(r.split(",")[1].split()[0]) for r in gpu.strip().split(NL) if "," in r)
    print("GPU:", gpu.strip().replace(NL, " | "), "| total", vram, "MiB", flush=True)
    if vram < 24000:
        raise SystemExit("ABORT: need 2xT4 - check machine_shape")
    print("node:", sh("node --version")[1].strip(), "npm:", sh("npm --version")[1].strip(), flush=True)

    banner("PREFLIGHT: express + the smoke test itself")
    xdir = os.path.join(TMP, "fx")
    os.makedirs(xdir, exist_ok=True)
    rc, o, e = sh("npm init -y >/dev/null && npm install express@4 --no-audit --no-fund", cwd=xdir, timeout=300)
    print("npm install express rc=%d" % rc, (e or o)[-200:], flush=True)
    # The scorer is only meaningful if golden PASSES and every broken case FAILS as a code bug (1).
    g_rc, g_out = smoke(GOLDEN, xdir, 5301)
    pre = {"golden_rc": g_rc, "broken_rc": {}}
    for i, c in enumerate(CASES):
        pre["broken_rc"][c["name"]] = smoke(c["code"], xdir, 5302 + i)[0]
    print("golden rc=%s (want 0) | broken: %s (want all 1)" % (g_rc, pre["broken_rc"]), flush=True)
    if g_rc != 0 or any(v != 1 for v in pre["broken_rc"].values()):
        raise SystemExit("ABORT: scorer preflight failed - functional scores would be meaningless: %s" % g_out)

    # ---- server (production flags)
    banner("SERVER: production flags")
    binp = OUT + "/bin"
    shutil.rmtree(binp, ignore_errors=True); os.makedirs(binp)
    cands = glob.glob("/kaggle/input/**/llama-server-cuda.zip", recursive=True)
    assert cands, "attach kernel_sources = linsxav/llama-server-builder"
    shutil.unpack_archive(cands[0], binp)
    for f in glob.glob(binp + "/*"):
        os.chmod(f, 0o755)
    env = "LD_LIBRARY_PATH=%s:$LD_LIBRARY_PATH " % binp
    _, helptxt, _ = sh(env + binp + "/llama-server --help 2>&1")
    m = re.search(r"--reasoning-budget[^\n]*(\n\s{10,}[^\n]*){0,3}", helptxt)
    budget_help = m.group(0) if m else ""
    # Older builds document "currently only one of: -1 (unrestricted) or 0 (disable)" - a positive
    # budget is NOT supported there. Trust it only if the flag exists without that restriction; the
    # boot below falls back to no flag if the server rejects it anyway.
    budget_ok = bool(budget_help) and not re.search(r"only one of", budget_help, re.I)
    print("reasoning-budget help: %r -> positive budget usable: %s" % (budget_help[:200], budget_ok), flush=True)

    from huggingface_hub import HfApi, hf_hub_download
    files = [f for f in HfApi().list_repo_files("unsloth/Qwen3.8-27B-GGUF") if re.search(r"UD-Q4_K_XL\.gguf$", f)]
    model = hf_hub_download("unsloth/Qwen3.8-27B-GGUF", sorted(files)[0], local_dir=TMP + "/models")
    flags = ("-m '%s' --ctx-size %d --n-gpu-layers 999 --flash-attn on -sm tensor --cache-type-k f16 "
             "--cache-type-v f16 --spec-type draft-mtp --spec-draft-n-max 2 --parallel 1 -b 2048 -ub 512 "
             "-t 4 -tb 4 -np 1 --jinja --host 0.0.0.0 --port %d --api-key %s"
             % (model, CTX, PORT, API_KEY))
    def boot(extra):
        sh("pkill -f llama-server; sleep 3")
        os.system(env + "nohup " + binp + "/llama-server " + flags + extra + " > " + OUT + "/server.log 2>&1 &")
        t = time.time()
        while time.time() - t < 600:
            try:
                if urllib.request.urlopen("http://localhost:%d/health" % PORT, timeout=5).status == 200:
                    return True
            except Exception:
                if not sh("pgrep -f llama-server")[1].strip():
                    return False                    # exited (e.g. rejected a flag) - fail fast
                time.sleep(5)
        return False

    t0 = time.time()
    up = boot(" --reasoning-budget %d" % THINK_BUDGET) if budget_ok else False
    if budget_ok and not up:
        print("server rejected --reasoning-budget %d; booting without it" % THINK_BUDGET, flush=True)
        budget_ok = False
    if not up and not boot(""):
        raise SystemExit("ABORT: server never became healthy:" + open(OUT + "/server.log").read()[-1500:])
    _, vr, _ = sh("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
    print("up in %.0fs, per-GPU VRAM %s (symmetric == tensor split engaged)" % (time.time() - t0, vr.split()), flush=True)

    def ask(body):
        req = urllib.request.Request("http://localhost:%d/v1/chat/completions" % PORT, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
        t = time.time()
        j = json.loads(urllib.request.urlopen(req, timeout=1800).read())
        return j, time.time() - t

    # ---- prompts from the REAL node
    prompts = {}
    for c in CASES:
        for with_log in (False, True):
            r = run_node(RUNNER_JS, BUILD_JS, prompt_ctx(c, with_log), work)
            pj = first_json(r)
            assert r.get("ok") and pj, "Build AI Prompt failed: %s" % r.get("error")
            prompts[(c["name"], with_log)] = pj
    print("prompts built by the real node: %d | example B log section:%s%s" % (
        len(prompts), NL, prompts[(CASES[-1]["name"], True)]["ci_log"][:600]), flush=True)

    arms = [("A", False, False), ("B", True, False), ("C", True, True)]
    R = {"started": datetime.datetime.now(datetime.timezone.utc).isoformat(), "reps": REPS,
         "preflight": pre, "budget_help": budget_help, "budget_server_flag": budget_ok,
         "think_budget": THINK_BUDGET, "vram": vr.split(), "runs": []}

    def save():
        json.dump(R, open(OUT + "/results.json", "w"), indent=1)

    banner("RUN: interleaved rep > case > arm")
    port = 5400
    for rep in range(REPS):
        for c in CASES:
            for arm, with_log, think in arms:
                if arm == "C" and c["set"] != "hard":
                    continue
                pj = prompts[(c["name"], with_log)]
                body = json.loads(json.dumps(pj["request_body"]))      # production body, verbatim
                # ...except transport: production streams to survive the tunnel's 120 s timeout;
                # this eval talks to localhost and parses one JSON reply. Same tokens either way.
                body["stream"] = False
                if think:
                    body["chat_template_kwargs"] = {"enable_thinking": True}
                    if not budget_ok:                                   # coarse cap, recorded as such
                        body["max_tokens"] = THINK_BUDGET + 2048
                try:
                    j, wall = ask(body)
                except Exception as ex:
                    R["runs"].append({"arm": arm, "case": c["name"], "set": c["set"], "rep": rep,
                                      "error": str(ex)[:200]}); save(); continue
                msg = j["choices"][0]["message"]
                content = msg.get("content") or ""
                port += 1
                s = score(content, c, pj, GOLDEN, RUNNER_JS, EXTRACT_JS, xdir, work, port)
                tim = j.get("timings") or {}
                dn, da = tim.get("draft_n"), tim.get("draft_n_accepted")
                rec = {"arm": arm, "case": c["name"], "set": c["set"], "rep": rep,
                       "completion_tokens": (j.get("usage") or {}).get("completion_tokens"),
                       "prompt_tokens": (j.get("usage") or {}).get("prompt_tokens"),
                       "reasoning_chars": len(msg.get("reasoning_content") or ""),
                       "finish_reason": j["choices"][0].get("finish_reason"),
                       "wall_s": round(wall, 1), "tps": round(tim.get("predicted_per_second") or 0, 2),
                       "accept_rate": round(da / dn, 3) if dn else None,
                       "raw": content[:6000], "raw_truncated": len(content) > 6000}
                rec.update(s)
                R["runs"].append(rec); save()
                print("r%d %-7s %-24s %s func=%d exact=%d applied=%d cheat=%d tok=%-4s %5.1fs %s"
                      % (rep, c["set"], c["name"], arm, s["functional"], s["byte_exact"], s["applied"],
                         s["cheat"], rec["completion_tokens"], wall, (s["gate"] or "")[:50]), flush=True)

    # ---- summary: per case, per arm, consistency strings
    summary = {}
    for c in CASES:
        for arm, _, _ in arms:
            rs = sorted([r for r in R["runs"] if r["case"] == c["name"] and r["arm"] == arm and "error" not in r],
                        key=lambda r: r["rep"])
            if rs:
                summary["%s|%s" % (c["name"], arm)] = {
                    "set": c["set"], "functional": "".join(str(int(r["functional"])) for r in rs),
                    "exact": "".join(str(int(r["byte_exact"])) for r in rs),
                    "gates": [r["gate"] for r in rs if r["gate"]]}
    R["summary"] = summary; save()
    banner("SUMMARY")
    for k, v in summary.items():
        print("%-34s %-7s func=%s exact=%s %s" % (k, v["set"], v["functional"], v["exact"], v["gates"][:1]), flush=True)
    for arm, _, _ in arms:
        for st in ("control", "hard"):
            rs = [r for r in R["runs"] if r["arm"] == arm and r["set"] == st and "error" not in r]
            if rs:
                print("ARM %s %-7s functional %d/%d  exact %d/%d  cheat %d" % (
                    arm, st, sum(r["functional"] for r in rs), len(rs), sum(r["byte_exact"] for r in rs),
                    len(rs), sum(r["cheat"] for r in rs)), flush=True)
    print(NL + "===RESULTS_JSON_BEGIN===", flush=True)
    print(json.dumps({"summary": summary, "preflight": pre, "budget_server_flag": budget_ok,
                      "per_call": [{k: r.get(k) for k in ("arm", "case", "set", "rep", "functional", "byte_exact",
                                                           "applied", "cheat", "must_ok", "gate", "completion_tokens",
                                                           "reasoning_chars", "wall_s", "tps", "accept_rate",
                                                           "finish_reason", "error")} for r in R["runs"]]}),
          flush=True)
    print("===RESULTS_JSON_END===", flush=True)
    sh("pkill -f llama-server")


if __name__ == "__main__" and os.path.isdir("/kaggle"):
    main()
