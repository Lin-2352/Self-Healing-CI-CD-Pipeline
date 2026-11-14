"""Live drive of the CI-feedback retry loop: push a breakage, then follow its heal PR to the end.

Per case:
  1. write the broken test.js to main and push: a real CI failure, first heal (attempt 0 -> 1)
  2. wait for the NEW heal PR (created after this push)
  3. follow that PR until one of these ends it:
       green      the PR's latest build-and-test run succeeded
       gave_up    github-actions commented "needs a human" (cap reached)
       timeout    neither within the window
       stalled    head CI red and no new heal commit for STALL_S (524 / Extract reject / n8n error)
       no_trailer the first heal commit lacks Heal-Attempt: 1 -> the whole run stops (loop cannot work)
     Along the way, count heal commits on the branch by their `Heal-Attempt: N` trailer.
  4. restore golden on main and wait for green, so cases never overlap

The PR's file is also re-scored independently: it must parse and pass the real smoke test.
All times come from GitHub's own timestamps. Results: eval/<GOLIVE_OUT or golive_retry_results.json>.

Usage: python tools/golive/run_retry.py multi_hunk callback_arity [...]  (names from either case set)
"""
import datetime, io, json, os, re, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "app")
GOLDEN = io.open(os.path.join(ROOT, "eval", "cases", "golden_test.js"), encoding="utf-8").read()
CASES = {c["name"]: c for f in ("eval_cases.json", "hard_cases.json")
         for c in json.load(io.open(os.path.join(ROOT, "eval", "cases", f), encoding="utf-8"))["cases"]}
OUT = os.path.join(ROOT, "eval", "runs", os.environ.get("GOLIVE_OUT", "golive_retry_results.json"))
os.makedirs(os.path.dirname(OUT), exist_ok=True)   # a new round folder (e.g. golive6/)
WINDOW = 1500          # s per case: up to 3 attempts, each CI (~40 s) + heal (up to ~70 s with thinking)
STALL_S = 300          # red head + no new heal commit for this long = the retry died (see docstring)
STOP = False


def sh(*a, check=True):
    p = subprocess.run(list(a), cwd=APP, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(a[:3]), (p.stderr or p.stdout)[:300]))
    return p.stdout.strip()


gh_json = lambda *a: json.loads(sh("gh", *a) or "null")
ts = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
REPO = gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


# Phase 6: every case is a file-tree diff against the demo's golden tree. A single-file case is just
# {test.js: broken}; a multi-file case may touch several files, add one (README.md), or need a 'pre'
# commit that lands a latent bug green first.
sys.path.insert(0, os.path.join(ROOT, "eval"))
from check_multifile_cases import tree_test                 # noqa: E402
MF = json.load(io.open(os.path.join(ROOT, "eval", "cases", "multifile_cases.json"), encoding="utf-8"))
BASE = MF["golden_tree"]
TREE_CASES = {c["name"]: c for c in MF["cases"]}


def case_of(name):
    if name in TREE_CASES:
        return TREE_CASES[name]
    return {"name": name, "files": {"test.js": CASES[name]["code"]}, "changed": ["test.js"]}


def push_tree(files, msg):
    """One commit: write every path (None = delete), push to main, return the commit sha."""
    for path, content in files.items():
        fp = os.path.join(APP, *path.split("/"))
        if content is None:
            if os.path.exists(fp):
                sh("git", "rm", "-q", path)
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        io.open(fp, "w", encoding="utf-8", newline="\n").write(content)
        sh("git", "add", path)
    sh("git", "commit", "-q", "-m", msg); sh("git", "push", "-q", "origin", "main")
    return sh("git", "rev-parse", "HEAD")


def push_file(content, msg):
    return push_tree({"test.js": content}, msg)


def wait_run(sha, timeout=420):
    t = time.time()
    while time.time() - t < timeout:
        runs = gh_json("run", "list", "--commit", sha, "--json", "databaseId,status,conclusion,createdAt")
        if runs and runs[0]["status"] == "completed":
            return runs[0]
        time.sleep(8)
    raise TimeoutError("CI run for %s never completed" % sha[:7])


def wait_new_pr(after, timeout=420):
    t = time.time()
    while time.time() - t < timeout:
        prs = gh_json("pr", "list", "--state", "open", "--json", "number,headRefName,createdAt,url", "--limit", "50")
        new = [p for p in prs if ts(p["createdAt"]) > after]
        if new:
            return sorted(new, key=lambda p: p["createdAt"])[0]
        time.sleep(6)
    return None


def pr_state(number):
    v = gh_json("pr", "view", str(number), "--json", "commits,comments,headRefOid,statusCheckRollup")
    attempts = []
    for c in v["commits"]:
        m = re.search(r"^Heal-Attempt: *(\d+)\s*$", (c.get("messageHeadline", "") + "\n" + c.get("messageBody", "")), re.M)
        if m:
            attempts.append(int(m.group(1)))
    gave_up = any(c["author"]["login"] == "github-actions" and "needs a human" in c["body"] for c in v["comments"])
    build = [c for c in v["statusCheckRollup"] if c.get("name") == "build-and-test"]
    head_ci = build[0] if build else {}
    return {"head": v["headRefOid"], "attempts": attempts, "gave_up": gave_up,
            "ci_status": head_ci.get("status"), "ci_conclusion": (head_ci.get("conclusion") or "").lower(),
            "ci_completed_at": head_ci.get("completedAt")}


def pr_file(branch, path="test.js"):
    p = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                        "repos/%s/contents/%s?ref=%s" % (REPO, path, branch)], cwd=APP, capture_output=True)
    return p.stdout.decode("utf-8") if p.returncode == 0 else None


