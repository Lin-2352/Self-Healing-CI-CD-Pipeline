"""Build HARDER repair cases, because the existing metric is saturated.

Every quant down to IQ2_S at 9.6 GB scored 3/3 on 4 of 5 of the original cases, and the current
5 are 60/60 for Q4. Scoring Bonsai on those would produce another perfect row and rank nothing.
So these six are chosen to be hard in a specific way: **the CI log does not point at the bug**.
Four of them produce an error message that actively misleads - two look like infrastructure
problems (ECONNREFUSED, ENOTFOUND) when the fault is in the source, and one reports
"unexpected status 200" where 200 is the correct status.

Every break is a transform of the same real `app/test.js`, so the correct answer is always
"restore golden exactly" and byte-exact scoring is unambiguous.

Each case must FAIL deterministically with a real captured log - a break that hangs, or that
only manifests under an infra fault, is useless in a bounded eval and is not used here.

Writes hard_cases.json (same shape as eval_cases.json).
"""
import io, json, os, re, subprocess, sys

NLPAT = chr(92) + 'n'   # regex for a newline, built to survive shell/heredoc mangling

HERE = os.path.dirname(os.path.abspath(__file__))
APP = r"D:\Desktop\Project\CI_CD_pipeline_autoheal\app"
GOLDEN = os.path.join(HERE, "cases", "golden_test.js")

good = io.open(GOLDEN, encoding="utf-8").read()


def sub(old, new):
    def f(s):
        assert old in s, "pattern absent: %r" % old[:60]
        return s.replace(old, new, 1)
    return f


def chain(*fns):
    def f(s):
        for fn in fns:
            s = fn(s)
        return s
    return f


# Each entry: name, what was broken, transform, why it is hard.
BREAKS = [
    # NOTE: an ECONNREFUSED break was tried here first and REJECTED. Node leaves err.message
    # empty for it on this platform, so the captured log was a bare "CODE_FAILURE:" - and on
    # Linux CI it would read "connect ECONNREFUSED ...". Writing the Linux text by hand would be
    # fabricating a fixture, which is the exact mistake that produced a false negative earlier
    # in this project. Replaced with a break whose message Node renders identically everywhere.
    ("https_protocol_mismatch",
     'require("http") becomes require("https"), so https.get() is handed an http:// URL',
     sub('const http = require("http");', 'const http = require("https");'),
     "log says the PROTOCOL is wrong and points at the URL; the bug is the require line 20 lines "
     "above, and the URL is correct"),

    ("callback_arity",
     "http.get callback written err-first: (res) => becomes (err, res) =>",
     sub('.get(`http://localhost:${PORT}`, (res) => {', '.get(`http://localhost:${PORT}`, (err, res) => {'),
     "log blames res.statusCode being undefined; the actual bug is the callback signature, "
     "since http.get is not err-first"),

    ("uninterpolated_url",
     "template literal replaced with a plain string, so ${PORT} never expands",
     sub("`http://localhost:${PORT}`", '"http://localhost:PORT"'),
     "log reads ENOTFOUND/Invalid URL - looks like DNS, is a missing backtick"),

    ("inverted_status_check",
     "res.statusCode === 200 becomes res.statusCode >= 400",
     sub("res.statusCode === 200", "res.statusCode >= 400"),
     "log says 'unexpected status 200' - the status it names is the CORRECT one, so the message "
     "contradicts itself"),

    ("route_wrong_status",
     'the route replies 500: res.send("ok") becomes res.status(500).send("ok")',
     sub('app.get("/", (req, res) => res.send("ok"));',
         'app.get("/", (req, res) => res.status(500).send("ok"));'),
     "log points at the statusCode check on line ~32; the bug is the route on line ~18"),

    ("multi_hunk",
     "TWO independent breaks: err-first callback AND the route replies 500",
     chain(sub('.get(`http://localhost:${PORT}`, (res) => {', '.get(`http://localhost:${PORT}`, (err, res) => {'),
           sub('app.get("/", (req, res) => res.send("ok"));',
               'app.get("/", (req, res) => res.status(500).send("ok"));')),
     "the TypeError fires first and is ALL the log shows; fixing only that leaves the 500 route "
     "broken, so a single-hunk answer still fails"),
]

