from datetime import datetime, timezone

from farq.config import SearchConfig
from farq.contracts import SearchState
from farq.corpus import MemoryCorpus, default_sample_path
from farq.eligibility import decide
from farq.intent import analyze
from farq.live_haraj import LiveBatch, ad_from_item
from farq.orchestrator import run_search
from farq.text import normalize


NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def corpus():
    return MemoryCorpus.from_json(default_sample_path())


def test_stainless_railing_intent_keeps_unknowns_and_city():
    intent = analyze("أبي درابزين ستان ستيل بالرياض")
    assert intent.understood
    assert intent.type.value == "service"
    assert intent.result_unit.value == "hybrid"
    assert intent.material.value == "ستانلس"
    assert intent.location_city.value == "الرياض"
    assert intent.year.value is None
    assert intent.condition.value is None
    assert intent.brand.value is None
    assert intent.clarification_question is None
    assert any("درابزين" in group for group in intent.eligibility_groups)
    assert any("ستانلس" in group for group in intent.eligibility_groups)


def test_spelling_and_two_digit_year_are_not_silently_certain():
    railing = analyze("دربزين ستانلس")
    assert railing.material.value == "ستانلس"
    assert railing.clarification_question
    camry = analyze("كامري 24 فل")
    assert camry.model.value == "Camry"
    assert camry.year.value == 2024
    assert camry.year.confidence <= 0.7
    assert camry.location_city.value is None
    full = analyze("كامري 2024 فل كامل مستعملة")
    assert full.year.value == 2024
    assert full.year.confidence > 0.9
    assert full.condition.value == "used"
    assert any(item.value == "full-options" for item in full.attributes)


def test_service_without_city_asks_instead_of_guessing_riyadh():
    intent = analyze("أبي نجار يسوي لي دولاب")
    assert intent.type.value == "service"
    assert intent.subcategory.value == "carpenter"
    assert intent.location_city.value is None
    assert intent.clarification_question
    assert any(item.value == "wardrobe" for item in intent.attributes)


def test_unknown_query_is_not_a_match():
    intent = analyze("asdf qqq")
    assert intent.understood is False
    response, _trace = run_search("asdf qqq", corpus(), None, SearchConfig(enable_live=False), NOW)
    assert response.state == SearchState.NOT_UNDERSTOOD
    assert response.results == []


def test_surname_and_unrelated_category_are_not_eligible():
    intent = analyze("نجار بالرياض")
    rows = {row.seller.id: row for row in corpus().rows}
    carpenter = rows["14371810"]
    surname = rows["12869076"]
    glass = rows["1031009"]
    assert decide(intent, None, carpenter.seller)[0] is True
    assert decide(intent, None, surname.seller)[0] is False
    assert decide(intent, None, glass.seller)[0] is False


def test_railing_search_rejects_glass_seller_and_uses_live_evidence():
    sample = corpus()
    riyadh = ad_from_item(
        {
            "id": 178987447,
            "title": "درابزين ستانلس ستيل بالرياض",
            "postDate": 1776526089,
            "authorUsername": "ابونايف",
            "authorId": 17033461,
            "URL": "11178987447/example/",
            "bodyTEXT": "تفصيل وتركيب درابزين ستانلس",
            "city": "الرياض",
            "geoNeighborhood": "اليرموك",
            "tags": ["خدمات مقاولات"],
            "thumbURL": "example.jpg",
            "status": True,
            "price": {"formattedPrice": "1", "inputPrice": "1"},
        }
    )
    jeddah = ad_from_item(
        {
            "id": 165658080,
            "title": "درابزين ستانلس في جدة",
            "postDate": 1757592903,
            "authorUsername": "الدروب",
            "authorId": 657975,
            "URL": "11165658080/example/",
            "bodyTEXT": "تفصيل درابزين ستانلس",
            "city": "جده",
            "geoNeighborhood": "صناعي",
            "tags": ["خدمات"],
            "thumbURL": "example.jpg",
            "status": True,
            "price": None,
        }
    )

    class FakeLive:
        def search(self, queries, city):
            assert city == "الرياض"
            return LiveBatch(ads=[riyadh, jeddah])

    response, trace = run_search(
        "أبي درابزين ستان ستيل بالرياض",
        sample,
        FakeLive(),
        SearchConfig(enable_live=True, min_qualified_local=3),
        NOW,
    )
    assert response.state == SearchState.RESULTS
    assert [item.ad.id for item in response.results if item.ad] == ["178987447"]
    assert riyadh.price_amount is None
    assert riyadh.url.startswith("https://haraj.com.sa/")
    assert "eligibility_groups" not in response.model_dump(mode="json")["intent"]
    assert any(stage["stage"] == "quality_gate" for stage in trace["stages"])


def test_product_outage_is_not_reported_as_no_results():
    class Down:
        def search(self, queries, city):
            return LiveBatch(error="connection refused")

    response, _trace = run_search("PS5 نظيف", corpus(), Down(), SearchConfig(enable_live=True), NOW)
    assert response.state == SearchState.LIVE_UNAVAILABLE
    assert response.results == []
    intent = response.intent
    assert intent.model.value == "PlayStation 5"
    assert intent.condition.value == "used"
    assert intent.location_city.value is None


def test_horse_is_not_forced_into_a_construction_category():
    intent = analyze("حصان عربي")
    assert intent.category.value == "animals"
    assert intent.type.value == "other"


def test_normalization_folds_railing_spellings():
    assert "درابزين" in normalize("دربزين استانلس")
    assert "ستانلس" in normalize("ستان ستيل")


