"""Server-side idempotency for the POSTs a retry must not repeat.

A client sends `Idempotency-Key: <any string up to 128 chars>` on POST /v1/requests,
POST /v1/requests/{id}/messages or POST /v1/subscriptions/checkout. The first call with a
key runs normally and its successful JSON answer is kept against (account, path, key);
a repeat with the same key and the same body gets that answer back without running again
(no second request, no second supplier message, no second pending payment). The same key
with a different body is refused (422); a repeat that arrives while the first is still
running gets 409. Validation failures release their key. In per-user Haraj mode, a server failure or
interruption retains the reservation until reconciled; a retry cannot create a
second request or supplier message after an uncertain outcome.

This sits in front of the route handlers as ASGI middleware so the handlers themselves
stay unchanged. Calls without the header behave exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import re

IDEMPOTENT_POSTS = (
    re.compile(r"^/v1/requests$"),
    re.compile(r"^/v1/requests/[^/]+/messages$"),
    re.compile(r"^/v1/requests/[^/]+/counter$"),
    re.compile(r"^/v1/subscriptions/checkout$"),
)
MAX_KEY_LENGTH = 128


class IdempotencyMiddleware:
    def __init__(self, app, store):
        self.app = app
        self.store = store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not any(pattern.match(scope["path"]) for pattern in IDEMPOTENT_POSTS):
            await self.app(scope, receive, send)
            return
        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
        key = headers.get("idempotency-key", "").strip()
        authorization = headers.get("authorization", "")
        user_id = self.store.user_for_token(authorization.removeprefix("Bearer ").strip()) if key and authorization.startswith("Bearer ") else None
        if not key or user_id is None:
            # No key, or not signed in (the handler answers 401): nothing to remember.
            await self.app(scope, receive, send)
            return
        if len(key) > MAX_KEY_LENGTH:
            await _json(send, 422, {"detail": f"Idempotency-Key is too long (max {MAX_KEY_LENGTH} characters)"})
            return

        # Read the body once to fingerprint it, then hand the same bytes to the handler.
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        fingerprint = hashlib.sha256(body).hexdigest()
        path = scope["path"]

        existing = self.store.reserve_idempotency(user_id, path, key, fingerprint)
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                await _json(send, 422, {"detail": "Idempotency-Key was already used for a different request"})
            elif existing["response"] is None:
                await _json(send, 409, {"detail": "a request with this Idempotency-Key is still in progress"})
            else:
                await _json(send, 200, existing["response"], replayed=True)
            return

        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        status = 500
        captured: list[bytes] = []

        async def capture_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                captured.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        except BaseException:
            if not _haraj_reconciliation_required(path):
                self.store.release_idempotency(user_id, path, key)
            raise
        try:
            response = json.loads(b"".join(captured)) if 200 <= status < 300 else None
        except ValueError:
            response = None
        if isinstance(response, dict):
            self.store.complete_idempotency(user_id, path, key, response)
        elif status < 500 or not _haraj_reconciliation_required(path):
            self.store.release_idempotency(user_id, path, key)


def _haraj_reconciliation_required(path):
    from farq.haraj_user_connection import enabled
    return enabled() and path.startswith("/v1/requests")


async def _json(send, status: int, payload: dict, replayed: bool = False) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if replayed:
        headers.append((b"idempotent-replayed", b"true"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
