"""Build the project report: chapters -> docs/PROJECT_REPORT.md -> LaTeX -> PDF.

    python docs/report/build.py            assemble the Markdown, convert it, run latexmk
    python docs/report/build.py --md       only assemble docs/PROJECT_REPORT.md

The chapters are docs/report/chapters/NN_name.md, joined in file-name order under the title block in
docs/report/title.md. docs/PROJECT_REPORT.md is generated; edit the chapters. md_to_tex.py turns the
Markdown into docs/report/main.tex, and latexmk (MiKTeX, with Strawberry Perl on PATH) makes
docs/report/main.pdf, which is copied to docs/PROJECT_REPORT.pdf.

Figures are referenced from the chapters as ![caption](figures/name.png), relative to docs/report/.
"""
import glob, io, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)


def assemble():
    title = io.open(os.path.join(HERE, "title.md"), encoding="utf-8").read().strip()
    parts = [title]
    for f in sorted(glob.glob(os.path.join(HERE, "chapters", "[0-9][0-9]_*.md"))):
        parts.append(io.open(f, encoding="utf-8").read().strip())
    md = "\n\n".join(parts) + "\n"
    out = os.path.join(DOCS, "PROJECT_REPORT.md")
    io.open(out, "w", encoding="utf-8", newline="\n").write(md)
    print("wrote %s (%d chapters, %d words)" % (os.path.relpath(out, os.path.dirname(DOCS)), len(parts) - 1, len(md.split())))


def main():
    assemble()
    if "--md" in sys.argv:
        return 0
    subprocess.run([sys.executable, os.path.join(HERE, "md_to_tex.py")], check=True)
    # latexmk needs Perl; Strawberry Perl on D: is on PATH for interactive shells, add it for safety.
    env = dict(os.environ)
    for extra in (r"D:\Strawberry\perl\bin", r"D:\Strawberry\c\bin"):
        if os.path.isdir(extra):
            env["PATH"] = extra + os.pathsep + env["PATH"]
    cmd = ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-outdir=build", "main.tex"]
    p = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = p.stdout + p.stderr
    io.open(os.path.join(HERE, "build.log"), "w", encoding="utf-8").write(log)
    pdf = os.path.join(HERE, "build", "main.pdf")
    if p.returncode != 0 or not os.path.exists(pdf):
        print("LaTeX failed (rc=%d). Last lines of build.log:" % p.returncode)
        print("\n".join(log.strip().splitlines()[-25:]))
        return 1
    shutil.copyfile(pdf, os.path.join(DOCS, "PROJECT_REPORT.pdf"))
    print("wrote docs/PROJECT_REPORT.pdf (%d KB)" % (os.path.getsize(pdf) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
