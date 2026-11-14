"""Live go-live drive: push each real breakage to the demo repo, time push->PR, check the PR's file.

Per scenario:
  1. write the broken test.js to main, commit, push           (a real CI failure)
  2. wait for the CI run on that commit to finish
  3. wait for a NEW heal PR - "new" means created after this push. There are stale ai-fix PRs
     from earlier runs, so "the latest open PR" would silently pick up the wrong one.
  4. read test.js AT THE PR BRANCH and check it: node --check + byte-compare to golden.
     A PR that opened is not a heal; a PR whose file parses and matches is.
  5. restore golden on main and wait for green before the next scenario, so runs never overlap.

All timings come from GitHub's own timestamps (run createdAt, job steps, PR createdAt) - one
clock, no local skew. Results land in eval/runs/golive1/golive_results.json.
"""
import datetime, io, json, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "app")
GOLDEN = io.open(os.path.join(ROOT, "eval", "cases", "golden_test.js"), encoding="utf-8").read()
CASES = json.load(io.open(os.path.join(ROOT, "eval", "cases", "eval_cases.json"), encoding="utf-8"))["cases"]
# one file per go-live round, so a summary never mixes rounds: GOLIVE_OUT=golive2_results.json
OUT = os.path.join(ROOT, "eval", "runs", os.environ.get("GOLIVE_OUT", "golive_results.json"))
os.makedirs(os.path.dirname(OUT), exist_ok=True)   # a new round folder (e.g. golive6/)
ONLY = sys.argv[1:]          # optional: run a subset by name


def sh(*a, cwd=APP, check=True):
    p = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(a[:3]), (p.stderr or p.stdout)[:300]))
    return p.stdout.strip()


def gh_json(*a):
    return json.loads(sh("gh", *a) or "null")


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


REPO = gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def push_file(content, msg):
    io.open(os.path.join(APP, "test.js"), "w", encoding="utf-8", newline="\n").write(content)
    sh("git", "add", "test.js")
    sh("git", "commit", "-q", "-m", msg)
    sh("git", "push", "-q", "origin", "main")
    return sh("git", "rev-parse", "HEAD")


def wait_run(sha, timeout=420):
    t = time.time()
    while time.time() - t < timeout:
        runs = gh_json("run", "list", "--commit", sha, "--json",
                       "databaseId,status,conclusion,createdAt")
        if runs and runs[0]["status"] == "completed":
            return runs[0]
        time.sleep(8)
    raise TimeoutError("CI run for %s never completed" % sha[:7])


def wait_pr(after, timeout=420):
    t = time.time()
    while time.time() - t < timeout:
        prs = gh_json("pr", "list", "--state", "open", "--json",
                      "number,headRefName,createdAt,url", "--limit", "50")
        new = [p for p in prs if ts(p["createdAt"]) > after]
        if new:
            return sorted(new, key=lambda p: p["createdAt"])[0]
        time.sleep(6)
    return None


def wait_pr_ci(number, timeout=420):
    """The heal PR's OWN CI result (main.yml runs on pull_request). A parse-clean PR can still be
    wrong; this is the gate that says so. Returns the conclusion, or 'none' if no check appeared."""
    t = time.time()
    while time.time() - t < timeout:
        checks = gh_json("pr", "view", str(number), "--json", "statusCheckRollup")["statusCheckRollup"]
        build = [c for c in checks if c.get("name") == "build-and-test"]
        if build and build[0].get("status") == "COMPLETED":
            return build[0].get("conclusion", "").lower()
        time.sleep(8)
    return "none"


def pr_file(branch):
    # NOT via sh(): sh() strips stdout, which removed the file's trailing newline and made a
    # byte-identical PR (#10) score golden=False. File content must come back untouched.
    p = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                        "repos/%s/contents/test.js?ref=%s" % (REPO, branch)],
                       cwd=APP, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("could not fetch test.js at %s" % branch)
    return p.stdout.decode("utf-8")


def parses(src):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "c.js")
    io.open(p, "w", encoding="utf-8", newline="\n").write(src)
    return subprocess.run(["node", "--check", p], capture_output=True).returncode == 0


results = []
if os.path.exists(OUT):
    results = json.load(io.open(OUT, encoding="utf-8"))

assert io.open(os.path.join(APP, "test.js"), encoding="utf-8").read() == GOLDEN, \
    "app/test.js is not golden - refusing to start from a broken baseline"

for case in CASES:
    name = case["name"]
    if ONLY and name not in ONLY:
        continue
    print("=" * 70 + "\n" + name, flush=True)
    rec = {"case": name, "started": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        sha = push_file(case["code"], "break: %s (go-live)" % name)
        run = wait_run(sha)
        rec["ci"] = {"conclusion": run["conclusion"], "run_id": run["databaseId"]}
        start = ts(run["createdAt"])
        print("  CI %s (run %s)" % (run["conclusion"], run["databaseId"]), flush=True)
        jobs = gh_json("run", "view", str(run["databaseId"]), "--json", "jobs")["jobs"]
        for j in jobs:
            if j.get("startedAt") and j.get("completedAt"):
                rec.setdefault("jobs", {})[j["name"]] = (ts(j["completedAt"]) - ts(j["startedAt"])).total_seconds()
        pr = wait_pr(start)
        if not pr:
            rec["healed"] = False
            rec["error"] = "no PR within timeout"
            print("  NO PR", flush=True)
        else:
            f = pr_file(pr["headRefName"])
            rec.update({"pr": pr["url"], "branch": pr["headRefName"],
                        "push_to_pr_s": round((ts(pr["createdAt"]) - start).total_seconds(), 1),
                        "file_parses": parses(f), "file_is_golden": f == GOLDEN})
            rec["healed"] = rec["file_parses"] and rec["file_is_golden"]
            print("  PR %s  %.0fs  parses=%s golden=%s" % (pr["url"], rec["push_to_pr_s"],
                                                           rec["file_parses"], rec["file_is_golden"]),
                  flush=True)
            rec["pr_ci"] = wait_pr_ci(pr["number"])
            print("  PR CI: %s" % rec["pr_ci"], flush=True)
    except Exception as e:
        rec["healed"] = False
        rec["error"] = str(e)[:300]
        print("  ERROR", e, flush=True)
    finally:
        rsha = push_file(GOLDEN, "restore: green baseline after %s (go-live)" % name)
        rr = wait_run(rsha)
        rec["restore_ci"] = rr["conclusion"]
        print("  restore CI %s" % rr["conclusion"], flush=True)
        results.append(rec)
        io.open(OUT, "w", encoding="utf-8").write(json.dumps(results, indent=1))

done = [r for r in results if r.get("push_to_pr_s")]
print("\nhealed %d/%d" % (sum(bool(r.get("healed")) for r in results), len(results)))
print("PR CI green %d/%d" % (sum(r.get("pr_ci") == "success" for r in results), len(results)))
if done:
    s = sorted(r["push_to_pr_s"] for r in done)
    print("push->PR median %.0fs (min %.0f, max %.0f)" % (s[len(s) // 2], s[0], s[-1]))