# Kept for comparability with the original rig, but byte-exact vs golden is the real score.
CHECKS = {
    "https_protocol_mismatch": ([r'require\(\s*"http"\s*\)'], [r'require\(\s*"https"\s*\)']),
    "callback_arity":        ([r"\(res\)\s*=>\s*\{"], [r"\(err,\s*res\)\s*=>"]),
    "uninterpolated_url":    ([r"`http://localhost:\$\{PORT\}`"], [r'"http://localhost:PORT"']),
    "inverted_status_check": ([r"statusCode\s*===\s*200"], [r"statusCode\s*>=\s*400"]),
    "route_wrong_status":    ([r'res\.send\("ok"\)'], [r"status\(500\)"]),
    "multi_hunk":            ([r"\(res\)\s*=>\s*\{", r'res\.send\("ok"\)'],
                              [r"\(err,\s*res\)\s*=>", r"status\(500\)"]),
}


def real_log(broken_src, name):
    """Run the broken file under node and capture what CI would actually print.

    Must run inside app/ so `require("express")` resolves; anywhere else every case degenerates
    to MODULE_NOT_FOUND and says nothing about the real bug.
    """
    tmp = os.path.join(APP, "_hardcase.js")
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(broken_src)
    env = dict(os.environ, PORT="5099")
    try:
        p = subprocess.run(["node", "_hardcase.js"], capture_output=True, text=True,
                           timeout=40, env=env, cwd=APP)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return None, None            # a hanging case is unusable - caller drops it
    finally:
        try: os.remove(tmp)
        except OSError: pass
    out = out.replace(tmp.replace("\\", "/"), "/home/runner/work/app/app/test.js")
    out = out.replace(tmp, "/home/runner/work/app/app/test.js")
    out = out.replace("_hardcase.js", "test.js")
    out = re.sub(r"\n\s*at .*node:internal.*", "", out)
    out = re.sub(r"\n\s*at (Module|Function|Object\.\.|require).*", "", out)
    lines = [l for l in out.split("\n") if l.strip()][:14]
    return "\n".join(lines) + "\nError: Process completed with exit code %d." % rc, rc


cases, dropped = [], []
for name, desc, fn, why in BREAKS:
    broken = fn(good)
    assert broken != good, "transform did not change the file: " + name
    log, rc = real_log(broken, name)
    if log is None:
        dropped.append((name, "hangs - no bounded log"))
        continue
    if rc == 0:
        dropped.append((name, "exits 0 - would never trigger a heal"))
        continue
    body = re.sub(NLPAT + r"Error: Process completed.*$", "", log).strip()
    # Reject ONLY an EMPTY diagnostic, not a short one. A first cut used len(body) < 40 and
    # silently dropped the two best cases - "CODE_FAILURE: unexpected status 200" is 35 chars,
    # comes from the app's own console.error (so it is identical on every platform), and is
    # precisely the self-contradicting log this set wants. Short != uninformative.
    if re.fullmatch(r"(CODE_FAILURE|INFRA_FAILURE):\s*", body):
        # Node left err.message empty -> no signal for the model at all, and the text would
        # differ on the Linux runner anyway. Unfair, not hard.
        dropped.append((name, "log has no diagnostic content (%r) - likely platform-specific" % body[:40]))
        continue
    must, mustnot = CHECKS[name]
    cases.append({"name": name, "break": desc, "why_hard": why, "code": broken, "log": log,
                  "must": must, "mustnot": mustnot})
    print("%-22s code=%4dch log=%3dch exit=%d" % (name, len(broken), len(log), rc))
    print("    " + log.replace("\n", "\n    ")[:300])
    print()

for name, reason in dropped:
    print("DROPPED %-20s %s" % (name, reason))

out = os.path.join(HERE, "cases", "hard_cases.json")
io.open(out, "w", encoding="utf-8").write(
    json.dumps({"golden": good, "cases": cases}, indent=1))
print("wrote %s (%d usable hard cases, golden %d chars)" % (out, len(cases), len(good)))
sys.exit(0 if len(cases) >= 5 else 1)
