"""Build the verify workflow FROM the shipped JSON, so it can never drift from production.

v1 of this rig STUBBED `Build AI Prompt` - and that is exactly the node that broke on the first live
heal (a string literal split across lines). v2 copies Build AI Prompt, Qwen3.8 Analyze and Extract AI
Response VERBATIM (and Fetch Failed Job Log except its host); only the GitHub/model URLs are pointed at the local mock and credentials removed.
"""
import io, json, os, copy
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ship = json.load(io.open(os.path.join(ROOT, "n8n", "self_healing_pipeline.json"), encoding="utf-8"))
S = {n["name"]: n for n in ship["nodes"]}
PORT = 5792
def http(name, path, x):
    return {"parameters": {"url": "http://127.0.0.1:%d/%s" % (PORT, path), "options": {}}, "id": name[:12],
            "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [x, 0]}
# Fetch Failed Job Log: VERBATIM - URL expression, text response format, onError and the github-pat
# credential - except the host. The credential must exist in the rig (drive_full imports a dummy
# one), so the rig proves the bearer token is NOT forwarded across the 302 to the blob host.
joblog = copy.deepcopy(S["Fetch Failed Job Log"])
joblog["parameters"]["url"] = joblog["parameters"]["url"].replace(
    "https://api.github.com", "http://127.0.0.1:%d" % PORT)
assert "127.0.0.1" in joblog["parameters"]["url"]
# Fetch File Content: VERBATIM (runs once per candidate, JSON contents API, continues on 404) except host
filefetch = copy.deepcopy(S["Fetch File Content"])
filefetch["parameters"]["url"] = filefetch["parameters"]["url"].replace(
    "https://api.github.com", "http://127.0.0.1:%d" % PORT)
assert "127.0.0.1" in filefetch["parameters"]["url"]
qwen = copy.deepcopy(S["Qwen3.8 Analyze"])
qwen["parameters"]["url"] = "http://127.0.0.1:%d/model" % PORT
qwen["parameters"]["authentication"] = "none"
qwen["parameters"].pop("genericAuthType", None); qwen.pop("credentials", None)
# Get Model URL: VERBATIM except the $vars fallback, which community n8n cannot read (the launcher's
# n8n_import.py makes the same substitution). Model OK? and Normalize OpenAI Reply: VERBATIM. The
# OpenAI node itself needs an OpenAI credential, so the rig swaps it for an HTTP call to the mock.
geturl = copy.deepcopy(S["Get Model URL"])
geturl["parameters"]["jsCode"] = geturl["parameters"]["jsCode"].replace(
    "$vars.QWEN_BASE_URL", json.dumps("http://127.0.0.1:%d" % PORT))
assert "$vars" not in geturl["parameters"]["jsCode"]
openai = {"parameters": {"method": "POST", "url": "http://127.0.0.1:%d/openai" % PORT, "sendBody": True,
                         "specifyBody": "json", "jsonBody": "={{ JSON.stringify({ stream: false }) }}", "options": {}},
          "id": "openai-stub", "name": "OpenAI Fallback", "type": "n8n-nodes-base.httpRequest",
          "typeVersion": 4.2, "onError": "continueRegularOutput"}
nodes = [
    {"parameters": {}, "id": "start", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0]},
    http("GitHub Failure Webhook", "hook", 200),   # the real one is a webhook trigger; its OUTPUT shape is {body: payload}
    http("Fetch Job Logs", "jobs", 400),
    dict(joblog, position=[500, 0]),                                    # verbatim except host
    dict(copy.deepcopy(S["Download Job Log"]), position=[550, 0]),      # VERBATIM (follows Location)
    http("Fetch Changed Files", "files", 600),
    dict(copy.deepcopy(S["Pick Candidate Files"]), position=[700, 0]),  # VERBATIM (which files)
    dict(filefetch, position=[800, 0]),                                 # verbatim except host (JSON contents API)
    dict(copy.deepcopy(S["Build AI Prompt"]), position=[1000, 0]),     # VERBATIM
    dict(geturl, position=[1100, 0]),                                   # verbatim except the $vars fallback
    dict(qwen, position=[1200, 0]),                                     # verbatim except URL/auth
    dict(copy.deepcopy(S["Model OK?"]), position=[1250, 0]),            # VERBATIM (the fork to the fallback)
    dict(copy.deepcopy(S["Assemble Model Reply"]), position=[1300, 0]), # VERBATIM (streamed reply)
    dict(copy.deepcopy(S["Extract AI Response"]), position=[1400, 0]),  # VERBATIM
    dict(copy.deepcopy(S["Fix Ready?"]), position=[1600, 0]),           # VERBATIM (the fork)
]
# failure branch: Explain Failure VERBATIM, Post Failure Comment verbatim except the GitHub host
comment = copy.deepcopy(S["Post Failure Comment"])
comment["parameters"]["url"] = comment["parameters"]["url"].replace(
    "https://api.github.com", "http://127.0.0.1:%d" % PORT)
assert "127.0.0.1" in comment["parameters"]["url"]
fail = [dict(copy.deepcopy(S["Explain Failure"]), position=[1800, 200]), dict(comment, position=[2000, 200]),
        dict(openai, position=[1250, 300]),
        dict(copy.deepcopy(S["Normalize OpenAI Reply"]), position=[1300, 300])]       # VERBATIM
order = [n["name"] for n in nodes]
link = lambda b: {"node": b, "type": "main", "index": 0}
conns = {a: {"main": [[link(b)]]} for a, b in zip(order, order[1:])}
conns["Model OK?"] = {"main": [[link("Assemble Model Reply")], [link("OpenAI Fallback")]]}
conns["OpenAI Fallback"] = {"main": [[link("Normalize OpenAI Reply")]]}
conns["Normalize OpenAI Reply"] = {"main": [[link("Assemble Model Reply")]]}
conns["Fix Ready?"] = {"main": [[], [link("Explain Failure")]]}     # true: end of the rig; false: comment
conns["Explain Failure"] = {"main": [[link("Post Failure Comment")]]}
wf = {"id": "vrfyFullFront001", "name": "verify-full-front", "nodes": nodes + fail,
      "connections": conns, "settings": {"executionOrder": "v1"}, "active": False}
io.open(os.path.join(HERE, "wf_full.json"), "w", encoding="utf-8").write(json.dumps(wf))
print("wf_full.json built:", len(wf["nodes"]), "nodes; failure branch included")
