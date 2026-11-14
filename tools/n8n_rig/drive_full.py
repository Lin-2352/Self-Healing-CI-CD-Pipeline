"""Run the full front half (real Build AI Prompt -> real Qwen node -> real Extract) per scenario."""
import base64, io, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
# Durable install (a %TEMP% install was wiped once). Falls back to the old location.
N8N = next(p for p in [os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n", "app", "node_modules", "n8n", "bin", "n8n"),
                       os.path.join(os.environ["TEMP"], "n8n_verify", "node_modules", "n8n", "bin", "n8n")]
           if os.path.exists(p))
JOBLOG = io.open(os.path.join(ROOT, "eval", "cases", "fixtures", "job_log_failure.txt"), encoding="utf-8", newline="").read()
LOG_NEEDLE = "ReferenceError: http is not defined"      # from the real GitHub job log fixture
G = io.open(os.path.join(ROOT, "eval", "cases", "golden_test.js"), encoding="utf-8").read()
CASES = {c["name"]: c for c in json.load(io.open(os.path.join(ROOT, "eval", "cases", "eval_cases.json"), encoding="utf-8"))["cases"]}
DIFF = json.load(io.open(os.path.join(ROOT, "eval", "runs", "kout_diff", "results.json"), encoding="utf-8"))["runs"]
SHIP = json.load(io.open(os.path.join(ROOT, "n8n", "self_healing_pipeline.json"), encoding="utf-8"))
SYS_SHIPPED = re.search(r'^const SYSTEM_PROMPT = (".*");$', next(n for n in SHIP["nodes"] if n["name"] == "Build AI Prompt")["parameters"]["jsCode"], re.M).group(1)
SYS_SHIPPED = json.loads(SYS_SHIPPED)
env = dict(os.environ, N8N_USER_FOLDER=os.path.join(HERE, "n8nhome2"), N8N_DIAGNOSTICS_ENABLED="false")

def model_for(case):
    r = next(x for x in DIFF if x.get("format") == "search_replace" and x["case"] == case and x["rep"] == 0)
    t = r["raw"]; a, b = t.find("{"), t.rfind("}"); return json.loads(t[a:b + 1])

def scenario(case, hook_over=None):
    hook = {"repo": "demo/app", "branch": "main", "commit": "abc1234def5678", "actor": "dev",
            "failure_kind": "code", "run_id": "999"}
    hook.update(hook_over or {})
    return {"hook": hook,
            "log": JOBLOG,
            "jobs": {"jobs": [{"id": 106924016599, "name": "build-and-test", "conclusion": "failure", "status": "completed",
                               "steps": [{"name": "Run tests", "conclusion": "failure",
                                          "started_at": "2026-09-22T20:00:00Z", "completed_at": "2026-09-22T20:00:05Z"}]}]},
            "files": {"files": [{"filename": "test.js", "status": "modified"}]},
            "file": CASES[case]["code"], "model": model_for(case)}

def run(label, scn):
    io.open(os.path.join(HERE, "scenario.json"), "w", encoding="utf-8").write(json.dumps(scn))
    for f in ("last_request.json", "log_hit.json", "blob_auth.json", "comment_post.json"):
        try: os.remove(os.path.join(HERE, f))
        except OSError: pass
    out = os.path.join(HERE, "out_full.json")
    rc = subprocess.run('node "%s" execute --id=vrfyFullFront001 --rawOutput > "%s" 2>&1' % (N8N, out),
                        shell=True, env=env).returncode
    txt = io.open(out, encoding="utf-8", errors="replace").read()
    req = None
    if os.path.exists(os.path.join(HERE, "last_request.json")):
        req = json.load(io.open(os.path.join(HERE, "last_request.json"), encoding="utf-8"))
    res = None
    try:
        # n8n can print notices before the JSON (since mid-November 2026: "Error tracking disabled
        # because this release is older than 6 weeks."), so parse from the first brace.
        d = json.loads(txt[txt.find("{"):]); ex = d["data"]["resultData"]["runData"].get("Extract AI Response")
        if ex: res = ex[0]["data"]["main"][0][0]["json"]
    except Exception: pass
    return rc, txt, req, res

def rec(name):
    p = os.path.join(HERE, name)
    return json.load(io.open(p)) if os.path.exists(p) else None

