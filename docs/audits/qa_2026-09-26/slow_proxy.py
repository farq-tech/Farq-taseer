"""Forwards to the local Taseer server, holding POST .../messages for 800ms (a phone on 4G to Sydney)."""
import time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
UP = "http://127.0.0.1:8790"
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _fwd(self):
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else None
        if self.command == "POST" and self.path.endswith("/messages"): time.sleep(0.8)
        req = urllib.request.Request(UP + self.path, data=body, method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "accept-encoding")})
        try:
            with urllib.request.urlopen(req, timeout=60) as r: status, hdrs, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e: status, hdrs, data = e.code, e.headers, e.read()
        self.send_response(status)
        for k, v in hdrs.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection", "content-encoding"): continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = do_DELETE = _fwd
ThreadingHTTPServer(("127.0.0.1", 8791), H).serve_forever()
