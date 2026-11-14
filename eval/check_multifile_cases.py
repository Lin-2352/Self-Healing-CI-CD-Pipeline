"""Preflight for the multi-file cases: prove each one is a real, well-formed test before any GPU time.

For every case:
  golden tree (with the case's golden overrides) must PASS  npm test  (exit 0)
  broken tree must FAIL with exit 1 (a code bug) - never 75 (environment) and never pass
  a 'pre' tree (latent bug) must PASS - otherwise it could not have been committed green first

Trees are materialised in temp dirs inside app/ so `require("express")` resolves to app/node_modules.
Also exposes tree_test() for the eval scorer.

Run:  python eval/check_multifile_cases.py
"""
import io, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
_port = [5600]


def tree_test(files, app_dir=APP):
    """Run the demo's `npm test` (node test.js && node test/api.test.js) on a full file tree.
    Returns (exit_code, full output) - callers slice what they print. Each run gets fresh ports so runs never collide."""
    d = tempfile.mkdtemp(dir=app_dir, prefix="_tree_")
    try:
        for p, c in files.items():
            fp = os.path.join(d, *p.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            io.open(fp, "w", encoding="utf-8", newline="\n").write(c)
        _port[0] += 2
        env = dict(os.environ, PORT=str(_port[0]), API_PORT=str(_port[0] + 1))
        out = ""
        for script in ("test.js", os.path.join("test", "api.test.js")):
            try:
                p = subprocess.run(["node", script], cwd=d, env=env, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                return -1, out + "\n%s: timeout" % script
            out += p.stdout + p.stderr
            if p.returncode != 0:                      # npm's `&&`: the first failure is the exit code
                return p.returncode, out
        return 0, out
    finally:
        shutil.rmtree(d, ignore_errors=True)


RUNNER_ROOT = "/home/runner/work/self-healing-cicd-demo/self-healing-cicd-demo/"


def as_github_log(node_output, ts="2026-09-28T00:00:00.0000000Z "):
    """Local node output -> what GitHub's job log shows for the same failure: paths re-rooted at the
    runner checkout, forward slashes, every line timestamped, the step wrapped in group/error markers."""
    import re
    node_output = node_output.replace("\\\\", "\\")     # node prints paths in error objects JSON-style
    out = re.sub(r"([A-Za-z]:\\[^\s()'\"]*?\\app\\(?:_tree_[A-Za-z0-9_]+\\)?)([^\s()'\":]+)",
                 lambda m: RUNNER_ROOT + m.group(2).replace("\\", "/"), node_output)
    lines = ["##[group]Run npm test", "npm test", "##[endgroup]", "> node test.js && node test/api.test.js"] + \
        out.splitlines() + ["##[error]Process completed with exit code 1."]
    return "".join(ts + l + "\n" for l in lines)


def trees(case, golden):
    good = dict(golden); good.update(case.get("golden", {}))
    bad = dict(golden); bad.update(case["files"])
    pre = None
    if case.get("pre"):
        pre = dict(golden); pre.update(case["pre"])
    return good, bad, pre


if __name__ == "__main__":
    D = json.load(io.open(os.path.join(HERE, "cases", "multifile_cases.json"), encoding="utf-8"))
    fails = 0
    rc, out = tree_test(D["golden_tree"])
    print("  %-30s golden rc=%s %s" % ("(base golden tree)", rc, "PASS" if rc == 0 else "FAIL " + out[-200:]))
    fails += rc != 0
    for c in D["cases"]:
        good, bad, pre = trees(c, D["golden_tree"])
        g, _ = tree_test(good)
        b, bout = tree_test(bad)
        p = tree_test(pre)[0] if pre else None
        ok = g == 0 and b == 1 and (pre is None or p == 0)
        fails += not ok
        first_err = next((l.strip() for l in bout.splitlines() if "Error" in l or "CODE_FAILURE" in l), "")
        print("  %-30s golden=%s broken=%s%s  %s  | %s" % (c["name"], g, b, "" if pre is None else " pre=%s" % p,
                                                          "PASS" if ok else "FAIL", first_err[:90]))
    print("\nFAILURES:", fails)
    sys.exit(1 if fails else 0)
