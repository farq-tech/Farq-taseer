"""The same journeys against PgStore, on a throwaway Postgres built from supabase/migrations.

Runs only when FARQ_TEST_DATABASE_URL points at a disposable database. Production reaches
Postgres through Supabase's transaction pooler, so test through PgBouncer in transaction mode:
  docker network create taseer-net
  docker run -d --name taseer-pg --network taseer-net -e POSTGRES_PASSWORD=pg postgres:16-alpine
  docker exec taseer-pg psql -U postgres -c "alter role postgres set search_path = taseer, public"
  docker run -d --name taseer-bouncer --network taseer-net -p 56432:5432 \
    -e DATABASE_URL=postgres://postgres:pg@taseer-pg:5432/postgres -e POOL_MODE=transaction \
    -e AUTH_TYPE=scram-sha-256 -e MAX_PREPARED_STATEMENTS=0 \
    -e IGNORE_STARTUP_PARAMETERS=extra_float_digits,options edoburu/pgbouncer
  FARQ_TEST_DATABASE_URL=postgresql://postgres:pg@127.0.0.1:56432/postgres pytest tests/test_store_pg.py
Every table in the taseer schema is emptied between tests.
"""

import os
from pathlib import Path

import pytest

URL = os.environ.get("FARQ_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="FARQ_TEST_DATABASE_URL is not set")

MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "supabase" / "migrations").glob("*.sql"))


@pytest.fixture(scope="module")
def schema():
    import psycopg

    with psycopg.connect(URL, autocommit=True) as conn:
        for role in ("anon", "authenticated"):
            if not conn.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone():
                conn.execute(f"create role {role}")
        conn.execute("drop schema if exists taseer cascade")
        for path in MIGRATIONS:
            conn.execute(path.read_text())
    yield


@pytest.fixture
def pg_store(schema, monkeypatch):
    import psycopg

    from farq.store_pg import PgStore

    with psycopg.connect(URL, autocommit=True) as conn:
        tables = [row[0] for row in conn.execute("select tablename from pg_tables where schemaname = 'taseer' and tablename <> 'subscription_plans'")]
        conn.execute("truncate " + ", ".join(f"taseer.{name}" for name in tables) + " cascade")
    stores = []

    def make(_path=None, upload_dir=None, **_kwargs):
        store = PgStore(URL, Path(upload_dir or "/tmp/farq-pg-uploads"))
        stores.append(store)
        return store

    yield make
    for store in stores:
        store.close()


def _run(module, name, tmp_path, monkeypatch, make):
    monkeypatch.setattr(module, "Store", make)
    getattr(module, name)(tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        "test_request_message_and_attachment_round_trip",
        "test_guest_request_seller_price_and_activity",
        "test_unique_reply_tokens_and_delivery_total",
        "test_item_conversation_routes_through_haraj",
        "test_not_connected_keeps_messages_queued",
        "test_unread_replies_and_phone_notifications",
        "test_every_customer_signs_in_and_sees_only_their_requests",
        "test_picked_suppliers_only_and_photos_reach_haraj",
        "test_replies_follow_the_latest_request_to_that_supplier_and_media_is_kept",
    ],
)
def test_api_journeys_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_api as api_tests

    monkeypatch.setattr(api_tests, "Store", pg_store)
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    getattr(api_tests, name)(tmp_path, monkeypatch) if "monkeypatch" in getattr(api_tests, name).__code__.co_varnames else getattr(api_tests, name)(tmp_path)


@pytest.mark.parametrize(
    "module, name",
    [
        ("test_reply_attribution", "test_every_message_to_a_seller_carries_its_request_reference"),
        ("test_reply_attribution", "test_a_reply_quoting_the_reference_reaches_that_buyer_only"),
        ("test_reply_attribution", "test_two_references_from_two_buyers_are_still_kept_unmatched"),
        ("test_reply_attribution", "test_one_open_request_in_the_conversation_keeps_the_old_rule"),
        ("test_reply_attribution", "test_a_reply_written_before_the_second_buyer_asked_goes_to_the_first"),
        ("test_limits", "test_recipients_must_come_from_the_customers_own_search"),
        ("test_limits", "test_streamed_and_signed_out_searches_count_when_named"),
        ("test_limits", "test_trial_caps_sellers_per_item_and_subscribers_have_a_ceiling"),
        ("test_limits", "test_trial_items_run_out_on_the_server"),
        ("test_limits", "test_daily_request_and_message_ceilings"),
    ],
)
def test_attribution_and_limits_on_postgres(module, name, tmp_path, monkeypatch, pg_store):
    import importlib

    _run(importlib.import_module(f"tests.{module}"), name, tmp_path, monkeypatch, pg_store)


