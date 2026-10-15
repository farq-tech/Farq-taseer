"""Recall of trade searches (2026-10-15): «نجار» returned two listings in the Farq app.

No network: Haraj is replaced by a fake `_post` or by literal listings.
"""

import pytest

from farq.cities import haraj_listing_city, strip_city_words
from farq.config import SearchConfig
from farq.eligibility import decide
from farq.haraj_harvest import CachedHarajClient, HarvestStore
from farq.intent import analyze
from farq.live_haraj import HarajLiveClient, LiveUnavailable, ad_from_item, haraj_queries
from farq.store import Store


def _listing(title, city="الرياض", body="", filtered=None, id="1"):
    return ad_from_item(
        {"id": id, "title": title, "bodyTEXT": body, "authorId": "s" + id, "authorUsername": "بائع",
         "city": city, "status": True, "tags": []},
        filtered,
    )


def _queries(text):
    intent = analyze(text)
    return haraj_queries(intent.search_terms, intent.location_city.value)


# Query building ------------------------------------------------------------------


def test_city_is_the_haraj_filter_not_query_text():
    queries = _queries("نجار الرياض")
    assert queries[0] == "نجار"
    assert not any("الرياض" in query for query in queries[: SearchConfig().live_max_queries])


@pytest.mark.parametrize("text,city,expected", [
    ("نجار الرياض", "الرياض", "نجار"),
    ("سباك بالرياض", "الرياض", "سباك"),
    ("نجار في جدة", "جده", "نجار"),
    ("نجار المدينة المنورة", "المدينة", "نجار"),
    ("نجار موبيليا", "الرياض", "نجار موبيليا"),
    ("نجار الرياض", "جده", "نجار الرياض"),
])
def test_strip_city_words(text, city, expected):
    assert strip_city_words(text, city) == expected


def test_carpenter_synonyms_fill_the_query_budget():
    queries = _queries("نجار الرياض")
    budget = queries[: SearchConfig().live_max_queries]
    assert len(budget) == SearchConfig().live_max_queries == 6
    for synonym in ("نجار موبيليا", "منجرة", "تفصيل خزائن", "نجار ابواب", "نجاره"):
        assert synonym in budget
    assert len({query for query in queries}) == len(queries)


def test_spare_slots_take_the_city_in_text_variant_last():
    # One-word trades: «دهان الرياض» as text finds listings «دهان» alone ranks lower.
    assert _queries("دهان الرياض") == ["دهان", "دهان الرياض"]


def test_a_route_keeps_both_cities_in_the_text():
    assert haraj_queries(["نقل عفش من الرياض الى جدة"], "الرياض") == ["نقل عفش من الرياض الى جدة"]


def test_live_queries_are_deduplicated_and_listings_once_across_queries():
    calls = []

    class Fake(HarajLiveClient):
        def _post(self, variables):
            calls.append((variables["search"], variables["city"]))
            items = [{"id": "same", "title": "نجار", "city": "الرياض", "status": True},
                     {"id": variables["search"], "title": "نجار " + variables["search"], "city": "الرياض", "status": True}]
            return {"items": items, "pageInfo": {"hasNextPage": False}}

    client = Fake(SearchConfig(live_max_pages=1, live_concurrency=1, live_max_queries=2))
    batch = client.search(["نجار الرياض", "نجار", "نجاره الرياض", "نجاره"], "الرياض")
    assert [query for query, _city in calls] == ["نجار", "نجاره"]
    assert all(city == "الرياض" for _query, city in calls)
    assert sorted(ad.id for ad in batch.ads) == ["same", "نجار", "نجاره"]


# City ---------------------------------------------------------------------------


