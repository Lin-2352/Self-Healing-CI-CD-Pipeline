"""Per-execution evidence from the live n8n database: what the model was actually sent and how it ran.

Reads %LOCALAPPDATA%\\selfheal-n8n\\.n8n\\database.sqlite (read-only). Execution data is stored
in n8n's `flatted` format, so fields are pulled with anchored regexes over the raw text instead of
a full decode - enough for flat scalars and for spotting the prompt's CI-log section.

Per execution: status, whether the prompt carried a REAL CI log (section present and not
"(log unavailable)"), llama-server timings (prompt_n, cache_n, predicted_n, predicted_per_second,
draft acceptance) and the model node's executionTime.

Usage:  python tools/golive/exec_stats.py [first_execution_id]
"""
import os, re, sqlite3, sys

DB = os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n", ".n8n", "database.sqlite")
since = int(sys.argv[1]) if len(sys.argv) > 1 else 0
c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
rows = c.execute("select e.id, e.status, d.data from execution_entity e join execution_data d "
                 "on d.executionId = e.id where e.id >= ? order by e.id", (since,)).fetchall()


def num(key, d):
    m = re.search(r'"%s":(-?[\d.]+)' % key, d)
    return m.group(1) if m else "-"


print("%-4s %-8s %-8s %8s %7s %9s %7s %7s %7s  %s" % ("exec", "status", "ci_log", "prompt_n", "cache_n",
                                                        "predicted", "tok/s", "accept", "model_s", "retry/error"))
for eid, status, d in rows:
    # the prompt is stored JSON-escaped: a real newline appears as the two characters \n
    sec = re.search(r"--- CI failure log ---\\n(.{0,40})", d)
    ci = "absent" if not sec else ("unavail" if sec.group(1).startswith("(log unavailable)") else "REAL")
    acc = "-"
    dn, da = num("draft_n", d), num("draft_n_accepted", d)
    if dn not in ("-", "0") and da != "-":
        acc = "%.3f" % (float(da) / float(dn))
    tps = num("predicted_per_second", d)
    # the model call is by far the slowest node, so the max executionTime IS the model call - the
    # number to hold against the quick tunnel's 100 s cut-off (a 524 shows up in `err`)
    et = [int(x) for x in re.findall(r'"executionTime":(\d+)', d)]
    model_s = "%.1f" % (max(et) / 1000.0) if et else "-"
    retry = "retry(think)" if '"enable_thinking":true' in d else ""
    em = re.search(r'"message":"([^"]{0,90})', d) if status != "success" else None
    print("%-4s %-8s %-8s %8s %7s %9s %7s %7s %7s  %s" % (eid, status, ci, num("prompt_n", d), num("cache_n", d),
                                                          num("predicted_n", d),
                                                          tps if tps == "-" else "%.2f" % float(tps), acc, model_s,
                                                          (retry + " " + (em.group(1) if em else "")).strip()))
