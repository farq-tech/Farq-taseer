import pytest

from farq.config import SearchConfig
from farq.corpus import HARAJ_SELLER_SQL
from farq.live_haraj import HarajLiveClient


def test_sql_stays_on_haraj_sellers_only():
    assert "source_system = 'HARAJ'" in HARAJ_SELLER_SQL
    assert "OWNED_REGISTRY" not in HARAJ_SELLER_SQL
    assert "catalog_products" not in HARAJ_SELLER_SQL


def test_search_fetches_each_query_first_page_before_deeper_pages(monkeypatch):
    import farq.live_haraj as live_haraj

    calls = []
    monkeypatch.setattr(live_haraj, "haraj_queries", lambda queries, city: queries)
    client = HarajLiveClient(SearchConfig(live_page_size=5, live_max_pages=2, live_max_queries=3, live_concurrency=2))

    def fake_post(variables):
        calls.append((variables["search"], variables["page"]))
        return {"items": [], "pageInfo": {"hasNextPage": variables["page"] == 1}}

    monkeypatch.setattr(client, "_post", fake_post)
    list(client.search_iter(["alpha", "beta", "gamma"], None))

    assert set(calls[:3]) == {("alpha", 1), ("beta", 1), ("gamma", 1)}
    assert len(calls) == 6
    assert set(calls[3:]) == {("alpha", 2), ("beta", 2), ("gamma", 2)}


@pytest.mark.integration
def test_live_haraj_search_returns_real_pages():
    client = HarajLiveClient(SearchConfig(live_page_size=5, live_max_pages=2, live_max_queries=1, live_timeout_seconds=20))
    first = client.search_one("درابزين ستانلس", None)
    assert first.pages >= 1
    assert first.ads
    assert all(ad.id.isdigit() and ad.title for ad in first.ads)
    assert any(ad.image_urls for ad in first.ads)
    second_batch = client.search(["درابزين ستانلس"], None)
    ids = [ad.id for ad in second_batch.ads]
    assert ids
    assert len(ids) == len(set(ids))
    assert second_batch.error is None
