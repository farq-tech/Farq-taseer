import json
from fastapi.testclient import TestClient
from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus
from farq.live_haraj import HarajLiveClient
from farq.store import Store


def make_api(tmp_path, monkeypatch):
    pages = []
    def post(self, variables):
        page = variables["page"]
        pages.append(page)
        return {"items": [{"id": page, "title": "سباك تأسيس وصيانة", "authorId": page + 100,
                "authorUsername": "مقدم خدمة", "city": "الرياض", "status": True}],
                "pageInfo": {"hasNextPage": page < 7}}
    monkeypatch.setattr(HarajLiveClient, "_post", post)
    config = SearchConfig(live_max_queries=1, live_max_pages=3)
    api = TestClient(create_app(Store(tmp_path / "db", tmp_path / "uploads"), MemoryCorpus([]), HarajLiveClient(config), config))
    return api, pages


def test_pages_continue_without_restarting_or_losing_trace(tmp_path, monkeypatch):
    api, pages = make_api(tmp_path, monkeypatch)
    first = api.post("/v1/search", json={"query": "سباك بالرياض", "page": 1}).json()
    assert pages == [1, 2, 3]
    assert first["next_page"] == 4
    second = api.post("/v1/search", json={"query": "سباك بالرياض", "page": 4, "continuation_trace": first["trace_id"]})
    assert second.status_code == 200
    assert pages == [1, 2, 3, 4, 5, 6]
    assert second.json()["trace_id"] == first["trace_id"]
    last = api.post("/v1/search", json={"query": "سباك بالرياض", "page": 7, "continuation_trace": first["trace_id"]})
    assert last.json()["next_page"] is None
    assert pages[-1] == 7


def test_continuation_cannot_change_query_or_skip_pages(tmp_path, monkeypatch):
    api, _ = make_api(tmp_path, monkeypatch)
    first = api.post("/v1/search", json={"query": "سباك بالرياض", "page": 1}).json()
    for query, page in [("كهربائي بالرياض", 4), ("سباك بالرياض", 7)]:
        assert api.post("/v1/search", json={"query": query, "page": page, "continuation_trace": first["trace_id"]}).status_code == 404


def test_user_cannot_continue_other_users_search(tmp_path, monkeypatch):
    api, _ = make_api(tmp_path, monkeypatch)
    def login(name):
        r = api.post("/v1/auth/register", json={"email": name + "@example.com", "password": "synthetic-pass", "name": name})
        return {"Authorization": "Bearer " + r.json()["token"]}
    a, b = login("user-a"), login("user-b")
    first = api.post("/v1/search", headers=a, json={"query": "سباك بالرياض", "page": 1}).json()
    body = {"query": "سباك بالرياض", "page": 4, "continuation_trace": first["trace_id"]}
    assert api.post("/v1/search", headers=b, json=body).status_code == 404
    assert api.post("/v1/search", json=body).status_code == 404
    assert api.post("/v1/search", headers=a, json=body).status_code == 200


def test_stream_continuation_uses_same_page_contract(tmp_path, monkeypatch):
    api, pages = make_api(tmp_path, monkeypatch)
    first = api.post("/v1/search/stream", json={"query": "سباك بالرياض", "page": 1})
    done = [json.loads(line) for line in first.text.splitlines()][-1]
    assert done["type"] == "done" and done["next_page"] == 4
    response = api.post("/v1/search/stream", json={"query": "سباك بالرياض", "page": 4, "continuation_trace": done["trace_id"]})
    last = [json.loads(line) for line in response.text.splitlines()][-1]
    assert last["trace_id"] == done["trace_id"] and last["next_page"] == 7
    assert pages == [1, 2, 3, 4, 5, 6]


def test_paged_search_keeps_older_ads_and_distinct_ads_by_same_seller(tmp_path, monkeypatch):
    api, _ = make_api(tmp_path, monkeypatch)
    def post(self, variables):
        return {"items": [{"id": i, "title": "سباك تأسيس وصيانة", "authorId": 100,
            "authorUsername": "مقدم خدمة", "city": "الرياض", "status": True,
            "postDate": 1000000000 if i % 2 else 1780000000} for i in range(1, 46)],
            "pageInfo": {"hasNextPage": False}}
    monkeypatch.setattr(HarajLiveClient, "_post", post)
    result = api.post("/v1/search", json={"query": "سباك بالرياض", "page": 1}).json()
    assert len(result["results"]) == 45
    assert any(item["ad"]["listing_state"] == "stale" for item in result["results"])
    assert result["next_page"] is None


def test_partial_failure_retries_current_window_without_skipping(tmp_path, monkeypatch):
    api, _ = make_api(tmp_path, monkeypatch)
    from farq.live_haraj import LiveUnavailable
    def post(self, variables):
        if variables["page"] == 2:
            raise LiveUnavailable("synthetic unavailable")
        return {"items": [{"id": 1, "title": "سباك تأسيس وصيانة", "authorId": 101,
                "city": "الرياض", "status": True}], "pageInfo": {"hasNextPage": True}}
    monkeypatch.setattr(HarajLiveClient, "_post", post)
    result = api.post("/v1/search", json={"query": "سباك بالرياض", "page": 1}).json()
    assert result["state"] == "PARTIAL_RESULTS"
    assert result["next_page"] == 1


def test_legacy_cached_search_cannot_advertise_or_crash_continuation(tmp_path,monkeypatch):
    api,_=make_api(tmp_path,monkeypatch)
    first=api.post('/v1/search',json={'query':'سباك بالرياض'}).json()
    cached=api.post('/v1/search',json={'query':'سباك بالرياض'}).json()
    assert first['next_page'] is None and cached['next_page'] is None
    body={'query':'سباك بالرياض','page':4,'continuation_trace':cached['trace_id']}
    assert api.post('/v1/search',json=body).status_code==404
    assert api.post('/v1/search/stream',json=body).status_code==404
    assert api.get('/v1/search/'+cached['trace_id']+'/availability').status_code==404
