"""Turn docs/PROJECT_REPORT.md into docs/report/main.tex, so the PDF and the Markdown report can never differ.

Only the Markdown the report actually uses is understood: headings (#, ##, ###, ####), paragraphs,
flat bullet and numbered lists, pipe tables, fenced code blocks, images, **bold**, *italic*, `code`
and [text](url) links. Anything else is passed through as plain text.

    python docs/report/md_to_tex.py            writes docs/report/main.tex
    python docs/report/build.py                 converts and runs latexmk (see build.py)

Section numbering comes from LaTeX, so headings in the Markdown carry no numbers. `##` is a chapter, `###` a
section, `####` a subsection. The first H1 is the
document title; the lines directly under it (author, date, course) go to the title block.
"""
import io, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
SRC = os.path.join(DOCS, "PROJECT_REPORT.md")
OUT = os.path.join(HERE, "main.tex")

UNICODE = {
    "\u2192": r"$\rightarrow$", "\u2190": r"$\leftarrow$", "\u2194": r"$\leftrightarrow$", "\u00d7": r"$\times$",
    "\u2248": r"$\approx$", "\u2264": r"$\leq$", "\u2265": r"$\geq$", "\u2260": r"$\neq$", "\u00b1": r"$\pm$",
    "\u2013": "--", "\u2014": "---", "\u2018": "`", "\u2019": "'", "\u201c": "``", "\u201d": "''",
    "\u2026": r"\ldots{}", "\u00a0": "~", "\u2713": r"$\checkmark$", "\u2022": r"$\bullet$", "\u00b2": r"$^2$",
    "\u00b5": r"$\mu$", "\u03bc": r"$\mu$", "\u00b7": r"$\cdot$", "\u2212": "-", "\u00e9": r"\'e", "\u00e4": r'\"a',
    "\u00f6": r'\"o', "\u00fc": r'\"u', "\u202f": "~", "\u2009": r"\,",
}


def esc(s):
    """Escape plain text for LaTeX."""
    out = []
    for ch in s:
        if ch in UNICODE:
            out.append(UNICODE[ch])
        elif ch == "\\":
            out.append(r"\textbackslash{}")
        elif ch in "&%$#_{}":
            out.append("\\" + ch)
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
    """\\texttt with breakable characters; s is raw text."""
    t = esc(s)
    t = t.replace("/", r"/\allowbreak{}").replace(r"\_", r"\_\allowbreak{}").replace(".", r".\allowbreak{}")
    t = t.replace("-", r"-\allowbreak{}")
    return r"\texttt{" + t + "}"


TOKEN = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|!\[[^\]]*\]\([^)]+\)|\[[^\]]+\]\([^)]+\))")


def inline(s):
    parts = TOKEN.split(s)
    out = []
    for p in parts:
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
            if re.match(r"https?://", url):
                out.append(r"\href{" + url.replace("%", r"\%").replace("#", r"\#") + "}{" + inline(text) + "}")
            else:
                out.append(inline(text))              # relative repo links are meaningless in a PDF
        else:
            out.append(esc(p))
    return "".join(out)


