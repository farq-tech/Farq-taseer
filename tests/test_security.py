"""Auth hardening, input limits, API 404s, security headers, push and idempotency."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store, token_digest


def make(tmp_path: Path, **kwargs):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), **kwargs)
    return TestClient(app), store


def register(api: TestClient, email: str = "a@example.com", password: str = "secret-pass") -> dict:
    response = api.post("/v1/auth/register", json={"email": email, "password": password, "name": "عميل"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_sessions_store_only_a_digest_of_the_token(tmp_path):
    api, store = make(tmp_path)
    headers = register(api)
    token = headers["Authorization"].removeprefix("Bearer ")
    stored = [row["token"] for row in store._connection.execute("select token from sessions")]
    assert token not in stored and token_digest(token) in stored
    assert api.get("/v1/auth/me", headers=headers).status_code == 200
    # The digest itself is not a usable token.
    assert api.get("/v1/auth/me", headers={"Authorization": f"Bearer {token_digest(token)}"}).status_code == 401


def test_a_session_from_before_hashing_still_works_and_is_rehashed(tmp_path):
    api, store = make(tmp_path)
    register(api)
    user_id = store._connection.execute("select id from users").fetchone()["id"]
    legacy = "legacy-raw-token-issued-before-hashing-0000"
    store._connection.execute("insert into sessions (token, user_id, created_at) values (?, ?, ?)", (legacy, user_id, datetime.now(timezone.utc).isoformat()))
    store._connection.commit()
    assert api.get("/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200
    stored = {row["token"] for row in store._connection.execute("select token from sessions")}
    assert legacy not in stored and token_digest(legacy) in stored
    assert api.get("/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200


def test_sessions_expire(tmp_path):
    api, store = make(tmp_path)
    headers = register(api)
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    store._connection.execute("update sessions set created_at = ?", (old,))
    store._connection.commit()
    assert api.get("/v1/auth/me", headers=headers).status_code == 401
    assert store._connection.execute("select count(*) from sessions").fetchone()[0] == 0


def test_repeated_wrong_passwords_are_throttled(tmp_path):
    api, _ = make(tmp_path)
    register(api)
    for _ in range(10):
        assert api.post("/v1/auth/login", json={"email": "a@example.com", "password": "wrong-pass"}).status_code == 401
    blocked = api.post("/v1/auth/login", json={"email": "a@example.com", "password": "secret-pass"})
    assert blocked.status_code == 429 and blocked.headers["retry-after"]
    # Registering the same email is not a way around it.
    assert api.post("/v1/auth/register", json={"email": "a@example.com", "password": "secret-pass", "name": "عميل"}).status_code == 429
    # Another address is not blocked by someone else's failures.
    other = api.post("/v1/auth/login", json={"email": "a@example.com", "password": "secret-pass"}, headers={"x-real-ip": "203.0.113.9"})
    assert other.status_code == 200


def test_an_unknown_email_and_a_wrong_password_look_the_same(tmp_path):
    api, _ = make(tmp_path)
    register(api)
    unknown = api.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "secret-pass"})
    wrong = api.post("/v1/auth/login", json={"email": "a@example.com", "password": "wrong-pass"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_search_input_is_capped(tmp_path):
    api, _ = make(tmp_path)
    long_query = "سباك بالرياض " * 60
    many = "، ".join(["سباك", "كهربائي", "نجار", "تنظيف مكيفات", "نقل عفش", "كشف تسربات"]) + " بالرياض"
    for path in ("/v1/intent", "/v1/search", "/v1/search/stream"):
        too_long = api.post(path, json={"query": long_query})
        assert too_long.status_code == 422 and "500" in too_long.json()["detail"]
        too_many = api.post(path, json={"query": many})
        assert too_many.status_code == 422 and "5" in too_many.json()["detail"]
    assert api.post("/v1/intent", json={"query": "سباك و كهربائي بالرياض"}).status_code == 200


def test_searches_are_rate_limited_per_address(tmp_path, monkeypatch):
    monkeypatch.setenv("FARQ_SEARCH_PER_MINUTE", "3")
    api, _ = make(tmp_path)
    for _ in range(3):
        assert api.post("/v1/search", json={"query": "أبي نجار"}).status_code == 200
    assert api.post("/v1/search", json={"query": "أبي نجار"}).status_code == 429
    assert api.post("/v1/search", json={"query": "أبي نجار"}, headers={"x-real-ip": "198.51.100.7"}).status_code == 200


def test_unknown_api_paths_are_json_404s(tmp_path):
    api, _ = make(tmp_path)
    for method in ("get", "post"):
        response = getattr(api, method)("/v1/does-not-exist")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
    assert api.get("/v1/cities").status_code == 200
    assert api.get("/some/app/route").headers["content-type"].startswith("text/html")


def test_security_headers_and_docs(tmp_path, monkeypatch):
    api, _ = make(tmp_path)
    for path in ("/", "/v1/cities"):
        response = api.get(path)
        csp = response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "https://cdn.moyasar.com" in csp and "https://fonts.googleapis.com" in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert api.get("/openapi.json").status_code == 200  # local development keeps the docs

    monkeypatch.setenv("VERCEL", "1")
    (tmp_path / "prod").mkdir()
    prod, _ = make(tmp_path / "prod")
    for path in ("/docs", "/redoc", "/openapi.json"):
        page = prod.get(path)
        # The web app answers instead of the docs.
        assert page.headers["content-type"].startswith("text/html")
        assert "swagger" not in page.text.lower() and "redoc" not in page.text.lower()
    assert '"openapi"' not in prod.get("/openapi.json").text


def test_other_users_cannot_read_a_search_trace(tmp_path):
    api, _ = make(tmp_path)
    owner = register(api)
    trace_id = api.post("/v1/search", json={"query": "أبي نجار"}, headers=owner).json()["trace_id"]
    assert api.get(f"/v1/search/{trace_id}/trace", headers=owner).status_code == 200
    assert api.get(f"/v1/search/{trace_id}/trace", headers=register(api, "b@example.com")).status_code == 404
    anonymous = api.post("/v1/search", json={"query": "أبي نجار"}).json()["trace_id"]
    assert api.get(f"/v1/search/{anonymous}/trace", headers=owner).status_code == 404


def test_push_endpoints_are_known_services_and_stay_with_their_account(tmp_path):
    api, _ = make(tmp_path)
    alice = register(api)
    bob = register(api, "b@example.com")
    keys = {"p256dh": "k", "auth": "a"}
    for endpoint in ("https://attacker.example/hook", "https://169.254.169.254/latest", "https://fcm.googleapis.com.evil.io/x", "http://fcm.googleapis.com/fcm/send/x", "https://fcm.googleapis.com:8443/x"):
        assert api.post("/v1/push/subscribe", headers=alice, json={"endpoint": endpoint, "keys": keys}).status_code == 422, endpoint
    endpoint = "https://fcm.googleapis.com/fcm/send/device-1"
    assert api.post("/v1/push/subscribe", headers=alice, json={"endpoint": endpoint, "keys": keys}).status_code == 200
    assert api.post("/v1/push/subscribe", headers=alice, json={"endpoint": endpoint, "keys": {"p256dh": "k2", "auth": "a2"}}).status_code == 200
    assert api.post("/v1/push/subscribe", headers=bob, json={"endpoint": endpoint, "keys": keys}).status_code == 409
    assert api.post("/v1/push/subscribe", headers=bob, json={"endpoint": "https://web.push.apple.com/abc", "keys": keys}).status_code == 200


def test_a_retried_request_or_message_with_an_idempotency_key_is_not_repeated(tmp_path, monkeypatch):
    # picks sellers by id without searching first; test_limits covers the search rule
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    api, store = make(tmp_path)
    headers = register(api)
    body = {"original_text": "سباك", "need": "سباك", "city": "الرياض", "recipients": [{"seller_id": "11", "seller_name": "محمد"}]}
    first = api.post("/v1/requests", headers={**headers, "Idempotency-Key": "req-1"}, json=body)
    again = api.post("/v1/requests", headers={**headers, "Idempotency-Key": "req-1"}, json=body)
    assert first.status_code == again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(api.get("/v1/requests", headers=headers).json()["requests"]) == 1
    # Without a key nothing changes: a second POST is a second request.
    assert api.post("/v1/requests", headers=headers, json=body).json()["id"] != first.json()["id"]

    request_id = first.json()["id"]
    message = {"body": "متى تقدر؟"}
    sent = api.post(f"/v1/requests/{request_id}/messages", headers={**headers, "Idempotency-Key": "msg-1"}, json=message)
    resent = api.post(f"/v1/requests/{request_id}/messages", headers={**headers, "Idempotency-Key": "msg-1"}, json=message)
    assert sent.status_code == resent.status_code == 200 and resent.json()["id"] == sent.json()["id"]
    bodies = [item["body"] for item in api.get(f"/v1/requests/{request_id}", headers=headers).json()["messages"]]
    assert bodies.count("متى تقدر؟") == 1
    # Another account's use of the same key is its own.
    other = register(api, "b@example.com")
    assert api.post("/v1/requests", headers={**other, "Idempotency-Key": "req-1"}, json=body).json()["id"] != first.json()["id"]
    # A failed attempt gives the key back.
    assert api.post("/v1/requests/missing/messages", headers={**headers, "Idempotency-Key": "msg-2"}, json=message).status_code == 404
    assert store._connection.execute("select count(*) from idempotency_keys where key = 'msg-2'").fetchone()[0] == 0
