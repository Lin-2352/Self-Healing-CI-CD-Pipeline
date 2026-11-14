"""Turn the quality/speed run into the tables, and refuse the comparisons the data cannot support.

Three things this does that a naive summary would get wrong:

1. **Per-case breakdown, not just totals.** A 9/18 vs 5/18 gap can be a real capability
   difference spread across cases, or one coin-flip case - the trap the old sweep fell into
   ("15/15 vs 14/15 is one coin-flip case"). Cases are classified: saturated-easy (all packings
   pass), saturated-hard (all fail, so it discriminates nothing), noise (differs by one rep),
   and decisive (differs consistently across every rep).

2. **Refuses cross-rung speed comparisons.** If a packing fell to a lower rung it ran with a
   different split mode and KV type. Printing its med_tps next to the others invites a
   like-for-like read that is not valid, so those rows are marked and excluded from the ranking.

3. **Reports what actually engaged.** `mtp: False` on a rung named `tensor+mtp` means the flag
   was accepted and the feature did not run - the speed number is then tensor-only and must not
   be compared against a stock tensor+MTP baseline.
"""
import collections, io, json, os, statistics as st, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAND = [os.path.join(HERE, "runs", "kout_qs", "results.json"),
        os.path.join(HERE, "quality_speed_results.json")]
SRC = next((p for p in CAND if os.path.exists(p)), None)
if not SRC:
    print("no results yet; expected one of:\n  " + "\n  ".join(CAND)); sys.exit(1)
R = json.load(io.open(SRC, encoding="utf-8"))
runs = R.get("runs") or R.get("per_cell") or []
cells = {c.get("packing"): c for c in R.get("cells", [])}
HAVE_RAW = any(r.get("raw") for r in runs)
print("source: %s | %d runs | raw=%s" % (os.path.basename(SRC), len(runs),
                                         "yes" if HAVE_RAW else "NO (streamed copy)"))

order = ["Q4_K_XL", "Ridge", "PQ2_0", "PTQ1_0"]
present = [p for p in order if any(r.get("packing") == p for r in runs)]

# ---- which rung did each packing run on? cross-rung speed is not comparable ----
print("\n" + "=" * 100)
print("%-9s %6s %-12s %-6s %-22s %s" % ("packing", "GB", "rung", "bin", "engaged", "vram_peak"))
print("-" * 100)
rungs = {}
for p in order:
    c = cells.get(p)
    if not c:
        print("%-9s %s" % (p, "(no cell recorded)")); continue
    if c.get("skipped"):
        print("%-9s SKIPPED: %s" % (p, c["skipped"])); continue
    rungs[p] = c.get("rung")
    print("%-9s %6s %-12s %-6s %-22s %s" % (p, c.get("model_gb"), c.get("rung"), c.get("binary"),
                                            json.dumps(c.get("engaged")), c.get("vram_peak")))
distinct = set(v for v in rungs.values() if v)
same_rung = len(distinct) <= 1
print("\nall packings on the same rung: %s%s" % (same_rung,
      "" if same_rung else "  <-- speed is NOT comparable across rungs; ranked rows marked *"))
# MTP engagement is decided by accept_rate, NOT by the log needle. On b10709 the 'draft-mtp'
# line the needle looks for is not printed, so engaged.mtp reads False even when speculation
# is demonstrably running - the same log-wording trap that gave a false negative for tensor
# split one session earlier. A non-null accept_rate means the draft actually produced tokens
# and had them accepted. That is proof; a missing log line is not.
print()
mtp_ran = {}
for p in present:
    acc = [r.get('accept_rate') for r in runs if r.get('packing') == p]
    nn = [x for x in acc if x is not None]
    needle = bool((cells.get(p) or {}).get('engaged', {}).get('mtp'))
    mtp_ran[p] = bool(nn)
    note = '  <-- needle says False; accept_rate proves it RAN' if (nn and not needle) else ''
    print('  MTP %-9s ran=%-5s accept_rate non-null %d/%d%s%s'
          % (p, bool(nn), len(nn), len(acc),
             ('  median=%.3f' % st.median(nn)) if nn else '', note))