which = sys.argv[1] if len(sys.argv) > 1 else "all"
if which == "setup":
    # a DUMMY github-pat so the verbatim Fetch Failed Job Log node has its credential; the mock
    # records whether it reaches the API host (it should) and the blob host (it must not)
    cp = os.path.join(HERE, "_rig_creds.json")
    io.open(cp, "w").write(json.dumps([{"id": "github-pat", "name": "GitHub PAT", "type": "httpBearerAuth",
                                        "data": {"token": "rig-dummy-token"}}]))
    for args in (["import:credentials", "--input=" + cp], ["import:workflow", "--input=" + os.path.join(HERE, "wf_full.json")]):
        p = subprocess.run(["node", N8N] + args, env=env, capture_output=True, text=True, errors="replace")
        print("n8n", args[0], "rc=%d" % p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1:])
    os.remove(cp)
    sys.exit(0)
if which in ("all", "happy"):
    for case in ["missing_app_init", "wrong_status_check"]:
        rc, txt, req, res = run(case, scenario(case))
        sys_ok = bool(req) and req["messages"][0]["content"] == SYS_SHIPPED
        user = req["messages"][1]["content"] if req else ""
        gold = bool(res) and base64.b64decode(res["base64_content"]).decode("utf-8") == G
        loglines = [l for l in CASES[case]["log"].split("\n") if re.search(r"Error|CODE_FAILURE|statusCode", l)][:2]
        print("%-4s %-20s rc=%d model_got_request=%s system==shipped=%s golden=%s edits=%s"
              % ("PASS" if (rc == 0 and sys_ok and gold) else "FAIL", case, rc, bool(req), sys_ok, gold, res and res.get("edits_applied")))
        hit, blob = rec("log_hit.json"), rec("blob_auth.json")
        # timestamps are checked in the LOG SECTION only: the failed-steps section legitimately
        # carries started_at/completed_at (a whole-message check falsely failed on those)
        sec = user.split("--- CI failure log ---", 1)[-1].split("--- changed source files ---", 1)[0]
        log_ok = LOG_NEEDLE in sec and "--- CI failure log ---" in user and not re.search(r"\d{4}-\d\d-\d\dT\d\d:", sec)
        redir_ok = bool(hit) and hit["job_id"] == "106924016599" and hit["auth"] and bool(blob) and not blob["auth"]
        print("%-4s   CI log in prompt (real error, no timestamps)=%s | job id from jobs API=%s | PAT sent to API=%s | PAT sent to blob host=%s"
              % ("PASS" if (log_ok and redir_ok) else "FAIL", log_ok, hit and hit["job_id"], hit and hit["auth"], blob and blob["auth"]))
if which in ("all", "retry"):
    # CI failed on the healer's own PR: attempt 1 on an ai-fix branch -> same branch, trailer 2,
    # and the prompt tells the model the file already carries its previous fix.
    br = "ai-fix-missing-express-init-abc1234"
    rc, txt, req, res = run("retry", scenario("missing_app_init", {"branch": br, "attempt": "1"}))
    user = req["messages"][1]["content"] if req else ""
    think = bool(req) and req.get("chat_template_kwargs") == {"enable_thinking": True}
    print("       retry request sent with thinking ON: %s" % think)
    ok = (rc == 0 and bool(res) and res.get("branch_name") == br and think
          and str(res.get("commit_message", "")).endswith("Heal-Attempt: 2") and "Heal attempt: 2." in user)
    print("%-4s retry: attempt 1 on %s -> branch kept=%s trailer=%r prompt says attempt 2=%s"
          % ("PASS" if ok else "FAIL", br, bool(res) and res.get("branch_name") == br,
             bool(res) and str(res.get("commit_message", "")).splitlines()[-1:], "Heal attempt: 2." in user))
    rc, txt, req, res = run("first", scenario("missing_app_init"))
    ok = rc == 0 and bool(res) and res.get("branch_name", "").endswith("-abc1234") \
        and str(res.get("commit_message", "")).endswith("Heal-Attempt: 1")
    print("%-4s retry: first heal on main -> new sha-suffixed branch %s, trailer 1=%s"
          % ("PASS" if ok else "FAIL", bool(res) and res.get("branch_name"),
             bool(res) and str(res.get("commit_message", "")).endswith("Heal-Attempt: 1")))
