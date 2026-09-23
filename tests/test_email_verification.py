"""The gate on the first send: a free trial per throwaway address is the cheapest way to
spend the shared Haraj send capacity."""

from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.limits import Limits
from farq.store import Store
from tests.test_api import signed_in

MAIL = {"SMTP_HOST": "smtp.example.com", "MAIL_FROM": "no-reply@farq.sa"}


def make(tmp_path: Path, **limits):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None,
                     SearchConfig(enable_live=False), limits=Limits(recipients_from_search=False, **limits))
    return store, TestClient(app)


def ask(api, headers):
    return api.post("/v1/requests", headers=headers, json={
        "original_text": "سباك", "need": "سباك", "city": "الرياض",
        "recipients": [{"seller_id": "8001", "seller_name": "مؤسسة"}],
    })


def test_the_gate_follows_the_mail_provider(tmp_path: Path, monkeypatch):
    """Demanding a confirmation nobody can send would lock out every customer, so with no
    provider the gate stands open — and closes by itself once one is configured."""
    store, api = make(tmp_path)
    headers = signed_in(api)
    assert api.get("/v1/auth/verify/status", headers=headers).json()["required"] is False
    assert ask(api, headers).status_code == 200

    for key, value in MAIL.items():
        monkeypatch.setenv(key, value)
    store2, api2 = make(tmp_path / "b")
    headers2 = signed_in(api2)
    assert api2.get("/v1/auth/verify/status", headers=headers2).json()["required"] is True
    refused = ask(api2, headers2)
    assert (refused.status_code, refused.json()["detail"]["code"]) == (403, "EMAIL_NOT_VERIFIED")
    assert store2._connection.execute("select count(*) from requests").fetchone()[0] == 0


def test_verifying_opens_the_gate(tmp_path: Path, monkeypatch):
    for key, value in MAIL.items():
        monkeypatch.setenv(key, value)
    store, api = make(tmp_path)
    headers = signed_in(api)
    assert ask(api, headers).status_code == 403

    user_id = store._connection.execute("select id from users order by created_at desc limit 1").fetchone()["id"]
    token = store.start_email_verification(user_id)
    assert api.post("/v1/auth/verify", json={"token": token}).status_code == 200
    assert api.get("/v1/auth/verify/status", headers=headers).json()["verified"] is True
    assert ask(api, headers).status_code == 200


def test_a_link_works_once_and_a_new_one_kills_the_old(tmp_path: Path, monkeypatch):
    for key, value in MAIL.items():
        monkeypatch.setenv(key, value)
    store, api = make(tmp_path)
    signed_in(api)
    user_id = store._connection.execute("select id from users order by created_at desc limit 1").fetchone()["id"]

    first = store.start_email_verification(user_id)
    second = store.start_email_verification(user_id)
    # Asking again invalidates the link sitting in the older mail.
    assert api.post("/v1/auth/verify", json={"token": first}).status_code == 404
    assert api.post("/v1/auth/verify", json={"token": second}).status_code == 200
    # And a used link cannot be replayed.
    assert api.post("/v1/auth/verify", json={"token": second}).status_code == 404


def test_an_expired_link_is_refused(tmp_path: Path, monkeypatch):
    for key, value in MAIL.items():
        monkeypatch.setenv(key, value)
    store, api = make(tmp_path)
    signed_in(api)
    user_id = store._connection.execute("select id from users order by created_at desc limit 1").fetchone()["id"]
    token = store.start_email_verification(user_id, ttl_hours=-1)
    assert api.post("/v1/auth/verify", json={"token": token}).status_code == 404
    assert store.email_verified(user_id) is False


def test_the_token_is_stored_hashed(tmp_path: Path):
    store, _api = make(tmp_path)
    user_id = store.register("hash@example.com", "a-password-1", "من")
    token = store.start_email_verification(user_id)
    stored = store._connection.execute("select token from email_verifications").fetchone()["token"]
    assert stored != token and len(stored) == 64


def test_the_gate_can_be_forced_on_or_off(tmp_path: Path):
    store, api = make(tmp_path, require_email_verification="on")
    headers = signed_in(api)
    assert ask(api, headers).status_code == 403
    store2, api2 = make(tmp_path / "b", require_email_verification="off")
    assert ask(api2, signed_in(api2)).status_code == 200


def test_browsing_never_needs_a_verified_address(tmp_path: Path, monkeypatch):
    """The gate is on sending, not on looking: someone must be able to see the product."""
    for key, value in MAIL.items():
        monkeypatch.setenv(key, value)
    _store, api = make(tmp_path)
    headers = signed_in(api)
    assert api.post("/v1/search", headers=headers, json={"query": "أبي سباك بالرياض"}).status_code == 200
    assert api.get("/v1/requests", headers=headers).status_code == 200
    assert api.get("/v1/subscriptions/me", headers=headers).status_code == 200
