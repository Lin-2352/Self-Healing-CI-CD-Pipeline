"""Model bake-off: which Qwen3.8-27B build heals best, fast, on Kaggle 2xT4? (one session, models in turn)

For each model in kaggle/endpoint/models.json (baseline first): download -> boot with the PRODUCTION
flags (-sm tensor, f16 KV, MTP n=2, --reasoning-budget 1024, ctx 131072 with the endpoint's fallbacks)
-> speed bench -> heal eval -> delete the file before the next download.

The heal eval is eval/multifile_eval.py's tested path, inlined by eval/build_bakeoff_eval.py: the
REAL Pick Candidate Files -> fetch -> Build AI Prompt -> model -> Extract AI Response, scored by the
demo's real test suite on the patched tree (exit 0 = healed). Cases: 5 control + 6 hard single-file
(both are test.js in the golden tree) + 6 multi-file.

Arms (REPS reps each):
  F  thinking off, request_body verbatim  (production's first heal)   - all 17 cases
  T  thinking on                           (production's retry)        - 6 hard + 6 multi

Decision rule, fixed BEFORE the data (decide() below):
  must pass  controls F 10/10 | MTP accept >= 0.5 | tensor split engaged | thinking really off in F
             (no reasoning_content and no <think> in any F reply - DavidAU ships custom templates)
  rank       F heals on the 24 hard+multi calls, then T heals, then median F call time
  switch     a candidate replaces the baseline only with >= 3 more F heals AND bench tok/s >= 95% of
             the baseline's (5% = run-to-run noise seen on the same build)

TEMPLATE: eval/build_bakeoff_eval.py fills the placeholders and the inline line. The pure functions
(cases, summarize, decide) are importable for eval/test_bakeoff_eval.py and the local re-score.
"""
import base64, datetime, glob, json, os, re, shutil, subprocess, tempfile, time, urllib.request
from multifile_eval import *  # noqa: F401,F403  INLINED-BY-BUILDER

PORT = 8000
API_KEY = "bakeoff-eval-key"
REPS = 2
THINK_BUDGET = 1024
LADDER = [131072, 65536, 32768]
RUNG_TIMEOUT = 300
DEADLINE_S = 10.5 * 3600          # Kaggle kills a session at 12 h; stop starting new models before
SWITCH_MARGIN = 3
SPEED_FLOOR = 0.95


def bakeoff_cases(MF, CONTROL, HARD):
    """All cases as trees. Control and hard cases replace test.js of the golden tree."""
    assert CONTROL["golden"] == HARD["golden"] == MF["golden_tree"]["test.js"], "golden test.js differs"
    single = lambda c, s: dict(name=c["name"], set=s, files={"test.js": c["code"]}, changed=["test.js"],
                               fixable_in=["test.js"])
    return ([single(c, "control") for c in CONTROL["cases"]] + [single(c, "hard") for c in HARD["cases"]] +
            [dict(c, set="multi") for c in MF["cases"]])


def arms_for(case):
    return ["F"] if case["set"] == "control" else ["F", "T"]


def thinking_leak(msg):
    """Reasoning characters in a reply that asked for no thinking (0 = template honoured it)."""
    content = msg.get("content") or ""
    m = re.search(r"<think>(.*?)(</think>|$)", content, re.S)
    return len(msg.get("reasoning_content") or "") + (len(m.group(1)) + 1 if m else 0)


