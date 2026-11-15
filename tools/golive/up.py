"""Bring the healer's front door up: n8n + a Cloudflare quick tunnel + the repo's webhook secret.

`up.py --local` starts n8n only (no tunnel, no secret change), for showing the workspace.

Run AFTER watch_deploy.py (the Qwen URL) and n8n_import.py (credentials + workflow, activated).

Readiness is judged BY EFFECT, not by log strings or /healthz:
  n8n answers /healthz BEFORE it activates workflows, so a probe right after health gave a false
  404 "webhook not registered" during the go-live. Here the webhook itself is probed with a WRONG
  secret: 401/403 means registered and guarded (ready); 404 means not registered yet (keep waiting).
  The same probe then goes through the tunnel, because a fresh quick-tunnel hostname takes a few
  seconds to resolve. Only after both pass is N8N_WEBHOOK_URL pointed at it.

Secrets: the webhook secret is never sent (the probe uses a deliberately wrong one); the URL goes
to `gh secret set` over stdin. Everything runtime lives in %LOCALAPPDATA%\\selfheal-n8n, not here.
"""
import io, json, os, re, subprocess, sys, time, urllib.error, urllib.request

HOME = os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n")
N8N = next((p for p in [os.path.join(HOME, "app", "node_modules", "n8n", "bin", "n8n"),
                        os.path.join(os.environ["TEMP"], "n8n_verify", "node_modules", "n8n", "bin", "n8n")]
            if os.path.exists(p)), None)
CLOUDFLARED = os.path.join(HOME, "bin", "cloudflared.exe")
REPO = "Lin-2352/Self-Healing-CI-CD-Pipeline"   # throwaway repo for live tests
PORT = 5678
HOOK_PATH = "/webhook/github-failure"
DETACH = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def probe(base):
    """HTTP status of the webhook when called with a wrong secret (None if unreachable)."""
    if base.startswith("https://"):
        # Tunnel hostnames are brand new: probing one before Cloudflare publishes it makes the
        # Windows DNS client CACHE the NXDOMAIN, and every later probe fails although the tunnel
        # is up (measured: 120 s of "unreachable", while a public-DNS probe answered 403).
        # GitHub's runners use public DNS, so probe the way they will: resolve over DoH.
        p = subprocess.run(["curl.exe", "-s", "-o", "NUL", "-w", "%{http_code}", "-m", "15",
                            "--doh-url", "https://1.1.1.1/dns-query", "-X", "POST",
                            "-H", "Content-Type: application/json",
                            "-H", "X-Webhook-Secret: readiness-probe-wrong-secret",
                            "-d", "{}", base + HOOK_PATH], capture_output=True, text=True)
        return int(p.stdout) if p.stdout.strip().isdigit() and p.stdout != "000" else None
    req =urllib.request.Request(base + HOOK_PATH, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Webhook-Secret": "readiness-probe-wrong-secret"})
    try:
        return urllib.request.urlopen(req, timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def wait_ready(base, label, timeout):
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        last = probe(base)
        if last in (401, 403):
            print("  %s ready: webhook registered and guarded (%s on wrong secret) after %.0fs"
                  % (label, last, time.time() - t0), flush=True)
            return True
        if last == 200:
            sys.exit("  %s: webhook ACCEPTED a wrong secret - header auth is not applied, refusing" % label)
        time.sleep(3)
    print("  %s NOT ready after %ds (last status %s)" % (label, timeout, last))
    return False


def spawn(args, name, env=None):
    out = open(os.path.join(HOME, name + ".out"), "w")
    err = open(os.path.join(HOME, name + ".err"), "w")
    p = subprocess.Popen(args, stdout=out, stderr=err, env=env, cwd=HOME, creationflags=DETACH)
    io.open(os.path.join(HOME, name + ".pid"), "w").write(str(p.pid))
    return p


def main():
    assert N8N, "n8n 1.x not installed - see tools/n8n_rig/README.md"
    sec = json.load(io.open(os.path.join(HOME, "secrets.json"), encoding="utf-8-sig"))
    local = probe("http://127.0.0.1:%d" % PORT)
    if local in (401, 403):
        print("n8n already up and guarded on :%d - reusing it" % PORT)
    elif local is not None:
        sys.exit("something else answers on :%d (status %s) - run down.py first" % (PORT, local))
    else:
        env = dict(os.environ, N8N_USER_FOLDER=HOME, N8N_ENCRYPTION_KEY=sec["encryption_key"],
                   N8N_USER_MANAGEMENT_DISABLED="true", N8N_DIAGNOSTICS_ENABLED="false",
                   N8N_PORT=str(PORT))
        spawn(["node", N8N, "start"], "n8n", env)
        print("n8n starting (logs: %s\\n8n.out)" % HOME, flush=True)
        if not wait_ready("http://127.0.0.1:%d" % PORT, "local n8n", 240):
            sys.exit(1)

    if "--local" in sys.argv[1:]:
        # n8n only, for showing the workspace: no public tunnel, no change to the repo secret.
        print("n8n is up at http://localhost:%d (local only; stop it with down.py)" % PORT)
        return

    url_file =os.path.join(HOME, "tunnel_url.txt")
    if os.path.exists(os.path.join(HOME, "tunnel.pid")) and os.path.exists(url_file):
        url = io.open(url_file).read().strip()
        if probe(url) in (401, 403):
            print("tunnel already up: %s - reusing it" % url)
            return set_secret(url)
    spawn([CLOUDFLARED, "tunnel", "--url", "http://localhost:%d" % PORT, "--no-autoupdate"], "tunnel")
    url, t0 = None, time.time()
    while not url and time.time() - t0 < 90:          # the URL is printed on STDERR
        time.sleep(2)
        m = re.search(r"https://[-a-z0-9]+\.trycloudflare\.com",
                      io.open(os.path.join(HOME, "tunnel.err"), errors="replace").read())
        url = m and m.group(0)
    if not url:
        sys.exit("tunnel never printed a URL (see tunnel.err)")
    io.open(os.path.join(HOME, "tunnel_url.txt"), "w").write(url)
    print("tunnel %s" % url, flush=True)
    if not wait_ready(url, "tunnel", 120):
        sys.exit(1)
    set_secret(url)


def set_secret(url):
    p = subprocess.run(["gh", "secret", "set", "N8N_WEBHOOK_URL", "--repo", REPO],
                       input=url + HOOK_PATH, text=True, capture_output=True)
    if p.returncode != 0:
        sys.exit("gh secret set failed: " + p.stderr[:200])
    print("N8N_WEBHOOK_URL -> %s%s (set via stdin)" % (url, HOOK_PATH))


if __name__ == "__main__":
    main()