if which in ("all", "multifile"):
    # Phase 6 in the real engine: a latent bug reachable ONLY through the stack trace (the commit
    # changed just the test). Pick Candidate Files must put src/format.js in front of the model, the
    # prompt must carry all candidates, and the fix must land in the file the model names.
    sys.path.insert(0, os.path.join(ROOT, "eval"))
    from check_multifile_cases import tree_test, trees, as_github_log
    MF = json.load(io.open(os.path.join(ROOT, "eval", "cases", "multifile_cases.json"), encoding="utf-8"))
    case = next(c for c in MF["cases"] if c["name"] == "test_exposes_latent_bug")
    good, bad, _ = trees(case, MF["golden_tree"])
    _, out = tree_test(bad)
    fix_old = "  const cap = clean.split(\"-\").map((p) => p[0].toUpperCase() + p.slice(1)).join(\"-\");\n  return `Hello, ${cap}!`;"
    fix_new = "  return `Hello, ${clean}!`;"
    base = scenario("missing_app_init")
    base.update({"tree": bad, "log": as_github_log(out),
                 "files": {"files": [{"filename": p, "status": "modified"} for p in case["changed"]]}})
    s = dict(base); s["model"] = {"branch_name": "ai-fix-greet", "file_path": "src/format.js",
                                  "edits": "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (fix_old, fix_new),
                                  "pr_title": "Fix greeting", "pr_body": "b"}
    try: os.remove(os.path.join(HERE, "contents_hits.json"))
    except OSError: pass
    rc, txt, req, res = run("multifile", s)
    hits = [json.loads(l) for l in io.open(os.path.join(HERE, "contents_hits.json"))] if os.path.exists(os.path.join(HERE, "contents_hits.json")) else []
    user = req["messages"][1]["content"] if req else ""
    healed = bool(res) and res.get("file_path") == "src/format.js" and \
        base64.b64decode(res.get("base64_content", "")).decode("utf-8") == good["src/format.js"]
    ok = "src/format.js" in hits and "File: src/format.js" in user and "candidates; fix exactly ONE" in user and healed
    print("%-4s multifile: trace-only bug -> fetched %s, prompt shows all=%s, fixed src/format.js exactly=%s"
          % ("PASS" if ok else "FAIL", hits, "candidates; fix exactly ONE" in user, healed))
    s = dict(base); s["model"] = dict(s_model := {"branch_name": "x", "file_path": "src/nope.js",
                                                  "edits": "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE",
                                                  "pr_title": "t", "pr_body": "b"})
    rc, txt, req, res = run("multifile-wrong-file", s)
    cm = rec("comment_post.json") or {}
    ok = "is not one of the files shown" in (cm.get("body") or "") and "base64_content" not in (res or {})
    print("%-4s multifile: model names a file it was not shown -> rejected, commit comment says so=%s"
          % ("PASS" if ok else "FAIL", "is not one of the files shown" in (cm.get("body") or "")))
if which in ("all", "extract"):
    # Extract's rejection paths in the REAL engine (the harness covers them under plain node only).
    # Each must fail the run for its OWN reason, matched on its message.
    GET = 'app.get("/", (req, res) => res.send("ok"));'
    sr = lambda a, b: "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (a, b)
    base = model_for("missing_app_init")
    dup_file = "const dup = 1;\nconst dup = 1;\n" + CASES["missing_app_init"]["code"]
    probes = [("SEARCH absent", {"edits": sr("const nope = 1;", "const yes = 2;")}, None, r"not in the file"),
              ("SEARCH not unique", {"edits": sr("const dup = 1;", "const dup = 2;")}, dup_file, r"not unique"),
              ("patch does not parse", {"edits": sr(GET, "const app = express(   \n" + GET)}, None, r"does not parse"),
              ("old fixed_code schema", {"edits": None, "fixed_code": "x"}, None, r"no edits field|no well-formed")]
    for label, over, file_over, must in probes:
        s = scenario("missing_app_init")
        m = dict(base); m.update(over)
        if m.get("edits") is None: m.pop("edits")
        s["model"] = m
        if file_over: s["file"] = file_over
        rc, txt, req, res = run(label, s)
        # Extract now continues on error: the rejection travels as data to "Fix Ready?", which routes
        # it to "Explain Failure" -> a comment on the commit (first heal, no PR). No silent dead end.
        err = str((res or {}).get("error", ""))
        cm = rec("comment_post.json") or {}
        ok = (bool(req) and re.search(must, err, re.I) is not None and "base64_content" not in (res or {})
              and cm.get("path") == "/repos/demo/app/commits/abc1234def5678/comments"
              and re.search(must, cm.get("body") or "", re.I) is not None and cm.get("auth"))
        print("%-4s extract: %-22s rejected=%s -> commit comment posted=%s quotes reason=%s"
              % ("PASS" if ok else "FAIL", label, bool(re.search(must, err, re.I)), bool(cm),
                 bool(re.search(must, cm.get("body") or "", re.I))))