@pytest.mark.parametrize(
    "name",
    [
        "test_offer_condition_comes_only_from_the_form",
        "test_counter_offer_is_one_fixed_message_to_one_supplier",
        "test_deal_outcome_and_rating_follow_the_award",
    ],
)
def test_deal_lifecycle_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_deal_lifecycle as deal_tests

    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    _run(deal_tests, name, tmp_path, monkeypatch, pg_store)


@pytest.mark.parametrize("name", ["test_sends_are_spaced_and_a_refusal_stops_the_batch", "test_an_uncertain_post_is_failed_not_retried", "test_send_slots_are_shared_by_every_instance"])
def test_worker_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_haraj_chat as chat_tests

    _run_any(chat_tests, name, tmp_path, monkeypatch, pg_store)


def _run_any(module, name, tmp_path, monkeypatch, make):
    monkeypatch.setattr(module, "Store", make)
    test = getattr(module, name)
    test(tmp_path, monkeypatch) if "monkeypatch" in test.__code__.co_varnames[: test.__code__.co_argcount] else test(tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        "test_full_happy_path_activates_subscription",
        "test_failed_payment_never_activates",
        "test_wrong_amount_is_rejected_and_marks_payment_failed",
        "test_duplicate_verify_call_does_not_double_activate_or_extend",
        "test_duplicate_webhook_delivery_is_a_no_op",
        "test_webhook_retries_after_transient_moyasar_outage_instead_of_being_dropped",
        "test_refund_before_verification_never_activates",
        "test_refund_after_activation_cancels_the_subscription",
        "test_renewal_while_active_extends_from_current_expiry_not_duplicated",
        "test_checkout_without_configured_publishable_key_fails_clearly",
        "test_3ds_payment_verified_while_initiated_is_activated_by_the_paid_webhook",
        "test_3ds_payment_is_activated_by_the_callback_verify",
        "test_a_failed_attempt_then_a_paid_retry_of_the_same_checkout_activates",
        "test_plans_say_whether_payments_are_available",
        "test_refunding_a_renewal_takes_back_only_that_term",
        "test_a_partial_refund_keeps_the_subscription",
    ],
)
def test_payments_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_subscriptions as subscription_tests

    _run_any(subscription_tests, name, tmp_path, monkeypatch, pg_store)


@pytest.mark.parametrize(
    "name",
    [
        "test_repeated_wrong_passwords_are_throttled",
        "test_an_unknown_email_and_a_wrong_password_look_the_same",
        "test_other_users_cannot_read_a_search_trace",
        "test_push_endpoints_are_known_services_and_stay_with_their_account",
    ],
)
def test_security_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_security as security_tests

    _run_any(security_tests, name, tmp_path, monkeypatch, pg_store)


@pytest.mark.parametrize(
    "name",
    [
        "test_offer_counts_and_range_in_one_currency",
        "test_a_later_offer_from_the_same_supplier_replaces_his_earlier_one",
        "test_mixed_currencies_withhold_the_range",
        "test_compared_needs_two_priced_offers_and_is_kept_once",
        "test_open_count_is_requests_not_awarded",
        "test_award_notice_is_sent_only_once_haraj_took_the_message",
        "test_award_notice_stays_queued_without_a_channel_and_reports_failure",
        "test_reply_notification_is_queued_then_recorded",
        "test_reply_notification_records_a_push_that_a_device_took_and_never_repeats",
    ],
)
def test_basket_summary_on_postgres(name, tmp_path, monkeypatch, pg_store):
    """The basket fields, the «قارنت» signal, the award notice and the reply notifications on PgStore
    (needs supabase/migrations/20260930120000_taseer_basket_offer_summary.sql)."""
    import tests.test_basket_summary as basket_tests

    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    for key in ("TASEER_REPLY_NOTIFICATIONS", "BILLING_S2S_SECRET", "VAPID_PRIVATE_KEY"):
        monkeypatch.delenv(key, raising=False)
    _run_any(basket_tests, name, tmp_path, monkeypatch, pg_store)


