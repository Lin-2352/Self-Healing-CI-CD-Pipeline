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
        sys.exit("No model URL saved yet. Run launcher\\start\\1_start_model_on_kaggle.bat first.")
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

    print("  cloud n8n                    : updated by 1_start_model_on_kaggle.bat (or run set_cloud_qwen_url.py)")

    subprocess.run("clip", input=url.encode("utf-8"), shell=True)
    print("\nThe base URL is on your clipboard.")


if __name__ == "__main__":
    main()
