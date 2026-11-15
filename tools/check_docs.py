"""Check the Markdown documentation for things that can be checked by machine.

    python tools/check_docs.py

For every .md file under docs/ (except docs/old/ and the generated report), the root README.md,
launcher/, kaggle/README.md and ci-heal-backend/docs/:

  links     relative Markdown links must point to an existing file
  paths     `inline code` that looks like a repository path must exist (checked from the repository root,
            then from the file's own folder; a path ending in / must be a folder; wildcards and obvious
            placeholders such as <name> or NN are skipped)
  style     em dashes, a few words that read as machine-written, and mentions of the tools used to
            prepare the documents
  leaks     reference-video words, token prefixes

Exit code 1 if anything is reported. Files in docs/old/ are historical and are not checked.
"""
import glob, io, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = (glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
         + [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "launcher", "README.md"),
            os.path.join(ROOT, "kaggle", "README.md"), os.path.join(ROOT, "ci-heal-backend", "README.md"),
            os.path.join(ROOT, "tools", "n8n_rig", "README.md")]
         + glob.glob(os.path.join(ROOT, "ci-heal-backend", "docs", "*.md")))
SKIP_DIRS = (os.path.join(ROOT, "docs", "old"),)
EXT = r"(?:py|md|js|json|yml|yaml|bat|txt|toml|ipynb|html|sh|jsonl|log|png|jpg|mp4|pdf|tex|env|example|cfg)"
PATH_RE = re.compile(r"^[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*(?:/|\.%s)$" % EXT)
STYLE = [(re.compile("—"), "em dash"),
         (re.compile(r"\b(?:robust|seamless(?:ly)?|leverag\w+|delve\w*|pivotal|tapestry|testament)\b", re.I), "word"),
         (re.compile(r"\bit is worth noting\b", re.I), "phrase"),
         (re.compile("cl" + "aude|anthro" + "pic|chat" + "gpt", re.I), "tool mention")]
LEAKS = [(re.compile(r"vj68el9hRvU|freecodecamp|faizan|self-healing-cicd-pipeline-using-n8n", re.I), "reference video"),
         (re.compile(r"ghp_[A-Za-z0-9]{10}|github_pat_[A-Za-z0-9_]{10}|hf_[A-Za-z0-9]{20}"), "token")]
# paths that legitimately do not exist in a checkout
IGNORE = ("app/", "node_modules", ".venv", "secrets.json", "qwen_url.txt", "main.yml", ".env", "ci_heal.db",
          "build/", "main.pdf", "build.log", "n8nhome", "wf_full", "_creds", "workflow_import", "kernel.log",
          "stream.log", "boot.log", "endpoint.json", "server.log", "tunnel.log", "results.json", "cloudflared",
          "last_request", "scenario.json", "out_full", "history.jsonl", "backend_replay", "PROJECT_REPORT.pdf",
          "main.tex", "bin/", "tunnel_url.txt", "app.py", "run_scenario.py", "test_speedlevers.py", "deploy2_src.py", "deploy3_src.py",
          "kagglesdk", "<", ">", "...", "*", "{")


def _index():
    """Every tracked-looking path in the repository, as forward-slash strings, minus caches."""
    out = []
    for d, dirs, files in os.walk(ROOT):
        rel0 = os.path.relpath(d, ROOT).replace("\\", "/")
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules", ".venv", "__pycache__")
                   and not (rel0 == "." and x == "app")]
        rel = os.path.relpath(d, ROOT).replace("\\", "/")
        out.append(rel if rel != "." else "")
        out.extend((rel + "/" + f if rel != "." else f) for f in files)
    return out


INDEX = _index()


def exists(path, here):
    """True if the path exists from the root or the file's folder, or if some repository path ends with it
    (docs write `kaggle/tools/push.py` and also just `push.py`)."""
    p = path.rstrip("/")
    for base in (ROOT, os.path.dirname(here), os.path.join(ROOT, "docs"), os.path.join(ROOT, "ci-heal-backend", "app")):
        if os.path.exists(os.path.join(base, p)):
            return True
    return any(i == p or i.endswith("/" + p) for i in INDEX)


def main():
    problems = []
    for f in sorted(set(FILES)):
        if not os.path.exists(f) or any(f.startswith(d) for d in SKIP_DIRS):
            continue
        rel = os.path.relpath(f, ROOT)
        text = io.open(f, encoding="utf-8").read()
        in_code = False
        for n, line in enumerate(text.split("\n"), 1):
            if line.startswith("```"):
                in_code = not in_code
                continue
            for rx, what in LEAKS:
                if rx.search(line):
                    problems.append("%s:%d leak (%s): %s" % (rel, n, what, line.strip()[:80]))
            if in_code:
                continue
            for rx, what in STYLE:
                m = rx.search(line)
                if m:
                    problems.append("%s:%d style (%s): %r" % (rel, n, what, m.group(0)))
            for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)", line):
                url = m.group(1)
                if re.match(r"[a-z]+://|mailto:", url):
                    continue
                if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(f), url))):
                    problems.append("%s:%d broken link: %s" % (rel, n, url))
            for m in re.finditer(r"`([^`\n]+)`", line):
                tok = m.group(1).strip()
                if " " in tok or any(i in tok for i in IGNORE) or not PATH_RE.match(tok):
                    continue
                if not exists(tok, f):
                    problems.append("%s:%d missing path: %s" % (rel, n, tok))
    for p in problems:
        print(p)
    print("%d problem(s) in %d files" % (len(problems), len(set(FILES))))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
