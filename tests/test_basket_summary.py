"""What the Farq basket reads from a request without opening its thread.

- priced offers, lowest and highest - only when they are one item in one currency;
- «قارنت» only on a real client signal (POST /compared), refused while there is nothing to compare;
- the open-request count for the basket badge;
- whether the awarded supplier was actually told (award_notice), from the award message's
  own deliveries - never claimed from the award POST alone;
- the supplier-reply notification to the customer: an in-app row plus a delivery record,
  written only while TASEER_REPLY_NOTIFICATIONS is on (default off).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store, offer_summary
from farq.contracts import Offer
from farq.worker import poll_once
from test_api import Clock, FakeHaraj, signed_in


@pytest.fixture(autouse=True)
def _any_recipient(monkeypatch):
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    monkeypatch.delenv("TASEER_REPLY_NOTIFICATIONS", raising=False)
    monkeypatch.delenv("BILLING_S2S_SECRET", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)


def app(tmp_path: Path, chat=None):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), chat=chat))
    return store, api


def request_with(api, headers, sellers, need="مكيف سبليت"):
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": need,
            "need": need,
            "city": "الرياض",
            "recipients": [{"seller_id": sid, "seller_name": name, "need": need} for sid, name in sellers],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    return body["id"], {item["seller_id"]: item["reply_token"] for item in body["recipients"]}


def quote(api, token, amount, currency="SAR"):
    reply = api.post(f"/v1/seller/{token}/messages", json={"body": "السعر", "offer_amount": amount, "offer_currency": currency})
    assert reply.status_code == 200, reply.text


def listed(api, headers, request_id):
    return next(item for item in api.get("/v1/requests", headers=headers).json()["requests"] if item["id"] == request_id)


def test_offer_counts_and_range_in_one_currency(tmp_path):
    _store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "ورشة النخيل"), ("2", "مؤسسة الريان"), ("3", "محل")])
    summary = listed(api, headers, request_id)
    assert summary["priced_offer_count"] == 0
    assert summary["lowest_offer"] is None and summary["highest_offer"] is None
    assert summary["open"] is True and summary["compared_at"] is None and summary["award_notice"] is None

    quote(api, tokens["1"], 1950)
    one = listed(api, headers, request_id)
    assert one["priced_offer_count"] == 1
    # One offer is a price, not a winner.
    assert (one["lowest_offer"], one["highest_offer"], one["lowest_offer_unique"]) == (1950, 1950, False)

    quote(api, tokens["2"], 2600)
    quote(api, tokens["3"], 2100)
    three = listed(api, headers, request_id)
    assert three["priced_offer_count"] == 3
    assert (three["lowest_offer"], three["highest_offer"], three["offer_currency"]) == (1950, 2600, "SAR")
    assert three["lowest_offer_unique"] is True
    assert three["offer_range_withheld"] is None


def test_a_later_offer_from_the_same_supplier_replaces_his_earlier_one(tmp_path):
    _store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ"), ("2", "ب")])
    quote(api, tokens["1"], 500)
    quote(api, tokens["1"], 450)
    summary = listed(api, headers, request_id)
    assert summary["priced_offer_count"] == 1
    assert summary["lowest_offer"] == 450


def test_mixed_currencies_withhold_the_range(tmp_path):
    _store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ"), ("2", "ب")])
    quote(api, tokens["1"], 500, "SAR")
    quote(api, tokens["2"], 120, "USD")
    summary = listed(api, headers, request_id)
    assert summary["priced_offer_count"] == 2
    assert (summary["lowest_offer"], summary["highest_offer"], summary["offer_currency"]) == (None, None, None)
    assert summary["lowest_offer_unique"] is False
    assert summary["offer_range_withheld"] == "mixed_currency"


def test_tied_lowest_is_not_unique_and_several_items_withhold_the_range():
    tie = offer_summary([Offer(total_price=100, currency="SAR", need="x"), Offer(total_price=100, currency="SAR", need="x"), Offer(total_price=150, currency="SAR", need="x")])
    assert (tie["lowest_offer"], tie["lowest_offer_unique"]) == (100, False)
    items = offer_summary([Offer(total_price=100, currency="SAR", need="سرير"), Offer(total_price=900, currency="SAR", need="باب")])
    assert items["offer_range_withheld"] == "multiple_items" and items["lowest_offer"] is None
    unpriced = offer_summary([Offer(total_price=None, need="x")])
    assert unpriced["priced_offer_count"] == 0
    basis = offer_summary([Offer(total_price=100, currency="SAR", need="x", delivery_included=True), Offer(total_price=90, currency="SAR", need="x", delivery_included=False)])
    assert basis["delivery_basis_mixed"] is True


def test_compared_needs_two_priced_offers_and_is_kept_once(tmp_path):
    _store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ"), ("2", "ب")])
    url = f"/v1/requests/{request_id}/compared"
    assert api.post(url, headers=headers).status_code == 409  # nothing to compare yet
    quote(api, tokens["1"], 500)
    assert api.post(url, headers=headers).status_code == 409  # one price is not a comparison
    assert listed(api, headers, request_id)["compared_at"] is None
    quote(api, tokens["2"], 650)
    first = api.post(url, headers=headers)
    assert first.status_code == 200
    stamp = first.json()["compared_at"]
    assert stamp
    assert api.post(url, headers=headers).json()["compared_at"] == stamp  # the first time is kept
    assert listed(api, headers, request_id)["compared_at"] == stamp
    assert api.get(f"/v1/requests/{request_id}", headers=headers).json()["compared_at"] == stamp
    # Another customer cannot mark it, and an unknown id is not found.
    other = signed_in(api)
    assert api.post(url, headers=other).status_code == 404
    assert api.post("/v1/requests/nope/compared", headers=headers).status_code == 404
    assert api.post(url).status_code == 401


def test_open_count_is_requests_not_awarded(tmp_path):
    _store, api = app(tmp_path)
    headers = signed_in(api)
    assert api.get("/v1/requests/open-count", headers=headers).json() == {"open_count": 0}
    first, tokens = request_with(api, headers, [("1", "أ")])
    request_with(api, headers, [("2", "ب")], need="باب خشب")
    assert api.get("/v1/requests/open-count", headers=headers).json() == {"open_count": 2}
    assert api.get("/v1/requests", headers=headers).json()["open_count"] == 2
    quote(api, tokens["1"], 300)
    assert api.post(f"/v1/requests/{first}/award", headers=headers, json={"seller_id": "1", "notify": False}).status_code == 200
    assert api.get("/v1/requests/open-count", headers=headers).json() == {"open_count": 1}
    assert listed(api, headers, first)["open"] is False
    # Another customer's count is his own.
    assert api.get("/v1/requests/open-count", headers=signed_in(api)).json() == {"open_count": 0}
    assert api.get("/v1/requests/open-count").status_code == 401


def test_award_notice_is_sent_only_once_haraj_took_the_message(tmp_path):
    haraj = FakeHaraj()
    clock = Clock()
    store, api = app(tmp_path, chat=haraj)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("11", "ورشة النخيل"), ("12", "مؤسسة الريان")])
    poll_once(store, haraj, budget_seconds=600, sleep=clock.sleep, clock=clock)
    quote(api, tokens["11"], 1950)
    quote(api, tokens["12"], 2600)
    clock.sleep(60)
    haraj.sent.clear()
    awarded = api.post(f"/v1/requests/{request_id}/award", headers=headers, json={"seller_id": "11", "notify": True})
    assert awarded.status_code == 200
    # The award POST queued the notice; it has not been sent, so the UI may not say so.
    notice = awarded.json()["award_notice"]
    assert notice["state"] in ("queued", "sent")
    if notice["state"] == "queued":
        clock.sleep(60)
        poll_once(store, haraj, budget_seconds=600, sleep=clock.sleep, clock=clock)
    assert any(seller == "11" and "تم اختيار عرضك" in body for _conv, seller, body in haraj.sent)
    after = listed(api, headers, request_id)["award_notice"]
    assert after["state"] == "sent" and after["channel"] == "haraj" and after["sent_at"]
    assert api.get(f"/v1/requests/{request_id}", headers=headers).json()["award_notice"]["state"] == "sent"


def test_award_notice_stays_queued_without_a_channel_and_reports_failure(tmp_path):
    store, api = app(tmp_path)  # not connected to Haraj: nothing can be sent
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ"), ("2", "ب")])
    quote(api, tokens["1"], 100)
    awarded = api.post(f"/v1/requests/{request_id}/award", headers=headers, json={"seller_id": "1"}).json()
    assert awarded["award_notice"]["state"] == "queued"
    assert listed(api, headers, request_id)["award_notice"]["state"] == "queued"
    # Every attempt refused for good: failed, never "sent".
    for item in store.claim_deliveries(limit=50):
        store.finish_delivery(item["id"], error="REFUSED_403", retry=False)
    assert listed(api, headers, request_id)["award_notice"] == {"state": "failed", "channel": None, "sent_at": None}


def test_award_without_notify_is_not_requested_and_writes_no_supplier_notice(tmp_path, monkeypatch):
    calls = []
    from farq import notify

    monkeypatch.setattr(notify, "notify_sellers", lambda store, ids, event, **kw: calls.append((tuple(ids), event)) or 0)
    _store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ"), ("2", "ب")])
    quote(api, tokens["1"], 100)
    awarded = api.post(f"/v1/requests/{request_id}/award", headers=headers, json={"seller_id": "1", "notify": False}).json()
    assert awarded["award_notice"]["state"] == "not_requested"
    assert all(event != "awarded" for _ids, event in calls)


def test_legacy_award_is_unknown_not_sent(tmp_path):
    store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ")])
    # An award recorded before award_notify existed.
    store._connection.execute("update requests set awarded_seller_id = '1', awarded_at = '2026-09-01T00:00:00+00:00' where id = ?", (request_id,))
    store._connection.commit()
    assert listed(api, headers, request_id)["award_notice"]["state"] == "unknown"


def test_reply_notification_is_off_by_default(tmp_path):
    store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "أ")])
    quote(api, tokens["1"], 100)
    inbox = api.get("/v1/notifications", headers=headers).json()
    assert inbox == {"enabled": False, "notifications": [], "unread": 0}
    assert store._connection.execute("select count(*) from customer_notifications").fetchone()[0] == 0


def test_reply_notification_is_queued_then_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("TASEER_REPLY_NOTIFICATIONS", "1")
    store, api = app(tmp_path)
    headers = signed_in(api)
    request_id, tokens = request_with(api, headers, [("1", "ورشة النخيل")])
    quote(api, tokens["1"], 100)
    inbox = api.get("/v1/notifications", headers=headers).json()
    assert inbox["enabled"] is True and inbox["unread"] == 1
    [row] = inbox["notifications"]
    assert row["event"] == "supplier_reply" and row["request_id"] == request_id
    assert row["title"] == "ورشة النخيل"
    # No push channel is configured here: recorded honestly as no_channel, never "sent".
    assert row["delivery_state"] == "no_channel"
    assert row["delivered"]["in_app"] is True and row["sent_at"] is None
    # Another customer never sees it; reading it persists per user.
    assert api.get("/v1/notifications", headers=signed_in(api)).json()["notifications"] == []
    assert api.post("/v1/notifications/read", headers=headers, json={}).json() == {"marked": 1}
    assert api.get("/v1/notifications", headers=headers).json()["unread"] == 0


def test_reply_notification_records_a_push_that_a_device_took_and_never_repeats(tmp_path, monkeypatch):
    monkeypatch.setenv("TASEER_REPLY_NOTIFICATIONS", "1")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    import pywebpush

    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: sent.append(kwargs))
    store, api = app(tmp_path)
    headers = signed_in(api)
    api.post("/v1/push/subscribe", headers=headers, json={"endpoint": "https://fcm.googleapis.com/fcm/send/x", "keys": {"p256dh": "k", "auth": "a"}})
    request_id, tokens = request_with(api, headers, [("1", "أ")])
    quote(api, tokens["1"], 100)
    [row] = api.get("/v1/notifications", headers=headers).json()["notifications"]
    assert row["delivery_state"] == "sent" and row["sent_at"] and row["delivered"]["web_push"] == 1
    assert len(sent) == 1
    # The same message notified again (a retried task) is not a second notification.
    from farq import push

    message = next(item for item in store.get_request(request_id, store.request_owner(request_id)).messages if item.sender_role == "seller")
    assert push.notify_reply(store, request_id, "1", message.body, message_id=message.id) == 0
    assert len(api.get("/v1/notifications", headers=headers).json()["notifications"]) == 1


def test_reply_notification_failed_when_every_device_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TASEER_REPLY_NOTIFICATIONS", "1")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    import pywebpush

    def refuse(**_kwargs):
        raise RuntimeError("push service down")

    monkeypatch.setattr(pywebpush, "webpush", refuse)
    _store, api = app(tmp_path)
    headers = signed_in(api)
    api.post("/v1/push/subscribe", headers=headers, json={"endpoint": "https://fcm.googleapis.com/fcm/send/y", "keys": {"p256dh": "k", "auth": "a"}})
    _request_id, tokens = request_with(api, headers, [("1", "أ")])
    quote(api, tokens["1"], 100)
    [row] = api.get("/v1/notifications", headers=headers).json()["notifications"]
    assert row["delivery_state"] == "failed" and row["sent_at"] is None
