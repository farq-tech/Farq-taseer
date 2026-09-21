import pytest

from farq.config import SearchConfig
from farq.corpus import HARAJ_SELLER_SQL
from farq.live_haraj import HarajLiveClient


def test_sql_stays_on_haraj_sellers_only():
    assert "source_system = 'HARAJ'" in HARAJ_SELLER_SQL
    assert "OWNED_REGISTRY" not in HARAJ_SELLER_SQL
    assert "catalog_products" not in HARAJ_SELLER_SQL


@pytest.mark.integration
def test_live_haraj_search_returns_real_pages():
    client = HarajLiveClient(SearchConfig(live_page_size=5, live_max_pages=2, live_max_queries=1, live_timeout_seconds=20))
    first, pages, _has_next = client.search_one("درابزين ستانلس", None)
    assert pages >= 1
    assert first
    assert all(ad.id.isdigit() and ad.title for ad in first)
    second_batch = client.search(["درابزين ستانلس"], None)
    ids = [ad.id for ad in second_batch.ads]
    assert ids
    assert len(ids) == len(set(ids))
    assert second_batch.error is None