def summarize(runs, bench):
    """Per-model totals from run records + bench. Pure: the local re-score calls it on re-scored runs."""
    S = {}
    for key in sorted({r["model"] for r in runs} | set(bench)):
        rs = [r for r in runs if r["model"] == key and "error" not in r]
        F = [r for r in rs if r["arm"] == "F"]
        T = [r for r in rs if r["arm"] == "T"]
        hm = [r for r in F if r["set"] in ("hard", "multi")]
        ctl = [r for r in F if r["set"] == "control"]
        b = bench.get(key) or {}
        walls = sorted(r["wall_s"] for r in F)
        S[key] = {"control": "%d/%d" % (sum(r["functional"] for r in ctl), len(ctl)),
                  "control_ok": bool(ctl) and all(r["functional"] for r in ctl),
                  "f_heals": sum(r["functional"] for r in hm), "f_calls": len(hm),
                  "t_heals": sum(r["functional"] for r in T), "t_calls": len(T),
                  "f_median_s": walls[len(walls) // 2] if walls else None,
                  "t_median_s": sorted(r["wall_s"] for r in T)[len(T) // 2] if T else None,
                  "think_leak_max": max([r.get("think_leak", 0) for r in F] or [0]),
                  "errors": sum(1 for r in runs if r["model"] == key and "error" in r),
                  "tps": b.get("avg"), "accept_rate": b.get("accept_rate"),
                  "tensor_split": b.get("tensor_split_engaged"), "ctx": b.get("ctx"),
                  "boot_error": b.get("error")}
    return S


def decide(S, baseline):
    """The fixed rule. Returns (ranking, verdict per model, recommended key)."""
    verdict = {}
    for k, s in S.items():
        why = []
        if s["boot_error"]: why.append("did not boot: " + s["boot_error"])
        if not s["control_ok"]: why.append("controls " + s["control"])
        if (s["accept_rate"] or 0) < 0.5: why.append("MTP accept %s < 0.5" % s["accept_rate"])
        if not s["tensor_split"]: why.append("tensor split not engaged")
        if s["think_leak_max"] > 0: why.append("thinking not off (%d chars)" % s["think_leak_max"])
        if s["f_calls"] == 0: why.append("no scored calls")
        verdict[k] = {"eligible": not why, "why": why}
    ok = [k for k in S if verdict[k]["eligible"]]
    ranking = sorted(ok, key=lambda k: (-S[k]["f_heals"], -S[k]["t_heals"], S[k]["f_median_s"] or 1e9))
    pick = baseline
    b = S.get(baseline)
    for k in ranking:
        if k == baseline:
            break
        if b and verdict.get(baseline, {}).get("eligible"):
            if S[k]["f_heals"] >= b["f_heals"] + SWITCH_MARGIN and (S[k]["tps"] or 0) >= SPEED_FLOOR * (b["tps"] or 0):
                pick = k; break
        else:                               # baseline itself failed a gate: best eligible wins
            pick = k; break
    return ranking, verdict, pick


def main():
    OUT, TMP = "/kaggle/working", "/kaggle/tmp"
    T0 = time.time()
    MF = json.loads(base64.b64decode("__MULTI_B64__").decode("utf-8"))
    CONTROL = json.loads(base64.b64decode("__CONTROL_B64__").decode("utf-8"))
    HARD = json.loads(base64.b64decode("__HARD_B64__").decode("utf-8"))
    MODELS = json.loads(base64.b64decode("__MODELS_B64__").decode("utf-8"))   # [(key, spec)], baseline first
    js = {k: base64.b64decode(v).decode("utf-8") for k, v in {
        "pick": "__PICK_JS_B64__", "build": "__BUILD_JS_B64__", "extract": "__EXTRACT_JS_B64__",
        "runner": "__RUNNER_B64__"}.items()}
    G = MF["golden_tree"]
    cases = bakeoff_cases(MF, CONTROL, HARD)
    os.makedirs(TMP, exist_ok=True)
    work = tempfile.mkdtemp(dir=TMP)

    def banner(m):
        print(NL + "=" * 78 + NL + m + NL + "=" * 78, flush=True)

    def vram_per_gpu():
        _, o, _ = sh("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
        try: return [int(x) for x in o.split()]
        except Exception: return []

    _, gpu, _ = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader")
    vram = sum(int(r.split(",")[1].split()[0]) for r in gpu.strip().split(NL) if "," in r)
    print("GPU:", gpu.strip().replace(NL, " | "), "| total", vram, "MiB", flush=True)
    if vram < 24000:
        raise SystemExit("ABORT: need 2xT4 - check machine_shape")

    banner("PREFLIGHT: express + every tree")
    xdir = os.path.join(TMP, "fx"); os.makedirs(xdir, exist_ok=True)
    rc, _, _ = sh("npm init -y >/dev/null && npm install express@4 --no-audit --no-fund", cwd=xdir, timeout=300)
    print("npm install express rc=%d" % rc, flush=True)
    pre = {"golden": run_tree(G, xdir)[0], "cases": {}}
    logs = {}
    for c in cases:
        good, bad = case_trees(c, G)
        b, bout = run_tree(bad, xdir)
        pre["cases"][c["name"]] = {"golden": run_tree(good, xdir)[0], "broken": b}
        logs[c["name"]] = github_log(bout)
    print(json.dumps(pre), flush=True)
    if pre["golden"] != 0 or any(v["golden"] != 0 or v["broken"] != 1 for v in pre["cases"].values()):
        raise SystemExit("ABORT: preflight failed - scores would be meaningless")

    built = {}
    for c in cases:
        _, bad = case_trees(c, G)
        cands, items, pj, res = build_request(c, bad, logs[c["name"]], js, work)
        assert pj, "Build AI Prompt failed on %s: %s" % (c["name"], res.get("error"))
        built[c["name"]] = (cands, items, pj)

    banner("SERVER BINARY")
    binp = OUT + "/bin"
    shutil.rmtree(binp, ignore_errors=True); os.makedirs(binp)
    zips = glob.glob("/kaggle/input/**/llama-server-cuda.zip", recursive=True)
    assert zips, "attach kernel_sources = linsxav/llama-server-builder"
    shutil.unpack_archive(zips[0], binp)
    for f in glob.glob(binp + "/*"):
        os.chmod(f, 0o755)
    env = "LD_LIBRARY_PATH=%s:$LD_LIBRARY_PATH " % binp
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi()

    R = {"started": datetime.datetime.now(datetime.timezone.utc).isoformat(), "reps": REPS, "preflight": pre,
         "models": [k for k, _ in MODELS], "specs": dict(MODELS), "bench": {}, "runs": [],
         "candidates": {k: v[0] for k, v in built.items()}}

    def save():
        json.dump(R, open(OUT + "/results.json", "w"), indent=1)

    def healthy(timeout):
        t = time.time()
        while time.time() - t < timeout:
            try:
                if urllib.request.urlopen("http://localhost:%d/health" % PORT, timeout=5).status == 200:
                    return True
            except Exception:
                if not sh("pgrep -f llama-server")[1].strip():
                    return False
                time.sleep(3)
        return False

    def ask(body, timeout=1800):
        req = urllib.request.Request("http://localhost:%d/v1/chat/completions" % PORT, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
        t = time.time()
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read()), time.time() - t

    for key, spec in MODELS:
        if time.time() - T0 > DEADLINE_S:
            R["bench"][key] = {"error": "skipped: session deadline"}; save(); continue
        banner("MODEL %s  (%s)" % (key, spec["repo"]))
        sh("pkill -f llama-server; sleep 3")
        for old in glob.glob(TMP + "/models/**/*.gguf", recursive=True):
            os.remove(old)
        try:
            hits = sorted(f for f in api.list_repo_files(spec["repo"]) if re.search(spec["pattern"], f))
            assert len(hits) == 1, "pattern matched %s" % hits
            t = time.time()
            path = hf_hub_download(spec["repo"], hits[0], local_dir=TMP + "/models")
            dl = round(time.time() - t)
            print("  got %s %.1f GB in %ds" % (hits[0], os.path.getsize(path) / 1e9, dl), flush=True)
        except Exception as ex:
            R["bench"][key] = {"error": "download: %s" % str(ex)[:200]}; save(); continue
        b = {"file": hits[0], "gb": round(os.path.getsize(path) / 1e9, 1), "download_s": dl}
        for ctx in LADDER:
            t = time.time()
            os.system(env + "nohup " + binp + "/llama-server -m '%s' --ctx-size %d --n-gpu-layers 999 --flash-attn on "
                      "-sm tensor --cache-type-k f16 --cache-type-v f16 --spec-type draft-mtp --spec-draft-n-max 2 "
                      "--parallel 1 -b 2048 -ub 512 -t 4 -tb 4 -np 1 --jinja --reasoning-budget %d --host 0.0.0.0 "
                      "--port %d --api-key %s > %s/server_%s.log 2>&1 &" % (path, ctx, THINK_BUDGET, PORT, API_KEY, OUT, key))
            if healthy(RUNG_TIMEOUT):
                b.update(ctx=ctx, boot_s=round(time.time() - t)); break
            sh("pkill -f llama-server; sleep 4")
            print("  ctx %d did not boot" % ctx, flush=True)
        else:
            b["error"] = "no context in %s booted" % LADDER
            R["bench"][key] = b; save(); continue
        # speed: same prompt as the endpoint's bench; acceptance + VRAM symmetry are the proof, not log lines
        sp = []
        for i in range(3):
            try:
                j, _ = ask({"model": "local", "max_tokens": 256, "temperature": 0.7, "messages": [{"role": "user",
                           "content": "Write a short technical explanation of how continuous integration pipelines detect broken builds."}],
                           "chat_template_kwargs": {"enable_thinking": False}}, timeout=600)
                tm = j.get("timings") or {}
                dn = tm.get("draft_n")
                sp.append({"tps": tm.get("predicted_per_second"),
                           "accept": round((tm.get("draft_n_accepted") or 0) / dn, 3) if dn else None})
            except Exception as ex:
                sp.append({"error": str(ex)[:120]})
        g = [x["tps"] for x in sp if x.get("tps")]
        acc = [x["accept"] for x in sp if x.get("accept") is not None]
        per = vram_per_gpu()
        b.update(runs=sp, avg=round(sum(g) / len(g), 1) if g else None, accept_rate=round(sum(acc) / len(acc), 3) if acc else None,
                 vram_per_gpu=per, tensor_split_engaged=bool(len(per) == 2 and abs(per[0] - per[1]) <= 64))
        R["bench"][key] = b; save()
        print("  ctx=%s boot %ss | %s tok/s | accept %s | VRAM %s" % (b["ctx"], b["boot_s"], b["avg"], b["accept_rate"], per), flush=True)

        for rep in range(REPS):
            for c in cases:
                for arm in arms_for(c):
                    cands, items, pj = built[c["name"]]
                    good, bad = case_trees(c, G)
                    body = json.loads(json.dumps(pj["request_body"]))
                    body["stream"] = False
                    if arm == "T":
                        body["chat_template_kwargs"] = {"enable_thinking": True}
                    base = {"model": key, "arm": arm, "case": c["name"], "set": c["set"], "rep": rep}
                    try:
                        j, wall = ask(body)
                    except Exception as ex:
                        R["runs"].append(dict(base, error=str(ex)[:200])); save(); continue
                    msg = j["choices"][0]["message"]; content = msg.get("content") or ""
                    s = score(content, c, bad, good, pj, items, js, xdir, work)
                    tim = j.get("timings") or {}
                    rec = dict(base, completion_tokens=(j.get("usage") or {}).get("completion_tokens"),
                               finish_reason=j["choices"][0].get("finish_reason"), wall_s=round(wall, 1),
                               tps=round(tim.get("predicted_per_second") or 0, 2),
                               think_leak=thinking_leak(msg) if arm == "F" else None,
                               reasoning_chars=len(msg.get("reasoning_content") or ""),
                               raw=content[:8000], raw_truncated=len(content) > 8000)
                    rec.update(s)
                    R["runs"].append(rec); save()
                    print("%-24s r%d %-7s %-30s %s func=%d file=%-16s tok=%-5s %5.1fs %s" % (
                        key, rep, c["set"], c["name"], arm, s["functional"], s["file"], rec["completion_tokens"],
                        wall, (s["gate"] or "")[:50]), flush=True)
        sh("pkill -f llama-server")

    banner("SUMMARY (rule fixed before the data)")
    S = summarize(R["runs"], R["bench"])
    ranking, verdict, pick = decide(S, MODELS[0][0])
    for k in R["models"]:
        s = S.get(k) or {}
        print("%-24s ctl %-5s F %2s/%-2s T %2s/%-2s  Fmed %-5s tok/s %-5s acc %-5s split %-5s leak %-4s %s" % (
            k, s.get("control"), s.get("f_heals"), s.get("f_calls"), s.get("t_heals"), s.get("t_calls"),
            s.get("f_median_s"), s.get("tps"), s.get("accept_rate"), s.get("tensor_split"), s.get("think_leak_max"),
            "; ".join(verdict.get(k, {}).get("why", []))), flush=True)
    print("RANKING:", ranking, "| RECOMMENDED:", pick, flush=True)
    R.update(summary=S, ranking=ranking, verdict=verdict, recommended=pick,
             finished=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save()


if __name__ == "__main__" and os.path.isdir("/kaggle"):
    main()
