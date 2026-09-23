"""Regression cases from the 2026-09-23 QA audit (docs/audits/qa_audit_2026-09-23.md).

The fixture holds the real top-20 titles the live service returned for each
"<query> بالرياض", with the hand labels from the audit. DANGEROUSLY_WRONG and
WRONG titles must be rejected; EXACT titles must be kept. RELATED is not scored.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from farq.cities import find_cities
from farq.config import SearchConfig
from farq.contracts import Ad, ResultUnit, SearchResult, SearchState, Seller
from farq.corpus import MemoryCorpus
from farq.dedup import deduplicate
from farq.eligibility import contains_term, decide, has_word
from farq.intent import analyze, analyze_needs
from farq.live_haraj import LiveBatch, _price
from farq.orchestrator import run_search
from farq.text import normalize

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "matching_live_prod_2026-09-23.json"
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
SCORED = [row for row in ROWS if row["label"] != "RELATED"]
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _ad(title, *, ad_id="1", city="الرياض", seller_id="s1", price=None, posted=None, body=None):
    seller = Seller(id=seller_id, name="", city=city)
    return Ad(
        id=ad_id,
        title=title,
        description=body,
        city=city,
        price_amount=price,
        posted_at=posted,
        listing_state="active",
        seller=seller,
    )


def _verdict(row):
    intent = analyze(f"{row['query']} بالرياض")
    ad = _ad(row["title"], seller_id=row["seller_id"])
    return decide(intent, ad, ad.seller)


@pytest.mark.parametrize("row", SCORED, ids=[f"{row['query']}#{row['rank']}" for row in SCORED])
def test_live_production_titles_are_judged_like_the_audit(row):
    ok, reasons = _verdict(row)
    if row["label"] == "EXACT":
        assert ok, (row["title"], reasons)
    else:
        assert not ok, (row["title"], reasons)


def test_live_production_fixture_pass_rate():
    passed = sum(1 for row in SCORED if _verdict(row)[0] == (row["label"] == "EXACT"))
    # Before the fix: 114/181 (63.0%). Every scored row must now agree.
    assert passed == len(SCORED)


# TSR-008 / TSR-045: electric products are not electricians; blocklists are whole words.
def test_electric_products_are_not_electricians():
    intent = analyze("كهربائي بالرياض")
    for title in ("مولد كهرباء للبيع", "سيارة كهربائية للبيع", "كرسي كهربائي للبيع", "قدر كهربائي جديد", "ميكانيكي وكهرباىي متنقل"):
        assert decide(intent, _ad(title), None)[0] is False, title
    for title in ("كهربائي الرياض", "فني كهرباء", "ابو محمد للكهرباء", "كهربائي وتركيب سيراميك", "تمديد كهرباء وصيانة"):
        assert decide(intent, _ad(title), None)[0] is True, title
    plumber = analyze("سباك بالرياض")
    assert decide(plumber, _ad("سباك سيرلانكي"), None)[0] is True
    assert has_word("سباك سيرلانكي", ("سير",)) is None


def test_electric_adjective_in_the_request_is_a_product():
    assert analyze("سكيت كهربائي").subcategory.value != "electrician"
    assert analyze("مولد كهرباء").category.value == "equipment"
    assert analyze("تركيب أفياش كهرباء بالرياض").subcategory.value == "electrician"


# TSR-007: a service verb with a product noun is the service.
def test_ac_cleaning_is_a_service_not_an_ac_for_sale():
    intent = analyze("تنظيف مكيف بالرياض")
    assert intent.type.value == "service"
    assert intent.subcategory.value == "ac_cleaning"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert "تنظيف مكيفات" in intent.search_terms and "غسيل مكيفات" in intent.search_terms
    assert decide(intent, _ad("للبيع مكيف سبليت", body="تمت صيانته"), None)[0] is False
    assert decide(intent, _ad("غسيل وتنظيف مكيفات بالرياض"), None)[0] is True
    dual = analyze("أبي تنظيف مكيفين سبليت بجدة")
    assert dual.subcategory.value == "ac_cleaning"
    assert analyze("صيانة مكيفات بالرياض").subcategory.value == "ac_repair"
    assert analyze("فك وتركيب مكيفات بالرياض").subcategory.value == "ac_install"
    assert analyze("مكيف سبليت").type.value == "product"


# TSR-004: joined "و" + a second service is a second need, each with its own words.
def test_joined_services_are_each_kept():
    intents = analyze_needs("أبي سباك يصلح التسريب وكهربائي يركب 3 أفياش بالرياض")
    assert [item.subcategory.value for item in intents] == ["plumber", "electrician"]
    assert all(item.location_city.value == "الرياض" for item in intents)
    assert intents[0].segment_text == "سباك يصلح التسريب الرياض"
    assert intents[1].quantity.value == 3
    assert intents[1].quantity_unit == "أفياش"
    four = analyze_needs("أبي نجار يركب 4 أبواب وسباك يصلح المغسلة وتنظيف مكيفات ونقل عفش بالرياض")
    assert [item.subcategory.value for item in four] == ["carpenter", "plumber", "ac_cleaning", "moving"]
    comma = analyze_needs("نجار، سباك، نقل عفش في جدة")
    assert [item.subcategory.value for item in comma] == ["carpenter", "plumber", "moving"]
    assert [item.subcategory.value for item in analyze_needs("فك وتركيب مكيفات بالرياض")] == ["ac_install"]


# TSR-006 / TSR-041: the need keeps the material, shows real spelling; segment_text keeps the rest.
def test_need_keeps_material_and_display_spelling():
    railing = analyze("أبي درابزين ستانلس للدرج بالرياض")
    assert railing.need == "درابزين ستانلس الرياض"
    assert railing.segment_text == "درابزين ستانلس للدرج بالرياض"
    again = analyze(railing.need)
    assert again.material.value == "ستانلس"
    assert analyze(railing.segment_text).material.value == "ستانلس"
    pair = analyze_needs("كهربائي و سباك في جدة")
    assert [item.need for item in pair] == ["كهربائي جدة", "سباك جدة"]
    assert pair[0].search_terms[0] == "كهربائي جدة"
    assert all("كهربايي" not in term for term in pair[0].search_terms)
    assert pair[0].location_city.value == "جده"


# TSR-040 / TSR-039: glazing keeps the customer's phrase and needs a glazing word.
def test_glazing_keeps_phrase_and_needs_real_glazing_context():
    intent = analyze("زجاج سيكوريت بالرياض")
    assert intent.need == "زجاج سيكوريت الرياض"
    assert intent.search_terms[0] == "زجاج سيكوريت الرياض"
    for title in ("زجاج سيارة كامري", "مراية زجاج", "طاولة زجاج", "تنظيف واجهات زجاج بالرياض"):
        assert decide(intent, _ad(title), None)[0] is False, title
    assert decide(intent, _ad("تركيب شاورات زجاج سكريت"), None)[0] is True


# TSR-047 / TSR-070: quantities, Arabic-Indic digits, English trade names.
def test_quantities_digits_and_english_names():
    assert analyze("نجار يركب 4 أبواب بالرياض").quantity.value == 4
    assert analyze("تنظيف ٥ مكيفات بالرياض").quantity.value == 5
    counted = analyze("مكيف سبليت عدد 3")
    assert counted.quantity.value == 3
    assert analyze("مكيف 24 وحده").quantity.value is None
    assert analyze("كامري 2024").quantity.value is None
    assert analyze("كامري ٢٠٢٤").year.value == 2024
    assert normalize("٢٠٢٤") == "2024"
    for text, sub in (("need electrician in Riyadh", "electrician"), ("plumber riyadh", "plumber"), ("carpenter riyadh", "carpenter"), ("movers riyadh", "moving"), ("cctv riyadh", "cctv"), ("ac cleaning riyadh", "ac_cleaning")):
        intent = analyze(text)
        assert intent.understood and intent.subcategory.value == sub, text
    assert analyze("need electrician in Riyadh").need == "كهربائي الرياض"


# TSR-048: a request that names nothing asks instead of searching filler words.
def test_vague_request_is_not_understood():
    intent = analyze("أبي أحد يضبط البيت")
    assert intent.understood is False
    assert intent.clarification_question
    response, _trace = run_search("أبي أحد يضبط البيت", MemoryCorpus([]), None, SearchConfig(enable_live=False), NOW)
    assert response.state == SearchState.NOT_UNDERSTOOD


# TSR-049: from X to Y is a route.
def test_moving_route_keeps_origin_and_destination():
    for text in ("نقل عفش من الرياض للدمام", "نقل عفش من الرياض الى جدة"):
        intent = analyze(text)
        assert intent.clarification_question is None, text
        assert intent.location_city.value == "الرياض"
        assert intent.destination_city.value in {"الدمام", "جده"}
    assert "الدمام" in find_cities("للدمام")
    mixed = analyze_needs("أبي سباك يصلح المغسلة ونقل عفش من الرياض للدمام")
    assert [item.subcategory.value for item in mixed] == ["plumber", "moving"]
    assert mixed[0].location_city.value == "الرياض"
    assert mixed[1].destination_city.value == "الدمام"
    assert isinstance(analyze("شقة في الرياض وجدة").location_city.value, list)


# TSR-042: long tail and the new categories.
def test_long_tail_needs_most_words_and_new_categories():
    leak = analyze("كشف تسريب مياه بالرياض")
    assert leak.subcategory.value == "leak_detection"
    assert decide(leak, _ad("شركة كشف تسربات المياه بالرياض"), None)[0] is True
    cams = analyze("كاميرات مراقبة بالرياض")
    assert cams.subcategory.value == "cctv"
    tail = analyze("عسل سدر جبلي اصلي")
    assert tail.eligibility_min == 3
    assert decide(tail, _ad("عسل سدر جبلي", city="جده"), None)[0] is True
    assert decide(tail, _ad("عسل جبلي", city="جده"), None)[0] is False
    assert all(len(term.split()) >= 2 for term in tail.search_terms)


# TSR-043: attached prefixes and plurals.
def test_prefixes_and_plurals_match():
    assert contains_term("شركة الرزق لنقل العفش", "نقل عفش")
    assert contains_term("كاميرات المراقبة", "كاميرات مراقبه")
    assert contains_term("وكهربائي", "كهربائي")
    moving = analyze("نقل عفش بالرياض")
    assert decide(moving, _ad("شركة الرزق لنقل العفش"), None)[0] is True
    carpenter = analyze("نجار بالرياض")
    assert decide(carpenter, None, Seller(id="x", name="شكرى النجار", city="الرياض", specialty_evidence=["lighting"]))[0] is False


# TSR-046: "داخل المدينة" is not Madinah.
def test_the_word_city_does_not_veto_riyadh():
    moving = analyze("نقل عفش بالرياض")
    assert decide(moving, _ad("نقل عفش داخل المدينة وخارجها"), None)[0] is True
    assert decide(moving, _ad("نقل عفش المدينة المنورة"), None)[0] is False


# TSR-087: worker transfers and Google Maps listings are not providers.
def test_transfers_and_map_listings_are_not_providers():
    plumber = analyze("سباك بالرياض")
    assert decide(plumber, _ad("سباك للتنازل الرياض"), None)[0] is False
    assert decide(plumber, _ad("سباك نقل كفالة"), None)[0] is False
    carpenter = analyze("نجار بالرياض")
    assert decide(carpenter, _ad("خريطة نشاط تجاري مثبتة على جوجل لنجار"), None)[0] is False


# TSR-085 / TSR-069: placeholder and glued prices.
def test_prices_are_first_number_and_above_the_floor():
    assert _price({"inputPrice": "9"}) is None
    assert _price({"inputPrice": "20"}) is None
    assert _price({"formattedPrice": "من 100 الى 200"}) == 100
    assert _price({"formattedPrice": "0555555555"}) is None
    assert _price({"formattedPrice": "123456789"}) is None
    assert _price({"formattedPrice": "2,800 ريال"}) == 2800


# TSR-050: a stated budget filters priced ads.
def test_price_limits_apply_to_priced_ads():
    intent = analyze("مكيف سبليت اقل من 1500")
    assert decide(intent, _ad("مكيف سبليت", price=1400), None)[0] is True
    assert decide(intent, _ad("مكيف سبليت", price=2200), None)[0] is False
    assert decide(intent, _ad("مكيف سبليت", price=None), None)[0] is True


# TSR-044: one card per seller for provider/hybrid results.
def test_one_card_per_seller_for_hybrid():
    seller = Seller(id="21813894", name="", city="الرياض")
    results = [
        SearchResult(result_unit=ResultUnit.HYBRID, ad=_ad(f"درابزين ستانلس {i}", ad_id=str(i), seller_id="21813894"), seller=seller, score=0.5 + i / 10)
        for i in range(4)
    ]
    kept, removed = deduplicate(results)
    assert len(kept) == 1 and removed == 3
    assert kept[0].ad.id == "3"


# TSR-026 / TSR-044 end to end: stale ads drop out while fresh ones qualify.
def test_stale_ads_are_left_out_when_fresh_ones_exist():
    fresh = _ad("درابزين ستانلس ستيل", ad_id="f", seller_id="a", posted="2026-09-10T00:00:00+00:00")
    old = _ad("درابزين ستانلس ستيل", ad_id="o", seller_id="b", posted="2023-02-21T00:00:00+00:00")
    twin = _ad("درابزين استيل", ad_id="t", seller_id="a", posted="2026-09-12T00:00:00+00:00")

    class Fake:
        def search(self, queries, city):
            return LiveBatch(ads=[fresh.model_copy(deep=True), old.model_copy(deep=True), twin.model_copy(deep=True)])

    response, _trace = run_search("درابزين ستانلس بالرياض", MemoryCorpus([]), Fake(), SearchConfig(enable_live=True), NOW)
    ids = [item.ad.id for item in response.results if item.ad]
    assert "o" not in ids
    assert Counter(item.seller.id for item in response.results) == Counter({"a": 1})

    class OnlyOld:
        def search(self, queries, city):
            return LiveBatch(ads=[old.model_copy(deep=True)])

    stale, _trace = run_search("درابزين ستانلس بالرياض", MemoryCorpus([]), OnlyOld(), SearchConfig(enable_live=True), NOW)
    assert stale.state == SearchState.STALE_AD
    assert stale.results[0].ad.posted_at.startswith("2023-02-21")


def test_later_needs_drop_the_joining_waw_but_keep_real_waw_words():
    texts = [item.segment_text for item in analyze_needs("أبي نجار يركب 4 أبواب خشب، وسباك يغير 2 سيفون، وتنظيف 5 مكيفات سبليت بالرياض")]
    assert texts[1].startswith("سباك") and texts[2].startswith("تنظيف")
    assert analyze_needs("أبي سباك، وايت ماء بالرياض")[1].segment_text.startswith("وايت")
