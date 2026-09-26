"""A supplier's reply asks Farq (server-to-server) to ring the owner's phone.

The Farq side is an httpx.MockTransport speaking POST /api/push/notify with the same
envelope api.farq.sa answers with.
"""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.farq_push import notify_farq_reply, reply_payload
from farq.limits import Limits
from farq.store import Store

SECRET = "s2s-test-secret-0123456789abcdef-0123456789abcdef"
FARQ_UID = "11111111-2222-4333-8444-555555555555"


class FarqPush:
    def __init__(self, delivered: int = 1, status: int = 200):
        self.calls: list[dict] = []
        self.delivered = delivered
        self.status = status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json={"ok": True, "data": {"devices": 1, "delivered": self.delivered}})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FARQ_API_URL", "https://api.farq.test/")
    monkeypatch.setenv("BILLING_S2S_SECRET", SECRET)


def owned_request(tmp_path: Path, *, linked: bool = True) -> tuple[Store, str]:
    """A real request through the API, sent to one seller named «مؤسسة النور»."""
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(
        store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False),
        limits=Limits(recipients_from_search=False),
    )
    api = TestClient(app)
    signup = api.post("/v1/auth/register", json={"email": f"c-{uuid4().hex}@example.com", "password": "secret-pass", "name": "عميل"})
    assert signup.status_code == 200, signup.text
    headers = {"Authorization": f"Bearer {signup.json()['token']}"}
    created = api.post("/v1/requests", headers=headers, json={
        "original_text": "سباك", "need": "سباك", "city": "الرياض",
        "recipients": [{"seller_id": "101", "seller_name": "مؤسسة النور"}],
    })
    assert created.status_code == 200, created.text
    if linked:
        assert store.link_farq(signup.json()["user_id"], FARQ_UID)
    return store, created.json()["id"]


def test_payload_opens_the_conversation_and_collapses_per_request():
    payload = reply_payload(FARQ_UID, "abc123", "مؤسسة النور", "  السعر 450  ")
    assert payload == {
        "user_id": FARQ_UID,
        "title": "مؤسسة النور",
        "body": "السعر 450",
        "path": "/taseer?r=abc123",
        "collapse_id": "taseer:abc123",
    }
    assert reply_payload(FARQ_UID, "abc", "", "")["body"] == "وصلك رد جديد على طلب التسعير"


def test_reply_asks_farq_with_the_service_credential(tmp_path, configured):
    store, request_id = owned_request(tmp_path)
    farq = FarqPush()
    assert notify_farq_reply(store, request_id, "101", "السعر 450 شامل التركيب", client=farq.client()) is True
    [call] = farq.calls
    assert call["path"] == "/api/push/notify"
    assert call["auth"] == f"Bearer {SECRET}"
    assert call["body"]["user_id"] == FARQ_UID
    assert call["body"]["title"] == "مؤسسة النور"
    assert call["body"]["path"] == f"/taseer?r={request_id}"


def test_unlinked_owner_is_not_pushed(tmp_path, configured):
    store, request_id = owned_request(tmp_path, linked=False)
    farq = FarqPush()
    assert notify_farq_reply(store, request_id, "101", "hi", client=farq.client()) is False
    assert farq.calls == []


def test_off_without_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("BILLING_S2S_SECRET", raising=False)
    store, request_id = owned_request(tmp_path)
    farq = FarqPush()
    assert notify_farq_reply(store, request_id, "101", "hi", client=farq.client()) is False
    assert farq.calls == []


def test_farq_failure_never_raises(tmp_path, configured):
    store, request_id = owned_request(tmp_path)
    assert notify_farq_reply(store, request_id, "101", "hi", client=FarqPush(status=503).client()) is False

    def boom(_request):
        raise httpx.ConnectError("down")

    down = httpx.Client(transport=httpx.MockTransport(boom))
    assert notify_farq_reply(store, request_id, "101", "hi", client=down) is False
