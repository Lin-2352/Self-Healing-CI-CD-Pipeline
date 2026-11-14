"""Quality x speed across four packings of Qwen3.8-27B, on 2xT4.

The champion is UD-Q4_K_XL + `-sm tensor` + `--spec-type draft-mtp` at 26.83 tok/s, 60/60. The
question is whether a SMALLER packing buys anything and at what quality cost - so this ranks on
quality first and speed second, not on tok/s.

Four packings, all Qwen3.8-27B (Bonsai and Ridge are quantizations OF it, not other models):

  UD-Q4_K_XL   17.60 GB  control, already measured
  Ridge        12.60 GB  architecture-aware: keeps the Gated-DeltaNet STATE path at Q8_0 while
                         generic quants damage it. Never tested.
  PQ2_0         7.21 GB  Bonsai ternary, 2.13 bpw, PrismML's preferred CUDA packing
  PTQ1_0        5.95 GB  Bonsai ternary, 1.75 bpw

TWO scoring sets, because the old metric is saturated (every quant down to IQ2_S scored 3/3 on
4 of 5 original cases):
  control (5)  the original cases - a REGRESSION gate, must stay 5/5
  hard    (6)  logs that do not point at the bug; two look like infra faults, one reports
               "unexpected status 200" where 200 is correct, one needs two hunks

Primary score is BYTE-EXACT vs the golden file, not regexes. A regex already passed a file that
failed `node --check` once in this project.

Bonsai needs PrismML's fork; stock llama.cpp loads its Q2_0 sibling WITHOUT WARNING and emits
gibberish, so the runtime is asserted before those cells rather than inferred from output.
"""
import base64, datetime, glob, json, os, re, shutil, subprocess, tarfile, time, urllib.request

NL = chr(10)
OUT = "/kaggle/working"
STOCK = OUT + "/bin_stock"
FORK = OUT + "/bin_fork"
MODELS = "/kaggle/tmp/models"
API_KEY = "qs-eval-key"
PORT = 8000
CTX = 8192
REPS = 3
TEMPERATURE = 0.1
MAX_TOKENS = 2048
FORK_URL = ("https://github.com/PrismML-Eng/llama.cpp/releases/download/prism-b10709-9a9394a/"
            "llama-prism-b10709-9a9394a-bin-linux-cuda-12.4-x64.tar.gz")
os.makedirs(OUT, exist_ok=True)
os.makedirs(MODELS, exist_ok=True)

CONTROL = json.loads(base64.b64decode("__CONTROL_B64__").decode("utf-8"))
HARD = json.loads(base64.b64decode("__HARD_B64__").decode("utf-8"))
GOLDEN = CONTROL["golden"]
assert HARD["golden"] == GOLDEN, "the two case sets must share one golden file"
CASES = ([dict(c, set="control") for c in CONTROL["cases"]] +
         [dict(c, set="hard") for c in HARD["cases"]])

SYS_PROMPT = (
    "You are an expert DevOps engineer and Node.js developer. Analyze the CI/CD failure "
    "logs and the source code, identify the bug, and provide a complete fix.\n"
    "Constraints: return ONLY a raw valid JSON object. No markdown, no code fences, no "
    "conversational text. The branch name must be unique and specific to the fix "
    "(e.g. ai-fix-express-init); never use main or master. fixed_code must be the ENTIRE "
    "file content with the fix applied.\n"
    'JSON schema: {"branch_name": string, "file_path": string, "fixed_code": string, '
    '"pr_title": string, "pr_body": string}'
)


def sh(cmd, timeout=None, cwd=None):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def banner(m):
    print(NL + "=" * 78 + NL + m + NL + "=" * 78, flush=True)


# ---------------------------------------------------------------- 1. environment
_, gpu, _ = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader")
vram = 0
for r in gpu.strip().split(NL):
    try: vram += int(r.split(",")[1].strip().split()[0])
    except Exception: pass
print("GPU:", gpu.strip().replace(NL, " | "), "| total", vram, "MiB", flush=True)
if vram < 24000:
    raise SystemExit("ABORT: need 2xT4, got %d MiB - check machine_shape" % vram)
_, topo, _ = sh("nvidia-smi topo -m")
_, df, _ = sh("df -h /kaggle/tmp /kaggle/working | tail -2")
print("disk:" + NL + df.strip(), flush=True)

