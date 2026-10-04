import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.haraj_chat import InboundMessage, SentMessage, extract_price
from farq.live_haraj import LiveBatch, QueryFetch
from farq.store import Store
from farq.worker import poll_once


_account = iter(range(1, 1_000_000))


@pytest.fixture(autouse=True)
def _any_recipient(monkeypatch):
    """These journeys pick sellers by id without searching first; test_limits covers the search rule."""
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")


def signed_in(api: TestClient) -> dict:
    """A registered Taseer account; every customer signs in before using the app."""
    email = f"customer-{next(_account)}@example.com"
    response = api.post("/v1/auth/register", json={"email": email, "password": "secret-pass", "name": "عميل تجريبي"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    corpus = MemoryCorpus.from_json(default_sample_path())
    app = create_app(store, corpus, None, SearchConfig(enable_live=False))
    return TestClient(app)


def test_request_message_and_attachment_round_trip(tmp_path: Path):
    api = client(tmp_path)
    registered = api.post("/v1/auth/register", json={"email": "user@example.com", "password": "secret-pass", "name": "عميل"})
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
    # The photo went out as its own message to the supplier, stored in the database.
    photo = next(item for item in body["messages"] if item["media"])
    assert photo["media"][0]["type"] == "image/jpeg" and photo["delivery_state"] == "queued"
    assert api.get(photo["media"][0]["url"]).content == b"fake-image"
    quote = body["messages"][0]
    assert quote["body"].startswith("طلب عرض سعر")
    assert body["messages"][-1]["body"] == "كم السعر؟"
    assert {item["delivery_state"] for item in body["messages"]} == {"queued"}
    assert body["recipients"][0]["send_status"] == "queued"
    assert "rfq" not in fetched.text.lower()


def test_empty_recipient_is_rejected(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/register", json={"email": "a@example.com", "password": "secret-pass", "name": "عميل"}).json()["token"]
    response = api.post(
        "/v1/requests",
        headers={"Authorization": f"Bearer {token}"},
        json={"original_text": "test", "recipients": []},
    )
    assert response.status_code == 422


def test_search_trace_is_stored(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/register", json={"email": "b@example.com", "password": "secret-pass", "name": "عميل"}).json()["token"]
    search = api.post("/v1/search", json={"query": "أبي نجار"}, headers={"Authorization": f"Bearer {token}"})
    assert search.status_code == 200
    assert search.json()["state"] == "CLARIFICATION_REQUIRED"
    trace_id = search.json()["trace_id"]
    trace = api.get(f"/v1/search/{trace_id}/trace", headers={"Authorization": f"Bearer {token}"})
    assert trace.status_code == 200
    assert trace.json()["state"] == "CLARIFICATION_REQUIRED"
    # Only the account that searched can read the trace.
    assert api.get(f"/v1/search/{trace_id}/trace", headers=signed_in(api)).status_code == 404


def test_guest_request_seller_price_and_activity(tmp_path: Path):
    api = client(tmp_path)
    headers = signed_in(api)
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
    downloaded = api.get(uploaded.json()["url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"image-bytes"
    assert api.post(f"/v1/requests/{request_id}/attachments", headers=headers, files={"file": ("x.exe", b"MZ", "application/x-msdownload")}).status_code == 415
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
    offer = next(item for item in thread["messages"] if item["sender_role"] == "seller")
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
    headers = signed_in(api)
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
    headers = signed_in(api)
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
    from farq.live_haraj import LiveBatch, QueryFetch, LiveBatch, QueryFetch, ad_from_item

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
        self.seq = 0

    def send(self, *, conversation_id, seller_id, ad_id, body, attachments=None):
        conversation = conversation_id or f"p2p1_{seller_id}"
        self.seq += 1
        self.sent.append((conversation, seller_id, body))
        self.attachments = getattr(self, "attachments", []) + [(seller_id, item["content_type"], item["data"]) for item in attachments or []]
        return SentMessage(haraj_conversation_id=conversation, haraj_message_id=f"{conversation}:{self.seq}", seq=self.seq)

    def fetch(self, *, conversation_id, seller_id, after_seq):
        return [item for item in self.inbox.get(conversation_id, []) if item.seq > after_seq]


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_item_conversation_routes_through_haraj(tmp_path: Path):
    haraj = FakeHaraj()
    clock = Clock()
    run = lambda: poll_once(store, haraj, budget_seconds=600, sleep=clock.sleep, clock=clock)
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), chat=haraj))
    headers = signed_in(api)
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي سباك وأبي كهربائي بالرياض",
            "need": "سباك",
            "city": "الرياض",
            "recipients": [
                {"seller_id": "11", "seller_name": "محمد", "need": "سباك", "ad_id": "a1"},
                {"seller_id": "12", "seller_name": "خالد", "need": "سباك", "ad_id": "a2"},
                {"seller_id": "21", "seller_name": "أحمد", "need": "كهربائي", "ad_id": "a3"},
            ],
        },
    ).json()
    request_id = created["id"]
    url = f"/v1/requests/{request_id}/messages"
    # The request goes out at once to one seller; the rest wait their 20 seconds.
    assert len(haraj.sent) == 1
    run()
    tokens = {item["seller_id"]: item["reply_token"] for item in created["recipients"]}
    # Every message to a seller ends with the request's reference, which files his replies here.
    tagged = lambda text: f"{text}\nرقم الطلب: {created['ref_code']}"

    def invite(item, seller):
        return tagged(
            "السلام عليكم عزيزي البائع\nلدينا مشتري يطلب توفير:\n"
            f"{item} في الرياض\nإذا كانت متوفرة، افتح الرابط التالي وقدّم عرضك\n"
            f"https://taseer.farq.sa/s/{tokens[seller]}"
        )

    # Farq's fixed invite, each seller with his own quote link; the customer still sees «طلب عرض سعر».
    assert sorted((seller, body) for _conv, seller, body in haraj.sent) == [
        ("11", invite("سباك", "11")),
        ("12", invite("سباك", "12")),
        ("21", invite("كهربائي", "21")),
    ]
    assert len(set(tokens.values())) == 3
    assert {item["send_status"] for item in api.get(f"/v1/requests/{request_id}", headers=headers).json()["recipients"]} == {"sent"}

    haraj.inbox["p2p1_11"] = [InboundMessage("p2p1_11:90", "أقدر بكرة والسعر ٢٥٠ ريال", "2099-01-01T00:00:01+00:00", 90)]
    haraj.inbox["p2p1_12"] = [InboundMessage("p2p1_12:91", "كم نقطة تسريب؟", "2099-01-01T00:00:02+00:00", 91)]
    clock.sleep(31)  # a conversation read a moment ago waits 30 seconds
    assert run() == (0, 2)
    clock.sleep(60)
    assert run() == (0, 0)  # read position moved past them: nothing is recorded twice

    thread = api.get(f"/v1/requests/{request_id}", headers=headers).json()
    question = next(item for item in thread["messages"] if item["body"] == "كم نقطة تسريب؟")
    assert question["direction"] == "seller_to_customer"
    assert question["haraj_conversation_id"] == "p2p1_12"
    offer = thread["offers"][0]
    assert (offer["seller_id"], offer["provider_name"], offer["total_price"]) == ("11", "محمد", 250)

    haraj.sent.clear()
    broadcast = api.post(url, headers=headers, json={"body": "أبي الشغل الخميس", "need": "سباك"}).json()
    reply = api.post(url, headers=headers, json={"body": "نقطتين", "reply_to": question["id"]}).json()
    direct = api.post(url, headers=headers, json={"body": "تقدر الصبح؟", "seller_id": "11"}).json()
    clock.sleep(60)
    run()
    assert (broadcast["scope"], broadcast["seller_id"]) == ("all_sellers", None)
    assert (reply["scope"], reply["seller_id"]) == ("single_seller", "12")
    assert (direct["scope"], direct["seller_id"]) == ("single_seller", "11")
    # Broadcast reaches only this item's sellers; replies and picks reach one seller's Haraj conversation.
    assert haraj.sent == [
        ("p2p1_11", "11", tagged("أبي الشغل الخميس")),
        ("p2p1_12", "12", tagged("أبي الشغل الخميس")),
        ("p2p1_12", "12", tagged("نقطتين")),
        ("p2p1_11", "11", tagged("تقدر الصبح؟")),
    ]
    assert api.post(url, headers=headers, json={"body": "x"}).status_code == 422  # several items: pick one
    assert api.post(url, headers=headers, json={"body": "x", "seller_id": "nobody"}).status_code == 422


def test_not_connected_keeps_messages_queued(tmp_path: Path):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    headers = signed_in(api)
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


def test_cron_route_is_not_swallowed_by_the_web_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    api = client(tmp_path)
    assert api.get("/v1/internal/haraj-sync").status_code == 401
    ran = api.get("/v1/internal/haraj-sync", headers={"Authorization": "Bearer s3cret"})
    assert ran.status_code == 200
    body = ran.json()
    assert (body["sent"], body["received"], body["closing"]) == (0, 0, 0)
    # The same run watches the Haraj backlog, which is empty and not alerting.
    assert body["queue"]["queued"] == 0 and body["queue"]["alert"] is False
    # And whether every Haraj conversation is being read: nothing sent, nothing to alert on.
    assert body["replies"]["never_read_1h"] == 0 and body["replies"]["alert"] is False


def test_unread_replies_and_phone_notifications(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: sent.append(kwargs))
    api = client(tmp_path)
    headers = signed_in(api)
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={"original_text": "سباك", "need": "سباك", "city": "الرياض", "recipients": [{"seller_id": "11", "seller_name": "محمد"}]},
    ).json()
    assert api.get("/v1/push/key").json() == {"public_key": "pub"}
    subscription = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert api.post("/v1/push/subscribe", headers=headers, json=subscription).json() == {"subscribed": True}
    assert api.post("/v1/push/subscribe", headers=headers, json={**subscription, "endpoint": "http://x"}).status_code == 422

    token = created["recipients"][0]["reply_token"]
    api.post(f"/v1/seller/{token}/messages", json={"body": "أقدر بكرة"})
    listed = api.get("/v1/requests", headers=headers).json()["requests"][0]
    assert listed["unread_count"] == 1
    # The supplier's reply reached the customer's phone, titled with his name.
    assert len(sent) == 1
    assert sent[0]["subscription_info"]["endpoint"] == "https://fcm.googleapis.com/fcm/send/abc"
    assert '"title": "محمد"' in sent[0]["data"] and f"/?r={created['id']}" in sent[0]["data"]

    api.get(f"/v1/requests/{created['id']}", headers=headers)  # opening the chat reads it
    assert api.get("/v1/requests", headers=headers).json()["requests"][0]["unread_count"] == 0


