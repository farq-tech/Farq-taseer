"""The same journeys against PgStore, on a throwaway Postgres built from supabase/migrations.

Runs only when FARQ_TEST_DATABASE_URL points at a disposable database, e.g.
  docker run -d --name taseer-pg -e POSTGRES_PASSWORD=pg -p 55432:5432 postgres:16-alpine
  FARQ_TEST_DATABASE_URL=postgresql://postgres:pg@127.0.0.1:55432/postgres pytest tests/test_store_pg.py
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
    ],
)
def test_api_journeys_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_api as api_tests

    _run(api_tests, name, tmp_path, monkeypatch, pg_store)


@pytest.mark.parametrize("name", ["test_sends_are_spaced_and_a_refusal_stops_the_batch", "test_an_uncertain_post_is_failed_not_retried"])
def test_worker_on_postgres(name, tmp_path, monkeypatch, pg_store):
    import tests.test_haraj_chat as chat_tests

    _run(chat_tests, name, tmp_path, monkeypatch, pg_store)
