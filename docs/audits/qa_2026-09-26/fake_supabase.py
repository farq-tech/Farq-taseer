"""A stand-in for Farq's Supabase Auth: GET /auth/v1/user answers for two known tokens."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

USERS = {
    "farq-good": {"id": "farq-user-1", "email": "sara@farq-qa.test", "email_confirmed_at": "2026-09-01T00:00:00Z", "user_metadata": {"full_name": "سارة القحطاني"}},
}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        user = USERS.get(auth.removeprefix("Bearer ").strip()) if self.path.startswith("/auth/v1/user") else None
        body = json.dumps(user if user else {"code": 401, "msg": "invalid JWT"}).encode()
        self.send_response(200 if user else 401)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

HTTPServer(("127.0.0.1", 8799), H).serve_forever()
