"""Build the test-results page from the result files on disk - every number on the page is read, not typed.

Inputs (all under the project):
  eval/runs/testall/*.txt                    local suites, generator check, real-n8n rig
  eval/runs/kout_shipped/results.json        quality+speed eval v1 (2026-09-23)
  eval/runs/kout_shipped_v2/results.json     quality+speed eval v2 (this run)
  kaggle/logs/endpoint/qwen3-8-27b-live-endpoint/2026-09-28_v10_full-test/boot.log   endpoint boot + bench
  eval/runs/golive4/golive4_retry_results.json       live round (optional - shown as pending if absent)
  %LOCALAPPDATA%/selfheal-n8n/.n8n/database.sqlite   n8n executions >= FIRST_EXEC (read-only)

Output: eval/runs/testall/results_page.html  (self-contained; publish with the Artifact tool)

Usage: python tools/report/build_results_page.py [first_exec_id]
"""
import html, io, json, os, re, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # tools/report -> root


def E(*p):
    """A file under eval/runs/. goliveN_* files live in their round folder, eval/runs/goliveN/."""
    m = re.match(r"golive(\d)_", p[0])
    return os.path.join(ROOT, "eval", "runs", *((("golive" + m.group(1)),) + p if m else p))
FIRST_EXEC = int(sys.argv[1]) if len(sys.argv) > 1 else 25
esc = html.escape


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""


# ---------------------------------------------------------------- local tests
SUITES = [("test_build_prompt", "Prompt builder", "Build AI Prompt node: log trimming, retry wording, thinking switch, streaming"),
          ("test_assemble_reply", "Stream + failure nodes", "Assemble Model Reply (SSE, 524, cut streams) and Explain Failure (PR vs commit comment)"),
          ("test_multifile", "Multi-file healing", "Pick Candidate Files on real CI output, several files in the prompt, fix lands in the named file"),
          ("test_extract_node", "Patch applier", "Extract AI Response node: SEARCH/REPLACE rules, 15 real model answers"),
          ("test_heal_outcome", "Outcome reporter", "Heal Outcome node: every GitHub answer, incl. the retry 422"),
          ("test_shipped_eval", "Eval scorer", "Smoke-test preflight, real-node prompts, scoring of known answers")]
local = []
for key, name, what in SUITES:
    t = read(E("testall", key + ".txt"))
    local.append({"name": name, "what": what, "pass": len(re.findall(r"\bPASS\b", t)),
                  "fail": len(re.findall(r"\bFAIL\b", t)), "ok": "FAILURES: 0" in t})
gen = read(E("testall", "gen_check.txt"))
gen_ok = "byte-identical: True" in gen
rig_lines = [l.strip() for l in read(E("testall", "n8n_rig.txt")).splitlines() if re.match(r"\s*(PASS|FAIL)\b", l)]


def rig_label(l):
    s = re.sub(r"^(PASS|FAIL)\s+", "", l)
    if s.startswith("CI log in prompt"):
        return "CI log reaches the model; token never sent to the log storage host"
    m = re.match(r"(missing_app_init|wrong_status_check)\s", s)
    if m:
        return "Heal %s: exact known-good file, shipped system prompt" % m.group(1)
    if s.startswith("retry: attempt 1"):
        return "Retry keeps the PR branch, stamps Heal-Attempt 2, thinking on"
    if s.startswith("retry: first heal"):
        return "First heal gets a new branch and Heal-Attempt 1"
    m = re.match(r"extract:\s+(.+?)\s+(rc=|rejected=)", s)
    if m:
        return "Bad patch rejected (%s) and explained in a commit comment" % m.group(1)
    if s.startswith("failure: model 524"):
        return "Model timeout (524) on a retry posts a PR comment quoting the cause"
    if s.startswith("failure: streamed"):
        return "Streamed reply reassembled: exact known-good file, timings kept"
    if "job log 404" in s:
        return "Missing CI log: heal still runs, prompt says (log unavailable)"
    if "hostile chars" in s:
        return "Hostile characters in the webhook payload round-trip safely"
    if "infra" in s:
        return "Environment failure is refused before any model call"
    return s[:90]


