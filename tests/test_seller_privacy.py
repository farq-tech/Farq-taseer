"""What a supplier sees, how offers are kept, and what an award closes.

None of these journeys needs the Haraj worker, so they run on SQLite here and on PgStore
through tests/test_store_pg.py's database (see test_seller_journeys_on_postgres below)."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.haraj_chat import InboundMessage
from farq.store import Store

from tests.test_api import signed_in
from tests.test_store_pg import pg_store, schema  # noqa: F401  (fixtures for the Postgres run)

AWARD_TEXT = "تم اختيار عرضك. سنتواصل معك لإكمال التفاصيل."


def app_with_store(tmp_path: Path):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    return api, store


def plumber_request(api: TestClient, headers: dict, notes: str | None = None) -> dict:
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي سباك بالرياض رقمي 0551234567",
            "need": "سباك",
            "notes": notes,
            "city": "الرياض",
            "recipients": [
                {"seller_id": "11", "seller_name": "محمد"},
                {"seller_id": "12", "seller_name": "خالد"},
                {"seller_id": "13", "seller_name": "سعد"},
            ],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    body["tokens"] = {item["seller_id"]: item["reply_token"] for item in body["recipients"]}
    return body


def test_a_message_to_some_suppliers_stays_with_them(tmp_path: Path):
    api, _store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers)
    sent = api.post(
        f"/v1/requests/{created['id']}/messages",
        headers=headers,
        json={"body": "محمد عرض 350، خالد تقدر تنزل عن 300؟", "seller_ids": ["11", "12"]},
    ).json()
    assert (sent["scope"], sent["seller_id"]) == ("some_sellers", None)
    seen = {seller: [item["body"] for item in api.get(f"/v1/seller/{token}").json()["messages"]] for seller, token in created["tokens"].items()}
    assert sent["body"] in seen["11"] and sent["body"] in seen["12"]
    assert sent["body"] not in seen["13"]
    # Everyone still gets the invite and a message to the whole item.
    everyone = api.post(f"/v1/requests/{created['id']}/messages", headers=headers, json={"body": "للكل"}).json()
    assert everyone["scope"] == "all_sellers"
    assert "للكل" in [item["body"] for item in api.get(f"/v1/seller/{created['tokens']['13']}").json()["messages"]]


def test_the_seller_view_shows_no_other_suppliers_and_no_buyer_words(tmp_path: Path):
    api, _store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers, notes="كلمني على 0551234567")
    token = created["tokens"]["11"]
    api.post(f"/v1/seller/{created['tokens']['12']}/messages", json={"offer_amount": 200, "body": "أقدر اليوم"})
    api.post(f"/v1/seller/{token}/messages", json={"offer_amount": 250, "body": "بكرة"})
    view = api.get(f"/v1/seller/{token}")
    assert view.status_code == 200
    body = view.json()
    assert "notes" not in body and "original_text" not in body
    assert "0551234567" not in view.text
    assert (body["need"], body["city"], body["offers_open"]) == ("سباك", "الرياض", True)
    assert [item["seller_id"] for item in body["recipients"]] == ["11"]
    for message in body["messages"]:
        assert set(message) == {"id", "sender_role", "body", "created_at", "media", "offer"}
    for other in ('"12"', '"13"', "خالد", "سعد", "أقدر اليوم"):
        assert other not in view.text
    own = [item for item in body["messages"] if item["sender_role"] == "seller"]
    assert [item["offer"]["amount"] for item in own] == [250]
    assert all(item["offer"] is None for item in body["messages"] if item["sender_role"] == "user")


def test_a_reply_narrows_to_the_quoted_audience_and_never_widens(tmp_path: Path):
    api, _store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers)
    url = f"/v1/requests/{created['id']}/messages"
    quoted = api.post(url, headers=headers, json={"body": "خاص لكما", "seller_ids": ["11", "12"]}).json()
    narrowed = api.post(url, headers=headers, json={"body": "لك أنت", "reply_to": quoted["id"], "seller_ids": ["11"]}).json()
    assert (narrowed["scope"], narrowed["seller_id"]) == ("single_seller", "11")
    assert [item["seller_id"] for item in narrowed["deliveries"]] == ["11"]
    same = api.post(url, headers=headers, json={"body": "لكما", "reply_to": quoted["id"]}).json()
    assert same["scope"] == "some_sellers"
    assert sorted(item["seller_id"] for item in same["deliveries"]) == ["11", "12"]
    assert api.post(url, headers=headers, json={"body": "لسعد", "reply_to": quoted["id"], "seller_ids": ["13"]}).status_code == 422
    assert "لكما" not in api.get(f"/v1/seller/{created['tokens']['13']}").text
    # Replying to one supplier's message still goes to him alone.
    reply = api.post(f"/v1/seller/{created['tokens']['12']}/messages", json={"body": "تمام"}).json()
    back = api.post(url, headers=headers, json={"body": "شكراً", "reply_to": reply["id"]}).json()
    assert (back["scope"], back["seller_id"]) == ("single_seller", "12")


def test_a_revised_offer_replaces_the_earlier_one(tmp_path: Path):
    api, _store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers)
    a, b = created["tokens"]["11"], created["tokens"]["12"]

    def cheapest():
        thread = api.get(f"/v1/requests/{created['id']}", headers=headers).json()
        return thread, [(item["seller_id"], item["total_price"]) for item in thread["offers"] if item["cheapest"]]

    api.post(f"/v1/seller/{a}/messages", json={"offer_amount": 250})
    api.post(f"/v1/seller/{b}/messages", json={"offer_amount": 200})
    assert cheapest()[1] == [("12", 200)]
    api.post(f"/v1/seller/{a}/messages", json={"offer_amount": 180})
    assert cheapest()[1] == [("11", 180)]
    api.post(f"/v1/seller/{a}/messages", json={"offer_amount": 300, "body": "غلطت السعر"})
    thread, winner = cheapest()
    assert winner == [("12", 200)]
    assert sorted((item["seller_id"], item["total_price"]) for item in thread["offers"]) == [("11", 300), ("12", 200)]
    listed = api.get("/v1/requests", headers=headers).json()["requests"][0]
    assert listed["needs"][0]["offer_count"] == 2
    assert listed["needs"][0]["lowest_total"] == 200
    # The history stays in the conversation.
    assert [item["offer"]["amount"] for item in thread["messages"] if item["sender_role"] == "seller" and item["seller_id"] == "11"] == [250, 180, 300]


def test_offer_amounts_names_and_phones_are_checked(tmp_path: Path):
    api, _store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers)
    url = f"/v1/seller/{created['tokens']['11']}/messages"
    for bad in (
        {"offer_amount": -500},
        {"offer_amount": 0},
        {"offer_amount": 1e308},
        {"offer_amount": 10_000_001},
        {"offer_amount": 100, "delivery_included": False, "delivery_price": -100},
        {"offer_amount": 100, "phone": "اتصل علي"},
    ):
        assert api.post(url, json=bad).status_code == 422, bad
    infinite = api.post(url, content='{"offer_amount": Infinity}', headers={"content-type": "application/json"})
    assert infinite.status_code == 422
    good = api.post(url, json={"offer_amount": 100, "provider_name": "فرق - الدعم الرسمي", "phone": "055 123-4567", "delivery_included": False, "delivery_price": 0})
    assert good.status_code == 200, good.text
    offer = api.get(f"/v1/requests/{created['id']}", headers=headers).json()["offers"][0]
    assert (offer["provider_name"], offer["phone"], offer["total_price"]) == ("محمد", "0551234567", 100)
    assert api.post(url, json={"offer_amount": 10_000_000}).status_code == 200


def test_a_request_is_awarded_once(tmp_path: Path):
    api, _store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers)
    url = f"/v1/requests/{created['id']}/award"
    first = api.post(url, headers=headers, json={"seller_id": "11"})
    assert first.status_code == 200 and first.json()["awarded_seller_id"] == "11"
    again = api.post(url, headers=headers, json={"seller_id": "11"})
    assert again.status_code == 200 and again.json()["awarded_seller_id"] == "11"
    other = api.post(url, headers=headers, json={"seller_id": "12"})
    assert other.status_code == 409
    thread = api.get(f"/v1/requests/{created['id']}", headers=headers).json()
    assert thread["awarded_seller_id"] == "11"
    told = [item for item in thread["messages"] if item["body"] == AWARD_TEXT]
    assert [(item["seller_id"], item["scope"]) for item in told] == [("11", "single_seller")]
    assert AWARD_TEXT not in api.get(f"/v1/seller/{created['tokens']['12']}").text
    assert api.post(url, headers=headers, json={"seller_id": "99"}).status_code == 422


def test_offers_close_after_the_award_but_the_winner_can_still_talk(tmp_path: Path):
    api, store = app_with_store(tmp_path)
    headers = signed_in(api)
    created = plumber_request(api, headers)
    a, b = created["tokens"]["11"], created["tokens"]["12"]
    api.post(f"/v1/seller/{a}/messages", json={"offer_amount": 300})
    api.post(f"/v1/requests/{created['id']}/award", headers=headers, json={"seller_id": "11", "notify": False})
    late = api.post(f"/v1/seller/{b}/messages", json={"offer_amount": 100})
    assert late.status_code == 422
    assert "closed" in late.json()["detail"]
    assert api.get(f"/v1/seller/{b}").json()["offers_open"] is False
    assert api.post(f"/v1/seller/{b}/messages", json={"body": "بالتوفيق"}).status_code == 200
    assert api.post(f"/v1/seller/{a}/messages", json={"body": "متى أجيك؟"}).status_code == 200
    assert api.post(f"/v1/seller/{a}/messages", json={"offer_amount": 280}).status_code == 200
    # A price in a Haraj reply from a supplier who lost is kept as a message, not an offer.
    thread = {"request_id": created["id"], "seller_id": "13", "need": "سباك", "haraj_conversation_id": "c13"}
    inbound = store.record_inbound(thread, InboundMessage(haraj_message_id="h1", body="أسويها لك بـ 90 ريال", sent_at="2026-09-23T10:00:00+00:00"))
    assert inbound is not None and inbound.offer is None
    offers = api.get(f"/v1/requests/{created['id']}", headers=headers).json()["offers"]
    assert [(item["seller_id"], item["total_price"], item["cheapest"]) for item in offers] == [("11", 280, True)]


JOURNEYS = [
    "test_a_message_to_some_suppliers_stays_with_them",
    "test_the_seller_view_shows_no_other_suppliers_and_no_buyer_words",
    "test_a_reply_narrows_to_the_quoted_audience_and_never_widens",
    "test_a_revised_offer_replaces_the_earlier_one",
    "test_offer_amounts_names_and_phones_are_checked",
    "test_a_request_is_awarded_once",
    "test_offers_close_after_the_award_but_the_winner_can_still_talk",
]


@pytest.mark.skipif(not os.environ.get("FARQ_TEST_DATABASE_URL"), reason="FARQ_TEST_DATABASE_URL is not set")
@pytest.mark.parametrize("name", JOURNEYS)
def test_seller_journeys_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_seller_privacy as journeys

    monkeypatch.setattr(journeys, "Store", pg_store)
    getattr(journeys, name)(tmp_path)
