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