def test_every_customer_signs_in_and_sees_only_their_requests(tmp_path: Path):
    api = client(tmp_path)
    assert api.post("/v1/auth/guest").status_code in (404, 405)
    assert api.post("/v1/auth/register", json={"email": "bad", "password": "secret-pass", "name": "سعد"}).status_code == 422
    assert api.post("/v1/auth/register", json={"email": "s@example.com", "password": "short", "name": "سعد"}).status_code == 422
    assert api.post("/v1/auth/register", json={"email": "s@example.com", "password": "secret-pass"}).status_code == 422
    first = api.post("/v1/auth/register", json={"email": "S@Example.com", "password": "secret-pass", "name": "سعد"})
    assert first.status_code == 200 and first.json()["name"] == "سعد"
    # An existing email is not confirmed to strangers; its owner (right password) is just signed in.
    taken = api.post("/v1/auth/register", json={"email": "s@example.com", "password": "other-pass", "name": "سعد"})
    assert taken.status_code == 409 and "already" not in taken.json()["detail"]
    again = api.post("/v1/auth/register", json={"email": "s@example.com", "password": "secret-pass", "name": "سعد"})
    assert again.status_code == 200 and again.json()["token"]
    assert api.post("/v1/auth/login", json={"email": "s@example.com", "password": "wrong-pass"}).status_code == 401
    login = api.post("/v1/auth/login", json={"email": "s@example.com", "password": "secret-pass"}).json()
    saad = {"Authorization": f"Bearer {login['token']}"}
    assert api.get("/v1/auth/me", headers=saad).json() == {"email": "s@example.com", "name": "سعد", "farq_user_id": None}

    other = signed_in(api)
    body = {"original_text": "سباك", "need": "سباك", "city": "الرياض", "recipients": [{"seller_id": "11", "seller_name": "محمد"}]}
    mine = api.post("/v1/requests", headers=saad, json=body).json()
    assert [item["id"] for item in api.get("/v1/requests", headers=saad).json()["requests"]] == [mine["id"]]
    assert api.get("/v1/requests", headers=other).json()["requests"] == []
    assert api.get(f"/v1/requests/{mine['id']}", headers=other).status_code == 404
    assert api.post("/v1/requests", json=body).status_code == 401

    api.post("/v1/auth/logout", headers=saad)
    assert api.get("/v1/requests", headers=saad).status_code == 401