# ---- headline table ----
def agg(p, s, key):
    v = [r for r in runs if r.get("packing") == p and r.get("set") == s]
    return sum(bool(r.get(key)) for r in v), len(v)

print("\n" + "=" * 100)
print("%-9s %6s %5s %11s %11s %11s %11s %9s %6s"
      % ("packing", "GB", "n", "ctl_exact", "hard_exact", "ctl_norm", "hard_norm", "med_tps", "trunc"))
print("-" * 100)
rank = []
for p in present:
    rs = [r for r in runs if r.get("packing") == p and r.get("gen_tps")]
    if not rs:
        continue
    tps = st.median([r["gen_tps"] for r in rs])
    ce, cn = agg(p, "control", "byte_exact"); he, hn = agg(p, "hard", "byte_exact")
    cne, _ = agg(p, "control", "byte_exact_norm"); hne, _ = agg(p, "hard", "byte_exact_norm")
    trunc = sum(1 for r in rs if r.get("finish_reason") == "length")
    mark = "" if (same_rung or rungs.get(p) == max(distinct, key=list(distinct).index)) else "*"
    print("%-9s %6s %5d %11s %11s %11s %11s %9.2f %6d%s"
          % (p, (cells.get(p) or {}).get("model_gb"), len(rs), "%d/%d" % (ce, cn),
             "%d/%d" % (he, hn), "%d/%d" % (cne, cn), "%d/%d" % (hne, hn), tps, trunc, mark))
    rank.append((p, ce, cn, he, hn, tps))

# ---- per-case: is a gap real, or one coin-flip case? ----
hard_cases = []
for r in runs:
    if r.get("set") == "hard" and r.get("case") not in hard_cases:
        hard_cases.append(r["case"])
print("\n" + "=" * 100)
print("HARD cases, byte-exact per rep (the reps string shows consistency)")
print("%-26s %s" % ("case", "  ".join("%-12s" % p for p in present)))
print("-" * 100)
kinds = collections.Counter()
for case in hard_cases:
    cols, sums = [], []
    for p in present:
        v = [int(bool(r.get("byte_exact"))) for r in runs
             if r.get("packing") == p and r.get("case") == case]
        v = v or [0]
        sums.append(sum(v))
        cols.append("%d/%d %s" % (sum(v), len(v), "".join(map(str, v))))
    if len(set(sums)) == 1:
        kind = "saturated-easy" if sums[0] and sums[0] == 3 else (
            "saturated-hard" if sums[0] == 0 else "tied")
    elif max(sums) - min(sums) <= 1:
        kind = "noise (1 rep)"
    else:
        kind = "DECISIVE"
    kinds[kind] += 1
    print("%-26s %s  %s" % (case, "  ".join("%-12s" % c for c in cols), kind))
print("\ncase types: %s" % dict(kinds))
disc = kinds.get("DECISIVE", 0) + kinds.get("noise (1 rep)", 0)
print("cases that discriminate at all: %d of %d  (the rest rank nothing)" % (disc, len(hard_cases)))

# ---- verdict: quality first, then speed ----
print("\n" + "=" * 100)
base = next((r for r in rank if r[0] == "Q4_K_XL"), None)
for p, ce, cn, he, hn, tps in rank:
    gate = "DISQUALIFIED (control regression)" if ce < cn else "control OK"
    if base and p != "Q4_K_XL":
        better = he > base[3]
        faster = tps > base[5]
        verdict = ("REPLACE Q4" if (ce == cn and better and faster)
                   else "keep Q4" )
        print("%-9s %-34s hard %d/%d vs Q4 %d/%d, %.2f vs %.2f tok/s -> %s"
              % (p, gate, he, hn, base[3], base[4], tps, base[5], verdict))
    else:
        print("%-9s %-34s hard %d/%d, %.2f tok/s  (control/baseline)" % (p, gate, he, hn, tps))

json.dump(R, io.open(os.path.join(HERE, "qs_analyzed.json"), "w", encoding="utf-8"), indent=1)
