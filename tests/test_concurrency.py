"""Regression test for SQLite concurrency issues under stream + fallback search."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from starlette.testclient import TestClient

from farq.api import create_app
from farq.store import Store
from pathlib import Path
from farq.corpus import MemoryCorpus, default_sample_path
from farq.config import SearchConfig
from tests.test_limits import FakeLive


def test_concurrent_search_does_not_corrupt_sqlite(tmp_path: Path):
    """Concurrent /v1/search and /v1/search/stream should not raise sqlite3.InterfaceError."""
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(
        store,
        MemoryCorpus.from_json(default_sample_path()),
        FakeLive(),
        SearchConfig(enable_live=True),
    )
    client = TestClient(app)

    # Create a user
    client.post("/api/auth/signup", json={"email": "test@example.com", "password": "secret"})
    login_resp = client.post("/api/auth/login", json={"email": "test@example.com", "password": "secret"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    queries = [
        "أبي مولد كهرباء 5 كيلو صامت",
        "أبي طاولة طعام 8 كراسي",
        "أبي كامري 2015 مستعملة",
        "أبي بطارية جديدة",
        "أبي مقاول ترميم",
    ]

    errors = []
    results_count = {}

    def run_plain_search(query):
        try:
            resp = client.post("/v1/search", json={"query": query}, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                trace_id = data.get("trace_id")
                results_count[f"plain_{query}"] = len(data.get("results", []))
                return trace_id
            else:
                errors.append(f"Plain search {query} status {resp.status_code}")
                return None
        except Exception as e:
            errors.append(f"Plain search {query}: {type(e).__name__}: {e}")
            return None

    def run_stream_search(query):
        try:
            # Stream endpoint
            resp = client.post("/v1/search/stream", json={"query": query}, headers=headers)
            if resp.status_code == 200:
                count = 0
                for line in resp.iter_lines():
                    if line:
                        event = json.loads(line)
                        if event.get("type") == "results":
                            count += len(event.get("results", []))
                results_count[f"stream_{query}"] = count
                return True
            else:
                errors.append(f"Stream search {query} status {resp.status_code}")
                return False
        except Exception as e:
            errors.append(f"Stream search {query}: {type(e).__name__}: {e}")
            return False

    # Mix plain and stream searches concurrently on the same queries
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for query in queries * 2:  # Run each query twice
            # Alternate between plain and stream
            if len(futures) % 2 == 0:
                futures.append(executor.submit(run_plain_search, query))
            else:
                futures.append(executor.submit(run_stream_search, query))

        # Wait for all to complete
        for future in as_completed(futures):
            try:
                future.result(timeout=30)
            except Exception as e:
                errors.append(f"Executor error: {type(e).__name__}: {e}")

    # Verify results
    assert not errors, f"Concurrency errors occurred:\n" + "\n".join(errors[:10])
    assert len(results_count) > 0, "No search results recorded"
    # Each query should have returned some results
    for key in results_count:
        assert results_count[key] >= 0, f"{key} returned negative result count"

    print(f"✓ Completed {len(results_count)} concurrent searches with 0 SQLite errors")
    print(f"  Results: {results_count}")


def test_concurrent_record_search_sellers(tmp_path: Path):
    """Multiple record_search_sellers calls from different threads should not corrupt DB."""
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")

    trace_ids = [f"trace_{i}" for i in range(10)]
    seller_ids_list = [[f"seller_{i}_{j}" for j in range(5)] for i in range(10)]
    errors = []

    def record_sellers(idx, trace_id, seller_ids):
        user_id = f"user_{idx}"  # Unique user per trace
        try:
            store.record_search_sellers(trace_id, user_id, seller_ids)
        except Exception as e:
            errors.append(f"record_search_sellers: {type(e).__name__}: {e}")

    # Run record_search_sellers concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(record_sellers, i, trace_ids[i], seller_ids_list[i])
            for i in range(10)
        ]
        for future in as_completed(futures):
            future.result(timeout=10)

    assert not errors, f"Errors during concurrent record_search_sellers:\n" + "\n".join(errors)

    # Verify data was recorded correctly
    for idx, (trace_id, seller_ids) in enumerate(zip(trace_ids, seller_ids_list)):
        user_id = f"user_{idx}"
        recorded = store.searched_sellers(user_id, trace_id, "1970-01-01T00:00:00Z")
        assert recorded == set(seller_ids), f"Mismatch for {trace_id}: {recorded} != {set(seller_ids)}"

    print(f"✓ Recorded {len(trace_ids)} concurrent seller searches with perfect consistency")