def test_picked_suppliers_only_and_photos_reach_haraj(tmp_path: Path):
    haraj = FakeHaraj()
    clock = Clock()
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), chat=haraj))
    headers = signed_in(api)
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={"original_text": "سباك", "need": "سباك", "city": "الرياض", "recipients": [
            {"seller_id": "11", "seller_name": "محمد"}, {"seller_id": "12", "seller_name": "خالد"}, {"seller_id": "13", "seller_name": "سعد"}]},
    ).json()
    url = f"/v1/requests/{created['id']}/messages"
    run = lambda: poll_once(store, haraj, budget_seconds=600, sleep=clock.sleep, clock=clock)
    run()
    haraj.sent.clear()

    photo = api.post(f"/v1/requests/{created['id']}/files", headers=headers, files={"file": ("p.jpg", b"jpeg", "image/jpeg")}, data={"width": "4", "height": "3"}).json()
    assert photo["type"] == "image/jpeg" and photo["width"] == 4
    two = api.post(url, headers=headers, json={"body": "مثل هذا", "seller_ids": ["11", "13"], "media_ids": [photo["file_id"]]}).json()
    everyone = api.post(url, headers=headers, json={"body": "للكل", "seller_ids": ["11", "12", "13"]}).json()
    assert (two["scope"], two["seller_id"], two["media"][0]["url"]) == ("some_sellers", None, photo["url"])
    assert everyone["scope"] == "all_sellers"
    assert api.post(url, headers=headers, json={"body": "x", "seller_ids": ["99"]}).status_code == 422
    assert api.post(url, headers=headers, json={"body": "", "seller_ids": ["11"]}).status_code == 422
    clock.sleep(60)
    run()
    ref = f"\nرقم الطلب: {created['ref_code']}"
    assert [(seller, body) for _c, seller, body in haraj.sent] == [("11", "مثل هذا" + ref), ("13", "مثل هذا" + ref), ("11", "للكل" + ref), ("12", "للكل" + ref), ("13", "للكل" + ref)]
    assert haraj.attachments == [("11", "image/jpeg", b"jpeg"), ("13", "image/jpeg", b"jpeg")]


