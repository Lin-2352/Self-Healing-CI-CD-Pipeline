"""Write chapters/appendix_a_tree.tex: the top two levels of the repository, from `git ls-files`.

    python docs/report-kiit/make_tree.py
"""
import collections, io, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SKIP = [":!app", ":!docs/media", ":!eval/runs", ":!kaggle/logs", ":!docs/report-kiit"]


def main():
    out = subprocess.run(["git", "ls-files", "--", "."] + SKIP, cwd=ROOT, capture_output=True, text=True).stdout
    files = [f.split("/") for f in out.split("\n") if f]
    tree = collections.OrderedDict()
    for parts in files:
        tree.setdefault(parts[0], []).append(parts)
    lines = ["self_healing_CI-CD_Pipeline/"]
    tops = sorted(tree)
    for i, t in enumerate(tops):
        last = i == len(tops) - 1
        items = tree[t]
        if len(items) == 1 and len(items[0]) == 1:
            lines.append(("`-- " if last else "|-- ") + t)
            continue
        lines.append(("`-- " if last else "|-- ") + t + "/")
        kids = collections.OrderedDict()
        for parts in items:
            if len(parts) > 1:
                kids[parts[1]] = kids.get(parts[1], 0) + 1
        pad = "    " if last else "|   "
        ks = list(kids.items())
        for j, (k, n) in enumerate(ks):
            isdir = any(len(p) > 2 and p[1] == k for p in items)
            lines.append(pad + ("`-- " if j == len(ks) - 1 else "|-- ") + k + ("/  (%d files)" % n if isdir else ""))
    bs = chr(92)
    head = (bs + "section{Directory Tree}\n"
            "The tree below lists the top two levels of the repository, with the number of files under each folder "
            "(generated from " + bs + "texttt{git ls-files}; the demo-app clone, run data, logs, media and this report "
            "are left out).\n\n" + bs + "begin{lstlisting}\n")
    tail = "\n" + bs + "end{lstlisting}\n"
    io.open(os.path.join(HERE, "chapters", "appendix_a_tree.tex"), "w", encoding="utf-8", newline="\n").write(
        head + "\n".join(lines) + tail)
    print("wrote chapters/appendix_a_tree.tex (%d lines)" % len(lines))


if __name__ == "__main__":
    main()
