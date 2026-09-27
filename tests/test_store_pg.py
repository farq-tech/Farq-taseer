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
        ("test_reply_attribution", "test_without_a_reference_a_reply_is_never_guessed_across_buyers"),
        ("test_reply_attribution", "test_one_open_request_in_the_conversation_keeps_the_old_rule"),
        ("test_reply_attribution", "test_a_reply_written_before_the_second_buyer_asked_goes_to_the_first"),
        ("test_reply_attribution", "test_the_seller_files_his_own_unmatched_reply_from_his_page"),
        ("test_reply_attribution", "test_another_sellers_link_cannot_see_or_file_the_reply"),
        ("test_reply_attribution", "test_a_reply_is_filed_only_into_a_request_it_could_belong_to"),
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


@pytest.mark.parametrize("name", ["test_sends_are_spaced_and_a_refusal_stops_the_batch", "test_an_uncertain_post_is_failed_not_retried", "test_send_slots_are_shared_by_every_instance"])
def test_worker_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_haraj_chat as chat_tests

    _run(chat_tests, name, tmp_path, monkeypatch, pg_store)


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
