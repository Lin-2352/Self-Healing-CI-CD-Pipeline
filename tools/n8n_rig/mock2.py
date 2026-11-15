"""Mock for the FULL front half of the workflow: webhook payload, jobs API, job log, commit files, raw
file, and the model. The model endpoint RECORDS the request it receives (last_request.json), so a run
shows exactly what production sends the model - not what the code appears to send.

The job log mimics GitHub exactly where it is risky: `/repos/<o>/<r>/actions/jobs/<id>/logs` answers
302 to a DIFFERENT host (127.0.0.2 vs 127.0.0.1), like GitHub's redirect to signed blob storage. The
blob hop records whether an Authorization header arrived (blob_auth.json) - it must not, or the PAT
would be leaking to the storage host. scenario["log"] = null makes the log endpoint 404."""
import base64, http.server, io, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1])
def scn(): return json.load(io.open(os.path.join(HERE, "scenario.json"), encoding="utf-8"))
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj=None, text=None):
        b = text.encode("utf-8") if text is not None else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8" if text is not None else "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        s = scn(); p = self.path
        if p.startswith("/repos/") and "/actions/jobs/" in p and p.endswith("/logs"):
            job = p.split("/actions/jobs/")[1].split("/")[0]
            io.open(os.path.join(HERE, "log_hit.json"), "w").write(json.dumps({
                "job_id": job, "auth": bool(self.headers.get("Authorization"))}))
            if s.get("log") is None:
                self.send_response(404); self.end_headers(); return
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2:%d/blob/log?sig=x" % PORT)
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if p.startswith("/blob/log"):
            io.open(os.path.join(HERE, "blob_auth.json"), "w").write(json.dumps({
                "auth": self.headers.get("Authorization") is not None}))
            return self.send(text=s["log"])
        if p.startswith("/hook"):  return self.send({"body": s["hook"]})
        if p.startswith("/jobs"):  return self.send(s["jobs"])
        if p.startswith("/files"): return self.send(s["files"])
        if p.startswith("/file"):  return self.send(text=s["file"])
        # GitHub's JSON contents API, one file per request (Fetch File Content runs per candidate).
        # scenario "tree": path -> content; otherwise the single legacy "file" is served as test.js.
        if p.startswith("/repos/") and "/contents/" in p:
            path = p.split("/contents/", 1)[1].split("?", 1)[0]
            io.open(os.path.join(HERE, "contents_hits.json"), "a").write(json.dumps(path) + "\n")
            tree = s.get("tree") or {"test.js": s["file"]}
            if path not in tree:
                b = json.dumps({"message": "Not Found", "status": "404"}).encode()
                self.send_response(404); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
            return self.send({"path": path, "type": "file", "encoding": "base64",
                              "content": base64.b64encode(tree[path].encode("utf-8")).decode("ascii")})
        self.send_response(404); self.end_headers()
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n).decode("utf-8")
        s = scn(); p = self.path
        # the failure branch's comment (PR: /issues/N/comments, first heal: /commits/SHA/comments)
        if p.startswith("/repos/") and p.endswith("/comments"):
            io.open(os.path.join(HERE, "comment_post.json"), "w", encoding="utf-8").write(json.dumps(
                {"path": p, "body": json.loads(body or "{}").get("body"),
                 "auth": bool(self.headers.get("Authorization"))}))
            return self.send({"id": 1, "html_url": "http://mock/comment/1"})
        io.open(os.path.join(HERE, "last_request.json"), "w", encoding="utf-8").write(body)
        # The OpenAI fallback stub: scenario["openai"] is the fix it answers with, "openai_status" makes it fail.
        if p.startswith("/openai"):
            if s.get("openai_status") or "openai" not in s:
                b = json.dumps({"error": "Forbidden - perhaps check your credentials?"}).encode()
                self.send_response(int(s.get("openai_status") or 403)); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
            return self.send({"choices": [{"message": {"content": json.dumps(s["openai"])}, "finish_reason": "stop"}]})
        # Cloudflare's quick-tunnel timeout, as it really answered live (n8n execution #33)
        if s.get("model_status"):
            b = json.dumps({"error": "The origin web server did not return a complete response within the "
                                     "120-second Proxy Read Timeout window."}).encode()
            self.send_response(int(s["model_status"])); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        content = json.dumps(s["model"])
        if json.loads(body or "{}").get("stream"):
            # llama-server's SSE shape: delta chunks, a final chunk with finish_reason + timings, [DONE]
            ev = lambda o: "data: " + json.dumps(o) + "\n\n"
            out = "".join(ev({"choices": [{"index": 0, "delta": {"content": content[i:i + 9]}, "finish_reason": None}]})
                          for i in range(0, len(content), 9))
            out += ev({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                       "timings": {"predicted_n": 99, "predicted_per_second": 25.0}})
            out += "data: [DONE]\n\n"
            b = out.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        return self.send({"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                          "usage": {"completion_tokens": 1}})
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
