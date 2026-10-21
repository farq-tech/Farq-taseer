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


def test_supplier_product_taxonomy_does_not_call_sofa_sales_upholstery_service():
    listing = read_business("بيع كنب زاوية وطاولات طعام وأسرة وشاشات وراوترات وطابعات")
    keys = {item["key"] for item in listing["products"]}

    assert keys >= {"sofas", "dining_tables", "beds", "screens", "routers", "printers"}
    assert not any(item["key"] == "upholstery" for item in listing["services"])


def test_upholstery_work_stays_a_service():
    listing = read_business("خدمات تنجيد كنب ومجالس")

    assert any(item["key"] == "upholstery" for item in listing["services"])