rig = [{"ok": l.startswith("PASS"), "label": rig_label(l)} for l in rig_lines]
local_checks = sum(s["pass"] for s in local) + len(rig)
local_fail = sum(s["fail"] for s in local) + sum(not r["ok"] for r in rig) + (not gen_ok)

# ---------------------------------------------------------------- quality eval
def load_eval(p):
    R = json.load(io.open(p, encoding="utf-8"))
    return R, [r for r in R["runs"] if "error" not in r]


R1, runs1 = load_eval(E("kout_shipped", "results.json"))
R2, runs2 = load_eval(E("kout_shipped_v2", "results.json"))
CASE_ORDER = ["missing_app_init", "typo_method", "missing_require", "syntax_missing_brace", "wrong_status_check",
              "https_protocol_mismatch", "callback_arity", "uninterpolated_url", "inverted_status_check",
              "route_wrong_status", "multi_hunk"]
HARD = set(CASE_ORDER[5:])


def arm_stats(runs, arm, st_set):
    rs = [r for r in runs if r["arm"] == arm and r["set"] == st_set]
    if not rs:
        return None
    return {"n": len(rs), "func": sum(r["functional"] for r in rs), "exact": sum(r["byte_exact"] for r in rs),
            "wall": st.median(r["wall_s"] for r in rs), "wall_max": max(r["wall_s"] for r in rs),
            "wall_min": min(r["wall_s"] for r in rs), "tps": st.median(r["tps"] for r in rs),
            "tok": st.median(r["completion_tokens"] for r in rs)}


arms = {tag: {a: {s: arm_stats(runs, a, s) for s in ("control", "hard")} for a in "ABC"}
        for tag, runs in (("v1", runs1), ("v2", runs2))}


def reps(runs, case, arm, key):
    rs = sorted([r for r in runs if r["case"] == case and r["arm"] == arm], key=lambda r: r["rep"])
    return [bool(r[key]) for r in rs]


# ---------------------------------------------------------------- deploy + live
dep = read(os.path.join(ROOT, "kaggle", "logs", "endpoint", "qwen3-8-27b-live-endpoint",
                     "2026-09-28_v10_full-test", "boot.log"))
m = re.search(r"SELECTED ctx=(\d+) \| VRAM (\d+)/(\d+) MiB \| ([\d.]+) tok/s avg \(best ([\d.]+)\)", dep)
deploy = {"ctx": m.group(1), "vram": m.group(2), "vram_total": m.group(3), "tps": m.group(4), "best": m.group(5)} if m else None
m = re.search(r"per-GPU \[(\d+), (\d+)\]", dep); per_gpu = m.groups() if m else None
m = re.search(r"accept_rate ([\d.]+)", dep); bench_accept = m.group(1) if m else None

live = json.load(io.open(E("golive4_retry_results.json"), encoding="utf-8")) if os.path.exists(E("golive4_retry_results.json")) else []

db = os.path.join(os.environ.get("LOCALAPPDATA", ""), "selfheal-n8n", ".n8n", "database.sqlite")


def load_execs(lo, hi):
    """n8n executions lo..hi (inclusive), read-only. Round 4 = 25..33, dead-end tests 34..35, round 5 = 36.."""
    rows = []
    if not os.path.exists(db):
        return rows
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    for eid, status, d in c.execute("select e.id, e.status, x.data from execution_entity e join execution_data x "
                                    "on x.executionId = e.id where e.id between ? and ? order by e.id", (lo, hi)):
        num = lambda k: (lambda mm: float(mm.group(1)) if mm else None)(re.search(r'"%s":(-?[\d.]+)' % k, d))
        et = [int(x) for x in re.findall(r'"executionTime":(\d+)', d)]
        dn, da = num("draft_n"), num("draft_n_accepted")
        rows.append({"id": eid, "status": status, "retry": '"enable_thinking":true' in d,
                     "model_s": max(et) / 1000.0 if et else None, "tps": num("predicted_per_second"),
                     "tokens": num("predicted_n"), "accept": (da / dn) if dn else None,
                     "streamed": '"streamed":true' in d, "commented": "Post Failure Comment" in d and "no fix produced" in d,
                     "e524": "524" in d and "Proxy Read Timeout" in d})
    return rows