def test_replies_follow_the_latest_request_to_that_supplier_and_media_is_kept(tmp_path: Path, monkeypatch):
    import farq.worker as worker

    monkeypatch.setattr(worker, "_download", lambda url: b"haraj-photo-bytes")
    haraj = FakeHaraj()
    clock = Clock()
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), chat=haraj))
    headers = signed_in(api)
    body = lambda need: {"original_text": need, "need": need, "city": "الرياض", "recipients": [{"seller_id": "11", "seller_name": "محمد"}]}
    run = lambda: poll_once(store, haraj, budget_seconds=600, sleep=clock.sleep, clock=clock)
    first = api.post("/v1/requests", headers=headers, json=body("سباك")).json()
    run()
    time.sleep(0.01)
    second = api.post("/v1/requests", headers=headers, json=body("كهربائي")).json()
    clock.sleep(60)
    run()

    def sent_at(request):
        fresh = api.get(f"/v1/requests/{request['id']}", headers=headers).json()
        return fresh["messages"][0]["deliveries"][0]["sent_at"]

    between, after = sent_at(first), sent_at(second)
    assert between < after
    # Haraj keeps one conversation per supplier, shared by both requests.
    haraj.inbox["p2p1_11"] = [
        InboundMessage("p2p1_11:90", "عن السباكة", between, 90),
        InboundMessage("p2p1_11:91", "", after, 91, ({"type": "image/jpeg", "url": "https://harajchat-media.example/a.jpg?X-Amz-Expires=86400"},)),
    ]
    clock.sleep(60)
    assert run() == (0, 2)
    clock.sleep(60)
    assert run() == (0, 0)
    one = api.get(f"/v1/requests/{first['id']}", headers=headers).json()
    two = api.get(f"/v1/requests/{second['id']}", headers=headers).json()
    assert [m["body"] for m in one["messages"] if m["sender_role"] == "seller"] == ["عن السباكة"]
    photo = next(m for m in two["messages"] if m["sender_role"] == "seller")
    assert photo["media"][0]["url"].startswith("/v1/files/")
    assert api.get(photo["media"][0]["url"]).content == b"haraj-photo-bytes"


