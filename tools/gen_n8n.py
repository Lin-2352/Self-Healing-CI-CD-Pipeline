"""Regenerate n8n/self_healing_pipeline.json, the source of truth for the workflow.

The original generator was lost when a temporary working folder was cleared; the workflow JSON
survived, so this is rebuilt FROM that JSON and is required to reproduce it byte-for-byte
before it may be trusted. `--check` does exactly that comparison and is the first thing to run
after any edit here. A generator that cannot reproduce the shipped workflow is worse than none,
because it invites silently regenerating a *different* pipeline.

Design difference from the original: the Code-node bodies live in `tools/nodes/*.js`
instead of being embedded as Python string constants. Editing real .js with syntax highlighting
beats editing a 3,900-character Python literal, and it keeps the diff of a node change readable.

Serialization is exact and deliberate: `json.dumps(indent=2)` with **CRLF** endings and **no**
trailing newline. That is what n8n wrote and what the repo holds; changing it would produce a
whole-file diff on every regeneration.

Usage:
    python tools/gen_n8n.py --check    # regenerate in memory, diff against the shipped file
    python tools/gen_n8n.py            # write the file
"""
import io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "n8n", "self_healing_pipeline.json")
NODES = os.path.join(HERE, "nodes")

WF_NAME = "Self-Healing CI/CD Pipeline (Qwen3.8)"

# Credentials are referenced by id/name only - no secrets live in this repo.
GH = {"httpHeaderAuth": {"id": "github-api-token", "name": "GitHub API Token"}}


def js(name):
    """A Code node's body, from tools/nodes/<name>.js (LF inside the JSON string)."""
    p = os.path.join(NODES, name + ".js")
    return io.open(p, encoding="utf-8", newline="").read().replace("\r\n", "\n")


def load_shipped():
    return json.loads(io.open(OUT, encoding="utf-8").read())


def build():
    """Compose the workflow.

    The node bodies and the many long URL/body expressions are taken from the shipped file
    rather than retyped: retyping ~19 KB of expressions by hand is how a subtle character
    difference gets introduced, and byte-identity is the whole contract here. What this
    generator owns is the STRUCTURE - node list, order, wiring, positions, versions - plus the
    Code bodies, which are the parts that actually get edited.
    """
    shipped = load_shipped()
    by_name = {n["name"]: n for n in shipped["nodes"]}

    nodes = []
    fork_x = -100 + 220 * ORDER.index(FORK)
    model_x = -100 + 220 * ORDER.index("Model OK?")
    placed = [(n, [-100 + 220 * i, 300]) for i, n in enumerate(ORDER)] + \
             [(n, [fork_x + 220 * (i + 1), 520]) for i, n in enumerate(FAIL)] + \
             [(n, [model_x + 220 * i, 540]) for i, n in enumerate(FALLBACK)] + \
             [(n, [-100 + 220 * i, 760]) for i, n in enumerate(URL_HOOK)]
    for name, pos in placed:
        src = NEW_NODES.get(name) or by_name[name]
        node = {
            "parameters": dict(src["parameters"]),
            "id": src["id"],
            "name": name,
            "type": src["type"],
            "typeVersion": src["typeVersion"],
            "position": pos,
        }
        # Code bodies come from disk so tools/nodes/*.js is authoritative for them.
        if "jsCode" in node["parameters"]:
            node["parameters"]["jsCode"] = js(SLUG[name])
        for extra in ("credentials", "webhookId", "onError"):
            if extra in src:
                node[extra] = src[extra]
        nodes.append(node)

    # Straight chain: every node feeds the next. `onError: continueRegularOutput` on Create
    # Branch and Open Pull Request is what makes a re-run idempotent rather than fatal.
    # One fork: "Fix Ready?" (an IF) sends output 0 (a fix exists) down the main chain and
    # output 1 (no fix) to the FAIL branch, which comments on the PR / commit.
    link = lambda b: {"node": b, "type": "main", "index": 0}
    connections = {}
    for a, b in zip(ORDER, ORDER[1:]):
        connections[a] = {"main": [[link(b)]]}
    connections[FORK]["main"].append([link(FAIL[0])])
    for a, b in zip(FAIL, FAIL[1:]):
        connections[a] = {"main": [[link(b)]]}
    # The model call can fail, time out or be cut off: "Model OK?" then sends the prompt to the
    # OpenAI node, whose reply is reshaped and rejoins the chain at "Assemble Model Reply".
    connections["Model OK?"]["main"].append([link(FALLBACK[0])])
    connections[FALLBACK[0]] = {"main": [[link(FALLBACK[1])]]}
    connections[FALLBACK[1]] = {"main": [[link("Assemble Model Reply")]]}
    # The launcher posts the new tunnel address here after each Kaggle boot.
    connections[URL_HOOK[0]] = {"main": [[link(URL_HOOK[1])]]}

    return {"name": WF_NAME, "nodes": nodes, "connections": connections,
            "settings": shipped.get("settings", {"executionOrder": "v1"}),
            "pinData": shipped.get("pinData", {})}