def pr_tree(branch, paths):
    """The PR branch's version of every path (None if absent), so the whole tree can be tested."""
    return {p: pr_file(branch, p) for p in paths}


def smoke(src):
    """The real smoke test against the file the PR carries (app/node_modules has express)."""
    d = tempfile.mkdtemp(dir=APP)
    try:
        io.open(os.path.join(d, "test.js"), "w", encoding="utf-8", newline="\n").write(src)
        env = dict(os.environ, PORT="5919")
        try:
            return subprocess.run(["node", "test.js"], cwd=d, env=env, capture_output=True, timeout=30).returncode
        except subprocess.TimeoutExpired:
            return -1
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)


results = json.load(io.open(OUT, encoding="utf-8")) if os.path.exists(OUT) else []
for _p, _c in BASE.items():
    assert io.open(os.path.join(APP, *_p.split("/")), encoding="utf-8").read() == _c, "app/%s is not golden" % _p
for name in sys.argv[1:]:
    if STOP:
        break
    print("=" * 70 + "\n" + name, flush=True)
    rec = {"case": name, "started": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        case = case_of(name)
        if case.get("pre"):                                  # land the latent bug green first
            pre_run = wait_run(push_tree(case["pre"], "setup: latent bug for %s (retry go-live)" % name))
            rec["pre_ci"] = pre_run["conclusion"]
            print("  pre-commit CI: %s (expected success)" % pre_run["conclusion"], flush=True)
        broken = {p: c for p, c in case["files"].items() if (case.get("pre") or {}).get(p) != c}
        sha = push_tree(broken, "break: %s (retry go-live)" % name)
        run = wait_run(sha)
        start = ts(run["createdAt"])
        rec["main_ci"] = run["conclusion"]
        pr = wait_new_pr(start)
        if not pr:
            rec.update(outcome="no_pr"); print("  NO PR", flush=True)
        else:
            rec.update(pr=pr["url"], branch=pr["headRefName"],
                       push_to_first_pr_s=round((ts(pr["createdAt"]) - start).total_seconds(), 1))
            print("  PR %s (%.0fs)" % (pr["url"], rec["push_to_first_pr_s"]), flush=True)
            # The loop hinges on the trailer surviving into the REAL commit (the rig stops at
            # Extract; Push Fix File's message path only runs live). Without it every retry is
            # silently skipped as "a human pushed" - so fail the whole run fast, don't wait it out.
            first = pr_state(pr["number"])
            if 1 not in first["attempts"]:
                rec.update(outcome="no_trailer", attempts=first["attempts"])
                print("  ABORT: first heal commit has no 'Heal-Attempt: 1' trailer - the loop cannot work",
                      flush=True)
                STOP = True
                raise RuntimeError("trailer missing on first heal commit")
            t0, seen, st, red_since = time.time(), None, {}, None
            while time.time() - t0 < WINDOW:
                st = pr_state(pr["number"])
                if st["attempts"] != seen:
                    seen = st["attempts"]; red_since = None
                    print("  heal commits (trailers): %s" % seen, flush=True)
                if st["gave_up"]:
                    rec["outcome"] = "gave_up"; break
                if st["ci_status"] == "COMPLETED" and st["ci_conclusion"] == "success":
                    rec["outcome"] = "green"
                    rec["push_to_green_s"] = round((ts(st["ci_completed_at"]) - start).total_seconds(), 1)
                    break
                # Stall: CI on the head is red and no new heal commit follows. A 524 from the
                # tunnel, an Extract rejection or an n8n error all look like this - record it and
                # move on instead of burning the GPU window (read exec_stats.py for which one).
                if st["ci_status"] == "COMPLETED" and st["ci_conclusion"] == "failure" and not st["gave_up"]:
                    red_since = red_since or time.time()
                    if time.time() - red_since > STALL_S:
                        rec["outcome"] = "stalled"; break
                time.sleep(10)
            else:
                rec["outcome"] = "timeout"
            rec["attempts"] = st.get("attempts")
            paths = sorted(set(BASE) | set(case["files"]))
            tree = {p: c for p, c in pr_tree(pr["headRefName"], paths).items() if c is not None}
            want = dict(BASE); want.update(case.get("golden", {}))
            rec["final_smoke_rc"] = tree_test(tree)[0]          # the full npm test on the PR's tree
            rec["final_is_golden"] = all(tree.get(p) == want[p] for p in want)
            rec["fixed_files"] = sorted(p for p in set(tree) | set(case["files"])
                                        if tree.get(p) != case["files"].get(p, BASE.get(p)))
            print("  -> %s | attempts %s | final smoke rc=%s golden=%s %s" % (
                rec["outcome"], rec["attempts"], rec["final_smoke_rc"], rec["final_is_golden"],
                ("| push->green %.0fs" % rec["push_to_green_s"]) if rec.get("push_to_green_s") else ""), flush=True)
    except Exception as e:
        rec.setdefault("outcome", "error"); rec["error"] = str(e)[:300]; print("  ERROR", e, flush=True)
    finally:
        touched = set(case_of(name)["files"]) | set(case_of(name).get("pre") or {})
        rr = wait_run(push_tree({p: BASE.get(p) for p in touched},
                                "restore: green baseline after %s (retry go-live)" % name))
        rec["restore_ci"] = rr["conclusion"]
        results.append(rec)
        io.open(OUT, "w", encoding="utf-8").write(json.dumps(results, indent=1))

print("\n" + "\n".join("%-24s %-8s attempts=%s smoke_rc=%s" % (r["case"], r.get("outcome"), r.get("attempts"),
                                                            r.get("final_smoke_rc")) for r in results))