def test_results_reach_the_customer_while_the_search_is_still_running(tmp_path: Path):
    """Each batch must leave the server as it lands, not all of them once the search ends."""
    import threading

    from farq.live_haraj import QueryFetch, ad_from_item
    from farq.orchestrator import iter_search

    def ad(number):
        return ad_from_item(
            {
                "id": number,
                "title": f"سباك معتمد {number}",
                "postDate": 1780000000,
                "authorUsername": f"مؤسسة {number}",
                "authorId": 100 + number,
                "URL": f"{number}/plumber/",
                "bodyTEXT": "سباك خبرة في الرياض",
                "city": "الرياض",
                "tags": [],
                "status": True,
                "price": {"formattedPrice": "150", "inputPrice": "150"},
            }
        )

    second_page = threading.Event()

    class SlowHaraj:
        def search_iter(self, queries, city):
            yield QueryFetch(ads=[ad(1), ad(2)], pages=1, has_next=True)
            second_page.wait(5)
            yield QueryFetch(ads=[ad(3)], pages=1, has_next=False)

    events = []
    stream = iter_search("أبي سباك بالرياض", MemoryCorpus.from_json(default_sample_path()), SlowHaraj(), SearchConfig(enable_live=True))
    for event in stream:
        events.append(event)
        if event["type"] == "results" and len(event["results"]) >= 2:
            break  # the first batch arrived while the second page is still pending
    assert not second_page.is_set()
    assert [item["type"] for item in events][:2] == ["intent", "status"]
    second_page.set()
    rest = list(stream)
    assert rest[-1]["type"] == "done"
    assert len(rest[-1]["response"].results) >= 3


def test_opening_the_invite_link_creates_a_guest_supplier_and_grants_nothing(tmp_path: Path):
    """He shows up, so he stops being a stranger - but the record hands out no access."""
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    headers = signed_in(api)
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي نجار يسوي دولاب",
            "need": "نجار دولاب",
            "city": "الرياض",
            "recipients": [{"seller_id": "5150", "seller_name": "أبو فهد للنجارة"}],
        },
    )
    assert created.status_code == 200, created.text
    token = created.json()["recipients"][0]["reply_token"]

    assert store.ensure_guest_supplier(None, "بلا معرّف") is None
    assert store.supplier_by_seller_id("5150") is None

    opened = api.get(f"/v1/seller/{token}")
    assert opened.status_code == 200

    guest = store.supplier_by_seller_id("5150")
    assert guest is not None
    assert guest["status"] == "guest"
    assert guest["name"] == "أبو فهد للنجارة"
    # No email and no password, so the sign-in door stays shut for a guest.
    assert store.login_supplier("", "") is None
    # Opening it again is the same supplier, not a second one.
    api.get(f"/v1/seller/{token}")
    assert store.ensure_guest_supplier("5150", "اسم آخر")["id"] == guest["id"]
    # And the reply he sends still works exactly as it did before the record existed.
    replied = api.post(f"/v1/seller/{token}/messages", json={"body": "السعر 300 ريال"})
    assert replied.status_code in (200, 201), replied.text