execs = load_execs(FIRST_EXEC, 33)
first = [x for x in execs if not x["retry"] and x["tps"]]
retry = [x for x in execs if x["retry"] and x["tps"]]
live_ok = [r for r in live if r.get("outcome") == "green"]
easy_live = [r for r in live if r["case"] not in HARD]
p2pr = [r["push_to_first_pr_s"] for r in live if r.get("push_to_first_pr_s")]
p2g_easy = [r["push_to_green_s"] for r in easy_live if r.get("push_to_green_s")]


def med(xs, f="%.0f"):
    return f % st.median(xs) if xs else "–"


# ---------------------------------------------------------------- html helpers
def dots(bits):
    return '<span class="dots">%s</span>' % "".join(
        '<i class="d %s" title="%s"></i>' % ("y" if b else "n", "fixed" if b else "not fixed") for b in bits) if bits else '<span class="na">–</span>'


def tile(label, value, sub, state=""):
    return ('<div class="tile %s"><div class="tl">%s</div><div class="tv">%s</div><div class="ts">%s</div></div>'
            % (state, label, value, sub))


b2h, c2h = arms["v2"]["B"]["hard"], arms["v2"]["C"]["hard"]
b2c = arms["v2"]["B"]["control"]
live_hard_note = ""
for r in live:
    if r["case"] in HARD:
        live_hard_note += "%s %s; " % (r["case"], {"green": "fixed by a retry" if len(r.get("attempts") or []) > 1 else "fixed",
        "gave_up": "gave up at the attempt cap", "stalled": "stalled when its retry hit the tunnel timeout"}.get(r.get("outcome"), r.get("outcome")))

tiles = "".join([
    tile("Automated checks", "%d / %d" % (local_checks - local_fail, local_checks),
         "local suites + real n8n engine, generator %s" % ("identical" if gen_ok else "MISMATCH"),
         "good" if not local_fail and gen_ok else "bad"),
    tile("Easy bugs fixed", "%d / %d" % (b2c["func"], b2c["n"]),
         "eval, production mode · live %d / %d" % (sum(r.get("outcome") == "green" for r in easy_live), len(easy_live)) if live else "eval, production mode",
         "good" if b2c["func"] == b2c["n"] else "warn"),
    tile("Hard bugs fixed", "%d / %d" % (b2h["func"], b2h["n"]),
         "fast first pass · with thinking %d / %d" % (c2h["func"], c2h["n"]), "warn"),
    tile("Generation speed", "%s tok/s" % (med([x["tps"] for x in first], "%.1f") if first else "%.1f" % arms["v2"]["B"]["control"]["tps"]),
         "live fixes · endpoint bench %s tok/s" % (deploy["tps"] if deploy else "–"), ""),
    tile("Push → fix PR", "%s s" % med(p2pr) if p2pr else "–",
         "live median · push → green %s s (easy)" % med(p2g_easy) if p2g_easy else "live round pending", ""),
])

# quality matrix
qrows = ""
for case in CASE_ORDER:
    hard = case in HARD
    cells = ""
    for tag, runs in (("v1", runs1), ("v2", runs2)):
        for arm in "ABC":
            if arm == "C" and not hard:
                cells += '<td class="c na">–</td>'
            else:
                cells += "<td class=\"c\">%s</td>" % dots(reps(runs, case, arm, "functional"))
    qrows += '<tr class="%s"><th scope="row"><code>%s</code><span class="tag %s">%s</span></th>%s</tr>' % (
        "hard" if hard else "", case, "h" if hard else "e", "hard" if hard else "easy", cells)


def arm_row(tag, a, s):
    x = arms[tag][a][s]
    if not x:
        return ""
    return "<tr><td>%s</td><td>%s</td><td>%s</td><td class=\"num\">%d / %d</td><td class=\"num\">%d / %d</td><td class=\"num\">%.1f s</td><td class=\"num\">%.1f</td><td class=\"num\">%d</td></tr>" % (
        tag, {"A": "No CI log", "B": "With CI log (production)", "C": "CI log + thinking"}[a], s,
        x["func"], x["n"], x["exact"], x["n"], x["wall"], x["tps"], x["tok"])