banner("BINARY 1: stock prebuilt (for Q4 / Ridge)")
cands = glob.glob("/kaggle/input/**/llama-server-cuda.zip", recursive=True)
if not cands:
    raise SystemExit("attach kernel_sources = linsxav/llama-server-builder")
shutil.rmtree(STOCK, ignore_errors=True)
os.makedirs(STOCK, exist_ok=True)
shutil.unpack_archive(cands[0], STOCK)
for f in glob.glob(STOCK + "/*"):
    try: os.chmod(f, 0o755)
    except OSError: pass
print("stock extracted:", os.path.exists(STOCK + "/llama-server"), flush=True)

banner("BINARY 2: PrismML fork b10709 (REQUIRED for Bonsai)")
shutil.rmtree(FORK, ignore_errors=True)
os.makedirs(FORK, exist_ok=True)
t0 = time.time()
tgz = "/kaggle/tmp/fork.tar.gz"
urllib.request.urlretrieve(FORK_URL, tgz)
with tarfile.open(tgz) as tf:
    tf.extractall(FORK)
inner = [d for d in glob.glob(FORK + "/*") if os.path.isdir(d)]
if inner:
    for f in glob.glob(inner[0] + "/*"):
        shutil.move(f, FORK)
for f in glob.glob(FORK + "/*"):
    try: os.chmod(f, 0o755)
    except OSError: pass
print("fork fetched+extracted in %.0fs; llama-server present: %s"
      % (time.time() - t0, os.path.exists(FORK + "/llama-server")), flush=True)


def probe_binary(path, label):
    """What does this runtime actually support? Probed, never assumed."""
    env = "LD_LIBRARY_PATH=%s:$LD_LIBRARY_PATH " % path
    rc, h, _ = sh(env + path + "/llama-server --help 2>&1")
    info = {"runs": rc == 0}
    for flag in ("--split-mode", "--spec-type", "--spec-draft-n-max"):
        info[flag] = flag in h
    # Quant and feature strings are NOT all in the library you would expect: measured on the
    # b10709 fork, PTQ1_0 lives in libggml-cuda while PQ2_0 is in libggml-base, and draft-mtp is
    # in libllama-common while SPLIT_MODE_TENSOR is in libllama. Grepping one guessed file
    # returns 0 and reads as "unsupported" - which would silently skip Bonsai entirely. So
    # search EVERY shared object. `grep -ac` on the binary is used instead of strings|grep:
    # libggml-cuda is ~567 MB and strings over it takes minutes.
    def hits(needle):
        n = 0
        for f in glob.glob(path + "/*.so*"):
            rc, o, _ = sh("grep -ac -- '%s' '%s' || true" % (needle, f))
            try: n += int((o or "0").strip() or 0)
            except ValueError: pass
        return n
    info["sm_75"] = hits("sm_75")
    info["PTQ1_0"] = hits("PTQ1_0")
    info["PQ2_0"] = hits("PQ2_0")
    info["SPLIT_MODE_TENSOR"] = hits("SPLIT_MODE_TENSOR")
    info["draft-mtp"] = hits("draft-mtp")
    info["nextn"] = hits("nextn")
    print("  %-6s %s" % (label, json.dumps(info)), flush=True)
    return info


banner("PROBE both runtimes")
P_STOCK = probe_binary(STOCK, "stock")
P_FORK = probe_binary(FORK, "fork")
# If the fork has everything, run ALL packings on it: one runtime removes a confound.
FORK_DOES_ALL = bool(P_FORK.get("runs") and P_FORK.get("SPLIT_MODE_TENSOR")
                     and P_FORK.get("draft-mtp") and P_FORK.get("sm_75"))
print(NL + "fork can host every packing (single-runtime comparison): %s" % FORK_DOES_ALL, flush=True)


# ---------------------------------------------------------------- 2. models
PACKINGS = [
    ("Q4_K_XL", "unsloth/Qwen3.8-27B-GGUF", r"UD-Q4_K_XL\.gguf$", False),
    ("Ridge", "empero-ai/Qwen3.8-27B-Ridge-GGUF", r"Ridge-3\.7bpw\.gguf$", False),
    ("PQ2_0", "prism-ml/Ternary-Bonsai-2-27B-gguf", r"PQ2_0\.gguf$", True),
    ("PTQ1_0", "prism-ml/Ternary-Bonsai-2-27B-gguf", r"PTQ1_0\.gguf$", True),
]

