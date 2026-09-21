from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store


def client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    corpus = MemoryCorpus.from_json(default_sample_path())
    app = create_app(store, corpus, None, SearchConfig(enable_live=False))
    return TestClient(app)


def test_request_message_and_attachment_round_trip(tmp_path: Path):
    api = client(tmp_path)
    registered = api.post("/v1/auth/register", json={"email": "user@example.com", "password": "secret-pass"})
    assert registered.status_code == 200
    token = registered.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي نجار يسوي دولاب",
            "need": "نجار دولاب",
            "notes": "شمال الرياض",
            "city": "الرياض",
            "attributes": {"item": "دولاب"},
            "recipients": [{"seller_id": "14371810", "seller_name": "نجار تفصيل وصيانه"}],
        },
    )
    assert created.status_code == 200
    request_id = created.json()["id"]
    uploaded = api.post(
        f"/v1/requests/{request_id}/attachments",
        headers=headers,
        files={"file": ("photo.jpg", b"fake-image", "image/jpeg")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["size_bytes"] == len(b"fake-image")
    message = api.post(
        f"/v1/requests/{request_id}/messages",
        headers=headers,
        json={"body": "كم السعر؟", "offer_amount": 1500, "offer_currency": "SAR"},
    )
    assert message.status_code == 200
    fetched = api.get(f"/v1/requests/{request_id}", headers=headers)
    body = fetched.json()
    assert body["attachments"]
    quote = body["messages"][0]
    assert quote["body"].startswith("طلب عرض سعر")
    assert body["messages"][1]["offer"]["amount"] == 1500
    assert "rfq" not in fetched.text.lower()


def test_empty_recipient_is_rejected(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/register", json={"email": "a@example.com", "password": "secret-pass"}).json()["token"]
    response = api.post(
        "/v1/requests",
        headers={"Authorization": f"Bearer {token}"},
        json={"original_text": "test", "recipients": []},
    )
    assert response.status_code == 422


def test_search_trace_is_stored(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/register", json={"email": "b@example.com", "password": "secret-pass"}).json()["token"]
    search = api.post("/v1/search", json={"query": "أبي نجار"})
    assert search.status_code == 200
    assert search.json()["state"] == "CLARIFICATION_REQUIRED"
    trace_id = search.json()["trace_id"]
    trace = api.get(f"/v1/search/{trace_id}/trace", headers={"Authorization": f"Bearer {token}"})
    assert trace.status_code == 200
    assert trace.json()["state"] == "CLARIFICATION_REQUIRED"


def test_guest_request_seller_price_and_activity(tmp_path: Path):
    api = client(tmp_path)
    guest = api.post("/v1/auth/guest")
    assert guest.status_code == 200
    headers = {"Authorization": f"Bearer {guest.json()['token']}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي درابزين ستانلس بالرياض",
            "need": "درابزين ستانلس",
            "notes": "تفصيل وتركيب",
            "city": "الرياض",
            "attributes": {},
            "recipients": [
                {"seller_id": "1", "seller_name": "لمسة معدن", "ad_id": "10"},
                {"seller_id": "2", "seller_name": "ورشة السلم", "ad_id": "11"},
            ],
        },
    )
    assert created.status_code == 200
    request_id = created.json()["id"]
    token = created.json()["reply_token"]
    uploaded = api.post(
        f"/v1/requests/{request_id}/attachments",
        headers=headers,
        files={"file": ("site.jpg", b"image-bytes", "image/jpeg")},
    )
    assert uploaded.status_code == 200
    attachment_id = uploaded.json()["id"]
    downloaded = api.get(f"/v1/requests/{request_id}/attachments/{attachment_id}", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"image-bytes"
    waiting = api.get("/v1/requests", headers=headers).json()["requests"][0]
    assert waiting["replied_count"] == 0
    assert waiting["waiting_count"] == 2
    assert waiting["has_new_offer"] is False
    reply = api.post(
        f"/v1/seller/{token}/messages",
        json={"seller_id": "1", "body": "يشمل التوصيل والتركيب", "offer_amount": 2800, "offer_currency": "SAR"},
    )
    assert reply.status_code == 200
    listed = api.get("/v1/requests", headers=headers).json()["requests"][0]
    assert listed["replied_count"] == 1
    assert listed["waiting_count"] == 1
    assert listed["has_new_offer"] is True
    assert listed["latest_offer_amount"] == 2800
    assert listed["seller_names"]
    assert "يشمل التوصيل" in listed["last_message"]
    assert listed["last_message_at"]
    thread = api.get(f"/v1/requests/{request_id}", headers=headers).json()
    assert thread["messages"][0]["body"].startswith("طلب عرض سعر")
    assert thread["messages"][0]["sender_role"] == "user"
    assert "الرياض" in thread["messages"][0]["body"]
    offer = thread["messages"][1]
    assert offer["offer"]["amount"] == 2800
    assert offer["sender_role"] == "seller"
    seller = api.get(f"/v1/seller/{token}")
    assert seller.status_code == 200
    follow = api.post(
        f"/v1/requests/{request_id}/messages",
        headers=headers,
        json={"body": "هل السعر شامل التركيب؟"},
    )
    assert follow.status_code == 200
    conversation = api.get(f"/v1/seller/{token}").json()["messages"]
    assert conversation[-1]["body"] == "هل السعر شامل التركيب؟"
    assert conversation[-1]["sender_role"] == "user"
    chat = api.post(
        f"/v1/seller/{token}/messages",
        json={"seller_id": "1", "body": "نعم شامل"},
    )
    assert chat.status_code == 200
    updated = api.get(f"/v1/requests/{request_id}", headers=headers).json()
    assert updated["messages"][-1]["body"] == "نعم شامل"
    assert updated["messages"][-1]["sender_role"] == "seller"


def test_quote_requires_a_city(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/guest").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    missing = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "تركيب واجهات",
            "need": "تركيب واجهات",
            "recipients": [{"seller_id": "1", "seller_name": "لمسة معدن"}],
        },
    )
    assert missing.status_code == 422
    cities = api.get("/v1/cities")
    assert cities.status_code == 200
    assert any(item["value"] == "الرياض" for item in cities.json()["cities"])


def test_search_stream_reports_live_before_the_final_result(tmp_path: Path):
    from farq.live_haraj import LiveBatch, QueryFetch, ad_from_item

    ad = ad_from_item(
        {
            "id": 55,
            "title": "تويوتا كامري 2024",
            "postDate": 1750000000,
            "authorUsername": "معرض",
            "authorId": 7,
            "URL": "55/camry/",
            "bodyTEXT": "كامري 2024 مستعملة",
            "city": "الرياض",
            "geoNeighborhood": "العارض",
            "tags": [],
            "thumbURL": "1800x1350_CAMRY.jpg",
            "status": True,
            "price": {"formattedPrice": "118000", "inputPrice": "118000"},
        }
    )

    class Fake:
        def search_iter(self, queries, city):
            yield QueryFetch(ads=[ad], pages=1, has_next=False)

        def search(self, queries, city):
            return LiveBatch(ads=[ad])

    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), Fake(), SearchConfig(enable_live=True))
    api = TestClient(app)
    with api.stream("POST", "/v1/search/stream", json={"query": "كامري 2024 بالرياض"}) as response:
        body = "".join(response.iter_text())
    assert "LIVE_SEARCHING" in body
    assert body.index("LIVE_SEARCHING") < body.index('"type": "done"')
    assert "118000" in body
    assert "thumbcdn.haraj.com.sa" in body
