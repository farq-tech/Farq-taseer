"""Signing in with the Farq account from inside farq.sa (POST /v1/auth/farq)."""

from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.farq_auth import FarqIdentity
from farq.store import Store


def make(tmp_path: Path, verifier):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), farq_verifier=verifier)
    return TestClient(app), store


def farq_user(token: str):
    people = {
        "tok-sara": FarqIdentity(user_id="farq-1", email="sara@example.com", name="سارة", email_verified=True),
        "tok-unconfirmed": FarqIdentity(user_id="farq-2", email="a@example.com", name=None, email_verified=False),
    }
    return people.get(token)


def test_a_farq_session_becomes_a_taseer_session_without_a_second_password(tmp_path, monkeypatch):
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    monkeypatch.setenv("FARQ_REQUIRE_EMAIL_VERIFICATION", "on")
    api, store = make(tmp_path, farq_user)
    first = api.post("/v1/auth/farq", json={"access_token": "tok-sara"})
    assert first.status_code == 200, first.text
    headers = {"Authorization": f"Bearer {first.json()['token']}"}
    assert first.json()["email"] == "sara@example.com"
    assert first.json()["name"] == "سارة"
    assert api.get("/v1/auth/me", headers=headers).json() == {"email": "sara@example.com", "name": "سارة"}
    # Farq confirmed the address, so the first send is not blocked behind Taseer's own email check.
    assert api.get("/v1/auth/verify/status", headers=headers).json()["verified"] is True
    sent = api.post(
        "/v1/requests",
        headers=headers,
        json={"original_text": "أبي سباك", "need": "سباك", "city": "الرياض", "recipients": [{"seller_id": "14371810", "seller_name": "سباك"}]},
    )
    assert sent.status_code == 200, sent.text
    # A second visit is the same account, not a second one.
    again = api.post("/v1/auth/farq", json={"access_token": "tok-sara"})
    assert again.status_code == 200
    assert api.get("/v1/requests", headers={"Authorization": f"Bearer {again.json()['token']}"}).json()["requests"][0]["id"] == sent.json()["id"]
    assert store._connection.execute("select count(*) as n from users").fetchone()["n"] == 1


def test_an_existing_taseer_account_is_linked_by_its_confirmed_email(tmp_path):
    api, store = make(tmp_path, farq_user)
    registered = api.post("/v1/auth/register", json={"email": "sara@example.com", "password": "secret-pass", "name": "سارة القديمة"})
    assert registered.status_code == 200
    linked = api.post("/v1/auth/farq", json={"access_token": "tok-sara"})
    assert linked.status_code == 200, linked.text
    assert linked.json()["name"] == "سارة القديمة"
    rows = store._connection.execute("select farq_user_id from users where email = 'sara@example.com'").fetchall()
    assert [row["farq_user_id"] for row in rows] == ["farq-1"]


def test_an_unconfirmed_farq_email_cannot_claim_someone_elses_account(tmp_path):
    api, _store = make(tmp_path, farq_user)
    assert api.post("/v1/auth/register", json={"email": "a@example.com", "password": "secret-pass", "name": "عميل"}).status_code == 200
    refused = api.post("/v1/auth/farq", json={"access_token": "tok-unconfirmed"})
    assert refused.status_code == 409


def test_a_bad_or_missing_token_is_refused(tmp_path):
    api, _store = make(tmp_path, farq_user)
    assert api.post("/v1/auth/farq", json={"access_token": "nope"}).status_code == 401
    assert api.post("/v1/auth/farq", json={}).status_code == 422


def test_without_any_verifier_settings_the_endpoint_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("FARQ_AUTH_API_URL", "")
    monkeypatch.delenv("FARQ_AUTH_SUPABASE_URL", raising=False)
    monkeypatch.delenv("FARQ_AUTH_SUPABASE_ANON_KEY", raising=False)
    api, _store = make(tmp_path, None)
    assert api.post("/v1/auth/farq", json={"access_token": "tok-sara"}).status_code == 503


def test_verify_asks_farq_api_first_for_a_farq_sa_session(monkeypatch):
    from farq import farq_auth

    monkeypatch.setenv("FARQ_AUTH_API_URL", "https://api.example.test/")
    monkeypatch.delenv("FARQ_AUTH_SUPABASE_URL", raising=False)
    calls = []

    class Response:
        def __init__(self, status, body):
            self.status_code = status; self._body = body
        def json(self):
            return self._body

    def fake_get(url, timeout, headers):
        calls.append((url, headers))
        if headers["Authorization"] == "Bearer good":
            return Response(200, {"ok": True, "data": {"user": {"id": "u-7", "email": "Sara@Example.com", "email_verified": True, "display_name": "سارة"}}})
        return Response(401, {"ok": False})

    monkeypatch.setattr(farq_auth.httpx, "get", fake_get)
    assert farq_auth.verify("good") == FarqIdentity(user_id="u-7", email="sara@example.com", name="سارة", email_verified=True)
    assert calls[0][0] == "https://api.example.test/api/auth/me"
    assert farq_auth.verify("bad") is None


def test_verify_reads_the_supabase_user(monkeypatch):
    from farq import farq_auth

    monkeypatch.setenv("FARQ_AUTH_API_URL", "")
    monkeypatch.setenv("FARQ_AUTH_SUPABASE_URL", "https://example.supabase.co/")
    monkeypatch.setenv("FARQ_AUTH_SUPABASE_ANON_KEY", "anon")
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"id": "u-1", "email": "Sara@Example.com", "email_confirmed_at": "2026-09-01T00:00:00Z", "user_metadata": {"full_name": "سارة"}}

    def fake_get(url, timeout, headers):
        calls.append((url, headers))
        return Response()

    monkeypatch.setattr(farq_auth.httpx, "get", fake_get)
    identity = farq_auth.verify("abc")
    assert identity == FarqIdentity(user_id="u-1", email="sara@example.com", name="سارة", email_verified=True)
    assert calls[0][0] == "https://example.supabase.co/auth/v1/user"
    assert calls[0][1]["Authorization"] == "Bearer abc" and calls[0][1]["apikey"] == "anon"
    assert farq_auth.verify("") is None


def test_the_owner_links_a_password_account_by_signing_in_once_with_farq_present(tmp_path):
    api, store = make(tmp_path, farq_user)
    # An old Taseer account, and a Farq account with the same but unconfirmed email.
    assert api.post("/v1/auth/register", json={"email": "a@example.com", "password": "secret-pass", "name": "المالك"}).status_code == 200
    refused = api.post("/v1/auth/farq", json={"access_token": "tok-unconfirmed"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "TASEER_ACCOUNT_EXISTS"
    # The password plus the Farq session, once.
    signed = api.post("/v1/auth/login", json={"email": "a@example.com", "password": "secret-pass", "farq_access_token": "tok-unconfirmed"})
    assert signed.status_code == 200 and signed.json()["farq_linked"] is True
    # From then on the Farq sign-in lands on the same account.
    again = api.post("/v1/auth/farq", json={"access_token": "tok-unconfirmed"})
    assert again.status_code == 200 and again.json()["name"] == "المالك"
    assert store._connection.execute("select count(*) as n from users").fetchone()["n"] == 1
    # A wrong Farq token links nothing and does not break the password sign-in.
    plain = api.post("/v1/auth/login", json={"email": "a@example.com", "password": "secret-pass", "farq_access_token": "nope"})
    assert plain.status_code == 200 and plain.json()["farq_linked"] is False
