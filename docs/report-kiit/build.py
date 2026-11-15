"""Build the KIIT-format report: chapters from Markdown, then LaTeX, then the layout checks.

    python docs/report-kiit/build.py            generate chapters, compile (latexmk), check the log
    python docs/report-kiit/build.py --check    ... and then run check_layout.py on the PDF

Fails (exit 1) on any LaTeX error, an overfull box wider than 1 pt, an undefined reference or citation,
a float that is too large, or a missing file. The PDF is copied to docs/report-kiit/PROJECT_REPORT_KIIT.pdf.
LaTeX needs Perl: Strawberry Perl on D: is added to PATH here.
"""
import os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")


def log_problems(log):
    probs = []
    for m in re.finditer(r"^! (.+)$", log, re.M):
        probs.append("LaTeX error: " + m.group(1))
    for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt too wide\)[^\n]*", log):
        if float(m.group(1)) > 1.0:
            probs.append("overfull box %spt: %s" % (m.group(1), m.group(0)[-70:]))
    for pat in (r"Citation `[^']+' [^\n]*undefined", r"Reference `[^']+' [^\n]*undefined",
                r"Float too large[^\n]*", r"File `[^']+' not found", r"There were undefined references"):
        for m in re.finditer(pat, log):
            probs.append(m.group(0))
    return probs


def main(argv):
    subprocess.run([sys.executable, "-W", "ignore", os.path.join(HERE, "build_chapters.py")], check=True,
                   stdout=subprocess.DEVNULL)
    env = dict(os.environ)
    for extra in (r"D:\Strawberry\perl\bin", r"D:\Strawberry\c\bin"):
        if os.path.isdir(extra):
            env["PATH"] = extra + os.pathsep + env["PATH"]
    os.makedirs(OUT, exist_ok=True)
    cmd = ["latexmk", "-pdf", "-interaction=nonstopmode", "-file-line-error-style" if False else "-f",
           "-outdir=output", "main.tex"]
    p = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    logf = os.path.join(OUT, "main.log")
    log = open(logf, encoding="utf-8", errors="replace").read() if os.path.exists(logf) else p.stdout + p.stderr
    probs = log_problems(log)
    pdf = os.path.join(OUT, "main.pdf")
    if not os.path.exists(pdf):
        probs.append("no PDF was produced")
    for x in probs[:60]:
        print(x)
    pages = re.search(r"Output written on [^(]*\((\d+) pages", log)
    print("%d problem(s); %s pages" % (len(probs), pages.group(1) if pages else "?"))
    if probs:
        return 1
    shutil.copyfile(pdf, os.path.join(HERE, "PROJECT_REPORT_KIIT.pdf"))
    if "--check" in argv:
        return subprocess.run([sys.executable, os.path.join(HERE, "check_layout.py"), pdf, "--skip-front", "14"]).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
