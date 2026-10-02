"""After the award: did the deal happen, how was the supplier, and the counter-offer before it."""

from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store, counter_text

_account = count(1)


@pytest.fixture(autouse=True)
def _any_recipient(monkeypatch):
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")


def signed_in(api: TestClient) -> dict:
    email = f"deal-{next(_account)}@example.com"
    response = api.post("/v1/auth/register", json={"email": email, "password": "secret-pass", "name": "عميل"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    return TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))


def request_with_two_sellers(api: TestClient, headers: dict) -> dict:
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي مكيف سبليت بالرياض",
            "need": "مكيف سبليت",
            "city": "الرياض",
            "attributes": {},
            "recipients": [
                {"seller_id": "1", "seller_name": "تكييف الشمال", "ad_id": "10"},
                {"seller_id": "2", "seller_name": "مكيفات الخليج", "ad_id": "11"},
            ],
        },
    )
    assert created.status_code == 200, created.text
    return created.json()


def price(api: TestClient, token: str, seller_id: str, amount: float, **extra):
    reply = api.post(f"/v1/seller/{token}/messages", json={"seller_id": seller_id, "body": "", "offer_amount": amount, **extra})
    assert reply.status_code == 200, reply.text
    return reply


def test_offer_condition_comes_only_from_the_form(tmp_path: Path):
    api = client(tmp_path)
    headers = signed_in(api)
    created = request_with_two_sellers(api, headers)
    # Each supplier answers through his own link.
    tokens = {item["seller_id"]: item["reply_token"] for item in created["recipients"]}
    token = tokens["1"]
    price(api, token, "1", 1500, condition="used")
    # A reply that only says «جديد» in its words is not a stated condition.
    said = api.post(f"/v1/seller/{tokens['2']}/messages", json={"body": "جديد بالكرتون", "offer_amount": 1900})
    assert said.status_code == 200, said.text
    offers = {item["seller_id"]: item for item in api.get(f"/v1/requests/{created['id']}", headers=headers).json()["offers"]}
    assert offers["1"]["condition"] == "used"
    assert offers["2"]["condition"] is None
    assert offers["1"]["created_at"]
    bad = api.post(f"/v1/seller/{token}/messages", json={"seller_id": "1", "body": "", "offer_amount": 1500, "condition": "refurbished"})
    assert bad.status_code == 422


def test_counter_offer_is_one_fixed_message_to_one_supplier(tmp_path: Path):
    api = client(tmp_path)
    headers = signed_in(api)
    created = request_with_two_sellers(api, headers)
    request_id, token = created["id"], created["reply_token"]
    # Nothing to counter before he prices.
    assert api.post(f"/v1/requests/{request_id}/counter", headers=headers, json={"seller_id": "1", "amount": 1000}).status_code == 422
    price(api, token, "1", 1500)
    # Not below his price, and not from someone else's account.
    assert api.post(f"/v1/requests/{request_id}/counter", headers=headers, json={"seller_id": "1", "amount": 1500}).status_code == 422
    assert api.post(f"/v1/requests/{request_id}/counter", headers=signed_in(api), json={"seller_id": "1", "amount": 1300}).status_code == 404
    sent = api.post(f"/v1/requests/{request_id}/counter", headers=headers, json={"seller_id": "1", "amount": 1300})
    assert sent.status_code == 200, sent.text
    record = sent.json()
    assert record["counters"] == [
        {**record["counters"][0], "seller_id": "1", "amount": 1300, "against_total": 1500, "answered": False}
    ]
    message = next(item for item in record["messages"] if item["id"] == record["counters"][0]["message_id"])
    assert message["body"] == counter_text(1300)
    assert message["seller_id"] == "1"
    # Only he was addressed: the other supplier has no delivery for it.
    assert {item["seller_id"] for item in message["deliveries"]} <= {"1"}
    # One counter per offer.
    assert api.post(f"/v1/requests/{request_id}/counter", headers=headers, json={"seller_id": "1", "amount": 1200}).status_code == 409
    # He prices again: the counter is answered, and a new one may follow.
    price(api, token, "1", 1400)
    after = api.get(f"/v1/requests/{request_id}", headers=headers).json()
    assert after["counters"][0]["answered"] is True
    assert api.post(f"/v1/requests/{request_id}/counter", headers=headers, json={"seller_id": "1", "amount": 1350}).status_code == 200
    # Closed once awarded.
    assert api.post(f"/v1/requests/{request_id}/award", headers=headers, json={"seller_id": "1", "notify": False}).status_code == 200
    price(api, token, "1", 1390)
    assert api.post(f"/v1/requests/{request_id}/counter", headers=headers, json={"seller_id": "1", "amount": 1000}).status_code == 409


def test_deal_outcome_and_rating_follow_the_award(tmp_path: Path):
    api = client(tmp_path)
    headers = signed_in(api)
    created = request_with_two_sellers(api, headers)
    request_id, token = created["id"], created["reply_token"]
    price(api, token, "1", 1500)
    # No outcome before an award, no rating before a completed deal.
    assert api.post(f"/v1/requests/{request_id}/outcome", headers=headers, json={"outcome": "completed"}).status_code == 409
    assert api.post(f"/v1/requests/{request_id}/award", headers=headers, json={"seller_id": "1", "notify": False}).status_code == 200
    assert api.get(f"/v1/requests/{request_id}", headers=headers).json()["deal"] is None
    assert api.post(f"/v1/requests/{request_id}/rating", headers=headers, json={"rating": 5}).status_code == 409
    fell = api.post(f"/v1/requests/{request_id}/outcome", headers=headers, json={"outcome": "not_completed", "paid_total": 1500})
    assert fell.status_code == 200
    assert fell.json()["deal"]["outcome"] == "not_completed"
    assert fell.json()["deal"]["paid_total"] is None
    assert api.post(f"/v1/requests/{request_id}/rating", headers=headers, json={"rating": 5}).status_code == 409
    # He corrects it, then rates once.
    done = api.post(f"/v1/requests/{request_id}/outcome", headers=headers, json={"outcome": "completed", "paid_total": 1450})
    assert done.json()["deal"] == {**done.json()["deal"], "outcome": "completed", "paid_total": 1450, "rating": None}
    assert api.get("/v1/requests", headers=headers).json()["requests"][0]["deal_outcome"] == "completed"
    assert api.post(f"/v1/requests/{request_id}/rating", headers=headers, json={"rating": 6}).status_code == 422
    rated = api.post(f"/v1/requests/{request_id}/rating", headers=headers, json={"rating": 4, "note": " التزم بالسعر "})
    assert rated.status_code == 200
    assert rated.json()["deal"]["rating"] == 4
    assert rated.json()["deal"]["rating_note"] == "التزم بالسعر"
    assert api.post(f"/v1/requests/{request_id}/rating", headers=headers, json={"rating": 1}).status_code == 409
    assert api.post(f"/v1/requests/{request_id}/outcome", headers=headers, json={"outcome": "not_completed"}).status_code == 409
    # Another customer cannot touch it.
    assert api.post(f"/v1/requests/{request_id}/outcome", headers=signed_in(api), json={"outcome": "completed"}).status_code == 404


def test_counter_text_never_uses_exponent_form():
    assert "1234567 ريال" in counter_text(1234567)
    assert "1300 ريال" in counter_text(1300.0)
    assert "1299.5 ريال" in counter_text(1299.5)