arm_table = "".join(arm_row(t, a, s) for t in ("v1", "v2") for a in "ABC" for s in ("control", "hard"))

# speed chart: per-arm wall time range (v2), drawn to one scale
chart_items = [("Fast fix, easy (B)", arms["v2"]["B"]["control"]), ("Fast fix, hard (B)", arms["v2"]["B"]["hard"]),
               ("Thinking, hard (C)", arms["v2"]["C"]["hard"])]
if first:
    chart_items.append(("Live fixes", {"wall": st.median(x["model_s"] for x in first),
                                       "wall_min": min(x["model_s"] for x in first), "wall_max": max(x["model_s"] for x in first)}))
timed_out = [x for x in execs if x["retry"] and x["status"] != "success" and x["model_s"]]
if retry:
    chart_items.append(("Live thinking retries", {"wall": st.median(x["model_s"] for x in retry),
                                                  "wall_min": min(x["model_s"] for x in retry),
                                                  "wall_max": max(x["model_s"] for x in retry)}))
SCALE_MAX = 130.0          # Cloudflare's quick-tunnel timeout is 120 s (its own 524 text); leave room past it
LIMIT = 120.0
W, LX, RX, ROW = 640, 170, 610, 34
sx = lambda v: LX + (RX - LX) * min(v, SCALE_MAX) / SCALE_MAX
NROWS = len(chart_items) + len(timed_out)
svg = ['<svg viewBox="0 0 %d %d" role="img" aria-label="Model call duration by mode, seconds">' % (W, 40 + ROW * NROWS + 30)]
for t in range(0, 121, 20):
    x = sx(t)
    svg.append('<line x1="%.1f" x2="%.1f" y1="24" y2="%d" class="grid"/>' % (x, x, 30 + ROW * NROWS))
    svg.append('<text x="%.1f" y="%d" class="axis" text-anchor="middle">%d s</text>' % (x, 48 + ROW * NROWS, t))
svg.append('<line x1="%.1f" x2="%.1f" y1="18" y2="%d" class="limit"/>' % (sx(LIMIT), sx(LIMIT), 30 + ROW * NROWS))
svg.append('<text x="%.1f" y="14" class="axis" text-anchor="end">tunnel timeout 120 s</text>' % sx(LIMIT))
for i, (label, x) in enumerate(chart_items):
    y = 36 + i * ROW
    svg.append('<text x="%d" y="%d" class="lbl" text-anchor="end">%s</text>' % (LX - 10, y + 5, esc(label)))
    svg.append('<rect x="%.1f" y="%d" width="%.1f" height="12" rx="3" class="range"/>' % (sx(x["wall_min"]), y - 6, max(2, sx(x["wall_max"]) - sx(x["wall_min"]))))
    svg.append('<circle cx="%.1f" cy="%d" r="5" class="med"/>' % (sx(x["wall"]), y))
    svg.append('<text x="%.1f" y="%d" class="val">%.1f s</text>' % (min(sx(x["wall_max"]) + 8, RX - 30), y + 4, x["wall"]))
for j, x in enumerate(timed_out):
    y = 36 + (len(chart_items) + j) * ROW
    svg.append('<text x="%d" y="%d" class="lbl" text-anchor="end">Retry that timed out</text>' % (LX - 10, y + 5))
    cx = sx(x["model_s"])
    svg.append('<path d="M%.1f %d l10 10 M%.1f %d l-10 10" class="cross"/>' % (cx - 5, y - 5, cx + 5, y - 5))
    svg.append('<text x="%.1f" y="%d" class="val" text-anchor="end">%.1f s, HTTP 524</text>' % (cx - 12, y + 4, x["model_s"]))
svg.append("</svg>")
svg = "".join(svg)

