"""Haraj transport against a simulated Haraj. No request leaves the machine."""

import json
import time
from pathlib import Path

import httpx
import pytest

from farq.haraj_chat import (
    HarajAccounts,
    HarajChatClient,
    HarajChatUnavailable,
    HarajRefused,
    HarajSendUncertain,
    HarajSession,
    InboundMessage,
    NotConnectedChat,
    SentMessage,
    author_id,
    chat_from_env,
)
from farq.store import Store
from farq.worker import dispatch_pending

APP_URL = "https://ios.haraj.sa/?version=7.8.1&clientId=abc"
ENV = {
    "HARAJ_USERNAME": "taseer",
    "HARAJ_PASSWORD": "secret",
    "HARAJ_APP_LOGIN_URL": APP_URL,
    "HARAJ_APP_USER_AGENT": "Haraj/7.8.1 iOS",
}


class FakeHaraj:
    """Answers the calls Haraj's web client makes, and records them."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.logins = 0
        self.message_status: list[int] = []
        self.drop_message_post = False
        self.omit_seq = False
        self.pages: list[dict] = []
        self.uploaded: list[bytes] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        if url.startswith("https://ios.haraj.sa/") or url.startswith("https://graphql.haraj.com.sa/"):
            self.logins += 1
            return httpx.Response(200, json={"data": {"login": {"status": 200, "accessToken": f"token-{self.logins}", "refreshToken": "refresh", "ATvalidUntil": time.time() + 10 * 86400}}})
        if request.method == "POST" and url.endswith("/chat/users/7/topics"):
            return httpx.Response(200, json={"status": 200, "data": {"topic": {"topic_id": "p2p7_19676360"}}})
        if request.method == "POST" and url.endswith("/messages"):
            if self.drop_message_post:
                raise httpx.ReadTimeout("timed out", request=request)
            status = self.message_status.pop(0) if self.message_status else 200
            if status != 200:
                return httpx.Response(status)
            message = {} if self.omit_seq else {"seq_id": 41}
            return httpx.Response(200, json={"status": 200, "data": {"message": message}})
        if request.method == "POST" and url.startswith("https://api-chat.haraj.com.sa/chat/uploads"):
            return httpx.Response(200, json={"status": 200, "data": {"url": "https://chat-media.haraj.test/u/1.jpg"}})
        if request.method == "PUT" and url.startswith("https://chat-media.haraj.test/"):
            self.uploaded.append(request.content)
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, json=self.pages.pop(0))
        return httpx.Response(404)


class FakeSocket:
    def __init__(self, log):
        self.log = log
        self.closed = False

    def close(self):
        self.closed = True


def make_client(haraj: FakeHaraj, env=ENV, sockets=None):
    http = httpx.Client(transport=httpx.MockTransport(haraj.handler))
    class FixtureSession:
        renewable = True
        def access_token(self):
            if not haraj.logins:
                haraj.logins = 1
            return f"token-{haraj.logins}"
        def invalidate(self):
            haraj.logins += 1
    session = FixtureSession()
    opened = sockets if sockets is not None else []

    def socket_factory(token):
        socket = FakeSocket(token)
        opened.append(socket)
        return socket, f"session-for-{token}"

    return HarajChatClient(session, "7", http=http, socket_factory=socket_factory), opened


def test_seller_address_is_the_author_id_only():
    assert author_id("haraj:seller:19676360") == "19676360"
    assert author_id("19676360") == "19676360"
    assert author_id("0500000000x") is None
    assert author_id("haraj:seller:abc") is None


def test_central_session_is_retired_without_reading_env_or_cache():
    from unittest.mock import Mock
    http, cache = Mock(), Mock()
    session = HarajSession(ENV, http=http, cache=cache)
    assert not session.configured and not session.renewable and not session.can_login
    with pytest.raises(HarajChatUnavailable, match="HARAJ_USER_CONNECTION_REQUIRED"):
        session.access_token()
    assert not http.mock_calls and not cache.mock_calls


def test_send_opens_the_topic_then_posts_with_a_fresh_socket_session():
    haraj = FakeHaraj()
    client, sockets = make_client(haraj)
    sent = client.send(conversation_id=None, seller_id="haraj:seller:19676360", ad_id="1", body="السلام عليكم")
    assert sent == SentMessage("p2p7_19676360", "p2p7_19676360:41", 41, account_id="7")
    topic, message = [call for call in haraj.calls if "/chat/" in str(call.url)]
    assert json.loads(topic.content) == {"type": "p2p", "with_id": 19676360}
    assert topic.headers["authorization"] == "Bearer token-1"
    assert topic.headers["content-type"] == "application/json; charset=utf-8"
    assert str(message.url) == "https://api-chat.haraj.com.sa/chat/users/7/topics/p2p7_19676360/messages"
    assert json.loads(message.content) == {"content": {"type": "text/plain", "payload": {"text": "السلام عليكم"}}, "session_id": "session-for-token-1"}
    assert [socket.closed for socket in sockets] == [True]


def test_a_rejected_token_is_renewed_and_the_message_tried_once_more():
    haraj = FakeHaraj()
    haraj.message_status = [401]
    client, sockets = make_client(haraj)
    sent = client.send(conversation_id="p2p7_19676360", seller_id="19676360", ad_id=None, body="x")
    assert sent.haraj_message_id == "p2p7_19676360:41"
    assert haraj.logins == 2
    haraj.message_status = [403, 403]
    with pytest.raises(HarajRefused) as refused:
        client.send(conversation_id="p2p7_19676360", seller_id="19676360", ad_id=None, body="x")
    assert refused.value.hard_stop


def test_a_post_that_may_have_landed_is_never_replayed():
    haraj = FakeHaraj()
    haraj.drop_message_post = True
    client, _ = make_client(haraj)
    with pytest.raises(HarajSendUncertain) as uncertain:
        client.send(conversation_id="p2p7_19676360", seller_id="19676360", ad_id=None, body="x")
    assert uncertain.value.code == "NETWORK_ERROR"
    assert sum(1 for call in haraj.calls if str(call.url).endswith("/messages")) == 1
    haraj.drop_message_post, haraj.omit_seq = False, True
    with pytest.raises(HarajSendUncertain) as missing:
        client.send(conversation_id="p2p7_19676360", seller_id="19676360", ad_id=None, body="x")
    assert missing.value.code == "MISSING_MESSAGE_ID"


def page(messages, last_key=None, topic="p2p7_19676360", user="7"):
    return {"status": 200, "data": {"user_id": user, "topic_id": topic, "messages": messages, "last_key": last_key}}


def message(seq, sender="19676360", text="نعم متوفر", **extra):
    return {"seq_id": seq, "from_id": sender, "is_deleted": False, "ts": 1790000000000 + seq, "content": {"type": "text/plain", "payload": {"text": text}}, **extra}


def test_fetch_reads_only_the_sellers_new_messages_page_by_page():
    haraj = FakeHaraj()
    haraj.pages = [
        page([message(45, text="٢٥٠ ريال"), message(44, sender="7"), message(43, is_deleted=True)], last_key="k1"),
        page([message(42, text="هلا"), message(41, sender="7"), message(40)], last_key="k2"),
    ]
    client, _ = make_client(haraj)
    found = client.fetch(conversation_id="p2p7_19676360", seller_id="19676360", after_seq=41, sleep=lambda _s: None)
    assert [(item.seq, item.body) for item in found] == [(42, "هلا"), (45, "٢٥٠ ريال")]
    gets = [call for call in haraj.calls if call.method == "GET"]
    assert [call.url.params.get("last_key") for call in gets] == [None, "k1"]
    assert all(call.url.params["limit"] == "80" for call in gets)


def test_fetch_refuses_topics_and_answers_that_are_not_ours():
    haraj = FakeHaraj()
    client, _ = make_client(haraj)
    with pytest.raises(HarajChatUnavailable) as foreign:
        client.fetch(conversation_id="p2p99_19676360", seller_id="19676360", after_seq=0)
    assert foreign.value.code == "AMBIGUOUS"
    haraj.pages = [page([message(50)], user="8")]
    with pytest.raises(HarajChatUnavailable) as mismatch:
        client.fetch(conversation_id="p2p7_19676360", seller_id="19676360", after_seq=0)
    assert mismatch.value.code == "INVALID_RESPONSE"
    haraj.pages = [page([{**message(51), "content": {"type": "image/jpeg"}}])]
    found = client.fetch(conversation_id="p2p7_19676360", seller_id="19676360", after_seq=0)
    assert found[0].body == "رسالة غير نصية"


TASEER_ENV = {
    "HARAJ_TASEER_USERNAME": "taseer-own",
    "HARAJ_TASEER_PASSWORD": "secret2",
    "HARAJ_APP_LOGIN_URL": APP_URL,
    "HARAJ_APP_USER_AGENT": "Haraj/7.8.1 iOS",
}


def test_nothing_is_sent_without_the_server_switches():
    assert isinstance(chat_from_env({}), NotConnectedChat)
    assert isinstance(chat_from_env({**TASEER_ENV, "HARAJ_TASEER_USER_ID": "8"}), NotConnectedChat)
    assert isinstance(chat_from_env({**TASEER_ENV, "HARAJ_SEND_ENABLED": "1"}), NotConnectedChat)  # no account id
    chat = chat_from_env({**TASEER_ENV, "HARAJ_SEND_ENABLED": "1", "HARAJ_TASEER_USER_ID": "8"})
    assert isinstance(chat, NotConnectedChat)
    # The old shared account never sends from Taseer: configured alone, nothing is sent.
    assert isinstance(chat_from_env({**ENV, "HARAJ_SEND_ENABLED": "1", "HARAJ_USER_ID": "7"}), NotConnectedChat)
    assert isinstance(chat_from_env({"HARAJ_TOKEN": "t", "HARAJ_SEND_ENABLED": "1", "HARAJ_FARQ_USER_ID": "7"}), NotConnectedChat)
    # With the inbox on, it is still read.
    reading = chat_from_env({**ENV, "HARAJ_INBOX_ENABLED": "1", "HARAJ_SEND_ENABLED": "1", "HARAJ_USER_ID": "7"})
    assert isinstance(reading, NotConnectedChat)


class ScriptedChat:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.bodies: list[str] = []

    def send(self, *, conversation_id, seller_id, ad_id, body, attachments=None):
        self.bodies.append(body)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def fetch(self, **_kwargs) -> list[InboundMessage]:
        return []


def request_with(store: Store, sellers: int) -> str:
    from farq.contracts import RequestRecipient

    owner = store.start_guest()["user_id"]
    return store.create_request(owner, "سباك", "سباك", None, "الرياض", {}, [RequestRecipient(seller_id=str(100 + i), seller_name=f"s{i}") for i in range(sellers)])


def statuses(store) -> list[tuple[str, str | None]]:
    sql = "select delivery_status, error from message_deliveries order by seller_id"
    if hasattr(store, "_pool"):
        with store._pool.connection() as conn:
            rows = conn.execute(sql).fetchall()
    else:
        rows = store._connection.execute(sql).fetchall()
    return [(row["delivery_status"], row["error"]) for row in rows]


def test_sends_are_spaced_and_a_refusal_stops_the_batch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HARAJ_SEND_SPACING_SECONDS", "20")
    monkeypatch.setenv("HARAJ_SEND_JITTER_SECONDS", "0")
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    request_with(store, 3)
    now = [1000.0]
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    ok = SentMessage("p2p7_100", "p2p7_100:1", 1)
    chat = ScriptedChat([ok, HarajRefused(429)])
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=sleep, clock=lambda: now[0]) == 1
    assert slept == [0, 20]
    assert statuses(store) == [("sent", None), ("queued", "REFUSED_429"), ("queued", None)]
    # Paused for 30 minutes: nothing goes out, not even the untouched third seller.
    assert dispatch_pending(store, ScriptedChat([]), budget_seconds=600, sleep=sleep, clock=lambda: now[0]) == 0
    now[0] += 30 * 60 + 1
    assert dispatch_pending(store, ScriptedChat([ok, ok]), budget_seconds=600, sleep=sleep, clock=lambda: now[0]) == 2


def test_an_uncertain_post_is_failed_not_retried(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    request_with(store, 1)
    chat = ScriptedChat([HarajSendUncertain("NETWORK_ERROR")])
    now = [1000.0]
    dispatch_pending(store, chat, budget_seconds=600, sleep=lambda s: None, clock=lambda: now[0])
    now[0] += 3600
    dispatch_pending(store, ScriptedChat([]), budget_seconds=600, sleep=lambda s: None, clock=lambda: now[0])
    assert statuses(store) == [("failed", "NETWORK_ERROR")]
    assert "https://taseer.farq.sa/s/" in chat.bodies[0]


def test_send_slots_are_shared_by_every_instance(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    # Two cron runs at the same moment get slots 20 s apart, never the same one.
    assert store.reserve_send_slot(20, 1000.0, 1100.0) == 1000.0
    assert store.reserve_send_slot(20, 1000.0, 1100.0) == 1020.0
    assert store.reserve_send_slot(20, 1000.0, 1030.0) is None
    store.release_send_slot(1020.0, 20)
    assert store.reserve_send_slot(20, 1001.0, 1100.0) == 1020.0


def test_a_photo_goes_through_harajs_upload_then_the_caption():
    haraj = FakeHaraj()
    client, sockets = make_client(haraj)
    photo = {"content_type": "image/jpeg", "data": b"jpeg-bytes", "name": "p.jpg", "width": 800, "height": 600}
    sent = client.send(conversation_id="p2p7_19676360", seller_id="19676360", ad_id=None, body="هذا المطلوب", attachments=[photo])
    assert sent.haraj_message_id == "p2p7_19676360:41"
    grant = next(call for call in haraj.calls if str(call.url).endswith("/chat/uploads"))
    assert json.loads(grant.content) == {"mime_type": "image/jpeg", "file_size": len(b"jpeg-bytes")}
    assert haraj.uploaded == [b"jpeg-bytes"]
    posts = [json.loads(call.content)["content"] for call in haraj.calls if str(call.url).endswith("/messages")]
    assert posts == [
        {"type": "image/jpeg", "payload": {"url": "https://chat-media.haraj.test/u/1.jpg", "file_size": 10, "height": 600, "width": 800}},
        {"type": "text/plain", "payload": {"text": "هذا المطلوب"}},
    ]
    assert len(sockets) == 2  # a fresh socket per message


def test_the_seller_can_send_photos_and_pdfs_back():
    haraj = FakeHaraj()
    haraj.pages = [
        page([
            {**message(52), "content": {"type": "application/pdf", "payload": {"url": "https://chat-media.haraj.test/q.pdf", "file_size": 900, "file_name": "عرض.pdf"}}},
            {**message(51), "content": {"type": "image/jpeg", "payload": {"url": "https://chat-media.haraj.test/a.jpg", "file_size": 5, "width": 10, "height": 20}}},
        ])
    ]
    client, _ = make_client(haraj)
    found = client.fetch(conversation_id="p2p7_19676360", seller_id="19676360", after_seq=0)
    assert [(item.body, item.media) for item in found] == [
        ("", ({"type": "image/jpeg", "url": "https://chat-media.haraj.test/a.jpg", "size": 5, "width": 10, "height": 20},)),
        ("", ({"type": "application/pdf", "url": "https://chat-media.haraj.test/q.pdf", "size": 900, "name": "عرض.pdf"},)),
    ]
