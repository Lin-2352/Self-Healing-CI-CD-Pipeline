"""Fill eval/bakeoff_eval.py from the CURRENT node bodies, cases and kaggle/endpoint/models.json, inline
eval/multifile_eval.py's tested helpers, and write kaggle/evals/bakeoff/push/. Re-run after any change.

    python eval/build_bakeoff_eval.py                 every model in models.json, default (baseline) first
    python eval/build_bakeoff_eval.py key1 key2 ...   baseline + these
"""
import base64, io, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from heal_harness import RUNNER                     # noqa: E402

KDIR = os.path.join(ROOT, "kaggle", "evals", "bakeoff", "push")
SLUG = "qwen3-8-model-bakeoff"
b64 = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")
read = lambda *p: io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")

REG = json.loads(read("kaggle", "endpoint", "models.json"))
keys = [REG["default"]] + [k for k in (sys.argv[1:] or REG["models"]) if k != REG["default"]]
models = [(k, {f: REG["models"][k][f] for f in ("repo", "pattern")}) for k in keys]

helpers = read("eval", "multifile_eval.py").split("\ndef main():")[0]
src = read("eval", "bakeoff_eval.py")
line = "from multifile_eval import *  # noqa: F401,F403  INLINED-BY-BUILDER"
assert src.count(line) == 1
src = src.replace(line, "# ---- inlined from eval/multifile_eval.py (tested helpers) ----\n" + helpers +
                  "\n# ---- end inline ----")
subs = {"__MULTI_B64__": b64(read("eval", "cases", "multifile_cases.json")),
        "__CONTROL_B64__": b64(read("eval", "cases", "eval_cases.json")),
        "__HARD_B64__": b64(read("eval", "cases", "hard_cases.json")),
        "__MODELS_B64__": b64(json.dumps(models)),
        "__PICK_JS_B64__": b64(read("tools", "nodes", "pick_candidate_files.js")),
        "__BUILD_JS_B64__": b64(read("tools", "nodes", "build_ai_prompt.js")),
        "__EXTRACT_JS_B64__": b64(read("tools", "nodes", "extract_ai_response.js")),
        "__RUNNER_B64__": b64(RUNNER)}
for k, v in subs.items():
    assert src.count('"%s"' % k) == 1, k
    src = src.replace('"%s"' % k, '"%s"' % v)
compile(src, "kernel", "exec")                      # refuse to write a kernel that does not parse
os.makedirs(KDIR, exist_ok=True)
io.open(os.path.join(KDIR, SLUG + ".py"), "w", encoding="utf-8", newline="\n").write(src)
meta = {"id": "lacrimatct/" + SLUG, "title": "Qwen3.8 Model Bakeoff", "code_file": SLUG + ".py",
        "language": "python", "kernel_type": "script", "is_private": True, "enable_gpu": True,
        "enable_tpu": False, "enable_internet": True, "keywords": ["gpu"], "dataset_sources": [],
        "kernel_sources": ["linsxav/llama-server-builder"], "competition_sources": [], "model_sources": [],
        "machine_shape": "NvidiaTeslaT4"}
io.open(os.path.join(KDIR, "kernel-metadata.json"), "w", encoding="utf-8").write(json.dumps(meta, indent=2))
print("wrote %s (%d bytes) models=%s" % (os.path.join(KDIR, SLUG + ".py"), len(src), keys))
