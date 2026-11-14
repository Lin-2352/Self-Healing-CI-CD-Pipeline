"""Build eval/cases/multifile_cases.json: bugs that span files, derived from the demo app's golden tree.

The first 11 cases all live in one self-contained file (test.js), so they cannot test the part of the
pipeline that decides WHICH file to fix. These do. Each case is an exact string mutation of the golden
tree (asserted to match exactly once), so every case is reproducible from app/ alone.

Case fields:
  files      path -> content of every file the broken tree changes (the rest is golden)
  changed    the paths the failing COMMIT touched (what GitHub's commit API reports)
  pre        optional: path -> content pushed FIRST, expected to pass CI (a latent bug)
  golden     optional: path -> content where this case's known-good tree differs from the base golden
  fixable_in the file(s) whose single-file fix makes the tree pass (informational, for the write-up)

Run:  python eval/build_multifile_cases.py   (then python eval/check_multifile_cases.py)
"""
import io, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
TREE = ["test.js", "src/app.js", "src/format.js", "test/api.test.js", "package.json"]
G = {p: io.open(os.path.join(APP, p), encoding="utf-8", newline="").read() for p in TREE}


def mut(path, old, new, base=None):
    src = (base or G)[path]
    assert src.count(old) == 1, "%s: %r occurs %d times" % (path, old, src.count(old))
    return src.replace(old, new)


GREET = "  return `Hello, ${clean}!`;"
cases = []

cases.append({
    "name": "helper_throws",
    "why_hard": "the TypeError is thrown in src/format.js but surfaces as a 500 from src/app.js; the test only "
                "sees a status code, the stack trace in the CI log is the only pointer to the helper",
    "files": {"src/format.js": mut("src/format.js", GREET, "  return `Hello, ${clean.toUppercase()}!`;")},
    "changed": ["src/format.js"], "fixable_in": ["src/format.js"]})

cases.append({
    "name": "readme_first_then_code",
    "why_hard": "the commit's first changed file is README.md (not code); the bug is a wrong argument in "
                "src/app.js with no stack trace, only a failed assertion",
    "files": {"README.md": "# self-healing-cicd-demo\n\nDemo app for the self-healing pipeline.\n",
              "src/app.js": mut("src/app.js", "res.json(formatSum(a, b));", "res.json(formatSum(a, a));")},
    "changed": ["README.md", "src/app.js"], "fixable_in": ["src/app.js"]})

cases.append({
    "name": "second_changed_file_buggy",
    "why_hard": "two code files changed; the harmless one (src/app.js) sorts FIRST, the bug is in the "
                "second (src/format.js). The old workflow fixed only the first changed code file",
    "files": {"src/app.js": mut("src/app.js", "// The demo API.", "// The demo API (routes only; see src/format.js)."),
              "src/format.js": mut("src/format.js", "sum: a + b", "sum: a - b")},
    "changed": ["src/app.js", "src/format.js"], "fixable_in": ["src/format.js"]})

# A latent bug: capitalising each '-'-separated part. The existing test ("Ada") passes; a new test
# for a name with '--' makes it throw inside src/format.js. The failing commit touches ONLY the test,
# so the buggy file is reachable only through the stack trace.
LATENT = mut("src/format.js", GREET,
             "  const cap = clean.split(\"-\").map((p) => p[0].toUpperCase() + p.slice(1)).join(\"-\");\n"
             "  return `Hello, ${cap}!`;")
NEW_TEST = mut("test/api.test.js", '  ["/greet/Ada", 200, { message: "Hello, Ada!" }],',
               '  ["/greet/Ada", 200, { message: "Hello, Ada!" }],\n'
               '  ["/greet/ada--lovelace", 200, { message: "Hello, ada--lovelace!" }],')
cases.append({
    "name": "test_exposes_latent_bug",
    "why_hard": "the failing commit changed only test/api.test.js; the bug was committed earlier in "
                "src/format.js and is reachable only through the stack trace in the CI log",
    "pre": {"src/format.js": LATENT},
    "files": {"src/format.js": LATENT, "test/api.test.js": NEW_TEST},
    "changed": ["test/api.test.js"], "golden": {"test/api.test.js": NEW_TEST},
    "fixable_in": ["src/format.js"]})

cases.append({
    "name": "wrong_require_path",
    "why_hard": "the test cannot even load the app: 'Cannot find module ../src/application'; the fix is in the "
                "test file, not in the code it tests",
    "files": {"test/api.test.js": mut("test/api.test.js", 'require("../src/app")', 'require("../src/application")')},
    "changed": ["test/api.test.js"], "fixable_in": ["test/api.test.js"]})

cases.append({
    "name": "signature_changed_caller_not",
    "why_hard": "formatSum now takes an object, but src/app.js still passes two numbers; no exception, just "
                "a wrong body. Fixable on either side of the call",
    "files": {"src/format.js": mut("src/format.js", "function formatSum(a, b) {", "function formatSum({ a, b }) {")},
    "changed": ["src/format.js"], "fixable_in": ["src/format.js", "src/app.js"]})

out = {"golden_tree": G, "cases": cases}
p = os.path.join(HERE, "cases", "multifile_cases.json")
io.open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(out, indent=1))
print("wrote %s: %d cases over a %d-file tree" % (p, len(cases), len(G)))
