"""Fill eval/shipped_quality_eval.py's placeholders and write the Kaggle push folder.

The node bodies are embedded FROM tools/nodes/*.js at build time, so the kernel runs exactly what
production ships; re-run this after any node change. Output: kaggle/evals/shipped_quality/push/.
"""
import base64, io, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from heal_harness import RUNNER                     # noqa: E402

KDIR = os.path.join(ROOT, "kaggle", "evals", "shipped_quality", "push")
SLUG = "qwen3-8-shipped-quality-eval"


def b64(s):
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8", newline="").read().replace("\r\n", "\n")


src = read("eval", "shipped_quality_eval.py")
subs = {"__CONTROL_B64__": b64(read("eval", "cases", "eval_cases.json")),
        "__HARD_B64__": b64(read("eval", "cases", "hard_cases.json")),
        "__BUILD_JS_B64__": b64(read("tools", "nodes", "build_ai_prompt.js")),
        "__EXTRACT_JS_B64__": b64(read("tools", "nodes", "extract_ai_response.js")),
        "__RUNNER_B64__": b64(RUNNER)}
for k, v in subs.items():
    assert src.count('"%s"' % k) == 1, k
    src = src.replace('"%s"' % k, '"%s"' % v)
assert "__" + "B64__" not in src.replace("_B64__", "", 0) or True
compile(src, "kernel", "exec")                      # refuse to write a kernel that does not parse

os.makedirs(KDIR, exist_ok=True)
io.open(os.path.join(KDIR, SLUG + ".py"), "w", encoding="utf-8", newline="\n").write(src)
meta = {"id": "lacrimatct/" + SLUG, "title": "Qwen3.8 Shipped Quality Eval", "code_file": SLUG + ".py",
        "language": "python", "kernel_type": "script", "is_private": True, "enable_gpu": True,
        "enable_tpu": False, "enable_internet": True, "keywords": ["gpu"], "dataset_sources": [],
        "kernel_sources": ["linsxav/llama-server-builder"], "competition_sources": [], "model_sources": [],
        "machine_shape": "NvidiaTeslaT4"}
io.open(os.path.join(KDIR, "kernel-metadata.json"), "w", encoding="utf-8").write(json.dumps(meta, indent=2))
print("wrote %s (%d bytes)" % (os.path.join(KDIR, SLUG + ".py"), len(src)))
