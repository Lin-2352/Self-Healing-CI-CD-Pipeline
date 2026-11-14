"""Run every local check of the self-healing pipeline in one go. No GPU, no GitHub writes.

  python run_tests.py          node suites (tools/tests), eval scorer self-test, generator check
  python run_tests.py --rig    + the real-n8n verify rig (needs n8n 1.x, see tools/n8n_rig/README.md;
                                 run from PowerShell - the rig shells out through cmd.exe)

Each check's full output is saved to eval/runs/testall/<name>.txt, which the results page
(tools/report/build_results_page.py) reads. Exit code 0 only if everything passed.
"""
import glob, os, re, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "eval", "runs", "testall")
os.makedirs(OUT, exist_ok=True)


def run(name, args, cwd=ROOT, ok=lambda rc, out: rc == 0):
    t = time.time()
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8").write(out)
    good = ok(p.returncode, out)
    last = [l for l in out.strip().splitlines() if l.strip()][-1:] or ["(no output)"]
    print("  %-4s %-22s %5.1fs  %s" % ("PASS" if good else "FAIL", name, time.time() - t, last[0][:90]), flush=True)
    return good


results = []
print("node suites (real node bodies under plain node)")
for t in sorted(glob.glob(os.path.join(ROOT, "tools", "tests", "test_*.py"))):
    results.append(run(os.path.splitext(os.path.basename(t))[0], [sys.executable, t]))
print("eval scorer, multi-file case preflight, generator")
results.append(run("test_shipped_eval", [sys.executable, os.path.join(ROOT, "eval", "test_shipped_eval.py")]))
results.append(run("check_multifile_cases", [sys.executable, os.path.join(ROOT, "eval", "check_multifile_cases.py")]))
results.append(run("test_multifile_eval", [sys.executable, os.path.join(ROOT, "eval", "test_multifile_eval.py")]))
results.append(run("test_bakeoff_eval", [sys.executable, os.path.join(ROOT, "eval", "test_bakeoff_eval.py")]))
results.append(run("gen_check", [sys.executable, os.path.join(ROOT, "tools", "gen_n8n.py"), "--check"],
                   ok=lambda rc, out: rc == 0 and "byte-identical: True" in out))

if "--rig" in sys.argv:
    print("real n8n engine (verify rig)")
    rig = os.path.join(ROOT, "tools", "n8n_rig")
    shutil.rmtree(os.path.join(rig, "n8nhome2"), ignore_errors=True)
    mock = subprocess.Popen([sys.executable, "mock2.py", "5792"], cwd=rig,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        subprocess.run([sys.executable, "build_wf.py"], cwd=rig, capture_output=True)
        subprocess.run([sys.executable, "drive_full.py", "setup"], cwd=rig, capture_output=True)
        results.append(run("n8n_rig", [sys.executable, "drive_full.py", "all"], cwd=rig,
                           ok=lambda rc, out: rc == 0 and "PASS" in out
                           and not re.search(r"^FAIL", out, re.M)))
    finally:
        mock.kill()

print("\n%d/%d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
