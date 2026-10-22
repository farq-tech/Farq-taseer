import json
from pathlib import Path

import pytest

from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.taxonomy import read_business


@pytest.mark.parametrize(
    "query,subcategory,qualifier",
    [
        ("أحتاج شاشة 27 بوصة 144 هرتز بالرياض", "screens", "27 بوصة 144 هرتز"),
        ("أبحث عن كنب زاوية خمس مقاعد", "corner_sofas", "خمس مقاعد"),
        ("أبي كنب حرف L مستعمل بجدة", "corner_sofas", "حرف L مستعمل"),
        ("أدور طاولة طعام 8 كراسي بالرياض", "dining_tables", "8 كراسي"),
        ("أحتاج سرير نفرين مقاس 180", "double_beds", "نفرين مقاس 180"),
        ("أبي جهاز راوتر 5G مستعمل بالرياض", "routers", "5G مستعمل"),
        ("أحتاج طابعة ليزر ملونة للبيت", "printers", "ليزر ملونة"),
    ],
)
def test_common_household_product_search_preserves_specifics(query, subcategory, qualifier):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.subcategory.value == subcategory
    assert intent.search_terms
    assert qualifier in intent.search_terms[0]
    if "أبحث عن" in query:
        assert "عن" not in intent.search_terms[0].split()


def test_eight_chairs_are_quantity_not_part_of_the_product_name():
    intent = analyze("أدور طاولة طعام 8 كراسي بالرياض")

    assert intent.quantity.value == 8
    assert intent.quantity_unit == "كراسي"


def test_refresh_rate_is_not_misread_as_requested_quantity():
    intent = analyze("أحتاج شاشة 27 بوصة 144 هرتز بالرياض")

    assert intent.quantity.known is False