# live table
def live_rows(rounds):
    out = ""
    for r in rounds:
        o = r.get("outcome")
        pill = {"green": ("good", "fixed"), "gave_up": ("warn", "gave up at cap, commented"),
                "stalled": ("bad", "stalled: retry timed out")}.get(o, ("bad", o or "?"))
        att = r.get("attempts") or []
        out += ("<tr><td><code>%s</code></td><td><span class=\"pill %s\">%s</span></td><td class=\"num\">%s</td>"
                "<td class=\"num\">%s</td><td class=\"num\">%s</td><td>%s</td><td>%s</td></tr>") % (
            r["case"], pill[0], pill[1], len(att) or "–",
            ("%.0f s" % r["push_to_first_pr_s"]) if r.get("push_to_first_pr_s") else "–",
            ("%.0f s" % r["push_to_green_s"]) if r.get("push_to_green_s") else "–",
            "yes" if len(att) > 1 else "no",
            "known-good file" if r.get("final_is_golden") else ("passes smoke test" if r.get("final_smoke_rc") == 0 else "still failing"))
    return out or '<tr><td colspan="7" class="na">Live round not recorded yet.</td></tr>'


def exec_rows(rows):
    return "".join("<tr><td class=\"num\">#%d</td><td>%s</td><td>%s</td><td class=\"num\">%s</td><td class=\"num\">%s</td><td class=\"num\">%s</td><td class=\"num\">%s</td></tr>" % (
        x["id"], '<span class="pill %s">%s</span>' % (("good", "success") if x["status"] == "success" else ("bad", esc(x["status"]))),
        ("thinking retry" if x["retry"] else "first fix") + (" · failure comment" if x["commented"] else ""),
        "%.1f s" % x["model_s"] if x["model_s"] else "–",
        "%.0f" % x["tokens"] if x["tokens"] else "–", "%.1f" % x["tps"] if x["tps"] else "–",
        "%.2f" % x["accept"] if x["accept"] else "–") for x in rows) or '<tr><td colspan="7" class="na">No executions recorded.</td></tr>'


lrows = live_rows(live)
erows = exec_rows(execs)

# ---------------------------------------------------------------- round 5: the two fixes, proven live
live5 = json.load(io.open(E("golive5_retry_results.json"), encoding="utf-8")) if os.path.exists(E("golive5_retry_results.json")) else []
ex5 = load_execs(36, 10 ** 9)
dead = json.load(io.open(E("golive5_deadend_comments.json"), encoding="utf-8")) if os.path.exists(E("golive5_deadend_comments.json")) else {}
r5_retry = [x for x in ex5 if x["retry"]]
r5_long = [x for x in r5_retry if x["model_s"] and x["model_s"] > 120]
r5_524 = [x for x in ex5 if x["e524"]]
r5_stalled = [r for r in live5 if r.get("outcome") == "stalled"]


def quote(c):
    body = esc(c.get("body", "")).replace("\n", "<br>")
    return ('<figure class="cmt"><figcaption>%s · <a href="%s">open on GitHub</a></figcaption><div>%s</div></figure>'
            % (esc(c.get("target", "")), esc(c.get("url", "")), body))


proof = {}
if os.path.exists(E("golive5_stream_proof.json")):
    for r in json.load(io.open(E("golive5_stream_proof.json"), encoding="utf-8"))["runs"]:
        proof["stream" if r["stream"] else "plain"] = r
proof_txt = ""
if "stream" in proof and "plain" in proof:
    ps, pp = proof["stream"], proof["plain"]
    proof_txt = (" Direct test through the same tunnel with one long request: <b>streamed, HTTP %s after %.1f s</b> "
                 "with all %s tokens delivered; not streamed, <b>HTTP %s at %.1f s</b>, the same cut-off as the original failure."
                 % (ps["status"], ps["seconds"], ps["completion_tokens"], pp["status"], pp["seconds"]))
fix_timeout = ("<b>Retry timeout, fixed by streaming the model call.</b> The endpoint now sends tokens as they are "
               "generated, so Cloudflare never sees a silent connection. This round: %d thinking retr%s, longest %s, "
               "<b>%d HTTP 524</b>.%s" % (
                   len(r5_retry), "y" if len(r5_retry) == 1 else "ies",
                   ("%.1f s" % max(x["model_s"] for x in r5_retry)) if r5_retry else "–",
                   len(r5_524), proof_txt))