def test_sessions_and_idempotency_on_postgres(tmp_path, pg_store, monkeypatch):
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    """Digest-only sessions, legacy raw tokens, expiry and Idempotency-Key replay on PgStore."""
    import psycopg
    from fastapi.testclient import TestClient

    from farq.api import create_app
    from farq.config import SearchConfig
    from farq.corpus import MemoryCorpus, default_sample_path
    from farq.store import token_digest

    store = pg_store(upload_dir=tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    token = api.post("/v1/auth/register", json={"email": "pg@example.com", "password": "secret-pass", "name": "عميل"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    legacy = "legacy-raw-token-issued-before-hashing-0000"
    with psycopg.connect(URL, autocommit=True) as conn:
        assert [row[0] for row in conn.execute("select token from taseer.sessions")] == [token_digest(token)]
        user_id = conn.execute("select id from taseer.users").fetchone()[0]
        conn.execute("insert into taseer.sessions (token, user_id) values (%s, %s)", (legacy, user_id))
    assert api.get("/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200
    assert api.get("/v1/auth/me", headers={"Authorization": f"Bearer {token_digest(token)}"}).status_code == 401
    with psycopg.connect(URL, autocommit=True) as conn:
        assert conn.execute("select count(*) from taseer.sessions where token = %s", (legacy,)).fetchone()[0] == 0

    body = {"original_text": "سباك", "need": "سباك", "city": "الرياض", "recipients": [{"seller_id": "11", "seller_name": "محمد"}]}
    first = api.post("/v1/requests", headers={**headers, "Idempotency-Key": "k1"}, json=body).json()
    again = api.post("/v1/requests", headers={**headers, "Idempotency-Key": "k1"}, json=body).json()
    assert first["id"] == again["id"]
    assert len(api.get("/v1/requests", headers=headers).json()["requests"]) == 1

    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("update taseer.sessions set created_at = now() - interval '31 days'")
    assert api.get("/v1/auth/me", headers=headers).status_code == 401


def test_unreferenced_reply_routing_on_postgres(tmp_path, monkeypatch, pg_store, caplog):
    import tests.test_reply_attribution as attribution

    monkeypatch.setattr(attribution, "Store", pg_store)
    attribution.test_without_a_reference_a_reply_goes_to_the_latest_request_he_was_sent(tmp_path, caplog)


def test_outreach_columns_on_postgres(pg_store):
    """search_listings, request_recipients.ad_title and the per-account send receipts
    (supabase/migrations/20261004120000_taseer_haraj_account_split.sql)."""
    import time
    from datetime import datetime, timedelta, timezone

    from farq.contracts import RequestRecipient
    from farq.haraj_chat import SentMessage

    store = pg_store()
    owner = store.start_guest()["user_id"]
    posted = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    store.record_search_listings("trace-1", owner, [
        {"seller_id": "haraj:seller:501", "ad_id": "9001", "ad_title": "بركس للبيع", "ad_city": "الرياض", "posted_at": posted,
         "listing_state": "active", "match": "exact", "title_match": True, "need": "بركسات"},
        {"seller_id": "502", "ad_id": "9002", "ad_title": "مطبخ", "ad_city": "الرياض", "posted_at": None,
         "listing_state": "active", "match": "near", "title_match": False, "need": "بركسات"},
    ])
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    found = {row["ad_id"]: row for row in store.searched_listings(owner, None, since)}
    assert found["9001"]["seller_id"] == "501" and found["9001"]["title_match"] is True and found["9001"]["ad_title"] == "بركس للبيع"
    assert found["9002"]["match"] == "near" and found["9002"]["title_match"] is False

    store.create_request(owner, "بركسات", "بركسات", None, "الرياض", {}, [RequestRecipient(seller_id="501", seller_name="ابو فهد", ad_id="9001", ad_title="بركس للبيع")])
    claimed = store.claim_deliveries(limit=5)
    assert [item["ad_title"] for item in claimed] == ["بركس للبيع"]
    store.finish_delivery(claimed[0]["id"], sent=SentMessage("p2p26038924_501", "p2p26038924_501:3", 3, account_id="26038924"))
    assert store.haraj_sends_since("26038924", time.time() - 86400) == 1
    assert store.haraj_sends_since("13935624", time.time() - 86400) == 0
    assert store.haraj_sends_since(None, time.time() - 86400) == 1
    with store._pool.connection() as conn:
        thread = conn.execute("select haraj_conversation_id, haraj_account_id, high_water from haraj_threads").fetchone()
    assert (thread["haraj_conversation_id"], thread["haraj_account_id"], thread["high_water"]) == ("p2p26038924_501", "26038924", 3)


def test_direct_claim_is_scoped_to_current_request(pg_store):
    from farq.contracts import RequestRecipient
    store=pg_store()
    owner_a=store.start_guest()['user_id']
    owner_b=store.start_guest()['user_id']
    recipient=[RequestRecipient(seller_id='900000002',seller_name='synthetic')]
    older=store.create_request(owner_b,'older','باب',None,'الرياض',{},recipient,supplier_message='بكم؟')
    current=store.create_request(owner_a,'current','باب',None,'الرياض',{},recipient,supplier_message='بكم؟')
    rows=store.claim_deliveries(request_id=current)
    assert len(rows)==1 and rows[0]['request_id']==current
    assert store.claim_deliveries(request_id=current)==[]
    with store._pool.connection() as conn:
        row=conn.execute('select delivery_status,attempts from message_deliveries where request_id=%s',(older,)).fetchone()
    assert row['delivery_status']=='queued' and row['attempts']==0


def test_sync_account_projection_on_postgres(pg_store, tmp_path):
    from farq.contracts import RequestRecipient
    from farq.haraj_chat import SentMessage
    store = pg_store(upload_dir=tmp_path / 'uploads')
    owner = store.start_guest()['user_id']
    rid = store.create_request(owner, 'synthetic', 'door', None, 'الرياض', {},
                               [RequestRecipient(seller_id='202', seller_name='synthetic')])
    delivery = store.claim_deliveries(request_id=rid)[0]
    store.finish_delivery(delivery['id'], sent=SentMessage('p2p101_202','p2p101_202:7',7,'101'))
    rows = store.threads_to_sync()
    assert len(rows) == 1 and rows[0]['haraj_account_id'] == '101'


def test_checked_thread_does_not_clear_legacy_failures(pg_store, tmp_path):
    from farq.contracts import RequestRecipient
    from farq.haraj_chat import SentMessage
    store = pg_store(upload_dir=tmp_path / 'uploads')
    owner = store.start_guest()['user_id']
    ids = [store.create_request(owner, 'synthetic', 'door', None, 'الرياض',
            {'_haraj_consent':{'account':'101'}} if i else {},
            [RequestRecipient(seller_id='202',seller_name='synthetic')]) for i in range(2)]
    for rid in ids:
        delivery=store.claim_deliveries(request_id=rid)[0]
        store.finish_delivery(delivery['id'],sent=SentMessage('p2p101_202','p2p101_202:7',7,'101'))
    rows = {row['request_id']:row for row in store.threads_to_sync()}
    assert rows[ids[1]]['consent_attributes']['_haraj_consent']['account']=='101'
    store.thread_checked(rows[ids[0]],failure_code='HARAJ_CONSENT_REQUIRED')
    store.thread_checked(rows[ids[1]],high_water=8)
    with store._pool.connection() as conn:
        statuses={r['request_id']:r for r in conn.execute('select request_id,failure_code,high_water from haraj_threads')}
    assert statuses[ids[0]]['failure_code']=='HARAJ_CONSENT_REQUIRED'
    assert statuses[ids[1]]['high_water']==8


@pytest.mark.parametrize("name", [
    "test_pages_continue_without_restarting_or_losing_trace",
    "test_continuation_cannot_change_query_or_skip_pages",
    "test_user_cannot_continue_other_users_search",
    "test_stream_continuation_uses_same_page_contract",
    "test_paged_search_keeps_older_ads_and_distinct_ads_by_same_seller",
    "test_partial_failure_retries_current_window_without_skipping",
])
def test_search_pagination_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_search_pagination as pagination
    monkeypatch.setattr(pagination, "Store", pg_store)
    getattr(pagination, name)(tmp_path, monkeypatch)

@pytest.mark.parametrize('name', [
    'test_background_resumes_without_browser_and_load_more_uses_saved_details',
    'test_failed_page_keeps_cursor_details_and_last_success',
    'test_lease_fencing_and_expiry',
    'test_rate_limit_and_challenge_preserve_data_and_pause',
])
def test_haraj_harvest_pg(name, tmp_path, monkeypatch, pg_store):
    import test_haraj_harvest as module
    monkeypatch.setattr(module, 'Store', pg_store)
    fn = getattr(module, name)
    if 'monkeypatch' in __import__('inspect').signature(fn).parameters:
        fn(tmp_path, monkeypatch)
    else:
        fn(tmp_path)