if which in ("all", "failure"):
    # The live dead end: a thinking retry hit the tunnel's 120 s timeout (524) and the PR sat red with
    # no word. Now the error must reach a comment ON THE PR, quoting the real cause.
    br = "ai-fix-missing-express-init-abc1234"
    s = scenario("missing_app_init", {"branch": br, "attempt": "1", "pr_number": "37"}); s["model_status"] = 524
    rc, txt, req, res = run("524", s)
    cm = rec("comment_post.json") or {}
    body = cm.get("body") or ""
    ok = (cm.get("path") == "/repos/demo/app/issues/37/comments" and "120-second" in body
          and "attempt 2" in body and "A human needs to look" in body and cm.get("auth"))
    print("%-4s failure: model 524 on retry -> PR #37 comment posted=%s, quotes the timeout=%s, names attempt 2=%s"
          % ("PASS" if ok else "FAIL", bool(cm), "120-second" in body, "attempt 2" in body))
    # streamed happy path: the reply really went through SSE and the Assemble node rebuilt it
    rc, txt, req, res = run("stream", scenario("missing_app_init"))
    try:
        asm = json.loads(txt[txt.find("{"):])["data"]["resultData"]["runData"]["Assemble Model Reply"][0]["data"]["main"][0][0]["json"]
    except Exception:
        asm = {}
    gold = bool(res) and base64.b64decode(res.get("base64_content", "")).decode("utf-8") == G
    ok = bool(req) and req.get("stream") is True and asm.get("streamed") and asm.get("timings", {}).get("predicted_n") == 99         and gold and not rec("comment_post.json")
    print("%-4s failure: streamed happy path -> request stream=%s, reassembled=%s, timings kept=%s, golden=%s, no comment=%s"
          % ("PASS" if ok else "FAIL", bool(req) and req.get("stream"), bool(asm.get("streamed")),
             asm.get("timings", {}).get("predicted_n") == 99, gold, not rec("comment_post.json")))
if which in ("all", "probes"):
    s = scenario("missing_app_init"); s["log"] = None
    rc, txt, req, res = run("log404", s)
    user = req["messages"][1]["content"] if req else ""
    ok = rc == 0 and bool(res) and "--- CI failure log ---\n(log unavailable)" in user
    print("%-4s probe: job log 404 -> heal still runs with '(log unavailable)'? rc=%d heal=%s marker=%s"
          % ("PASS" if ok else "FAIL", rc, bool(res), "(log unavailable)" in user))
    rc, txt, req, res = run("hostile", scenario("missing_app_init", {"actor": 'ev"il`${x}\n', "branch": "fix/`quote\"s`"}))
    ok = rc == 0 and bool(req) and bool(res)
    print("%-4s probe: hostile chars in payload -> valid request? %s, heal ok? %s, actor round-trips: %s"
          % ("PASS" if ok else "FAIL", bool(req), bool(res), bool(req) and 'ev"il`${x}\n' in req["messages"][1]["content"]))
    rc, txt, req, res = run("infra", scenario("missing_app_init", {"failure_kind": "infra"}))
    m = re.search(r"Refusing to run[^\n\"]{0,80}", txt)
    print("%-4s probe: failure_kind=infra -> refused before model? rc=%d model_called=%s msg=%s"
          % ("PASS" if (rc != 0 and req is None and m) else "FAIL", rc, req is not None, m.group(0) if m else txt[-120:]))