def table(rows):
    header, body = rows[0], rows[2:]
    n = len(header)
    widths = [max([len(r[i]) if i < len(r) else 0 for r in [header] + body] + [4]) for i in range(n)]
    tot = float(sum(min(w, 60) for w in widths))
    spec = "".join(r">{\raggedright\arraybackslash}p{%.3f\dimexpr\linewidth-%d\tabcolsep\relax}" % (min(w, 60) / tot, 2 * n)
                   for w in widths)
    lines = [r"\begin{longtable}{" + spec + "}", r"\toprule"]
    lines.append(" & ".join(r"\textbf{" + inline(c) + "}" for c in header) + r" \\")
    lines += [r"\midrule", r"\endhead"]
    for r in body:
        r = r + [""] * (n - len(r))
        lines.append(" & ".join(inline(c) for c in r[:n]) + r" \\")
    lines += [r"\bottomrule", r"\end{longtable}", ""]
    return "\n".join(lines)


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def convert(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, title, meta = [], 0, None, []
    para = []

    def flush():
        if para:
            out.append(inline(" ".join(para)) + "\n")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            lang = ln[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            out.append(r"\begin{lstlisting}" + "\n" + "\n".join(buf).replace("\u2192", "->").replace("\u2014", "--")
                       .replace("\u2013", "-") + "\n" + r"\end{lstlisting}" + "\n")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            flush()
            lvl, text = len(m.group(1)), m.group(2).strip()
            if lvl == 1 and title is None:
                title = text
                i += 1
                while i < len(lines) and not lines[i].startswith("## "):
                    if lines[i].strip():
                        meta.append(lines[i].strip())
                    i += 1
                continue
            cmd = {1: "chapter", 2: "chapter", 3: "section", 4: "subsection"}[lvl]
            out.append("\\%s{%s}\n" % (cmd, inline(text)))
            i += 1
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", ln)
        if m:
            flush()
            cap, path = m.groups()
            out.append("\\begin{figure}[H]\n\\centering\n\\includegraphics[width=\\linewidth,height=0.42\\textheight,keepaspectratio]{%s}\n"
                       "\\caption{%s}\n\\end{figure}\n" % (path.replace("\\", "/"), inline(cap)))
            i += 1
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(split_row(lines[i])); i += 1
            out.append(table(rows))
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m:
            flush()
            ordered = m.group(2)[0].isdigit()
            env = "enumerate" if ordered else "itemize"
            out.append("\\begin{%s}[leftmargin=*,itemsep=2pt]" % env)
            while i < len(lines):
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not m:
                    if lines[i].startswith("  ") and lines[i].strip():     # continuation line
                        out[-1] += " " + inline(lines[i].strip()); i += 1; continue
                    break
                out.append("\\item " + inline(m.group(3))); i += 1
            out.append("\\end{%s}\n" % env)
            continue
        if not ln.strip():
            flush(); i += 1; continue
        para.append(ln.strip()); i += 1
    flush()
    return title, meta, "\n".join(out)


PRE = r"""\documentclass[11pt,a4paper]{report}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern}
\usepackage[margin=2.5cm]{geometry}
\usepackage{microtype}
\usepackage{graphicx}
\usepackage{float}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage{listings}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage{titlesec}
\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt}
\setlength{\emergencystretch}{3em}
\renewcommand{\arraystretch}{1.15}
\lstset{basicstyle=\ttfamily\footnotesize, breaklines=true, breakatwhitespace=false, columns=fullflexible,
  keepspaces=true, frame=single, rulecolor=\color{black!25}, backgroundcolor=\color{black!3},
  xleftmargin=2pt, xrightmargin=2pt, aboveskip=8pt, belowskip=8pt, upquote=true, showstringspaces=false,
  literate={->}{{$\rightarrow$}}2}
\titleformat{\section}{\Large\bfseries}{\thesection}{0.8em}{}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\leftmark}
\fancyfoot[C]{\thepage}
\renewcommand{\headrulewidth}{0.3pt}
\setcounter{secnumdepth}{3}
\setcounter{tocdepth}{2}
"""


def main():
    md = io.open(SRC, encoding="utf-8").read()
    title, meta, body = convert(md)
    tex = [PRE]
    author = " \\\\ ".join(inline(x) for x in ([meta[0]] + meta[2:])) if meta else ""
    tex.append("\\title{%s}\n\\author{%s}\n\\date{%s}\n" % (
        inline(title or "Project report"), author, inline(meta[1]) if len(meta) > 1 else ""))
    tex.append("\\begin{document}\n\\maketitle\n")
    tex.append("\\tableofcontents\n\\clearpage\n")
    tex.append(body)
    tex.append("\n\\end{document}\n")
    io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(tex))
    print("wrote %s (%d chars)" % (OUT, sum(len(t) for t in tex)))


if __name__ == "__main__":
    sys.exit(main())