from huggingface_hub import HfApi, hf_hub_download
API = HfApi()


def fetch(repo, pattern):
    files = [f for f in API.list_repo_files(repo) if f.lower().endswith(".gguf")]
    hits = sorted(f for f in files if re.search(pattern, f, re.I))
    assert hits, "no file matching %s in %s" % (pattern, repo)
    t = time.time()
    p = hf_hub_download(repo, hits[0], local_dir=MODELS)
    print("  got %-34s %5.2f GB in %3.0fs" % (os.path.basename(p),
                                              os.path.getsize(p) / 1e9, time.time() - t),
          flush=True)
    return p


# ---------------------------------------------------------------- 3. server + scoring
def wait_health(timeout=900):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if urllib.request.urlopen("http://localhost:%d/health" % PORT, timeout=5).status == 200:
                return True
        except Exception:
            if not sh("pgrep -f llama-server")[1].strip():
                return False
            time.sleep(5)
    return False


BASE = ("--n-gpu-layers 999 --flash-attn on -b 2048 -ub 512 -t 4 -tb 4 "
        "--host 0.0.0.0 --port %d -np 1 --jinja --api-key %s" % (PORT, API_KEY))

# Tried in order; the first that boots is used, and the table records which rung ran. This is
# how the unknowns (does Bonsai accept tensor split? does it keep the MTP head?) get answered
# without guessing them up front.
LADDER = [
    ("tensor+mtp", "-sm tensor -ctk f16 -ctv f16 --spec-type draft-mtp "
                   "--spec-draft-n-max 2 --parallel 1"),
    ("tensor", "-sm tensor -ctk f16 -ctv f16 --parallel 1"),
    ("layer+mtp", "-ctk q8_0 -ctv q8_0 --spec-type draft-mtp --spec-draft-n-max 2 --parallel 1"),
    ("layer", "-ctk q8_0 -ctv q8_0 --parallel 1"),
]
PROOF = {"tensor": "SPLIT_MODE_TENSOR", "mtp": "draft-mtp"}


def start(binpath, model, extra, logname):
    sh("pkill -f llama-server; sleep 4")
    log = "%s/%s.log" % (OUT, logname)
    env = "LD_LIBRARY_PATH=%s:$LD_LIBRARY_PATH " % binpath
    os.system(env + "nohup " + binpath + "/llama-server" +
              " -m '%s' --ctx-size %d %s %s > %s 2>&1 &" % (model, CTX, BASE, extra, log))
    ok = wait_health()
    return ok, (open(log, errors="replace").read() if os.path.exists(log) else "")


