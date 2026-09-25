"""Taseer inside Farq signs in with the customer's Farq account: a one-minute ticket from
Farq's API is traded for an ordinary Taseer session."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.limits import Limits
from farq.store import Store

SECRET = "s" * 40


def make(tmp_path: Path):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None,
                     SearchConfig(enable_live=False), limits=Limits(recipients_from_search=False))
    return store, TestClient(app)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def ticket(secret=SECRET, alg="HS256", **overrides) -> str:
    now = int(time.time())
    claims = {"iss": "farq-auth", "aud": "taseer-sso", "sub": "farq-user-1", "email": "Ali@Example.com",
              "email_verified": True, "name": "علي", "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex}
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    body = f"{b64(json.dumps({'alg': alg, 'typ': 'JWT'}).encode())}.{b64(json.dumps(claims).encode())}"
    return f"{body}.{b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())}"


def sso(api, value):
    return api.post("/v1/auth/farq-sso", json={"ticket": value})


def test_off_without_a_secret(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FARQ_SSO_SECRET", raising=False)
    _, api = make(tmp_path)
    assert sso(api, ticket()).status_code == 503


def test_a_farq_ticket_opens_a_taseer_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_SSO_SECRET", SECRET)
    store, api = make(tmp_path)
    first = sso(api, ticket())
    assert first.status_code == 200, first.text
    headers = {"Authorization": f"Bearer {first.json()['token']}"}
    assert api.get("/v1/auth/me", headers=headers).json() == {"email": "ali@example.com", "name": "علي"}
    # Farq verified the address, so Taseer's own verification gate is already satisfied.
    assert api.get("/v1/auth/verify/status", headers=headers).json()["verified"] is True
    # The next ticket for the same Farq user lands on the same account, even if his email changed.
    again = sso(api, ticket(email="new@example.com"))
    assert again.status_code == 200
    assert store._connection.execute("select count(*) from users").fetchone()[0] == 1


def test_forged_or_stale_tickets_are_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_SSO_SECRET", SECRET)
    _, api = make(tmp_path)
    now = int(time.time())
    for bad in [
        ticket(secret="x" * 40),
        ticket(alg="none"),
        ticket(aud="farq-api"),
        ticket(iss="someone"),
        ticket(exp=now - 1),
        ticket(iat=now, exp=now + 3600),
        ticket(exp=None),
        ticket(jti=None),
        ticket(email=None),
        "not.a.ticket",
    ]:
        assert sso(api, bad).status_code == 401, bad


def test_a_ticket_is_spent_on_first_use(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_SSO_SECRET", SECRET)
    _, api = make(tmp_path)
    value = ticket()
    assert sso(api, value).status_code == 200
    assert sso(api, value).status_code == 401


def test_an_unverified_farq_email_cannot_take_over_a_taseer_account(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_SSO_SECRET", SECRET)
    store, api = make(tmp_path)
    owner = api.post("/v1/auth/register", json={"email": "ali@example.com", "password": "secret-pass", "name": "علي"})
    assert owner.status_code == 200

    refused = sso(api, ticket(email_verified=False))
    assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "EMAIL_TAKEN_UNVERIFIED")

    linked = sso(api, ticket())
    assert linked.status_code == 200
    assert store._connection.execute("select count(*) from users").fetchone()[0] == 1
    # Once linked to one Farq user, another Farq user with the same address is not let in.
    assert sso(api, ticket(sub="farq-user-2")).status_code == 409


def test_an_existing_taseer_account_links_once_with_its_password(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_SSO_SECRET", SECRET)
    store, api = make(tmp_path)
    owner = api.post("/v1/auth/register", json={"email": "ali@example.com", "password": "secret-pass", "name": "علي"})
    headers = {"Authorization": f"Bearer {owner.json()['token']}"}
    assert sso(api, ticket(email_verified=False)).status_code == 409

    # A Farq ticket for another address cannot be tied to this account.
    wrong = api.post("/v1/auth/farq-link", headers=headers, json={"ticket": ticket(email="eve@example.com", email_verified=False)})
    assert wrong.status_code == 409
    linked = api.post("/v1/auth/farq-link", headers=headers, json={"ticket": ticket(email_verified=False)})
    assert linked.status_code == 200, linked.text

    # From now on Farq alone signs him in, to the same account.
    again = sso(api, ticket(email_verified=False))
    assert again.status_code == 200
    assert store._connection.execute("select count(*) from users").fetchone()[0] == 1
    assert api.post("/v1/auth/farq-link", json={"ticket": ticket()}).status_code == 401
