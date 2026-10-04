"""2026-10-04: Taseer's own Haraj account, capped and spaced sends, listing-matched targeting,
a personal opener, and unreferenced replies routed to the latest request. Mocks only: nothing
here reaches Haraj."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import time

import pytest

from farq.contracts import RequestRecipient
from farq.haraj_chat import (
    AccountChat,
    HarajChatUnavailable,
    InboundMessage,
    NotConnectedChat,
    SentMessage,
    chat_from_env,
    conversation_account,
    with_reference,
)
from farq.limits import LimitExceeded, Limits, check_new_request, entitlement, qualify_recipients
from farq.outreach import Outreach, invite_seed, listing_matches, need_terms, personal_invite, title_from_url
from farq.store import QUOTE_LINK, Store, choose_thread, invite_text, search_listings
from farq.worker import CANARY_KEY, dispatch_pending, sync_replies

NEW, OLD = "26038924", "13935624"
APP_URL = "https://ios.haraj.sa/?version=7.8.1&clientId=abc"
BOTH = {
    "HARAJ_SEND_ENABLED": "1",
    "HARAJ_INBOX_ENABLED": "1",
    "HARAJ_APP_LOGIN_URL": APP_URL,
    "HARAJ_USER_ID": OLD,
    "HARAJ_USERNAME": "shared",
    "HARAJ_PASSWORD": "old-secret",
    "HARAJ_TASEER_USER_ID": NEW,
    "HARAJ_TASEER_USERNAME": "taseer",
    "HARAJ_TASEER_PASSWORD": "new-secret",
}
NO_JITTER = Outreach(min_spacing=45, jitter=0, daily_invites=60, max_invites_per_request=8)


class Account:
    """One Haraj account as the worker sees it. Records what would have been sent."""

    def __init__(self, user_id: str, send_enabled: bool = True):
        self.user_id = user_id
        self.send_enabled = send_enabled
        self.sent: list[tuple[str | None, str, str]] = []
        self.inbox: dict[str, list[InboundMessage]] = {}
        self.read: list[str] = []

    def send(self, *, conversation_id, seller_id, ad_id, body, attachments=None):
        topic = conversation_id or f"p2p{self.user_id}_{seller_id}"
        self.sent.append((conversation_id, seller_id, body))
        seq = len(self.sent)
        return SentMessage(haraj_conversation_id=topic, haraj_message_id=f"{topic}:{seq}", seq=seq)

    def fetch(self, *, conversation_id, seller_id, after_seq):
        assert self.user_id in conversation_id
        self.read.append(conversation_id)
        return [item for item in self.inbox.get(conversation_id, []) if item.seq > after_seq]


def two_accounts(mode: str = "open"):
    new, old = Account(NEW), Account(OLD, send_enabled=False)
    return AccountChat(new, {NEW: new, OLD: old}, send_mode=mode), new, old


def make_request(store: Store, sellers, need="بركسات", owner=None, titles=None) -> str:
    owner = owner or store.start_guest()["user_id"]
    recipients = [
        RequestRecipient(seller_id=seller, seller_name=f"بائع {seller}", ad_id=f"ad{seller}", listing_title=(titles or {}).get(seller))
        for seller in sellers
    ]
    return store.create_request(owner, need, need, None, "الرياض", {}, recipients)


def rows(store: Store, sql: str, *args):
    return [dict(row) for row in store._connection.execute(sql, args).fetchall()]


class Clock:
    def __init__(self, at: float = 2_000_000_000.0):
        self.now = at
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def store(tmp_path: Path):
    return Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")


# -- 1. The account split ---------------------------------------------------------------------


def test_taseer_sends_only_from_its_own_account_and_reads_both():
    chat = chat_from_env({**BOTH, "HARAJ_TASEER_SEND_ENABLED": "1"})
    assert isinstance(chat, AccountChat)
    assert chat.send_account_id == NEW and chat.send_mode == "open"
    assert set(chat.readers) == {NEW, OLD}
    assert chat.readers[NEW].send_enabled and not chat.readers[OLD].send_enabled
    # The old account's credentials are never the sending session's.
    assert chat.sender.session.username == "taseer" and chat.readers[OLD].session.username == "shared"


def test_the_new_account_is_held_to_a_canary_until_the_owner_opens_it():
    assert chat_from_env(BOTH).send_mode == "canary"
    assert chat_from_env({**BOTH, "HARAJ_TASEER_SEND_ENABLED": "0"}).send_mode == "canary"
    # The master switch still stops everything.
    closed = chat_from_env({**BOTH, "HARAJ_SEND_ENABLED": "0"})
    assert closed.send_mode == "closed"
    assert isinstance(chat_from_env({**BOTH, "HARAJ_SEND_ENABLED": "0", "HARAJ_INBOX_ENABLED": "0"}), NotConnectedChat)


def test_without_the_new_account_nothing_is_sent_at_all():
    legacy_only = {key: value for key, value in BOTH.items() if not key.startswith("HARAJ_TASEER_")}
    chat = chat_from_env(legacy_only)
    assert chat.send_account_id is None and chat.send_mode == "closed"
    with pytest.raises(HarajChatUnavailable):
        chat.send(conversation_id=None, seller_id="5", ad_id=None, body="x")


def test_each_account_keeps_its_own_tokens(store: Store):
    far = str(datetime.now(timezone.utc).timestamp() + 30 * 86400)
    # What the shared account cached before keys carried an account id.
    store.set_value("session:access_token", "old-account-token")
    store.set_value("session:access_valid_until", far)
    chat = chat_from_env(BOTH, cache=store)
    assert chat.readers[OLD].session.access_token() == "old-account-token"
    # The new account never picks up the old one's token: it logs in with its own credentials.
    assert chat.sender.session._current is None
    chat.sender.session._store("new-account-token", float(far), "new-refresh")
    assert store.get_value(f"session:{NEW}:access_token") == "new-account-token"
    assert store.get_value("session:access_token") == "old-account-token"
    reopened = chat_from_env(BOTH, cache=store)
    assert reopened.sender.session.access_token() == "new-account-token"
    assert reopened.readers[OLD].session.access_token() == "old-account-token"


def test_a_thread_on_the_old_account_continues_from_a_new_conversation(store: Store):
    chat, new, _old = two_accounts()
    request_id = make_request(store, ["501"], titles={"501": "بركس 3x4 مستخدم نظيف"})
    old_topic = f"p2p{OLD}_501"
    # The thread already lives on the shared account, read up to seq 1790950549551.
    store._connection.execute(
        "update haraj_threads set haraj_conversation_id = ?, high_water = 1790950549551, checked_at = '2026-10-01T00:00:00+00:00' where request_id = ?",
        (old_topic, request_id),
    )
    store._connection.commit()
    clock = Clock()
    assert dispatch_pending(store, chat, budget_seconds=120, sleep=clock.sleep, clock=clock, outreach=NO_JITTER) == 1
    assert new.sent[0][0] is None  # a fresh topic from the new account, never the shared one
    thread = rows(store, "select * from haraj_threads where request_id = ?", request_id)[0]
    assert thread["haraj_conversation_id"] == f"p2p{NEW}_501" and thread["haraj_account_id"] == NEW
    assert thread["high_water"] == 1 and thread["checked_at"] is None  # read the new conversation from its start
    delivery = rows(store, "select * from message_deliveries where request_id = ?", request_id)[0]
    assert delivery["haraj_account_id"] == NEW and delivery["delivery_status"] == "sent"


def test_replies_are_read_on_whichever_account_holds_the_conversation(store: Store):
    chat, new, old = two_accounts()
    first = make_request(store, ["601"])
    second = make_request(store, ["602"])
    store._connection.execute("update haraj_threads set haraj_conversation_id = ?, high_water = 0 where request_id = ?", (f"p2p{OLD}_601", first))
    store._connection.execute("update haraj_threads set haraj_conversation_id = ?, high_water = 0 where request_id = ?", (f"p2p{NEW}_602", second))
    store._connection.commit()
    old.inbox[f"p2p{OLD}_601"] = [InboundMessage(f"p2p{OLD}_601:5", "متوفر بـ 900 ريال", "2026-10-04T10:00:00+00:00", 5)]
    new.inbox[f"p2p{NEW}_602"] = [InboundMessage(f"p2p{NEW}_602:7", "موجود", "2026-10-04T10:00:00+00:00", 7)]
    clock = Clock()
    assert sync_replies(store, chat, budget_seconds=60, sleep=clock.sleep, clock=clock) == 2
    assert old.read == [f"p2p{OLD}_601"] and new.read == [f"p2p{NEW}_602"]
    assert conversation_account(f"p2p{OLD}_601", [NEW, OLD]) == OLD
    with pytest.raises(HarajChatUnavailable):
        chat.fetch(conversation_id="p2p999_601", seller_id="601", after_seq=0)


# -- 2. Volume: canary, daily cap, spacing --------------------------------------------------


def test_canary_mode_sends_exactly_the_one_approved_delivery(store: Store):
    chat, new, _old = two_accounts(mode="canary")
    make_request(store, ["701", "702", "703"])
    clock = Clock()
    # No approval: nothing goes out.
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=clock.sleep, clock=clock, outreach=NO_JITTER) == 0
    assert new.sent == []
    approved = rows(store, "select id from message_deliveries where seller_id = '702'")[0]["id"]
    store.set_value(CANARY_KEY, approved)
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=clock.sleep, clock=clock, outreach=NO_JITTER) == 1
    assert [seller for _conv, seller, _body in new.sent] == ["702"]
    assert store.get_value(CANARY_KEY) is None  # one approval, one send
    # Nothing more until the owner opens the account or approves another.
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=clock.sleep, clock=clock, outreach=NO_JITTER) == 0
    left = {row["seller_id"]: row["delivery_status"] for row in rows(store, "select seller_id, delivery_status from message_deliveries")}
    assert left == {"701": "queued", "702": "sent", "703": "queued"}


def test_a_spent_daily_allowance_stops_new_sellers_but_not_live_conversations(store: Store):
    chat, new, _old = two_accounts()
    first = make_request(store, ["801", "802"])
    clock = Clock(at=time.time())  # sent_at is stamped with the wall clock
    capped = Outreach(min_spacing=45, jitter=0, daily_invites=2)
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=clock.sleep, clock=clock, outreach=capped) == 2
    assert store.account_invites_since(NEW, (datetime.fromtimestamp(clock.now, tz=timezone.utc) - timedelta(days=1)).isoformat()) == 2
    # A third seller waits for tomorrow; the customer's follow-up to 801 still goes out.
    make_request(store, ["803"])
    store.route_customer_message(first, rows(store, "select owner_user_id from requests where id = ?", first)[0]["owner_user_id"], "متى التوصيل؟", seller_id="801")
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=clock.sleep, clock=clock, outreach=capped) == 1
    assert new.sent[-1][1] == "801" and "متى التوصيل؟" in new.sent[-1][2]
    assert rows(store, "select delivery_status from message_deliveries where seller_id = '803'")[0]["delivery_status"] == "queued"
    clock.sleep(86400 + 1)
    assert dispatch_pending(store, chat, budget_seconds=600, sleep=clock.sleep, clock=clock, outreach=capped) == 1
    assert new.sent[-1][1] == "803"


def test_sends_are_spaced_by_at_least_the_minimum_and_never_regularly(store: Store):
    chat, _new, _old = two_accounts()
    make_request(store, [str(900 + i) for i in range(6)])
    clock = Clock()
    random.seed(7)
    assert dispatch_pending(store, chat, budget_seconds=3600, sleep=clock.sleep, clock=clock, outreach=Outreach(min_spacing=45, jitter=30)) == 6
    gaps = [gap for gap in clock.slept if gap > 0]
    assert len(gaps) == 5 and all(45 <= gap <= 75 for gap in gaps)
    assert len({round(gap, 3) for gap in gaps}) > 1
    assert Outreach().min_spacing >= 45 and Outreach().max_invites_per_request == 8 and Outreach().daily_invites == 60


def test_a_minute_of_cron_sends_at_most_one_message(store: Store):
    chat, new, _old = two_accounts()
    make_request(store, ["950", "951", "952"])
    clock = Clock()
    dispatch_pending(store, chat, budget_seconds=42, sleep=clock.sleep, clock=clock, outreach=Outreach())
    assert len(new.sent) == 1


def test_no_request_reaches_more_than_eight_sellers(store: Store, monkeypatch):
    limits = Limits(require_email_verification="off", recipients_from_search=False, trial_sellers_per_item=20, max_sellers_per_item=20)
    owner = store.start_guest()["user_id"]
    assert entitlement(store, limits, owner).sellers_per_item == 8
    nine = [RequestRecipient(seller_id=str(1000 + i), seller_name="x", need="بركسات") for i in range(9)]
    with pytest.raises(LimitExceeded) as refused:
        check_new_request(store, limits, owner, nine, "بركسات")
    assert refused.value.detail["code"] == "TOO_MANY_SELLERS" and refused.value.detail["limit"] == 8
    check_new_request(store, limits, owner, nine[:8], "بركسات")
    monkeypatch.setenv("FARQ_MAX_INVITES_PER_REQUEST", "5")
    assert entitlement(store, Limits(require_email_verification="off"), owner).sellers_per_item == 5


# -- 3. Targeting -----------------------------------------------------------------------------


def listing(seller, ad, title, match="exact", city="الرياض", posted="2026-10-03T08:00:00+00:00", tags=()):
    return {"seller_id": seller, "ad_id": ad, "title": title, "category_tags": list(tags), "city": city, "posted_at": posted, "match": match}


def test_only_sellers_whose_listing_names_the_item_are_invited(store: Store):
    owner = store.start_guest()["user_id"]
    store.record_search_listings(
        "t1",
        owner,
        [
            listing("11", "a11", "بركس 3x4 مستخدم نظيف"),
            listing("12", "a12", "غرفة نوم مستعملة"),  # his listing is not about the item
            listing("13", "a13", "بركسات غرف", match="near"),  # shown as near, never invited
            listing("14", "a14", "للبيع بركس مطبخ متنقل", city="جدة"),
        ],
    )
    picked = [
        RequestRecipient(seller_id=seller, seller_name="x", ad_id=f"a{seller}", need="بركسات")
        for seller in ("11", "12", "13", "14", "15")  # 15: a profile with no listing at all
    ]
    limits = Limits(require_email_verification="off")
    kept, dropped = qualify_recipients(store, limits, owner, picked, "بركسات", "الرياض", "t1")
    assert [(item.seller_id, item.listing_title) for item in kept] == [("11", "بركس 3x4 مستخدم نظيف"), ("14", "للبيع بركس مطبخ متنقل")]
    assert {(item.seller_id, why) for item, why in dropped} == {("12", "no_matching_listing"), ("13", "no_matching_listing"), ("15", "no_listing")}


def test_the_listing_the_customer_picked_wins_then_the_city_then_the_newest(store: Store):
    owner = store.start_guest()["user_id"]
    store.record_search_listings(
        "t2",
        owner,
        [
            listing("21", "old", "سباك الرياض", posted="2026-09-01T00:00:00+00:00"),
            listing("21", "new", "سباك شمال الرياض", posted="2026-10-03T00:00:00+00:00"),
            listing("21", "far", "سباك جدة", city="جدة", posted="2026-10-04T00:00:00+00:00"),
        ],
    )
    limits = Limits(require_email_verification="off")
    unpicked = [RequestRecipient(seller_id="21", seller_name="x", need="سباك")]
    kept, _ = qualify_recipients(store, limits, owner, unpicked, "سباك", "الرياض", "t2")
    assert (kept[0].ad_id, kept[0].listing_title) == ("new", "سباك شمال الرياض")
    chosen = [RequestRecipient(seller_id="21", seller_name="x", ad_id="old", need="سباك")]
    kept, _ = qualify_recipients(store, limits, owner, chosen, "سباك", "الرياض", "t2")
    assert kept[0].listing_title == "سباك الرياض"


def test_a_registered_supplier_is_not_held_to_a_listing(store: Store):
    owner = store.start_guest()["user_id"]
    store.ensure_guest_supplier("31", "مورد")
    store._connection.execute("update suppliers set status = 'active' where haraj_seller_id = '31'")
    store._connection.commit()
    kept, dropped = qualify_recipients(store, Limits(require_email_verification="off"), owner, [RequestRecipient(seller_id="31", seller_name="x", need="سباك")], "سباك", "الرياض", None)
    assert [item.seller_id for item in kept] == ["31"] and dropped == []


def test_listing_matching_reads_arabic_forms():
    assert listing_matches("بركسات", "بركس 3x4 مستخدم نظيف") == "بركسات"
    assert listing_matches("مدرسة خصوصي", "معلمة دروس خصوصية و تأسيس") == "خصوصي"
    assert listing_matches("سباك", "معلم كهرباء وسباك") == "سباك"
    assert listing_matches("أبواب PVC: ابواب pvc", "باب بي في سي") is None
    assert listing_matches("أبواب PVC: ابواب pvc", "ابواب pvc") is not None
    assert listing_matches("بركسات", "") is None and listing_matches("بركسات", None) is None
    assert "الرياض" not in need_terms("سباك الرياض")
    assert title_from_url("https://haraj.com.sa/11189747805/بركس_3x4_مستخدم_نظيف/") == "بركس 3x4 مستخدم نظيف"


def test_search_results_are_remembered_as_listings():
    from farq.contracts import Ad, ResultUnit, SearchResult, Seller

    seller = Seller(id="haraj:seller:41", name="x")
    ad = Ad(id="a41", title="بركس للبيع", seller=seller, city="الرياض", posted_at="2026-10-03T08:00:00+00:00")
    found = search_listings([
        SearchResult(result_unit=ResultUnit.AD, ad=ad, seller=seller, score=1, match="near"),
        SearchResult(result_unit=ResultUnit.AD, ad=ad, seller=seller, score=1),
        SearchResult(result_unit=ResultUnit.SELLER, seller=Seller(id="42", name="no listing"), score=1),
    ])
    assert found == [{"seller_id": "41", "ad_id": "a41", "title": "بركس للبيع", "category_tags": [], "city": "الرياض",
                      "posted_at": "2026-10-03T08:00:00+00:00", "match": "exact"}]


# -- 4. The opener ----------------------------------------------------------------------------


def test_the_invite_opens_with_the_sellers_own_listing_and_keeps_the_reference(store: Store):
    chat, new, _old = two_accounts()
    request_id = make_request(store, ["51", "52", "53", "54"], titles={s: f"بركس رقم {s} مستخدم" for s in ("51", "52", "53", "54")})
    ref = rows(store, "select ref_code from requests where id = ?", request_id)[0]["ref_code"]
    clock = Clock()
    assert dispatch_pending(store, chat, budget_seconds=900, sleep=clock.sleep, clock=clock, outreach=NO_JITTER) == 4
    bodies = {seller: body for _conv, seller, body in new.sent}
    for seller, body in bodies.items():
        lines = body.split("\n")
        assert f"«بركس رقم {seller} مستخدم»" in lines[0] and ("شفت إعلانك" in lines[0] or "بخصوص إعلانك" in lines[0])
        assert "بركسات في الرياض" in lines
        assert lines[-1] == f"رقم الطلب: {ref}"
        assert lines[-2].startswith("https://taseer.farq.sa/s/")
        assert len(body) < 260
        for word in ("حراج", "Haraj", "haraj.com", "فرق", "عزيزي البائع"):
            assert word not in body.replace("https://taseer.farq.sa", "")
    # Not one text for everyone.
    assert len({"\n".join(body.split("\n")[:2]).split("«")[0] + body.split("\n")[1] + body.split("\n")[3] for body in bodies.values()}) > 1


def test_wording_is_stable_per_seller_and_falls_back_without_a_title():
    template = invite_text("سباك", "الرياض")
    seed = invite_seed("r1", "haraj:seller:77")
    assert seed == invite_seed("r1", "77")
    once = personal_invite(template, title="سباك شمال الرياض تأسيس وتشطيب وصيانة", seed=seed)
    assert once == personal_invite(template, title="سباك شمال الرياض تأسيس وتشطيب وصيانة", seed=seed)
    bare = personal_invite(template, title=None, seed=seed)
    assert "إعلانك" not in bare and bare.split("\n")[0] in ("السلام عليكم", "هلا والله", "حيّاك الله")
    assert QUOTE_LINK in once and "{" not in once.replace(QUOTE_LINK, "")
    # A message queued before this change is sent as it was written.
    assert personal_invite("السلام عليكم عزيزي البائع", title="x", seed=seed) == "السلام عليكم عزيزي البائع"
    assert "{cta}" in personal_invite(template, title="عرض {cta} خاص", seed=seed)  # quoted as written, never expanded
    long_title = "بركسات و كرفانات جديدة من مصنع الشايع بأفضل الأسعار وتوصيل لجميع مناطق المملكة"
    assert "…»" in personal_invite(template, title=long_title, seed=seed)


def test_the_seller_page_shows_the_invite_as_he_received_it(store: Store):
    request_id = make_request(store, ["61"], titles={"61": "بركس للبيع"})
    token = rows(store, "select reply_token from request_recipients where request_id = ?", request_id)[0]["reply_token"]
    view = store.seller_view(token)
    first = view["messages"][0]["body"]
    assert "«بركس للبيع»" in first and "{" not in first


# -- 5. Unreferenced replies ------------------------------------------------------------------


def candidate(request_id, owner, sends, created="2026-09-22T00:00:00+00:00", ref=None, awarded=None):
    return {"request_id": request_id, "seller_id": "17035483", "need": "", "owner_user_id": owner, "ref_code": ref,
            "awarded_seller_id": awarded, "request_created_at": created, "sends": sends}


def test_an_unreferenced_reply_goes_to_the_latest_request_we_wrote_him_about():
    rows_ = [
        candidate("09186b5a", "b0b3", ["2026-09-22T16:40:45+00:00"]),
        candidate("0eaa93fb", "b41a", ["2026-09-22T17:11:13+00:00"]),
        candidate("c349b956", "30d8", ["2026-09-23T13:31:12+00:00"], ref="T-188270"),
    ]
    chosen = choose_thread(rows_, "هلا", "2026-10-02T14:15:49+00:00")
    assert chosen["request_id"] == "c349b956" and chosen["routed_by"] == "latest_sent"
    # Only what he had heard about when he wrote.
    assert choose_thread(rows_, "هلا", "2026-09-22T17:00:00+00:00")["request_id"] == "09186b5a"
    # A quoted reference still decides, and is not marked as a guess.
    quoted = choose_thread(rows_, "بخصوص T-188270 السعر 500", "2026-10-02T14:15:49+00:00")
    assert quoted["request_id"] == "c349b956" and "routed_by" not in quoted
    # A request awarded to someone else is not where his «هلا» goes.
    rows_[2]["awarded_seller_id"] = "999"
    assert choose_thread(rows_, "هلا", "2026-10-02T14:15:49+00:00")["request_id"] == "0eaa93fb"


def test_the_worker_files_and_logs_a_routed_reply(store: Store, caplog):
    chat, new, _old = two_accounts()
    first = make_request(store, ["17035483"], need="طلب تجريبي")
    clock = Clock()
    dispatch_pending(store, chat, budget_seconds=60, sleep=clock.sleep, clock=clock, outreach=NO_JITTER)
    clock.sleep(3600)
    second = make_request(store, ["17035483"], need="سباك يصلح تسريب حمام")
    dispatch_pending(store, chat, budget_seconds=60, sleep=clock.sleep, clock=clock, outreach=NO_JITTER)
    topic = f"p2p{NEW}_17035483"
    reply_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    new.inbox[topic] = [InboundMessage(f"{topic}:99", "هلا", reply_at.isoformat(), 99)]
    with caplog.at_level("WARNING", logger="farq.worker"):
        assert sync_replies(store, chat, budget_seconds=60, sleep=clock.sleep, clock=clock) == 1
    filed = rows(store, "select request_id from messages where haraj_message_id = ?", f"{topic}:99")
    assert filed == [{"request_id": second}]
    assert store.unmatched_inbound() == []
    assert any("filed under the latest request" in record.message and second in record.message for record in caplog.records)
    assert first != second


def test_with_reference_is_kept_on_every_invite():
    body = personal_invite(invite_text("بركسات", "الرياض"), title="بركس", seed="x")
    assert with_reference(body, "T-123456").endswith("رقم الطلب: T-123456")