ORDER = ["GitHub Failure Webhook", "Fetch Job Logs", "Fetch Failed Job Log", "Download Job Log",
         "Fetch Changed Files", "Pick Candidate Files", "Fetch File Content", "Build AI Prompt", "Get Model URL",
         "Qwen3.8 Analyze", "Model OK?", "Assemble Model Reply", "Extract AI Response", "Fix Ready?", "Create Branch", "Branch Ready",
         "Get File SHA", "Push Fix File", "Open Pull Request", "Heal Outcome"]
FORK = "Fix Ready?"
FAIL = ["Explain Failure", "Post Failure Comment"]
FALLBACK = ["OpenAI Fallback", "Normalize OpenAI Reply"]
URL_HOOK = ["Set Model URL", "Store Model URL"]

# Nodes whose spec this generator OWNS (added after the shipped JSON was reverse-engineered).
# They take precedence over the shipped copy, so editing them here is how they change; --check
# keeps the shipped file in step.
NEW_NODES = {
    # The failing job's LOG (Fetch Job Logs only returns metadata). GitHub answers 302 to a signed
    # blob URL on ANOTHER host. n8n's HTTP node re-sends the credential's Authorization header on
    # that hop - proven in the verify rig (the mock blob host received the bearer token) - which
    # would hand the GitHub PAT to the storage host. So this node does NOT follow the redirect: it
    # returns the 302 (neverError + fullResponse) and "Download Job Log", which carries no
    # credential at all, fetches the signed Location URL.
    "Fetch Failed Job Log": {
        "parameters": {
            "url": "=https://api.github.com/repos/{{ $('GitHub Failure Webhook').first().json.body.repo }}"
                   "/actions/jobs/{{ (($('Fetch Job Logs').first().json.jobs || [])"
                   ".find(j => j.conclusion === 'failure') || {}).id }}/logs",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBearerAuth",
            "options": {"redirect": {"redirect": {"followRedirects": False}},
                        "response": {"response": {"fullResponse": True, "neverError": True,
                                                  "responseFormat": "text"}}},
        },
        "id": "fetch-failed-job-log",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "credentials": {"httpBearerAuth": {"id": "github-pat", "name": "GitHub PAT"}},
        "onError": "continueRegularOutput",
    },
    # No authentication on purpose (see above): the Location URL is pre-signed.
    # continueRegularOutput: an expired/missing log must never block a heal - Build AI Prompt
    # turns the error item into "(log unavailable)".
    "Download Job Log": {
        "parameters": {
            "url": "={{ ($json.headers || {}).location || '' }}",
            "options": {"response": {"response": {"responseFormat": "text"}}},
        },
        "id": "download-job-log",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "onError": "continueRegularOutput",
    },
    # Phase 6: which files the model sees. Stack-trace files first, then the commit's changed code
    # files, capped at 3; one item per file (see tools/nodes/pick_candidate_files.js).
    "Pick Candidate Files": {
        "parameters": {"jsCode": ""},          # body from tools/nodes/pick_candidate_files.js
        "id": "pick-candidate-files",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
    },
    # Runs once per candidate. The JSON contents API (not vnd.github.raw) returns `path` + base64
    # `content` in every item, so later nodes never need `$()` on a Code node's output (unreliable
    # in n8n's sandbox) to know which file is which. A 404 (a traced file that is not in the repo)
    # continues as an error item and is skipped by Build AI Prompt / Extract AI Response.
    "Fetch File Content": {
        "parameters": {
            "url": "=https://api.github.com/repos/{{ $('GitHub Failure Webhook').first().json.body.repo }}"
                   "/contents/{{ $json.path }}?ref={{ $('GitHub Failure Webhook').first().json.body.commit }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBearerAuth",
            "options": {},
        },
        "id": "fetch-file-content",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "credentials": {"httpBearerAuth": {"id": "github-pat", "name": "GitHub PAT"}},
        "onError": "continueRegularOutput",
    },
    # The model call is STREAMED (a thinking retry died at 125.9 s on the quick tunnel's 120 s
    # origin timeout). This rebuilds the usual completion object; errors continue as data so the
    # failure branch can quote them.
    "Assemble Model Reply": {
        "parameters": {"jsCode": ""},          # body from tools/nodes/assemble_model_reply.js
        "id": "assemble-model-reply",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "onError": "continueRegularOutput",
    },
    # A fix exists only if Extract produced file content and no error. Anything else - model call
    # failed, stream cut, SEARCH not found, patch does not parse - goes to "Explain Failure".
    "Fix Ready?": {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "conditions": [{
                    "id": "fix-ready-cond",
                    "leftValue": "={{ !$json.error && !!$json.base64_content }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                }],
                "combinator": "and",
            },
            "options": {},
        },
        "id": "fix-ready",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
    },
    "Explain Failure": {
        "parameters": {"jsCode": ""},          # body from tools/nodes/explain_failure.js
        "id": "explain-failure",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
    },
    # PR comment when there is a heal PR (retries), else a comment on the commit that broke main.
    "Post Failure Comment": {
        "parameters": {
            "method": "POST",
            "url": "={{ 'https://api.github.com/repos/' + $json.repo + ($json.target === 'pr'"
                   " ? '/issues/' + $json.pr_number + '/comments' : '/commits/' + $json.commit + '/comments') }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBearerAuth",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ body: $json.body }) }}",
            "options": {},
        },
        "id": "post-failure-comment",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "credentials": {"httpBearerAuth": {"id": "github-pat", "name": "GitHub PAT"}},
    },
    # The Kaggle endpoint gets a new tunnel address on every boot. "Get Model URL" supplies it
    # (stored address first, QWEN_BASE_URL second) so the HTTP node below never needs editing.
    "Get Model URL": {
        "parameters": {"jsCode": ""},          # body from tools/nodes/get_model_url.js
        "id": "get-model-url",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
    },
    # Streamed (see Assemble Model Reply). 240 s, not 15 min: a stuck endpoint should reach the
    # OpenAI fallback while the run is still useful. Errors continue as data for "Model OK?".
    "Qwen3.8 Analyze": {
        "parameters": {
            "method": "POST",
            "url": "={{ $json.model_base_url }}/v1/chat/completions",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBearerAuth",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.request_body) }}",
            "options": {"timeout": 240000, "response": {"response": {"responseFormat": "text"}}},
        },
        "id": "qwen3.8-analyze",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "credentials": {"httpBearerAuth": {"id": "qwen-endpoint", "name": "Qwen3.8 Endpoint Key"}},
        "onError": "continueRegularOutput",
    },
    # True when the Qwen reply is a finished stream: no error, and the text ends in [DONE] or
    # carries a finish_reason. Anything else (error item, connection reset, cut stream) is false.
    "Model OK?": {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "conditions": [{
                    "id": "model-ok-cond",
                    "leftValue": "={{ !$json.error && typeof $json.data === 'string' && "
                                 "($json.data.includes('[DONE]') || $json.data.includes('\"finish_reason\":\"')) }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                }],
                "combinator": "and",
            },
            "options": {},
        },
        "id": "model-ok",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
    },
    # Fallback model. No credential is bound here: each n8n attaches its own OpenAI credential
    # (on n8n Cloud the included free credits; they did not authorise webhook-triggered runs in
    # our test, see docs). With none attached the node fails, continues as data and
    # "Normalize OpenAI Reply" turns that into a clear error for the failure comment.
    "OpenAI Fallback": {
        "parameters": {
            "modelId": {"__rl": True, "value": "gpt-4.1", "mode": "list", "cachedResultName": "GPT-4.1"},
            "responses": {"values": [
                {"type": "text", "role": "system",
                 "content": "={{ $('Build AI Prompt').first().json.request_body.messages[0].content }}"},
                {"type": "text", "role": "user",
                 "content": "={{ $('Build AI Prompt').first().json.request_body.messages[1].content }}"}]},
            "builtInTools": {},
            "options": {},
        },
        "id": "openai-fallback",
        "type": "@n8n/n8n-nodes-langchain.openAi",
        "typeVersion": 2.3,
        "onError": "continueRegularOutput",
    },
    "Normalize OpenAI Reply": {
        "parameters": {"jsCode": ""},          # body from tools/nodes/normalize_openai_reply.js
        "id": "normalize-openai-reply",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "onError": "continueRegularOutput",
    },
    # Second webhook, same header-auth credential as the failure webhook.
    "Set Model URL": {
        "parameters": {"httpMethod": "POST", "path": "set-qwen-url", "authentication": "headerAuth",
                       "responseMode": "lastNode", "options": {"rawBody": False}},
        "id": "set-model-url",
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "credentials": {"httpHeaderAuth": {"id": "n8n-webhook-secret", "name": "N8N Webhook Secret"}},
        "webhookId": "b3e5a8c1-6d27-4f90-8a14-5c9e2d7f1b63",
    },
    "Store Model URL": {
        "parameters": {"jsCode": ""},          # body from tools/nodes/store_model_url.js
        "id": "store-model-url",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
    },
}

