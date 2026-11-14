"""Stand-in for GitHub raw content + the model endpoint. Scenario chosen per request via file."""
import http.server, json, io, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _scn(self):
        return json.load(io.open(os.path.join(HERE, "scenario.json"), encoding="utf-8"))
    def do_GET(self):
        s = self._scn()
        if self.path.startswith("/file"):
            b = s["file"].encode("utf-8"); ct = "text/plain; charset=utf-8"
        elif self.path.startswith("/model"):
            content = json.dumps(s["model"])
            b = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}).encode()
            ct = "application/json"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