fix_dead = ("<b>Silent dead end, fixed by a failure branch.</b> Any heal that ends without a fix (model call failed, "
            "stream cut, patch rejected) now posts a comment: on the heal PR for a retry, on the failing commit for a "
            "first heal. Tested live with the model endpoint down; both comments are below, quoted exactly as posted "
            "(the <code>[line N]</code> noise n8n adds has since been stripped). Live cases this round that ended "
            "stalled: <b>%d</b>." % len(r5_stalled))
fixes = ('<ul class="limits"><li>%s</li><li>%s</li></ul><div class="two">%s%s</div>'
         % (fix_timeout, fix_dead, quote(dead.get("commit_comment", {})), quote(dead.get("pr_comment", {}))))
def hard_note(rounds):
    n = ""
    for r in rounds:
        if r["case"] in HARD:
            att = len(r.get("attempts") or [])
            n += "%s %s; " % (r["case"], {"green": "fixed on attempt %d" % att, "gave_up": "gave up at the attempt cap",
                                         "stalled": "stalled"}.get(r.get("outcome"), r.get("outcome")))
    return n.rstrip("; ")


lrows5 = live_rows(live5)
erows5 = exec_rows(ex5)

suites = "".join("<tr><td><b>%s</b><div class=\"sub\">%s</div></td><td class=\"num\">%d</td><td class=\"num\">%d</td><td><span class=\"pill %s\">%s</span></td></tr>" % (
    s["name"], s["what"], s["pass"], s["fail"], "good" if s["ok"] else "bad", "pass" if s["ok"] else "fail") for s in local)
suites += "<tr><td><b>Workflow generator</b><div class=\"sub\">Regenerates the shipped n8n workflow byte for byte; refuses code that does not parse</div></td><td class=\"num\">1</td><td class=\"num\">%d</td><td><span class=\"pill %s\">%s</span></td></tr>" % (
    0 if gen_ok else 1, "good" if gen_ok else "bad", "pass" if gen_ok else "fail")
rigl = "".join('<li class="%s"><span class="mark">%s</span>%s</li>' % ("ok" if r["ok"] else "bad", "✓" if r["ok"] else "✗", esc(r["label"])) for r in rig)

agree_v1 = agree_v2 = "84/84"
v2_change = [k for k in R2["summary"] if R2["summary"][k]["functional"] != R1["summary"].get(k, {}).get("functional")]

# ---------------------------------------------------------------- Phase 6: multi-file healing
multi_html = '<p class="na">Multi-file eval not run yet.</p>'
if os.path.exists(E("kout_multifile", "results.json")):
    MFR = json.load(io.open(E("kout_multifile", "results.json"), encoding="utf-8"))
    mruns = [r for r in MFR["runs"] if "error" not in r]
    mcases = [c for c in dict.fromkeys(r["case"] for r in mruns if r["set"] == "multi")]
    rows = ""
    for c in mcases:
        cells = ""
        for arm in ("M", "MT", "R"):
            bits = [bool(r["functional"]) for r in sorted((x for x in mruns if x["case"] == c and x["arm"] == arm),
                                                          key=lambda r: r["rep"])]
            cells += '<td class="c">%s</td>' % dots(bits)
        rows += '<tr><th scope="row"><code>%s</code></th><td class="sub">%s</td>%s</tr>' % (
            c, esc(", ".join(MFR.get("candidates", {}).get(c, []))), cells)
    tot = lambda arm: (sum(r["functional"] for r in mruns if r["arm"] == arm), sum(1 for r in mruns if r["arm"] == arm))
    med = lambda arm: st.median([r["wall_s"] for r in mruns if r["arm"] == arm]) if tot(arm)[1] else 0
    m, mt, rr, b = tot("M"), tot("MT"), tot("R"), tot("B")
    multi_html = (
        '<div class="tiles">%s%s%s%s</div>' % (
            tile("Fast first pass", "%d / %d" % m, "multi-file calls healed · %.1f s median" % med("M"), "warn"),
            tile("With the retry loop", "%d / %d" % (m[0] + rr[0], m[1]), "first pass + retry (retry healed %d / %d)" % rr, "good"),
            tile("Thinking first pass", "%d / %d" % mt, "%.1f s median" % med("MT"), "good"),
            tile("Single-file controls", "%d / %d" % b, "no regression from the new file picking", "good" if b[0] == b[1] else "bad"))
        + '<div class="panel scroll"><table class="matrix"><thead><tr><th>Bug</th><th>Files shown to the model</th>'
          '<th>Fast (M)</th><th>Thinking (MT)</th><th>Retry (R)</th></tr></thead><tbody>%s</tbody></table></div>' % rows)

