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
