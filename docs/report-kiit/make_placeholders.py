"""Write PLACEHOLDERS.md: every \\ph{...} placeholder, where it is, and the PDF pages it prints on.

    python docs/report-kiit/make_placeholders.py      (run after build.py)
"""
import collections, glob, io, os, re, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BS = chr(92)
PH = re.compile(re.escape(BS) + r"ph\{([^{}]*)\}")


def main():
    found = collections.OrderedDict()
    files = ["main.tex"] + sorted(glob.glob(os.path.join(HERE, "frontmatter", "*.tex"))) + \
        sorted(glob.glob(os.path.join(HERE, "chapters", "*.tex")))
    for f in files:
        path = f if os.path.isabs(f) else os.path.join(HERE, f)
        text = io.open(path, encoding="utf-8").read()
        text = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("%"))
        for m in PH.finditer(text):
            key = " ".join(m.group(1).split())
            if len(key) > 40:
                key = " ".join(key.split()[:5]) + " ..."
            found.setdefault(key, set()).add(os.path.relpath(path, HERE).replace(BS, "/"))
    exe = shutil.which("pdftotext") or r"D:\Program Files (x86)\MikiTek\miktex\bin\x64\pdftotext.exe"
    pages = {}
    pdf = os.path.join(HERE, "output", "main.pdf")
    if os.path.exists(exe) and os.path.exists(pdf):
        txt = subprocess.run([exe, "-layout", pdf, "-"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout
        for n, page in enumerate(txt.split("\f"), 1):
            for key in found:
                if "[" + key + "]" in page:
                    pages.setdefault(key, []).append(n)
    lines = ["# Placeholders", "",
             "Every value that has to be filled in later is written `\\ph{...}` in the LaTeX source and prints in bold",
             "square brackets in the PDF. Search the source for `\\ph{` to find them. Names, roll numbers, the guide,",
             "the school, institute, city, month and the submission date (17/11/2026) are filled in.", "",
             "| placeholder | source files | PDF pages |", "|---|---|---|"]
    for key, fs in found.items():
        pg = pages.get(key, [])
        shown = ", ".join(str(p) for p in pg[:12]) + (" ..." if len(pg) > 12 else "") if pg else "footer or inside text"
        lines.append("| `%s` | %s | %s |" % (key, ", ".join("`%s`" % x for x in sorted(fs)), shown))
    lines += ["", "The abstract and all chapters are written; the acknowledgement text and the contribution lines are yours to write.", ""]
    io.open(os.path.join(HERE, "PLACEHOLDERS.md"), "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print("wrote PLACEHOLDERS.md with %d placeholders" % len(found))


if __name__ == "__main__":
    main()