def _ad(**overrides):
    payload = {
        "id": 1,
        "title": "درابزين ستانلس ستيل",
        "postDate": 1750000000,
        "authorUsername": "ورشة",
        "authorId": 99,
        "URL": "111/example/",
        "bodyTEXT": "تفصيل وتركيب",
        "city": "الرياض",
        "geoNeighborhood": "النخيل",
        "tags": [],
        "thumbURL": "1350x1800_2CA29BD0-8A1E-4FC5-BEC8-8D6FEDA93472.jpg",
        "status": True,
        "price": {"formattedPrice": "2800", "inputPrice": "2800"},
    }
    payload.update(overrides)
    return ad_from_item(payload)


def test_sink_railing_is_not_eligible_and_year_mismatch_is_rejected():
    railing = analyze("أبي درابزين ستانلس بالرياض")
    sink = _ad(id=2, title="للبيع درابزين سلم وشبك واغراض مغطس للبيع", bodyTEXT="مغسلة")
    assert decide(railing, sink, sink.seller)[0] is False
    camry = analyze("كامري 2024 مستعملة")
    older = _ad(id=3, title="كامري 2019 فل", bodyTEXT="مستعملة", city="جده")
    assert decide(camry, older, older.seller)[0] is False
    same = _ad(id=4, title="كامري 2024", bodyTEXT="مستعملة")
    assert decide(camry, same, same.seller)[0] is True
    unstated = _ad(id=5, title="كامري فل كامل", bodyTEXT="نظيفة")
    assert decide(camry, unstated, unstated.seller)[0] is True


def test_rejected_live_ads_are_not_called_suitable_results():
    class Fake:
        def search(self, queries, city):
            sink = _ad(id=8, title="درابزين مع مغسلة", bodyTEXT="مغسلة فقط")
            return LiveBatch(ads=[sink])

    response, _trace = run_search("أبي درابزين ستانلس بالرياض", corpus(), Fake(), SearchConfig(enable_live=True), NOW)
    assert response.state == SearchState.NO_QUALIFIED_RESULTS
    assert response.results == []


def test_deleted_ads_keep_their_own_state():
    class Fake:
        def search(self, queries, city):
            return LiveBatch(ads=[_ad(id=9, status=False, title="بلايستيشن 5 مستعمل", bodyTEXT="ps5")])

    response, _trace = run_search("PS5 مستعمل", corpus(), Fake(), SearchConfig(enable_live=True), NOW)
    assert response.state == SearchState.DELETED_AD
    assert response.results == []


def test_timeout_with_a_qualified_ad_stays_partial():
    class Fake:
        def search(self, queries, city):
            posted = int(datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp())
            return LiveBatch(
                ads=[_ad(id=10, title="درابزين ستانلس", bodyTEXT="ستانلس", postDate=posted)],
                timed_out=True,
                error="timed out",
            )

    response, _trace = run_search("أبي درابزين ستانلس بالرياض", corpus(), Fake(), SearchConfig(enable_live=True), NOW)
    assert response.state == SearchState.PARTIAL_RESULTS
    assert [item.ad.id for item in response.results if item.ad] == ["10"]


def test_thumbnail_file_names_use_the_measured_cdn_sizes():
    ad = _ad()
    assert ad.image_ref.endswith(".jpg")
    assert ad.image_urls[0] == "https://thumbcdn.haraj.com.sa/1350x1800_2CA29BD0-8A1E-4FC5-BEC8-8D6FEDA93472.jpg-400x400.webp"
    assert ad.image_urls[1].endswith("-140x140.webp")
    empty = _ad(thumbURL=None)
    assert empty.image_urls == []
    assert empty.image_ref is None


def test_parts_games_and_side_mentions_are_not_the_requested_thing():
    camry = analyze("كامري 2024 مستعملة بالرياض")
    rim = _ad(id=21, title="جنوط كامري 2024 مستعمل", bodyTEXT="جنوط وكالة", city="الرياض")
    car = _ad(id=22, title="تويوتا كامري 2024 استاندر", bodyTEXT="ممشى 40 ألف مستعملة", city="الرياض")
    newer = _ad(id=27, title="كامري 2025", bodyTEXT="ذكر 2024 في الوصف", city="الرياض")
    fan = _ad(id=28, title="مروحه تبريد كامري 2024", bodyTEXT="قطعة", city="الرياض")
    assert decide(camry, rim, rim.seller)[0] is False
    assert decide(camry, car, car.seller)[0] is True
    assert decide(camry, newer, newer.seller)[0] is False
    assert decide(camry, fan, fan.seller)[0] is False
    ps5 = analyze("PS5 مستعمل")
    game = _ad(id=23, title="لعبة فيفا PS5", bodyTEXT="لعبة فقط", city="الرياض")
    fresh = _ad(id=24, title="بلايستيشن 5 جديد", bodyTEXT="جديد بكرتونه", city="الرياض")
    used = _ad(id=25, title="سوني PS5 مستعمل", bodyTEXT="استخدام خفيف", city="الرياض")
    assert decide(ps5, game, game.seller)[0] is False
    assert decide(ps5, fresh, fresh.seller)[0] is False
    assert decide(ps5, used, used.seller)[0] is True
    carpenter = analyze("نجار بالرياض")
    mover = _ad(
        id=26,
        title="نقل عفش بالرياض",
        bodyTEXT="نقل عفش نجار حداد دهان مكيف سباك",
        city="الرياض",
    )
    assert decide(carpenter, mover, mover.seller)[0] is False
    assert analyze("أبي شقة في الرياض وجدة").clarification_question