def test_the_guest_record_does_not_lock_him_out_of_signing_up(tmp_path: Path):
    """The convenience record must not become the reason he cannot own his account."""
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    created = api.post(
        "/v1/requests",
        headers=signed_in(api),
        json={
            "original_text": "أبي حداد",
            "need": "حداد",
            "city": "الرياض",
            "recipients": [{"seller_id": "7788", "seller_name": "ورشة الحداد"}],
        },
    )
    token = created.json()["recipients"][0]["reply_token"]
    api.get(f"/v1/seller/{token}")
    guest = store.supplier_by_seller_id("7788")
    assert guest["status"] == "guest"

    signed_up = api.post(
        "/v1/supplier/register",
        json={
            "name": "ورشة الحداد",
            "email": "hadad@example.com",
            "phone": "0500000000",
            "password": "secret-pass",
            "activity_type": "services",
            "description": "أعمال حدادة وأبواب",
            "categories": [],
            "token": token,
        },
    )
    assert signed_up.status_code == 200, signed_up.text
    # Same row, filled in - not a second supplier for the same Haraj seller.
    upgraded = store.supplier_by_seller_id("7788")
    assert upgraded["id"] == guest["id"]
    assert upgraded["status"] == "active"
    assert store.login_supplier("hadad@example.com", "secret-pass") is not None


def test_a_finished_search_is_served_from_the_store_for_ten_minutes(tmp_path: Path):
    """The same sentence again answers from the cache: no second trip to Haraj, a fresh trace, the sellers remembered."""
    calls = []

    class CountingHaraj:
        def search_iter(self, queries, city):
            calls.append(list(queries))
            yield QueryFetch(ads=[], pages=1, has_next=False)

        def search(self, queries, city):
            calls.append(list(queries))
            return LiveBatch()

    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), CountingHaraj(), SearchConfig(enable_live=True)))
    first = api.post("/v1/search", json={"query": "أبي سباك بالرياض"})
    assert first.status_code == 200
    seen = len(calls)
    assert seen >= 1
    second = api.post("/v1/search", json={"query": "ابي سباك بالرياض"})
    assert second.status_code == 200
    assert len(calls) == seen, "the second search must not reach Haraj"
    assert second.json()["trace_id"] != first.json()["trace_id"]
    # streaming answers from the cache too, ending with «done»
    lines = [json.loads(line) for line in api.post("/v1/search/stream", json={"query": "أبي سباك بالرياض"}).text.splitlines() if line.strip()]
    assert lines[-1]["type"] == "done" and len(calls) == seen
    # warming: a sentence not yet searched is searched in the background, then found ready
    warmed = api.post("/v1/search/warm", json={"query": "أبي نجار بالرياض"})
    assert warmed.status_code == 200 and warmed.json()["warming"] is True
    assert api.post("/v1/search/warm", json={"query": "أبي نجار بالرياض"}).json()["ready"] is True
    after = len(calls)
    api.post("/v1/search", json={"query": "أبي نجار بالرياض"})
    assert len(calls) == after


def test_a_search_that_arrives_during_its_warm_up_follows_it_instead_of_starting_over(tmp_path: Path):
    import threading as _threading

    calls = []
    gate = _threading.Event()

    class GatedHaraj:
        def search_iter(self, queries, city):
            calls.append(list(queries))
            yield QueryFetch(ads=[], pages=1, has_next=True)
            gate.wait(5)
            yield QueryFetch(ads=[], pages=1, has_next=False)

        def search(self, queries, city):
            calls.append(list(queries))
            return LiveBatch()

    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), GatedHaraj(), SearchConfig(enable_live=True)))
    # The warm-up starts in the background and blocks on the gate...
    assert api.post("/v1/search/warm", json={"query": "أبي سباك بالرياض"}).json()["warming"] is True
    out = {}

    def follow():
        out["lines"] = [json.loads(line) for line in api.post("/v1/search/stream", json={"query": "أبي سباك بالرياض"}).text.splitlines() if line.strip()]

    follower = _threading.Thread(target=follow)
    follower.start()
    time.sleep(0.3)
    gate.set()
    follower.join(10)
    assert out["lines"][-1]["type"] == "done"
    # ...and Haraj was asked once for the sentence, not twice.
    assert len(calls) == 1
