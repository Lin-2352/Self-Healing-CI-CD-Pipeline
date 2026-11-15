"""Rebuild the live n8n instance: credentials + workflow, activated. Secrets never touch the project.

Lessons from earlier rounds:
- n8n **1.x** only - 2.x never registers the webhook.
- Credentials import as DECRYPTED plaintext; N8N_USER_FOLDER + N8N_ENCRYPTION_KEY must match
  between import and start or they decrypt to nothing. The plaintext file is deleted right after.
- `$vars` is licensed and never resolves on community edition, so the model URL fallback in the
  "Get Model URL" node is substituted as a LITERAL into an import copy of the workflow (the shipped
  JSON keeps `$vars` untouched).
- N8N_USER_MANAGEMENT_DISABLED=true, or a fresh instance never activates anything.
"""
import io, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n")
# Durable install first: the %TEMP% one was wiped between sessions.
N8N = next(p for p in [os.path.join(HOME, "app", "node_modules", "n8n", "bin", "n8n"),
                       os.path.join(os.environ["TEMP"], "n8n_verify", "node_modules", "n8n", "bin", "n8n")]
           if os.path.exists(p))
SEC = json.load(io.open(os.path.join(HOME, "secrets.json"), encoding="utf-8-sig"))
QWEN = open(os.path.join(HOME, "qwen_url.txt")).read().strip()
QWEN_KEY = "selfheal-e2e-key"          # fixed in kaggle/endpoint/deploy_src.py
WF_ID = "selfHealPipeline01"

def env():
    e = dict(os.environ, N8N_USER_FOLDER=HOME, N8N_ENCRYPTION_KEY=SEC["encryption_key"],
             N8N_USER_MANAGEMENT_DISABLED="true", N8N_DIAGNOSTICS_ENABLED="false")
    return e

def n8n(*args):
    p = subprocess.run(["node", N8N] + list(args), capture_output=True, text=True, env=env(),
                       encoding="utf-8", errors="replace")
    out = (p.stdout + p.stderr).strip().splitlines()
    print("  n8n %-22s rc=%d  %s" % (args[0], p.returncode, out[-1][:110] if out else ""))
    if p.returncode != 0:
        sys.exit("n8n %s failed" % args[0])

gh_token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
assert gh_token, "gh auth token returned nothing"

creds = [
    {"id": "n8n-webhook-secret", "name": "N8N Webhook Secret", "type": "httpHeaderAuth",
     "data": {"name": "X-Webhook-Secret", "value": SEC["webhook_secret"]}},
    {"id": "github-pat", "name": "GitHub PAT", "type": "httpBearerAuth", "data": {"token": gh_token}},
    {"id": "qwen-endpoint", "name": "Qwen3.8 Endpoint Key", "type": "httpBearerAuth",
     "data": {"token": QWEN_KEY}},
]
cpath = os.path.join(HOME, "_creds_plain.json")
try:
    io.open(cpath, "w", encoding="utf-8").write(json.dumps(creds))
    n8n("import:credentials", "--input=" + cpath)
finally:
    os.remove(cpath)                   # plaintext secrets live only for the import itself
print("  plaintext credential file removed:", not os.path.exists(cpath))

wf = json.load(io.open(os.path.join(ROOT, "n8n", "self_healing_pipeline.json"), encoding="utf-8"))
# The Qwen URL is built by "Get Model URL" (stored address, else $vars.QWEN_BASE_URL). Local n8n has
# no $vars, so that fallback becomes the literal address in this import copy.
g = next(n for n in wf["nodes"] if n["name"] == "Get Model URL")
code = g["parameters"]["jsCode"]
assert "$vars.QWEN_BASE_URL" in code, "Get Model URL no longer reads $vars.QWEN_BASE_URL"
g["parameters"]["jsCode"] = code.replace("$vars.QWEN_BASE_URL", json.dumps(QWEN))
assert "$vars" not in g["parameters"]["jsCode"], "Get Model URL still references $vars"
wf["id"] = WF_ID
wpath = os.path.join(HOME, "workflow_import.json")
io.open(wpath, "w", encoding="utf-8").write(json.dumps(wf))
n8n("import:workflow", "--input=" + wpath)
n8n("update:workflow", "--id=" + WF_ID, "--active=true")
print("  model URL fallback in import copy:", QWEN)
