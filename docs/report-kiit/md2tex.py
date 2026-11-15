"""Convert the Markdown chapters of the project report into body fragments for the KIIT-format report.

Used by build_chapters.py. Differences from docs/report/md_to_tex.py: tables become booktabs tables with a
caption below (as in the reference report), short tables float with [!htbp], long tables use longtable, and
paths and identifiers may break at / and _ so that nothing runs into the page border.
"""
import io, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report"))
import md_to_tex as base   # noqa: E402

UN = base.UNICODE


def esc(s):
    out = []
    for ch in s:
        if ch in UN:
            out.append(UN[ch])
        elif ch == "\\":
            out.append(r"\textbackslash{}")
        elif ch in "&%$#{}":
            out.append("\\" + ch)
        elif ch == "_":
            out.append(r"\_\allowbreak{}")
        elif ch == "/":
            out.append(r"/\allowbreak{}")
        elif ch == "~":
            out.append(r"\textasciitilde{}")
        elif ch == "^":
            out.append(r"\textasciicircum{}")
        elif ch == "<":
            out.append(r"\textless{}")
        elif ch == ">":
            out.append(r"\textgreater{}")
        elif ch == "|":
            out.append(r"\textbar{}")
        elif ord(ch) > 127:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def code_inline(s):
    t = esc(s)
    t = t.replace(".", r".\allowbreak{}").replace("-", r"-\allowbreak{}").replace(":", r":\allowbreak{}")
    return r"\texttt{" + t + "}"


TOKEN = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)]+\))")


def inline(s):
    out = []
    for p in TOKEN.split(s):
        if not p:
            continue
        if p.startswith("`") and p.endswith("`") and len(p) > 2:
            out.append(code_inline(p[1:-1]))
        elif p.startswith("**") and p.endswith("**"):
            out.append(r"\textbf{" + inline(p[2:-2]) + "}")
        elif p.startswith("*") and p.endswith("*") and len(p) > 2:
            out.append(r"\emph{" + inline(p[1:-1]) + "}")
        elif p.startswith("[") and "](" in p:
            text, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", p).groups()
            out.append(inline(text))
        else:
            out.append(esc(p))
    return "".join(out)


def table(rows, caption, label):
    header, body = rows[0], rows[2:]
    n = len(header)
    cols = [[r[i] if i < len(r) else "" for r in [header] + body] for i in range(n)]
    def longest_word(col):
        return max((len(w) for c in col for w in re.sub(r'[`*]', '', c).split()), default=4)
    widths = [max(min(max(len(c) for c in col), 48), 6, int(longest_word(col) * 1.9) + 3) for col in cols]
    tot = float(sum(widths))
    spec = "".join(r">{\raggedright\arraybackslash}p{%.3f\dimexpr\linewidth-%d\tabcolsep\relax}" % (w / tot, 2 * n)
                   for w in widths)
    body_tex = [" & ".join(r"\textbf{" + inline(c) + "}" for c in header) + r" \\", r"\midrule"]
    for r in body:
        r = r + [""] * (n - len(r))
        body_tex.append(" & ".join(inline(c) for c in r[:n]) + r" \\")
    cap = r"\caption{%s}\label{%s}" % (inline(caption), label)
    if True:   # every table is a longtable: no floats that leave holes in the page
        return "\n".join([r"\begingroup\small", r"\begin{longtable}{" + spec + "}", r"\toprule", body_tex[0],
                          r"\midrule", r"\endhead", r"\bottomrule", r"\endfoot", r"\endlastfoot", *body_tex[2:],
                          r"\bottomrule", cap + r"\\", r"\end{longtable}", r"\endgroup", ""])
    return "\n".join([r"\begin{table}[!htbp]", r"\centering", r"\small", r"\begin{tabular}{" + spec + "}", r"\toprule",
                      body_tex[0], r"\midrule", *body_tex[2:], r"\bottomrule", r"\end{tabular}", cap,
                      r"\end{table}", ""])


def convert(md, chapter_key, demote=0):
    """Body of one chapter. `##` headings are dropped (the caller writes \\chapter)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, para, i, last_heading, tcount = [], [], 0, "Overview", 0

    def voice(t):
        """The report is written by a team: 'I recomputed' becomes 'we recomputed'."""
        t = re.sub(r"\bI (recomputed|did|could|checked|found|ran|used|treat|observed|counted|re-scored|read|took|compared)\b",
                   r"we \1", t)
        t = re.sub(r"(^|[.!?] )we ", lambda m: m.group(1) + "We ", t)
        return t.replace("my arithmetic", "our arithmetic").replace("my own", "our own")

    def flush():
        if para:
            out.append(inline(voice(" ".join(para))) + "\n")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            txt = "\n".join(buf).replace("\u2192", "->").replace("\u2014", "--").replace("\u2013", "-")
            out.append("\\begin{lstlisting}\n" + txt + "\n\\end{lstlisting}\n")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            flush()
            lvl, text = len(m.group(1)), m.group(2).strip()
            if lvl <= 2:
                i += 1
                continue
            last_heading = text
            names = ["section", "subsection", "subsubsection", "paragraph"]
            out.append("\\%s{%s}\n" % (names[lvl - 3 + demote], inline(text)))
            i += 1
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(base.split_row(lines[i])); i += 1
            tcount += 1
            out.append(table(rows, last_heading, "tab:%s-%d" % (chapter_key, tcount)))
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m:
            flush()
            env = "enumerate" if m.group(2)[0].isdigit() else "itemize"
            out.append("\\begin{%s}[leftmargin=*, itemsep=2pt]" % env)
            while i < len(lines):
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not m:
                    if lines[i].startswith("  ") and lines[i].strip():
                        out[-1] += " " + inline(lines[i].strip()); i += 1; continue
                    break
                out.append("\\item " + inline(m.group(3))); i += 1
            out.append("\\end{%s}\n" % env)
            continue
        if not ln.strip():
            flush(); i += 1; continue
        para.append(ln.strip()); i += 1
    flush()
    return "\n".join(out)
