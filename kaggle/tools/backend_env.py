"""Point ci-heal-backend's classifier and fixer at the live Kaggle endpoint.

    python kaggle/tools/backend_env.py              update ci-heal-backend/.env (made from .env.example if missing)
    python kaggle/tools/backend_env.py --check      also call GET /v1/models through the tunnel

The URL comes from %LOCALAPPDATA%\\selfheal-n8n\\qwen_url.txt, which watch_deploy.py writes when the
endpoint is up. The key is the API_KEY the endpoint was started with (kaggle/endpoint/deploy_src.py).
Only the CLASSIFIER_/FIXER_LLM_{PROVIDER,BASE_URL,API_KEY,MODEL} lines change; every other line of
.env is kept as it is. The key is never printed. .env is ignored by git.
"""
import io, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "ci-heal-backend")
ENV, EXAMPLE = os.path.join(BACKEND, ".env"), os.path.join(BACKEND, ".env.example")
URL_FILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "selfheal-n8n", "qwen_url.txt")


def endpoint_key():
    src = io.open(os.path.join(ROOT, "kaggle", "endpoint", "deploy_src.py"), encoding="utf-8").read()
    m = re.search(r'^API_KEY = "([^"]+)"', src, re.M)
    if not m:
        sys.exit("API_KEY not found in kaggle/endpoint/deploy_src.py")
    return m.group(1)


def set_values(text, values):
    """Replace KEY=... lines in place; append any key that is not there yet."""
    for k, v in values.items():
        line = "%s=%s" % (k, v)
        pat = re.compile(r"^%s=.*$" % re.escape(k), re.M)
        text = pat.sub(lambda _: line, text) if pat.search(text) else text.rstrip("\n") + "\n" + line + "\n"
    return text


def main():
    if not os.path.exists(URL_FILE):
        sys.exit("no endpoint URL at %s - run kaggle/tools/watch_deploy.py first" % URL_FILE)
    url = open(URL_FILE, encoding="utf-8").read().strip()
    key = endpoint_key()
    values = {}
    for role in ("CLASSIFIER", "FIXER"):
        values.update({role + "_LLM_PROVIDER": "http", role + "_LLM_BASE_URL": url,
                       role + "_LLM_API_KEY": key, role + "_LLM_MODEL": "local"})
    base = ENV if os.path.exists(ENV) else EXAMPLE
    text = io.open(base, encoding="utf-8").read()
    io.open(ENV, "w", encoding="utf-8", newline="\n").write(set_values(text, values))
    print("wrote %s from %s: both roles -> %s (key not shown)" % (os.path.relpath(ENV, ROOT),
                                                                   os.path.basename(base), url))
    if "--check" in sys.argv:
        req = urllib.request.Request(url + "/v1/models", headers={"Authorization": "Bearer " + key})
        try:
            status = urllib.request.urlopen(req, timeout=30).status
        except Exception as ex:
            sys.exit("endpoint check failed: %s" % str(ex)[:120])
        print("GET /v1/models -> %d" % status)


if __name__ == "__main__":
    main()
