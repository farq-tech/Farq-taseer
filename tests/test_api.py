from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.haraj_chat import InboundMessage, SentMessage, extract_price
from farq.store import Store
from farq.worker import poll_once


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
        json={"body": "كم السعر؟"},
    )
    assert message.status_code == 200
    fetched = api.get(f"/v1/requests/{request_id}", headers=headers)
    body = fetched.json()
    assert body["attachments"]
    quote = body["messages"][0]
    assert quote["body"].startswith("طلب عرض سعر")
    assert body["messages"][1]["body"] == "كم السعر؟"
    assert {item["delivery_state"] for item in body["messages"]} == {"queued"}
    assert body["recipients"][0]["send_status"] == "queued"
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


def test_unique_reply_tokens_and_delivery_total(tmp_path: Path):
    api = client(tmp_path)
    headers = {"Authorization": f"Bearer {api.post('/v1/auth/guest').json()['token']}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي سباك وأبي كهربائي بالرياض",
            "need": "سباك",
            "city": "الرياض",
            "recipients": [
                {"seller_id": "p1", "seller_name": "محمد", "need": "سباك"},
                {"seller_id": "e1", "seller_name": "أحمد", "need": "كهربائي"},
            ],
        },
    )
    assert created.status_code == 200
    body = created.json()
    tokens = [item["reply_token"] for item in body["recipients"]]
    assert len(tokens) == 2
    assert tokens[0] != tokens[1]
    reply = api.post(
        f"/v1/seller/{tokens[0]}/messages",
        json={
            "provider_name": "محمد",
            "phone": "0500000000",
            "offer_amount": 250,
            "delivery_included": False,
            "delivery_price": 50,
            "body": "أقدر أجيك بكرة",
        },
    )
    assert reply.status_code == 200
    thread = api.get(f"/v1/requests/{body['id']}", headers=headers).json()
    offer = thread["offers"][0]
    assert offer["base_price"] == 250
    assert offer["delivery_price"] == 50
    assert offer["total_price"] == 300
    assert offer["need"] == "سباك"
    assert offer["cheapest"] is True
    listed = api.get("/v1/requests", headers=headers).json()["requests"][0]
    assert listed["needs"][0]["offer_count"] == 1
    assert listed["needs"][0]["lowest_total"] == 300


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


class FakeHaraj:
    """Records what would go out to Haraj and serves seller replies back."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.inbox: dict[str, list[InboundMessage]] = {}

    def send(self, *, conversation_id, seller_id, ad_id, body):
        conversation = conversation_id or f"conv-{seller_id}-{ad_id}"
        self.sent.append((conversation, seller_id, body))
        return SentMessage(haraj_conversation_id=conversation, haraj_message_id=f"out-{len(self.sent)}")

    def fetch(self, *, conversation_id, since):
        return [item for item in self.inbox.get(conversation_id, []) if since is None or item.sent_at > since]


def test_item_conversation_routes_through_haraj(tmp_path: Path):
    haraj = FakeHaraj()
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), chat=haraj))
    headers = {"Authorization": f"Bearer {api.post('/v1/auth/guest').json()['token']}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي سباك وأبي كهربائي بالرياض",
            "need": "سباك",
            "city": "الرياض",
            "recipients": [
                {"seller_id": "p1", "seller_name": "محمد", "need": "سباك", "ad_id": "a1"},
                {"seller_id": "p2", "seller_name": "خالد", "need": "سباك", "ad_id": "a2"},
                {"seller_id": "e1", "seller_name": "أحمد", "need": "كهربائي", "ad_id": "a3"},
            ],
        },
    ).json()
    request_id = created["id"]
    url = f"/v1/requests/{request_id}/messages"
    # Opening message: one per item, into each seller's own Haraj conversation.
    assert sorted((seller, body) for _conv, seller, body in haraj.sent) == [
        ("e1", "طلب عرض سعر\nكهربائي\nالمدينة: الرياض"),
        ("p1", "طلب عرض سعر\nسباك\nالمدينة: الرياض"),
        ("p2", "طلب عرض سعر\nسباك\nالمدينة: الرياض"),
    ]
    assert {item["send_status"] for item in api.get(f"/v1/requests/{request_id}", headers=headers).json()["recipients"]} == {"sent"}

    haraj.inbox["conv-p1-a1"] = [InboundMessage("in-1", "أقدر بكرة والسعر ٢٥٠ ريال", "2099-01-01T00:00:01+00:00")]
    haraj.inbox["conv-p2-a2"] = [InboundMessage("in-2", "كم نقطة تسريب؟", "2099-01-01T00:00:02+00:00")]
    assert poll_once(store, haraj) == (0, 2)
    assert poll_once(store, haraj) == (0, 0)  # already recorded, not duplicated

    thread = api.get(f"/v1/requests/{request_id}", headers=headers).json()
    question = next(item for item in thread["messages"] if item["body"] == "كم نقطة تسريب؟")
    assert question["direction"] == "seller_to_customer"
    assert question["haraj_conversation_id"] == "conv-p2-a2"
    offer = thread["offers"][0]
    assert (offer["seller_id"], offer["provider_name"], offer["total_price"]) == ("p1", "محمد", 250)

    haraj.sent.clear()
    broadcast = api.post(url, headers=headers, json={"body": "أبي الشغل الخميس", "need": "سباك"}).json()
    reply = api.post(url, headers=headers, json={"body": "نقطتين", "reply_to": question["id"]}).json()
    direct = api.post(url, headers=headers, json={"body": "تقدر الصبح؟", "seller_id": "p1"}).json()
    assert (broadcast["scope"], broadcast["seller_id"]) == ("all_sellers", None)
    assert (reply["scope"], reply["seller_id"]) == ("single_seller", "p2")
    assert (direct["scope"], direct["seller_id"]) == ("single_seller", "p1")
    # Broadcast reaches only this item's sellers; replies and picks reach one seller's Haraj conversation.
    assert haraj.sent == [
        ("conv-p1-a1", "p1", "أبي الشغل الخميس"),
        ("conv-p2-a2", "p2", "أبي الشغل الخميس"),
        ("conv-p2-a2", "p2", "نقطتين"),
        ("conv-p1-a1", "p1", "تقدر الصبح؟"),
    ]
    assert api.post(url, headers=headers, json={"body": "x"}).status_code == 422  # several items: pick one
    assert api.post(url, headers=headers, json={"body": "x", "seller_id": "nobody"}).status_code == 422


def test_not_connected_keeps_messages_queued(tmp_path: Path):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    headers = {"Authorization": f"Bearer {api.post('/v1/auth/guest').json()['token']}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={"original_text": "أبي نجار", "need": "نجار", "city": "الرياض", "recipients": [{"seller_id": "n1", "seller_name": "نجار"}]},
    ).json()
    assert poll_once(store) == (0, 0)
    thread = api.get(f"/v1/requests/{created['id']}", headers=headers).json()
    assert thread["messages"][0]["delivery_state"] == "queued"
    assert thread["last_synced_at"] is None
    listed = api.get("/v1/requests", headers=headers).json()["requests"][0]
    assert (listed["sent_count"], listed["queued_count"]) == (0, 1)


def test_price_needs_a_currency():
    assert extract_price("السعر ١٬٢٠٠ ريال شامل") == 1200
    assert extract_price("500 ر.س") == 500
    assert extract_price("عندي 3 حبات") is None