def vram_used():
    _, o, _ = sh("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
    return [int(x.strip()) for x in o.strip().split(NL) if x.strip().isdigit()]


def user_msg(case):
    return ("Repository: demo/app\nBranch: main\nFailure kind: code\nFailed job: build-and-test\n\n"
            "--- failed steps ---\n- step \"Run tests\": failure\n\n"
            "--- CI failure log ---\n%s\n\n"
            "--- changed source files ---\nFile: test.js\n```javascript\n%s\n```\n"
            % (case["log"], case["code"]))


def norm(t):
    """Ignore cosmetic-only differences: line endings and trailing whitespace per line."""
    return NL.join(l.rstrip() for l in t.replace(chr(13) + NL, NL).split(NL)).rstrip() + NL


def syntax_ok(js):
    if not js or not js.strip():
        return False
    p = "/kaggle/tmp/_chk.js"
    open(p, "w", newline=NL).write(js)
    return sh("node --check " + p)[0] == 0


def parse_envelope(txt):
    clean = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    clean = re.sub(r"^```(?:json)?|```$", "", clean, flags=re.M).strip()
    try:
        return json.loads(clean)
    except Exception:
        m = re.search(r"\{.*\}", clean, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None


def ask(case):
    body = json.dumps({
        "model": "local",
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": user_msg(case)}],
        "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS, "stream": False,
        "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    t0 = time.time()
    try:
        req = urllib.request.Request("http://localhost:%d/v1/chat/completions" % PORT, body,
                                     {"Content-Type": "application/json",
                                      "Authorization": "Bearer " + API_KEY})
        j = json.loads(urllib.request.urlopen(req, timeout=1200).read())
    except Exception as ex:
        return {"error": str(ex)[:200], "byte_exact": False, "fix_correct": False}
    wall = time.time() - t0
    txt = j["choices"][0]["message"]["content"]
    obj = parse_envelope(txt)
    code = (obj or {}).get("fixed_code") if isinstance(obj, dict) else None
    ok = isinstance(code, str) and bool(code.strip())
    s_ok = syntax_ok(code) if ok else False
    # PRIMARY score: did it reproduce the known-good file exactly?
    # Also score a NORMALISED variant. Strict equality fails on a single trailing newline or a
    # CRLF, which would read 0 for every packing and discriminate nothing - the very saturation
    # this eval exists to escape. Reporting both keeps the strict number honest while
    # guaranteeing a usable signal if everyone trips the same cosmetic difference.
    byte_exact = bool(ok and code == GOLDEN)
    byte_exact_norm = bool(ok and code is not None and norm(code) == norm(GOLDEN))
    fix_ok = bool(ok and code and s_ok
                  and all(re.search(p, code, re.M) for p in case["must"])
                  and not any(re.search(p, code, re.M) for p in case["mustnot"]))
    tim = j.get("timings", {}) or {}
    dn, da = tim.get("draft_n"), tim.get("draft_n_accepted")
    return {
        "format_ok": ok, "syntax_ok": s_ok, "byte_exact": byte_exact,
        "byte_exact_norm": byte_exact_norm, "fix_correct": fix_ok,
        "gen_tokens": (j.get("usage") or {}).get("completion_tokens"),
        "prompt_tokens": (j.get("usage") or {}).get("prompt_tokens"),
        "gen_tps": round(tim.get("predicted_per_second") or 0, 2),
        "accept_rate": round((da or 0) / dn, 3) if dn else None,
        "finish_reason": (j["choices"][0] or {}).get("finish_reason"),
        "wall_s": round(wall, 1),
        "raw": txt[:4000], "raw_truncated": len(txt) > 4000,
    }


# ---------------------------------------------------------------- 4. run
R = {"started": datetime.datetime.now(datetime.timezone.utc).isoformat(),
     "ctx": CTX, "reps": REPS, "temperature": TEMPERATURE, "topo": topo.strip()[:700],
     "probe": {"stock": P_STOCK, "fork": P_FORK, "fork_does_all": FORK_DOES_ALL},
     "cells": [], "runs": []}


def save():
    json.dump(R, open(OUT + "/results.json", "w"), indent=1)


banner("RUNNING %d packings x %d cases x %d reps = %d CALLS"
       % (len(PACKINGS), len(CASES), REPS, len(PACKINGS) * len(CASES) * REPS))

for name, repo, pat, needs_fork in PACKINGS:
    banner("PACKING %s" % name)
    # A Bonsai file on stock llama.cpp loads silently and emits gibberish - assert, do not infer.
    binpath = FORK if (needs_fork or FORK_DOES_ALL) else STOCK
    if needs_fork and not (P_FORK.get("PTQ1_0") or P_FORK.get("PQ2_0")):
        print("  SKIP: fork does not advertise ternary types; refusing to load on stock",
              flush=True)
        R["cells"].append({"packing": name, "skipped": "fork lacks ternary quant support"})
        save(); continue
    try:
        model = fetch(repo, pat)
    except Exception as ex:
        print("  SKIP: download failed: %s" % str(ex)[:160], flush=True)
        R["cells"].append({"packing": name, "skipped": "download failed: %s" % str(ex)[:160]})
        save(); continue

    rung, log, ok = None, "", False
    for rname, flags in LADDER:
        ok, log = start(binpath, model, flags, "%s_%s" % (name, rname))
        if ok:
            rung = rname
            break
        print("  rung %-11s did not boot; falling back" % rname, flush=True)
    cell = {"packing": name, "binary": "fork" if binpath == FORK else "stock",
            "model_gb": round(os.path.getsize(model) / 1e9, 2),
            "rung": rung, "server_up": ok,
            "engaged": dict((k, v in log) for k, v in PROOF.items()),
            "vram": vram_used()}
    if not ok:
        cell["log_tail"] = log[-1500:]
        print("  NO RUNG BOOTED - recorded as a result", flush=True)
        R["cells"].append(cell); save()
        # remove the FILE, not its directory: dirname(model) is MODELS itself, and rmtree-ing it
        # would delete the download root so every later fetch fails.
        try: os.remove(model)
        except OSError: pass
        continue
    print("  up on %-11s | %s | engaged=%s | vram=%s"
          % (rung, cell["binary"], cell["engaged"], cell["vram"]), flush=True)

    peak = list(cell["vram"])
    for case in CASES:
        for rep in range(REPS):
            r = ask(case)
            r.update({"packing": name, "case": case["name"], "set": case["set"], "rep": rep})
            R["runs"].append(r); save()
            peak = [max(a, b) for a, b in zip(peak, vram_used() or peak)]
            print("%-8s %-8s %-24s r%d tps=%-6s exact=%d syn=%d fix=%d %ss"
                  % (name, case["set"], case["name"], rep, r.get("gen_tps"),
                     r.get("byte_exact", False), r.get("syntax_ok", False),
                     r.get("fix_correct", False), r.get("wall_s")), flush=True)
    cell["vram_peak"] = peak
    R["cells"].append(cell); save()
    # free the disk before the next multi-GB download
    try: os.remove(model)
    except OSError: pass
    sh("pkill -f llama-server; sleep 3")

# ---------------------------------------------------------------- 5. summary
import statistics
summary = {}
for name, _, _, _ in PACKINGS:
    rs = [r for r in R["runs"] if r.get("packing") == name and r.get("gen_tps")]
    if not rs:
        continue
    ctl = [r for r in rs if r["set"] == "control"]
    hrd = [r for r in rs if r["set"] == "hard"]
    tps = [r["gen_tps"] for r in rs]
    cell = next((c for c in R["cells"] if c.get("packing") == name), {})
    summary[name] = {
        "n": len(rs), "gb": cell.get("model_gb"), "rung": cell.get("rung"),
        "binary": cell.get("binary"),
        "median_tps": round(statistics.median(tps), 2),
        "median_wall_s": round(statistics.median([r["wall_s"] for r in rs]), 1),
        "control_exact": "%d/%d" % (sum(bool(r.get("byte_exact")) for r in ctl), len(ctl)),
        "hard_exact": "%d/%d" % (sum(bool(r.get("byte_exact")) for r in hrd), len(hrd)),
        "control_exact_norm": "%d/%d" % (sum(bool(r.get("byte_exact_norm")) for r in ctl), len(ctl)),
        "hard_exact_norm": "%d/%d" % (sum(bool(r.get("byte_exact_norm")) for r in hrd), len(hrd)),
        "control_fix": "%d/%d" % (sum(bool(r.get("fix_correct")) for r in ctl), len(ctl)),
        "hard_fix": "%d/%d" % (sum(bool(r.get("fix_correct")) for r in hrd), len(hrd)),
        "syntax_ok": "%d/%d" % (sum(bool(r.get("syntax_ok")) for r in rs), len(rs)),
        "truncated": sum(1 for r in rs if r.get("finish_reason") == "length"),
    }
R["summary"] = summary
save()

banner("SUMMARY  (ranked on quality first, speed second)")
print("%-8s %6s %-11s %-6s %5s %9s %9s %9s %5s"
      % ("packing", "GB", "rung", "bin", "n", "ctl_exact", "hard_exact", "med_tps", "trunc"),
      flush=True)
for k, v in summary.items():
    print("%-8s %6s %-11s %-6s %5d %9s %9s %9s %5d"
          % (k, v["gb"], v["rung"], v["binary"], v["n"], v["control_exact"],
             v["hard_exact"], v["median_tps"], v["truncated"]), flush=True)

print(NL + "===RESULTS_JSON_BEGIN===", flush=True)
print(json.dumps({"summary": summary, "probe": R["probe"],
                  "cells": [{k: c.get(k) for k in
                             ("packing", "binary", "model_gb", "rung", "server_up", "engaged",
                              "vram_peak", "skipped")} for c in R["cells"]],
                  "per_cell": [{k: r.get(k) for k in
                                ("packing", "set", "case", "rep", "gen_tps", "gen_tokens",
                                 "byte_exact", "byte_exact_norm", "syntax_ok", "fix_correct", "accept_rate",
                                 "wall_s", "finish_reason", "raw_truncated", "error")}
                               for r in R["runs"]]}), flush=True)
print("===RESULTS_JSON_END===", flush=True)
sh("pkill -f llama-server")
