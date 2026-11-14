"""Take the healer's front door down: stop the tunnel and n8n started by up.py.

N8N_WEBHOOK_URL is left pointing at the dead tunnel on purpose. The notify job now warns
("n8n webhook unreachable - heal not triggered") instead of failing, so a dead healer no longer
turns an unrelated commit red, and the next up.py re-points it.
"""
import io, os, subprocess, urllib.request

HOME = os.path.join(os.environ["LOCALAPPDATA"], "selfheal-n8n")
for name in ("tunnel", "n8n"):
    f = os.path.join(HOME, name + ".pid")
    if not os.path.exists(f):
        print("%-6s no pid file" % name)
        continue
    pid = io.open(f).read().strip()
    p = subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, text=True)
    print("%-6s pid %s: %s" % (name, pid, "stopped" if p.returncode == 0 else "not running"))
    os.remove(f)

try:
    urllib.request.urlopen("http://127.0.0.1:5678/healthz", timeout=3)
    print("WARNING: something still answers on :5678")
except Exception:
    print(":5678 free")
