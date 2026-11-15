"""Print what the n8n workflow and the backend need to reach the Kaggle model, and copy the URL.

Shows the current model URL (saved by watch_deploy.py), the endpoint's API key (fixed in
kaggle/endpoint/deploy_src.py), whether the model answers, and whether the cloud n8n variable
QWEN_BASE_URL already holds this URL. The URL goes to the clipboard.
"""
import io, json, os, re, subprocess, sys, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = os.path.join(os.environ.get("LOCALAPPDATA", ""), "selfheal-n8n")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        url = io.open(os.path.join(HOME, "qwen_url.txt"), encoding="utf-8").read().strip()
    except OSError:
        sys.exit("No model URL saved yet. Run launcher\start\1_start_model_on_kaggle.bat first.")
    src = io.open(os.path.join(ROOT, "kaggle", "endpoint", "deploy_src.py"), encoding="utf-8").read()
    key = re.search(r'^API_KEY = "([^"]+)"', src, re.M).group(1)

    try:
        req = urllib.request.Request(url + "/v1/models", headers={"Authorization": "Bearer " + key})
        status = "answering (HTTP %d)" % urllib.request.urlopen(req, timeout=20).status
    except urllib.error.HTTPError as e:
        status = "reachable but returned HTTP %d" % e.code
    except Exception as e:
        status = "NOT reachable (%s); the Kaggle session may be stopped" % type(e).__name__

    print("Qwen endpoint")
    print("  base URL (n8n QWEN_BASE_URL) : %s" % url)
    print("  chat endpoint                : %s/v1/chat/completions" % url)
    print("  API key (n8n 'Qwen3.8 Endpoint Key' token) : %s" % key)
    print("  status                       : %s" % status)

    cfg_path = os.path.join(HOME, "cloud_api.json")
    if os.path.exists(cfg_path):
        import set_cloud_qwen_url as s
        cfg = json.load(io.open(cfg_path, encoding="utf-8"))
        code, data = s.call(cfg, "GET", "/variables?limit=250")
        rows = data.get("data", data) if code == 200 and isinstance(data, dict) else []
        cur = next((v.get("value") for v in rows if v.get("key") == "QWEN_BASE_URL"), None)
        print("  cloud n8n QWEN_BASE_URL      : %s" % ("up to date" if cur == url else "OUT OF DATE (%s)" % (cur or "not set")))
    else:
        print("  cloud n8n QWEN_BASE_URL      : not checked (run launcher\start\setup_cloud_api_key.bat once to automate it)")

    subprocess.run("clip", input=url.encode("utf-8"), shell=True)
    print("\nThe base URL is on your clipboard.")


if __name__ == "__main__":
    main()
