import pytest

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