SLUG = {n: re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_") for n in ORDER + FAIL + FALLBACK + URL_HOOK}


def serialize(wf):
    # Exactly what n8n wrote: indent=2, CRLF, no trailing newline.
    return json.dumps(wf, indent=2).replace("\n", "\r\n").encode("utf-8")


def check_code_nodes_parse(wf):
    """Refuse to emit a workflow whose Code nodes do not parse.

    Byte-identity (--check) only proves the generator reproduces ITSELF. It said "identical"
    while Build AI Prompt shipped with a string literal split across 10 physical lines, and the
    live pipeline died on `Unterminated string constant` at the first real heal. Parsing every
    body with the same `new Function` wrapper n8n's Code node uses catches that class here.
    """
    import subprocess, tempfile
    bad = []
    for n in wf["nodes"]:
        body = n["parameters"].get("jsCode")
        if body is None:
            continue
        d = tempfile.mkdtemp()
        f = os.path.join(d, "b.js")
        io.open(f, "w", encoding="utf-8", newline="\n").write(body)
        js = ("const b=require('fs').readFileSync(process.argv[1],'utf8');"
              "try{new Function('$input','$','$node','$json',b)}"
              "catch(e){console.log(e.message);process.exit(1)}")
        p = subprocess.run(["node", "-e", js, f], capture_output=True, text=True)
        if p.returncode != 0:
            bad.append("%s: %s" % (n["name"], (p.stdout or p.stderr).strip()[:160]))
    return bad


def main():
    wf = build()
    bad = check_code_nodes_parse(wf)
    if bad:
        print("REFUSING: Code node(s) do not parse:\n  " + "\n  ".join(bad))
        return 2
    blob = serialize(wf)
    current = io.open(OUT, "rb").read() if os.path.exists(OUT) else b""

    if "--check" in sys.argv:
        same = blob == current
        print("regenerated %d bytes | shipped %d bytes | byte-identical: %s"
              % (len(blob), len(current), same))
        if not same:
            a = current.decode("utf-8", "replace").split("\r\n")
            b = blob.decode("utf-8", "replace").split("\r\n")
            import difflib
            d = [l for l in difflib.unified_diff(a, b, "shipped", "regenerated", lineterm="")]
            print("\n".join(d[:40]))
            print("... %d diff lines total" % len(d))
        return 0 if same else 1

    io.open(OUT, "wb").write(blob)
    print("wrote %s (%d bytes, %d nodes)" % (OUT, len(blob), len(wf["nodes"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