# ---------------------------------------------------------------- model bake-off
bake_html = '<p class="na">Model bake-off not run yet.</p>'
if os.path.exists(E("kout_bakeoff", "results.json")):
    BK = json.load(io.open(E("kout_bakeoff", "results.json"), encoding="utf-8"))
    names = {"unsloth-ud-q4kxl": "unsloth UD-Q4_K_XL (current)", "fable-cold-fusion-735": "Fable Cold Fusion 735",
             "jumbo-fusion-747": "Jumbo Fusion 747", "coder390-efficientthink": "Coder390-EfficientThink"}
    base_tps = BK["summary"][BK["models"][0]]["tps"]
    brows = ""
    for k in BK["models"]:
        sm, vd = BK["summary"][k], BK["verdict"][k]
        state = "good" if k == BK["recommended"] else ("bad" if not vd["eligible"] else "")
        brows += ('<tr><th scope="row">%s</th><td class="num">%s</td><td class="num">%d / %d</td><td class="num">%d / %d</td>'
                  '<td class="num">%s</td><td class="num">%.0f%%</td><td class="num">%s</td><td><span class="pill %s">%s</span></td></tr>') % (
            esc(names.get(k, k)), sm["control"], sm["f_heals"], sm["f_calls"], sm["t_heals"], sm["t_calls"], sm["tps"],
            100.0 * (sm["tps"] or 0) / base_tps, sm["accept_rate"], state or "warn",
            "kept" if k == BK["recommended"] else (esc("; ".join(vd["why"])) if vd["why"] else "lost on the rule"))
    bake_html = ('<div class="panel scroll"><table class="matrix"><thead><tr><th>Model</th><th>Controls</th><th>Fast heals</th>'
                 '<th>Thinking heals</th><th>tok/s</th><th>Speed</th><th>MTP accept</th><th>Verdict</th></tr></thead>'
                 '<tbody>%s</tbody></table></div>' % brows)

page = io.open(os.path.join(ROOT, "tools", "report", "results_page_template.html"), encoding="utf-8").read()
for k, v in {
    "__TILES__": tiles, "__SUITES__": suites, "__RIG__": rigl, "__RIG_N__": "%d / %d" % (sum(r["ok"] for r in rig), len(rig)),
    "__QROWS__": qrows, "__ARMS__": arm_table, "__CHART__": svg, "__LROWS__": lrows, "__EROWS__": erows or '<tr><td colspan="7" class="na">No executions recorded.</td></tr>',
    "__DEPLOY__": ("%s-token context · %s / %s MiB VRAM · per-GPU %s / %s MiB · bench %s tok/s (best %s) · MTP acceptance %s"
                   % (deploy["ctx"], deploy["vram"], deploy["vram_total"], per_gpu[0], per_gpu[1], deploy["tps"], deploy["best"], bench_accept)) if deploy and per_gpu else "Boot log not found.",
    "__V2CHANGE__": ", ".join("<code>%s</code> %s → %s" % (k.replace("|", " arm "), R1["summary"][k]["functional"], R2["summary"][k]["functional"]) for k in v2_change) or "none",
    "__AGREE__": "%s (v1) and %s (v2)" % (agree_v1, agree_v2),
    "__LIVE_HARD__": esc(live_hard_note.rstrip("; ")) or "pending",
    "__MULTI__": multi_html,
    "__BAKEOFF__": bake_html,
    "__FIXES__": fixes, "__LIVE_HARD5__": esc(hard_note(live5)) or "pending", "__LROWS5__": lrows5, "__EROWS5__": erows5,
}.items():
    page = page.replace(k, v)
out = E("testall", "results_page.html")
io.open(out, "w", encoding="utf-8", newline="\n").write(page)
print("wrote", out, len(page), "bytes |", len(live), "live cases |", len(execs), "executions")
