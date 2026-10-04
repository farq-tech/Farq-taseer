"""Seller outreach from Taseer's own Haraj account (owner-approved change set, 2026-10-04).

Production, 2026-09-28 on: invite link opens fell from about half to 1 in 59 and replies to
none. The Haraj account Taseer sent from was shared with Farq Construction, which had just
messaged hundreds of sellers one identical template; Taseer itself wrote one identical
opener to everyone, to generic personal accounts, and to the same seller once per picked ad.
These tests pin the fix, all against simulated Haraj (nothing leaves the machine):
  - every send leaves from the Taseer account; the old account is only read, with its own
    token cache;
  - a day's sends per account are capped and spaced with jitter;
  - only sellers whose own listing matches the item are invited, once each, the best first,
    at most 8 a request;
  - the opener names the seller's listing, varies, names no source, keeps the reference;
  - an unreferenced reply across buyers goes to the latest request he was sent.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.contracts import RequestRecipient
from farq.corpus import MemoryCorpus
from farq.haraj_chat import (
    HarajAccounts,
    HarajChatClient,
    HarajChatUnavailable,
    HarajSession,
    InboundMessage,
    SentMessage,
    chat_from_env,
    conversation_account,
)
from farq.limits import Limits
from farq.live_haraj import LiveBatch, ad_from_item
from farq.outreach import Targeting, generic_account, invite_text, render_invite, select_recipients, short_title
from farq.store import QUOTE_LINK, Store
from farq.worker import dispatch_pending, sync_replies
from tests.test_api import signed_in

NOW = datetime.now(timezone.utc)
APP_URL = "https://ios.haraj.sa/?version=7.8.1&clientId=abc"
OLD, NEW = "13935624", "26038924"


# -- accounts ---------------------------------------------------------------------------


class MemoryCache:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value):
        self.values[key] = value


def both_accounts(**extra) -> dict:
    return {
        "HARAJ_SEND_ENABLED": "1",
        "HARAJ_INBOX_ENABLED": "1",
        "HARAJ_USER_ID": OLD,
        "HARAJ_USERNAME": "shared",
        "HARAJ_PASSWORD": "shared-secret",
        "HARAJ_TASEER_USER_ID": NEW,
        "HARAJ_TASEER_USERNAME": "taseer",
        "HARAJ_TASEER_PASSWORD": "taseer-secret",
        "HARAJ_APP_LOGIN_URL": APP_URL,
        **extra,
    }


def test_taseer_sends_only_from_its_own_account_and_reads_both():
    chat = chat_from_env(both_accounts())
    assert isinstance(chat, HarajAccounts)
    assert chat.send_account_id == NEW and chat.can_send
    assert chat.sender.session.username == "taseer"
    assert set(chat.readers) == {OLD, NEW}
    assert not chat.readers[OLD].send_enabled and chat.readers[OLD].inbox_enabled
    assert chat.readers[OLD].session.username == "shared"


def test_without_the_taseer_account_nothing_is_sent_but_old_replies_are_still_read():
    env = both_accounts()
    for name in ("HARAJ_TASEER_USER_ID", "HARAJ_TASEER_USERNAME", "HARAJ_TASEER_PASSWORD"):
        env.pop(name)
    chat = chat_from_env(env)
    assert isinstance(chat, HarajAccounts) and not chat.can_send and set(chat.readers) == {OLD}
    with pytest.raises(HarajChatUnavailable):
        chat.send(conversation_id=None, seller_id="17035483", ad_id=None, body="x")


def test_each_account_has_its_own_token_cache_and_the_new_one_never_reads_the_old():
    future = str(time.time() + 9 * 86400)
    # The single unprefixed slice the shared account used before the split.
    cache = MemoryCache({"session:access_token": "old-token", "session:access_valid_until": future, "session:refresh_token": "old-refresh"})
    chat = chat_from_env(both_accounts(), cache=cache)
    # The old account carries its live session over; the new one has none and must log in.
    assert chat.readers[OLD].session.access_token() == "old-token"
    assert chat.sender.session._current is None
    assert chat.sender.session._refresh == ""
    chat.sender.session._store("new-token", time.time() + 9 * 86400, "new-refresh")
    assert cache.values[f"session:{NEW}:access_token"] == "new-token"
    assert cache.values["session:access_token"] == "old-token"  # never overwritten
    assert f"session:{OLD}:access_token" not in cache.values
    # A refreshed old-account token lands in its own slice.
    chat.readers[OLD].session._store("old-token-2", time.time() + 9 * 86400)
    assert cache.values[f"session:{OLD}:access_token"] == "old-token-2"
    again = chat_from_env(both_accounts(), cache=cache)
    assert again.readers[OLD].session.access_token() == "old-token-2"
    assert again.sender.session.access_token() == "new-token"


class RecordingClient:
    def __init__(self, user_id):
        self.user_id = user_id
        self.send_enabled = True
        self.inbox_enabled = True
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        return SentMessage(f"p2p{self.user_id}_{kwargs['seller_id']}", f"p2p{self.user_id}_{kwargs['seller_id']}:5", 5, account_id=self.user_id)

    def fetch(self, **kwargs):
        self.calls.append(("fetch", kwargs))
        return []


def test_reads_go_to_the_account_the_conversation_lives_on_and_sends_never_reuse_an_old_conversation():
    old, new = RecordingClient(OLD), RecordingClient(NEW)
    chat = HarajAccounts(new, {OLD: old, NEW: new})
    chat.fetch(conversation_id=f"p2p{OLD}_17035483", seller_id="17035483", after_seq=0)
    chat.fetch(conversation_id=f"p2p17035483_{NEW}", seller_id="17035483", after_seq=0)
    assert [call[0] for call in old.calls] == ["fetch"] and [call[0] for call in new.calls] == ["fetch"]
    with pytest.raises(HarajChatUnavailable):
        chat.fetch(conversation_id="p2p999_17035483", seller_id="17035483", after_seq=0)
    sent = chat.send(conversation_id=f"p2p{OLD}_17035483", seller_id="17035483", ad_id=None, body="x")
    assert new.calls[-1][1]["conversation_id"] is None  # opens its own conversation
    assert sent.account_id == NEW and old.calls == [("fetch", {"conversation_id": f"p2p{OLD}_17035483", "seller_id": "17035483", "after_seq": 0})]
    assert conversation_account(f"p2p{OLD}_17035483", "17035483") == OLD
    assert conversation_account(f"p2p17035483_{NEW}", "17035483") == NEW
    assert conversation_account("p2p1_1", "1") is None


def test_the_real_client_stamps_its_account_on_every_receipt():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://ios.haraj.sa/"):
            return httpx.Response(200, json={"data": {"login": {"status": 200, "accessToken": "t", "refreshToken": "r", "ATvalidUntil": time.time() + 9 * 86400}}})
        if url.endswith(f"/chat/users/{NEW}/topics"):
            return httpx.Response(200, json={"status": 200, "data": {"topic": {"topic_id": f"p2p{NEW}_19676360"}}})
        return httpx.Response(200, json={"status": 200, "data": {"message": {"seq_id": 3}}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    session = HarajSession({"HARAJ_USERNAME": "taseer", "HARAJ_PASSWORD": "p", "HARAJ_APP_LOGIN_URL": APP_URL}, http=http)
    client = HarajChatClient(session, NEW, http=http, socket_factory=lambda token: (type("S", (), {"close": lambda self: None})(), "sid"))
    sent = client.send(conversation_id=None, seller_id="19676360", ad_id=None, body="هلا")
    assert sent == SentMessage(f"p2p{NEW}_19676360", f"p2p{NEW}_19676360:3", 3, account_id=NEW)


# -- pacing ------------------------------------------------------------------------------


class AccountChat:
    """A Taseer account that records sends (what conversation, what text)."""

    def __init__(self, account=NEW):
        self.send_account_id = account
        self.can_send = True
        self.sent = []

    def send(self, *, conversation_id, seller_id, ad_id, body, attachments=None):
        self.sent.append((conversation_id, seller_id, body))
        topic = conversation_id or f"p2p{self.send_account_id}_{seller_id}"
        return SentMessage(topic, f"{topic}:{len(self.sent)}", len(self.sent), account_id=self.send_account_id)

    def fetch(self, **_kwargs):
        return []


def make_request(store: Store, sellers, need="بركسات", titles=None) -> str:
    owner = store.start_guest()["user_id"]
    titles = titles or {}
    return store.create_request(
        owner, need, need, None, "الرياض", {},
        [RequestRecipient(seller_id=seller, seller_name=f"s{seller}", ad_id=f"ad{seller}", ad_title=titles.get(seller)) for seller in sellers],
    )


def test_a_days_sends_per_account_are_capped_and_the_rest_wait_queued(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HARAJ_DAILY_SEND_CAP", "3")
    monkeypatch.setenv("HARAJ_SEND_SPACING_SECONDS", "1")
    monkeypatch.setenv("HARAJ_SEND_JITTER_SECONDS", "0")
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    make_request(store, [str(100 + n) for n in range(5)])
    chat = AccountChat()
    now = [time.time()]

    def sleep(seconds):
        now[0] += seconds

    assert dispatch_pending(store, chat, budget_seconds=600, sleep=sleep, clock=lambda: now[0]) == 3
    statuses = [row["delivery_status"] for row in store._connection.execute("select delivery_status from message_deliveries")]
    assert sorted(statuses) == ["queued", "queued", "sent", "sent", "sent"]
    assert store.haraj_sends_since(NEW, now[0] - 86400) == 3
    # Another account's sends do not count against this one.
    assert store.haraj_sends_since(OLD, now[0] - 86400) == 0
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=sleep, clock=lambda: now[0]) == 0
    # The receipts carry the account, on the delivery and on the thread.
    rows = store._connection.execute("select haraj_account_id from message_deliveries where delivery_status = 'sent'").fetchall()
    assert {row["haraj_account_id"] for row in rows} == {NEW}
    threads = store._connection.execute("select haraj_account_id from haraj_threads where haraj_conversation_id is not null").fetchall()
    assert {row["haraj_account_id"] for row in threads} == {NEW}


def test_sends_are_spaced_by_at_least_the_minimum_with_jitter(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("HARAJ_SEND_SPACING_SECONDS", raising=False)
    monkeypatch.delenv("HARAJ_SEND_JITTER_SECONDS", raising=False)
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    make_request(store, [str(200 + n) for n in range(6)])
    now = [time.time()]
    stamps = []
    chat = AccountChat()
    original = chat.send

    def send(**kwargs):
        stamps.append(now[0])
        return original(**kwargs)

    chat.send = send

    def sleep(seconds):
        now[0] += seconds

    dispatch_pending(store, chat, budget_seconds=3600, sleep=sleep, clock=lambda: now[0])
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert len(stamps) == 6
    # Default: 45 s apart at least, up to 45 s of random jitter on top, never all the same.
    assert all(45 <= gap <= 90 for gap in gaps), gaps
    assert len({round(gap, 3) for gap in gaps}) > 1


def test_a_follow_up_on_an_old_account_thread_opens_a_conversation_on_the_new_one(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HARAJ_SEND_JITTER_SECONDS", "0")
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    request_id = make_request(store, ["17035483"])
    # The invite went out from the old shared account before the split.
    old_topic = f"p2p{OLD}_17035483"
    store._connection.execute(
        "update haraj_threads set haraj_conversation_id = ?, haraj_account_id = ?, high_water = 40", (old_topic, OLD)
    )
    store._connection.execute("update message_deliveries set delivery_status = 'sent', sent_at = ?", (NOW.isoformat(),))
    store._connection.commit()
    owner = store._connection.execute("select owner_user_id from requests where id = ?", (request_id,)).fetchone()[0]
    store.route_customer_message(request_id, owner, "كم آخر سعر؟")
    chat = AccountChat()
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=lambda s: None, clock=time.time) == 1
    conversation, seller, body = chat.sent[0]
    assert conversation is None and seller == "17035483" and body.startswith("كم آخر سعر؟")
    thread = store._connection.execute("select haraj_conversation_id, haraj_account_id, high_water from haraj_threads").fetchone()
    assert (thread["haraj_conversation_id"], thread["haraj_account_id"], thread["high_water"]) == (f"p2p{NEW}_17035483", NEW, 1)


def test_an_account_that_cannot_send_leaves_the_queue_untouched(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    make_request(store, ["300"])
    reading_only = HarajAccounts(None, {OLD: RecordingClient(OLD)})
    assert dispatch_pending(store, reading_only, budget_seconds=600, sleep=lambda s: None, clock=time.time) == 0
    row = store._connection.execute("select delivery_status, attempts from message_deliveries").fetchone()
    assert (row["delivery_status"], row["attempts"]) == ("queued", 0)


# -- the opener ------------------------------------------------------------------------


def test_the_invite_names_the_sellers_listing_and_keeps_the_reference(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HARAJ_SEND_JITTER_SECONDS", "0")
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    request_id = make_request(store, ["401", "402", "403", "404", "405", "406"], titles={str(400 + n): f"بركس غرفة متنقلة مقاس {n}x4" for n in range(1, 7)})
    ref = store._connection.execute("select ref_code from requests where id = ?", (request_id,)).fetchone()[0]
    chat = AccountChat()
    dispatch_pending(store, chat, budget_seconds=3600, sleep=lambda s: None, clock=time.time)
    assert len(chat.sent) == 6
    openers = set()
    for _conversation, seller, body in chat.sent:
        n = int(seller) - 400
        lines = body.split("\n")
        assert f"إعلانك «بركس غرفة متنقلة مقاس {n}x4»" in lines[0]
        assert lines[1].endswith(": بركسات في الرياض")
        assert lines[3].startswith("https://taseer.farq.sa/s/")
        assert lines[-1] == f"رقم الطلب: {ref}"
        assert len(body) < 260
        # No source names and no claims beyond the request itself.
        for word in ("حراج", "تسعير", "Taseer", "فرق", "مجان", "أرخص", "الأفضل"):
            assert word not in body
        openers.add(lines[0].split("،")[0])
    # The wording differs between sellers.
    assert len(openers) > 1 or len({body.split("\n")[2] for _c, _s, body in chat.sent}) > 1


def test_render_invite_is_stable_per_delivery_and_titles_are_cleaned():
    template = invite_text("بركسات", "الرياض", QUOTE_LINK)
    assert render_invite(template, "بركس", seed="d1") == render_invite(template, "بركس", seed="d1")
    assert len({render_invite(template, "بركس", seed=f"d{n}") for n in range(30)}) > 3
    assert "{listing_opener}" not in render_invite(template, None, seed="x")
    assert render_invite("كم آخر سعر؟", "بركس", seed="x") == "كم آخر سعر؟"
    assert short_title("بركس «جديد» للبيع 0555555555 https://x.y/z\nاتصل") == "بركس جديد للبيع اتصل"
    long = short_title("غرف بركسات جاهزة متنقلة جديدة بأفضل الأسعار والتوصيل لجميع مناطق المملكة")
    assert long.endswith("…") and len(long) <= 46
    assert short_title("  ") is None


# -- targeting -------------------------------------------------------------------------


def listing(seller, ad, title, *, city="الرياض", days=1, match="exact", title_match=True, need="بركسات"):
    return {
        "seller_id": seller, "ad_id": ad, "ad_title": title, "ad_city": city,
        "posted_at": (NOW - timedelta(days=days)).isoformat(), "listing_state": "active",
        "match": match, "title_match": title_match, "need": need,
    }


def pick(seller, name, ad=None, need=None):
    return RequestRecipient(seller_id=seller, seller_name=name, ad_id=ad, need=need)


def test_only_sellers_whose_own_listing_matches_are_invited():
    listings = [
        listing("1", "a1", "بركس للبيع"),
        listing("2", "a2", "غرف بركسات", match="near"),
        listing("3", "a3", "مطبخ متنقل", title_match=False),
        listing("4", "a4", "بركس 3x4 مستخدم نظيف", days=60),  # generic account, old listing
        listing("5", "a5", "بركسة للبيع", city="جدة"),  # generic account, other city
        listing("6", "a6", "غرف بركسات جديد", days=2),  # generic account, recent, in city: allowed
    ]
    picked = [
        pick("1", "ابو عبدالرحمن للبركسات", "a1"),
        pick("2", "مؤسسة الغرف", "a2"),
        pick("3", "msobaihi", "a3"),
        pick("4", "anonymous4haraj", "a4"),
        pick("5", "لا إله إلا الله 66", "a5"),
        pick("6", "عضو 57 9284", "a6"),
        pick("7", "seafooo", "a7"),  # no listing recorded for him at all
    ]
    selection = select_recipients(picked, listings, "الرياض", "بركسات", Targeting(max_invites=8, require_listing_match=True), NOW)
    assert [item.seller_id for item in selection.kept] == ["1", "6"]
    assert selection.kept[0].ad_title == "بركس للبيع"
    assert {item["seller_id"]: item["reason"] for item in selection.skipped} == {
        "2": "near_match", "3": "listing_not_matching", "4": "generic_account",
        "5": "generic_account", "7": "no_listing",
    }


def test_one_invite_per_seller_and_at_most_eight_the_best_first():
    listings = [listing("27314", f"a{n}", f"للبيع بركس غرفه جاهزه {n}", days=n) for n in (1, 2, 3)]
    listings += [listing(str(500 + n), f"b{n}", "بركسات", days=n, city="الرياض" if n % 2 else "الخرج") for n in range(12)]
    picked = [pick("27314", "ابو داحم", f"a{n}") for n in (1, 2, 3)] + [pick(str(500 + n), f"مؤسسة {n}", f"b{n}") for n in range(12)]
    selection = select_recipients(picked, listings, "الرياض", "بركسات", Targeting(max_invites=8, require_listing_match=True), NOW)
    kept = [item.seller_id for item in selection.kept]
    assert len(kept) == 8 and kept.count("27314") == 1
    # His freshest matching ad is the one named.
    assert next(item for item in selection.kept if item.seller_id == "27314").ad_id == "a1"
    # In the request's city first: all seven of them, then the freshest from outside.
    in_city = {str(500 + n) for n in range(12) if n % 2} | {"27314"}
    assert in_city <= set(kept) and kept[-1] == "500"
    reasons = [item["reason"] for item in selection.skipped]
    assert reasons.count("duplicate_seller") == 2 and reasons.count("over_invite_cap") == 5


def test_the_cap_is_shared_fairly_across_items():
    listings = [listing(str(n), f"a{n}", "سباك", need="سباك") for n in range(10)]
    listings += [listing(str(100 + n), f"b{n}", "كهربائي", need="كهربائي") for n in range(10)]
    picked = [pick(str(n), f"سباك {n}", f"a{n}", "سباك") for n in range(10)] + [pick(str(100 + n), f"كهربائي {n}", f"b{n}", "كهربائي") for n in range(10)]
    selection = select_recipients(picked, listings, "الرياض", None, Targeting(max_invites=8, require_listing_match=True), NOW)
    needs = [item.need for item in selection.kept]
    assert needs.count("سباك") == 4 and needs.count("كهربائي") == 4


def test_generic_accounts():
    for name in ("عضو 88 32564", "عضو 6989402", "anonymous4haraj", "لا إله إلا الله 66", "باسمك ربي وكلت امري", "12345", ""):
        assert generic_account(name), name
    for name in ("ابو عبدالرحمن للبركسات", "seafooo", "al-shaayie factory", "معلمة تأسيس بريده"):
        assert not generic_account(name), name


# -- the whole journey through the API ----------------------------------------------------


def _ad(ad_id, title, seller_id, seller_name, days=1, city="الرياض"):
    return ad_from_item(
        {
            "id": ad_id, "title": title, "postDate": int((NOW - timedelta(days=days)).timestamp()),
            "authorUsername": seller_name, "authorId": seller_id, "URL": f"{ad_id}/x/",
            "bodyTEXT": "غرف بركسات جاهزة", "city": city, "tags": [], "status": True,
        }
    )


class Live:
    def __init__(self, ads):
        self.ads = ads

    def search(self, queries, city):
        return LiveBatch(ads=list(self.ads), pages_fetched=1, queries_run=1)


def test_a_request_from_a_live_search_keeps_only_matching_sellers_and_says_who_was_left_out(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_REQUIRE_LISTING_MATCH", "1")
    ads = [_ad(1000 + n, f"بركس للبيع {n}", 5000 + n, f"مؤسسة البركسات {n}") for n in range(10)]
    ads += [_ad(2000, "بركس 3x4 مستخدم نظيف", 23474779, "anonymous4haraj", days=60)]
    ads += [_ad(2001, "للبيع بركس حمامات", 27314, "ابو داحم"), _ad(2002, "للبيع بركس غرفه جاهزه", 27314, "ابو داحم", days=3)]
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    chat = AccountChat()
    app = create_app(
        store, MemoryCorpus([]), Live(ads), SearchConfig(enable_live=True), chat=chat,
        limits=Limits(trial_sellers_per_item=20, trial_daily_contacts=100, max_sellers_per_item=20),
    )
    api = TestClient(app)
    headers = signed_in(api)
    found = api.post("/v1/search", headers=headers, json={"query": "بركسات في الرياض"}).json()
    results = found["results"]
    assert len(results) == 13, [item["ad"]["title"] for item in results]
    recipients = [
        {"seller_id": item["seller"]["id"], "seller_name": item["seller"]["name"], "ad_id": item["ad"]["id"], "need": "بركسات"}
        for item in results
    ]
    created = api.post(
        "/v1/requests", headers=headers,
        json={"original_text": "بركسات", "need": "بركسات", "city": "الرياض", "recipients": recipients, "trace_id": found["trace_id"]},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    kept = [item["seller_id"] for item in body["recipients"]]
    assert len(kept) == 8 and len(set(kept)) == 8
    assert "23474779" not in kept
    skipped = {(item["seller_id"], item["reason"]) for item in body["skipped_recipients"]}
    assert ("23474779", "generic_account") in skipped
    assert ("27314", "duplicate_seller") in skipped or "27314" not in kept
    # Each kept seller is invited about his own listing.
    titles = {item["seller_id"]: item["ad_title"] for item in body["recipients"]}
    assert all(title and title.startswith("للبيع بركس" if seller == "27314" else "بركس للبيع") for seller, title in titles.items())
    dispatch_pending(store, chat, budget_seconds=3600, sleep=lambda s: None, clock=time.time)
    for _conversation, seller, text in chat.sent:
        assert f"«{titles[seller]}»" in text.split("\n")[0]
    assert {seller for _c, seller, _t in chat.sent} == set(kept)


def test_a_request_with_no_matching_listing_is_refused_and_nothing_is_queued(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARQ_REQUIRE_LISTING_MATCH", "1")
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus([]), None, SearchConfig(enable_live=False), chat=AccountChat(), limits=Limits(recipients_from_search=False))
    api = TestClient(app)
    headers = signed_in(api)
    response = api.post(
        "/v1/requests", headers=headers,
        json={"original_text": "بركسات", "need": "بركسات", "city": "الرياض", "recipients": [{"seller_id": "77", "seller_name": "عضو 12 345"}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NO_MATCHING_LISTINGS"
    assert store._connection.execute("select count(*) from message_deliveries").fetchone()[0] == 0


# -- replies ----------------------------------------------------------------------------


def test_an_unreferenced_reply_is_filed_under_the_latest_request_sent_to_him(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    first = make_request(store, ["17035483"], need="مكيف")
    second = make_request(store, ["17035483"], need="ثلاجة")
    conversation = f"p2p{NEW}_17035483"
    base = NOW - timedelta(hours=2)
    for index, request_id in enumerate((first, second)):
        sent_at = (base + timedelta(minutes=10 * index)).isoformat()
        store._connection.execute(
            "update message_deliveries set delivery_status = 'sent', sent_at = ?, haraj_account_id = ? where request_id = ?", (sent_at, NEW, request_id)
        )
        store._connection.execute("update haraj_threads set haraj_conversation_id = ?, haraj_account_id = ? where request_id = ?", (conversation, NEW, request_id))
    store._connection.commit()

    class Inbox(AccountChat):
        def fetch(self, *, conversation_id, seller_id, after_seq):
            return [InboundMessage(f"{conversation_id}:9", "هلا", (base + timedelta(hours=1)).isoformat(), 9)]

    assert sync_replies(store, Inbox(), sleep=lambda s: None) == 1
    owners = {row["request_id"]: row["body"] for row in store._connection.execute("select request_id, body from messages where sender_role = 'seller'")}
    assert owners == {second: "هلا"}
    assert store.unmatched_inbound() == []
