"""Check the shape of the shipped workflow: wiring, the model-URL webhook and the OpenAI fallback.

Reads n8n/self_healing_pipeline.json as n8n would import it. No n8n, no network.

Run:  python tools/tests/test_workflow_structure.py
"""
import io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = json.load(io.open(os.path.join(ROOT, "n8n", "self_healing_pipeline.json"), encoding="utf-8"))
N = {n["name"]: n for n in WF["nodes"]}
C = WF["connections"]
fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print("  %-66s %s %s" % (label, "PASS" if cond else "FAIL", "" if cond else str(detail)[:140]))


def targets(name):
    return [[l["node"] for l in out] for out in C.get(name, {}).get("main", [])]


print("model call path")
check("Build AI Prompt -> Get Model URL -> Qwen3.8 Analyze",
      targets("Build AI Prompt") == [["Get Model URL"]] and targets("Get Model URL") == [["Qwen3.8 Analyze"]],
      (targets("Build AI Prompt"), targets("Get Model URL")))
check("Qwen3.8 Analyze -> Model OK?", targets("Qwen3.8 Analyze") == [["Model OK?"]], targets("Qwen3.8 Analyze"))
check("Model OK? yes -> Assemble Model Reply, no -> OpenAI Fallback",
      targets("Model OK?") == [["Assemble Model Reply"], ["OpenAI Fallback"]], targets("Model OK?"))
check("OpenAI Fallback -> Normalize OpenAI Reply -> Assemble Model Reply",
      targets("OpenAI Fallback") == [["Normalize OpenAI Reply"]]
      and targets("Normalize OpenAI Reply") == [["Assemble Model Reply"]])
q = N["Qwen3.8 Analyze"]["parameters"]
check("Qwen URL comes from Get Model URL", q["url"] == "={{ $json.model_base_url }}/v1/chat/completions", q["url"])
check("Qwen gives up after 240 s so the fallback can run", q["options"]["timeout"] == 240000, q["options"])
check("Qwen errors continue as data (the IF reads them)", N["Qwen3.8 Analyze"].get("onError") == "continueRegularOutput")

print("Set Model URL webhook")
w = N["Set Model URL"]
check("is a POST webhook on /set-qwen-url", w["type"] == "n8n-nodes-base.webhook"
      and w["parameters"]["path"] == "set-qwen-url" and w["parameters"]["httpMethod"] == "POST", w["parameters"])
check("uses the same header-auth credential as the failure webhook",
      w["parameters"]["authentication"] == "headerAuth"
      and w["credentials"] == N["GitHub Failure Webhook"]["credentials"], w.get("credentials"))
check("answers with the last node's output", w["parameters"]["responseMode"] == "lastNode")
check("Set Model URL -> Store Model URL", targets("Set Model URL") == [["Store Model URL"]])
check("webhook ids differ (n8n registers them per id)",
      w["webhookId"] != N["GitHub Failure Webhook"]["webhookId"])

print("OpenAI fallback node")
o = N["OpenAI Fallback"]
check("is the n8n OpenAI node on gpt-4.1", o["type"] == "@n8n/n8n-nodes-langchain.openAi"
      and o["parameters"]["modelId"]["value"] == "gpt-4.1", o["parameters"].get("modelId"))
check("sends the same two messages the Qwen request carries",
      [m["content"] for m in o["parameters"]["responses"]["values"]]
      == ["={{ $('Build AI Prompt').first().json.request_body.messages[0].content }}",
          "={{ $('Build AI Prompt').first().json.request_body.messages[1].content }}"])
check("its own failure continues as data", o.get("onError") == "continueRegularOutput")
check("ships with no credential bound (each n8n attaches its own)", "credentials" not in o, o.get("credentials"))

print("whole file")
check("node names are unique", len(N) == len(WF["nodes"]))
check("every connection target exists", all(t in N for k in C for outs in targets(k) for t in outs))
check("every connection source exists", all(k in N for k in C))
blob = json.dumps(WF)
check("no secrets: no ghp_/github_pat_/sk- tokens", not re.search(r"ghp_|github_pat_|sk-[A-Za-z0-9]{10}", blob))
check("$vars.QWEN_BASE_URL appears only in Get Model URL",
      [n["name"] for n in WF["nodes"] if "QWEN_BASE_URL" in json.dumps(n)] == ["Get Model URL"],
      [n["name"] for n in WF["nodes"] if "QWEN_BASE_URL" in json.dumps(n)])

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
