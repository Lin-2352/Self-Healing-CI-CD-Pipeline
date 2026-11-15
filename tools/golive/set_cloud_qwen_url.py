r"""Tell the cloud n8n workflow where the Kaggle model is, after each boot.

The Kaggle endpoint gets a new trycloudflare.com address on every boot. watch_deploy.py saves it to
%LOCALAPPDATA%\selfheal-n8n\qwen_url.txt; this script posts it to the workflow's "Set Model URL"
webhook (the same shared secret as the failure webhook), and the workflow remembers it. No n8n API
key is needed, which matters because the API is switched off on n8n Cloud trials.

The secret is the `webhook_secret` in %LOCALAPPDATA%\selfheal-n8n\secrets.json (the same value as the
n8n credential "N8N Webhook Secret" and the repo secret N8N_WEBHOOK_SECRET). It is never printed.

Exit codes: 0 stored, 2 nothing to do yet (start scripts treat this as a warning), 1 failed.
"""
import io, json, os, sys, urllib.error, urllib.request

HOME = os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n")
BASE = os.environ.get("N8N_CLOUD_URL", "https://self-heal.app.n8n.cloud").rstrip("/")


def main():
    try:
        with io.open(os.path.join(HOME, "qwen_url.txt"), encoding="utf-8") as f:
            url = f.read().strip()
        with io.open(os.path.join(HOME, "secrets.json"), encoding="utf-8-sig") as f:
            secret = json.load(f)["webhook_secret"]
    except (OSError, KeyError) as e:
        print("cannot read the saved model URL or webhook secret (%s)" % type(e).__name__); sys.exit(2)

    req = urllib.request.Request(BASE + "/webhook/set-qwen-url", method="POST",
                                 data=json.dumps({"url": url}).encode(),
                                 headers={"Content-Type": "application/json", "X-Webhook-Secret": secret,
                                          "User-Agent": "selfheal-launcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        hint = " (wrong secret? it must match the n8n credential)" if e.code in (401, 403) else ""
        print("cloud n8n rejected the update: HTTP %d%s %s" % (e.code, hint, e.read().decode("utf-8", "replace")[:200]))
        sys.exit(1)
    except Exception as e:
        print("could not reach the cloud n8n: %s" % type(e).__name__); sys.exit(1)

    if body.get("url") != url:
        print("unexpected reply from n8n: %s" % json.dumps(body)[:200]); sys.exit(1)
    print("cloud n8n now uses the model at %s (stored %s)" % (url, body.get("stored_at", "")))


if __name__ == "__main__":
    main()
