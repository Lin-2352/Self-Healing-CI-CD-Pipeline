"""Run the workflow's Code nodes locally, without n8n and without a GPU.

Phase 2 edits `Extract AI Response`, which is ~2.5 KB of JavaScript doing JSON parsing,
SEARCH/REPLACE application and a syntax gate. Iterating on that against a live n8n + a live
Kaggle endpoint is minutes per attempt; here it is under a second, and a logic bug never costs
GPU quota. The original harness was lost with a temporary working folder; this is the rebuild.

It emulates the n8n Code-node surface a node actually touches:

    $input.first().json          the incoming item
    $('Node Name').first().json  another node's output   <- typed RPC in real n8n
    $node['Node Name'].json      the same, via path lookup
    $getWorkflowStaticData('global')   ctx["static"], returned afterwards as `static_after`
    $vars.NAME                   ctx["vars"]
    return [{ json: {...}, pairedItem: {...} }]

⚠️ One real n8n behaviour is deliberately NOT emulated, because faking it would hide the bug it
caused: under the isolated-vm expression engine (default on n8n 1.123+), `$('Some Code Node')`
structured-clones items across the isolate boundary and **silently yields undefined** for a
*Code* node's output, while `$node['X'].json.field` returns primitives and works. That is why
the shipped nodes use `$node[...]` for Code-node reads. This harness resolves both accessors
happily, so **a passing harness run does not prove the accessor choice is safe in n8n**; only
a live run does.

Usage:
    python tools/heal_harness.py <node_slug> <context.json>
    python tools/heal_harness.py extract_ai_response ctx.json
"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NODES = os.path.join(HERE, "nodes")

RUNNER = r"""
const fs = require('fs');
const body = fs.readFileSync(process.argv[2], 'utf8');
const ctx  = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

// ctx shape: { input: <item json>, nodes: { "Node Name": <item json> | [<item json>, ...], ... } }
// A LIST means the node output several items (e.g. "Fetch File Content" runs once per candidate
// file). An n8n item's json is always an object, so a list is never a single item's json.
const wrap = (j) => {
  const items = Array.isArray(j) ? j : [j];
  return {
    first: () => ({ json: items[0] }),
    all:   () => items.map(x => ({ json: x })),
    last:  () => ({ json: items[items.length - 1] }),
    json:  items[0],
  };
};

const $input = wrap(ctx.input || {});
const $ = (name) => {
  if (!(name in (ctx.nodes || {}))) {
    throw new Error("harness: no context supplied for node '" + name + "'");
  }
  return wrap(ctx.nodes[name]);
};
const $node = new Proxy({}, {
  get: (_, name) => {
    if (!(name in (ctx.nodes || {}))) {
      throw new Error("harness: no context supplied for node '" + String(name) + "'");
    }
    const v = ctx.nodes[name];
    return { json: Array.isArray(v) ? v[0] : v };      // $node[...] sees the first item, like n8n
  }
});
const $json = (ctx.input || {});
// Workflow static data and variables: ctx.static / ctx.vars. The static object is printed back as
// `static_after` so a test can see what a node stored.
const staticData = JSON.parse(JSON.stringify(ctx.static || {}));
const $getWorkflowStaticData = () => staticData;
const $vars = ctx.vars || {};

let out, err = null;
try {
  out = new Function('$input', '$', '$node', '$json', '$getWorkflowStaticData', '$vars', body)(
    $input, $, $node, $json, $getWorkflowStaticData, $vars);
} catch (e) {
  err = { message: e.message, stack: (e.stack || '').split('\n').slice(0, 3).join(' | ') };
}
process.stdout.write(JSON.stringify({ ok: err === null, error: err, output: out, static_after: staticData }, null, 1));
"""


def run_node(slug, ctx):
    """Execute tools/nodes/<slug>.js against `ctx`. Returns the parsed runner result."""
    body = os.path.join(NODES, slug + ".js")
    if not os.path.exists(body):
        raise SystemExit("no such node body: %s" % body)
    d = tempfile.mkdtemp()
    try:
        rp = os.path.join(d, "_runner.js")
        cp = os.path.join(d, "_ctx.json")
        io.open(rp, "w", encoding="utf-8", newline="\n").write(RUNNER)
        io.open(cp, "w", encoding="utf-8", newline="\n").write(json.dumps(ctx))
        p = subprocess.run(["node", rp, body, cp], capture_output=True, text=True, timeout=60)
        if p.returncode != 0 and not p.stdout.strip():
            return {"ok": False, "error": {"message": (p.stderr or "")[:400]}, "output": None}
        try:
            return json.loads(p.stdout)
        except Exception:
            return {"ok": False,
                    "error": {"message": "runner produced no JSON: " + (p.stdout or p.stderr)[:300]},
                    "output": None}
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def first_json(res):
    """The single item a node returns, or None if it threw / returned nothing."""
    out = res.get("output")
    if isinstance(out, list) and out and isinstance(out[0], dict):
        return out[0].get("json")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__.strip().split("Usage:")[-1].strip())
    ctx = json.load(io.open(sys.argv[2], encoding="utf-8"))
    res = run_node(sys.argv[1], ctx)
    print(json.dumps(res, indent=1)[:4000])
    sys.exit(0 if res.get("ok") else 1)
