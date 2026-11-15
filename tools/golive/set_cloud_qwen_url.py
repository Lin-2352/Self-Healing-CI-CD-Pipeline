"""Point the cloud n8n's QWEN_BASE_URL variable at the model URL from the latest Kaggle boot.

The Kaggle endpoint gets a new trycloudflare.com address on every boot. watch_deploy.py saves it to
%LOCALAPPDATA%\\selfheal-n8n\\qwen_url.txt; this script copies it into the cloud n8n variable through
n8n's public API, so nothing has to be pasted by hand.

One-time setup (the key is stored only in %LOCALAPPDATA%\\selfheal-n8n\\cloud_api.json, never in the repo):
    python tools/golive/set_cloud_qwen_url.py --save-key
In n8n: Settings > n8n API > Create an API key, then paste it at the hidden prompt.

Exit codes: 0 updated, 2 nothing to do or not set up yet (start scripts treat this as a warning), 1 failed.
"""
import getpass, io, json, os, sys, urllib.error, urllib.request

HOME = os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n")
CFG = os.path.join(HOME, "cloud_api.json")
DEFAULT_BASE = "https://self-heal.app.n8n.cloud"
VAR = "QWEN_BASE_URL"


def save_key():
    base = input("n8n cloud address [%s]: " % DEFAULT_BASE).strip() or DEFAULT_BASE
    key = getpass.getpass("n8n API key (input hidden): ").strip()
    if not key:
        sys.exit("no key entered")
    io.open(CFG, "w", encoding="utf-8").write(json.dumps({"base": base.rstrip("/"), "key": key}))
    print("saved to", CFG)


def call(cfg, method, path, body=None):
    req = urllib.request.Request(cfg["base"] + "/api/v1" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-N8N-API-KEY": cfg["key"], "Accept": "application/json",
                                          "Content-Type": "application/json", "User-Agent": "selfheal-launcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:300]}


def main():
    if "--save-key" in sys.argv[1:]:
        return save_key()
    try:
        url = io.open(os.path.join(HOME, "qwen_url.txt"), encoding="utf-8").read().strip()
    except OSError:
        print("no saved model URL yet (run the model start script first)"); sys.exit(2)
    if not os.path.exists(CFG):
        print("cloud n8n API key not set up, so %s was NOT updated." % VAR)
        print("one-time fix: run launcher\\start\\setup_cloud_api_key.bat"); sys.exit(2)
    cfg = json.load(io.open(CFG, encoding="utf-8"))

    code, data = call(cfg, "GET", "/variables?limit=250")
    if code != 200:
        print("could not list n8n variables: HTTP %s %s" % (code, data.get("error", ""))); sys.exit(1)
    rows = data.get("data", data) if isinstance(data, dict) else data
    cur = next((v for v in rows if v.get("key") == VAR), None)
    if cur and cur.get("value") == url:
        print("%s already points at %s" % (VAR, url)); return
    if cur:
        code, data = call(cfg, "PUT", "/variables/%s" % cur["id"], {"key": VAR, "value": url})
    else:
        code, data = call(cfg, "POST", "/variables", {"key": VAR, "value": url})
    if code not in (200, 201, 204):
        print("could not set %s: HTTP %s %s" % (VAR, code, data.get("error", ""))); sys.exit(1)

    code, data = call(cfg, "GET", "/variables?limit=250")
    rows = data.get("data", data) if code == 200 and isinstance(data, dict) else []
    now = next((v.get("value") for v in rows if v.get("key") == VAR), None)
    if now != url:
        print("update was accepted but the variable reads back as %r" % now); sys.exit(1)
    print("cloud n8n %s -> %s" % (VAR, url))


if __name__ == "__main__":
    main()