def test_haraj_region_label_is_the_filtered_city():
    # A Dammam-filtered search returns listings labelled «الشرقيه»; 173 of 173 were
    # rejected as another city before.
    assert haraj_listing_city("الشرقيه", "الدمام") == "الدمام"
    assert haraj_listing_city("الرياض", "الخرج") == "الخرج"
    assert haraj_listing_city(None, "الرياض") == "الرياض"
    assert haraj_listing_city("جده", "الرياض") == "جده"
    ad = _listing("نجار موبيليا", city="الشرقيه", filtered="الدمام")
    ok, _ = decide(analyze("نجار الدمام"), ad, ad.seller)
    assert ok


def test_clearly_different_city_still_rejected():
    intent = analyze("نجار الرياض")
    other = _listing("نجار موبيليا", city="جده", filtered="الرياض")
    assert decide(intent, other, other.seller) == (False, ["location_mismatch"])
    named = _listing("نجار الخرج", filtered="الرياض")
    assert decide(intent, named, named.seller) == (False, ["location_mismatch"])


# Eligibility --------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "نجار موبيليا وفك وتركيب جميع انواع العفش واعمال الصيانة",
    "نجار تصميم العاب الاطفال",
    "نجار فك وتركيب سرير وغرف نوم",
    "تفصيل دواليب ملابس وتسريحات",
    "تفصيل خزائن ملابس",
    "منجرة ابواب خشب",
])
def test_real_carpenter_titles_pass(title):
    ad = _listing(title)
    ok, why = decide(analyze("نجار الرياض"), ad, ad.seller)
    assert ok, why


@pytest.mark.parametrize("title,reason", [
    ("كنب للبيع", "for_sale_not_service"),
    ("كنب مستعمل نظيف", "product_not_trade"),
    ("سرير طبي كهربائي", "product_not_trade"),
    ("منشار كهربائي مستعمل", "product_not_trade"),
    ("ابواب خشب جديده", "missing"),
    ("دولاب ملابس جديد", "missing"),
    ("شركة نقل اثاث فك وتركيب غرف نوم مكيفات", "mover_not_trade"),
])
def test_off_topic_titles_still_fail_for_carpenter(title, reason):
    ad = _listing(title, body="نجار" if reason in {"product_not_trade", "mover_not_trade"} else "")
    ok, why = decide(analyze("نجار الرياض"), ad, ad.seller)
    assert not ok and why[0].startswith(reason), why


def test_electrician_products_and_car_electricians_still_fail():
    intent = analyze("كهربائي الرياض")
    for title in ("سخان كهربائي", "دباب كهربائي شبه جديد", "كهربائي سيارات متنقل"):
        ad = _listing(title)
        assert decide(intent, ad, ad.seller)[0] is False, title
    fitter = _listing("فني كهربائي تركيب سخانات وصيانة")
    assert decide(intent, fitter, fitter.seller)[0] is True


# Paused harvest cache -----------------------------------------------------------


def _page(variables):
    return {"items": [{"id": "p" + str(variables["page"]), "title": "نجار", "authorId": "a", "city": "الرياض", "status": True}],
            "pageInfo": {"hasNextPage": False}}


def test_paused_cache_still_answers_the_customer_and_resumes(tmp_path, monkeypatch):
    harvest = HarvestStore(Store(tmp_path / "db", tmp_path / "uploads"))
    harvest.pause("challenge")
    monkeypatch.setattr(HarajLiveClient, "_post", lambda self, variables: _page(variables))
    fetched = CachedHarajClient(SearchConfig(live_max_pages=1), harvest).search_one("نجار", "الرياض")
    assert [ad.id for ad in fetched.ads] == ["p1"]
    assert not harvest.paused()


def test_paused_cache_reports_a_failed_live_answer(tmp_path, monkeypatch):
    harvest = HarvestStore(Store(tmp_path / "db", tmp_path / "uploads"))
    harvest.pause("rate_limited")

    def blocked(self, variables):
        raise LiveUnavailable("HTTP 429")

    monkeypatch.setattr(HarajLiveClient, "_post", blocked)
    with pytest.raises(LiveUnavailable):
        CachedHarajClient(SearchConfig(live_max_pages=1), harvest).search_one("نجار", "الرياض")
    assert harvest.paused()