@pytest.mark.parametrize(
    "query",
    [
        "أدور على سرير طبي لكبار السن",
        "أبحث عن سرير طبي لكبار السن",
        "نبغى سرير طبي لكبار السن",
    ],
)
def test_request_openers_and_connectors_preserve_the_actual_product_need(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.subcategory.value == "beds"
    assert intent.segment_text == "سرير طبي لكبار السن"
    assert intent.need == "سرير طبي لكبار السن"
    assert intent.search_terms[0] == "سرير طبي لكبار السن"


@pytest.mark.parametrize(
    "query,wrong_title,marker",
    [
        ("أبحث عن كنب زاوية بالرياض", "كنب مودرن للبيع", "زاوية"),
        ("أحتاج سرير نفرين بالرياض", "سرير مفرد جديد للبيع", "نفرين"),
    ],
)
def test_specific_furniture_variant_rejects_generic_or_conflicting_listings(query, wrong_title, marker):
    intent = analyze(query)
    seller = Seller(id="s1", name="", city="الرياض")
    listing = Ad(id="1", title=wrong_title, city="الرياض", listing_state="active", seller=seller)

    assert any(marker in group for group in intent.eligibility_groups)
    assert decide(intent, listing, seller)[0] is False


@pytest.mark.parametrize(
    "query,phrase,marker",
    [
        ("gaming monitor", "gaming monitor", "gaming"),
        ("computer monitor", "computer monitor", "computer"),
    ],
)
def test_english_monitor_type_is_retained_in_search_and_matching(query, phrase, marker):
    intent = analyze(query)

    assert any(phrase in term.lower() for term in intent.search_terms)
    assert any(marker in group for group in intent.eligibility_groups)


def test_screen_maintenance_is_a_local_service_not_a_product_search():
    intent = analyze("أحتاج صيانة شاشة بالرياض")
    seller = Seller(id="s1", name="", city="الرياض")
    for_sale = Ad(id="1", title="شاشة للبيع", city="الرياض", listing_state="active", seller=seller)

    assert intent.type.value == "service"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert intent.location_sensitivity.value == "required"
    assert "صيانة شاشة الرياض" in intent.search_terms
    assert decide(intent, for_sale, seller)[0] is False


def test_screen_maintenance_without_city_asks_for_city():
    intent = analyze("أحتاج صيانة شاشة")

    assert intent.type.value == "service"
    assert intent.location_sensitivity.value == "required"
    assert "location_city" in intent.missing_decision_information


def test_cleaning_a_sofa_remains_a_service_request():
    intent = analyze("أبي شركة تنظيف كنب بالرياض")

    assert intent.type.value == "service"
    assert intent.result_unit.value == "service_provider"


@pytest.mark.parametrize(
    "query,city",
    [
        ("أبي أحد ينظف البيت في جدة", "جده"),
        ("أحتاج تنظيف البيت بالرياض", "الرياض"),
        ("أبي أحد ينظف البيت", None),
    ],
)
def test_plain_home_cleaning_requests_are_understood_as_a_local_service(query, city):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "cleaning"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == city
    assert any("تنظيف منازل" in term or "شركة تنظيف" in term for term in intent.search_terms)
    if city is None:
        assert "location_city" in intent.missing_decision_information


def test_supplier_product_taxonomy_does_not_call_sofa_sales_upholstery_service():
    listing = read_business("بيع كنب زاوية وطاولات طعام وأسرة وشاشات وراوترات وطابعات")
    keys = {item["key"] for item in listing["products"]}

    assert keys >= {"sofas", "dining_tables", "beds", "screens", "routers", "printers"}
    assert not any(item["key"] == "upholstery" for item in listing["services"])


def test_supplier_desks_are_classified_as_office_desk_products():
    listing = read_business("بيع مكاتب دراسة وطاولات كمبيوتر ومكتب قيمنق")

    assert any(item["key"] == "office_desks" for item in listing["products"])


def test_upholstery_work_stays_a_service():
    listing = read_business("خدمات تنجيد كنب ومجالس")

    assert any(item["key"] == "upholstery" for item in listing["services"])


@pytest.mark.parametrize(
    "query,subcategory,qualifier",
    [
        ("أبي غرفة نوم كاملة", "bedroom_sets", "غرفة نوم كاملة"),
        ("أحتاج طقم غرفة نوم بالرياض", "bedroom_sets", "طقم غرفة نوم"),
        ("أبي غرفه نوم كامله", "bedroom_sets", "غرفة نوم كاملة"),
        ("أبي ثلاجة بابين بالرياض", "refrigerators", "ثلاجة بابين"),
        ("أبي ثلاجه بابان", "refrigerators", "ثلاجة بابين"),
        ("أبغى ثلاجة صغيرة", "refrigerators", "ثلاجة صغيرة"),
        ("أبي براد صغير", "refrigerators", "ثلاجة صغيرة"),
        ("أحتاج ثلاجة عرض بابين", "refrigerators", "ثلاجة عرض"),
        ("I need a fridge in Riyadh", "refrigerators", "fridge"),
    ],
)
def test_bedroom_and_refrigerator_requests_are_specific_products(query, subcategory, qualifier):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.result_unit == ResultUnit.AD
    assert intent.subcategory.value == subcategory
    assert any(qualifier.lower() in term.lower() for term in intent.search_terms)


def test_complete_bedroom_and_two_door_refrigerator_require_the_requested_variant():
    seller = Seller(id="s1", name="")
    complete_room = analyze("أبي غرفة نوم كاملة")
    generic_room = analyze("أبي غرفة نوم")
    two_door = analyze("أبي ثلاجة بابين")
    complete_ad = Ad(id="1", title="غرفة نوم كاملة للبيع", seller=seller)
    incomplete_ad = Ad(id="2", title="غرفة نوم للبيع", seller=seller)
    wrong_fridge = Ad(id="3", title="ثلاجة باب واحد صغيرة", seller=seller)
    matching_fridge = Ad(id="4", title="ثلاجة هام بابين", seller=seller)

    assert decide(complete_room, complete_ad, seller)[0] is True
    assert decide(complete_room, incomplete_ad, seller)[0] is False
    assert decide(generic_room, incomplete_ad, seller)[0] is True
    assert decide(two_door, matching_fridge, seller)[0] is True
    assert decide(two_door, wrong_fridge, seller)[0] is False


@pytest.mark.parametrize(
    "query,subcategory",
    [
        ("تصليح ثلاجات بالرياض", "appliance_repair"),
        ("صيانة ثلاجة بالرياض", "appliance_repair"),
        ("تفصيل غرفة نوم بالرياض", "carpenter"),
        ("نقل عفش غرف نوم بالرياض", "moving"),
    ],
)
def test_household_service_requests_do_not_turn_into_products(query, subcategory):
    intent = analyze(query)

    assert intent.type.value == "service"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert intent.subcategory.value == subcategory


def test_historical_exact_bedroom_and_two_door_fridge_results_remain_eligible():
    fixture_root = Path(__file__).parent / "fixtures"
    fixture_queries = (
        (fixture_root / "matching_live_prod_2026-09-26_b.json", "أبي غرفة نوم كاملة", "bedroom_sets"),
        (fixture_root / "matching_live_prod_2026-09-26.json", "أبي ثلاجة بابين", "refrigerators"),
    )
    for fixture_path, query, subcategory in fixture_queries:
        rows = [row for row in json.loads(fixture_path.read_text())["rows"] if row["query"] == query]
        assert len(rows) == 12
        intent = analyze(query)
        assert intent.subcategory.value == subcategory
        for row in rows:
            seller = Seller(id=f"fixture-{row['rank']}", name="")
            listing = Ad(
                id=str(row["rank"]),
                title=row["title"],
                description=row.get("body", ""),
                seller=seller,
            )
            assert decide(intent, listing, seller)[0] is True, row["title"]


def test_supplier_taxonomy_recognizes_bedroom_sets_refrigerators_and_related_repairs():
    listing = read_business("بيع غرف نوم كاملة وثلاجات بابين وصيانة ثلاجات وتفصيل غرفة نوم")
    product_keys = {item["key"] for item in listing["products"]}
    service_keys = {item["key"] for item in listing["services"]}

    assert {"bedroom_sets", "refrigerators"} <= product_keys
    assert {"appliance_repair", "carpentry"} <= service_keys
