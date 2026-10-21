import pytest

from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze


@pytest.mark.parametrize(
    ("query", "subcategory", "city"),
    [
        ("أبحث عن مكتب تجاري للإيجار بالرياض", "commercial_offices", "الرياض"),
        ("مكتب للإيجار في King Fahd الرياض", "commercial_offices", "الرياض"),
        ("محل للإيجار جدة", "commercial_shops", "جده"),
        ("مستودع للإيجار الرياض", "warehouses", "الرياض"),
    ],
)
def test_commercial_property_queries_are_understood_and_keep_city(query, subcategory, city):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "property"
    assert intent.category.value == "property"
    assert intent.subcategory.value == subcategory
    assert intent.result_unit == ResultUnit.AD
    assert intent.location_city.value == city
    assert any("للإيجار" in group or "للايجار" in group for group in intent.eligibility_groups)


def test_commercial_property_request_without_city_asks_for_one():
    intent = analyze("أبحث عن مكتب تجاري للإيجار")

    assert intent.subcategory.value == "commercial_offices"
    assert intent.location_city.value is None
    assert intent.clarification_question == "في أي مدينة؟"


def test_office_rental_matches_offices_but_rejects_a_shop_or_an_office_for_sale():
    intent = analyze("مكتب تجاري للإيجار بالرياض")
    seller = Seller(id="seller", name="معلن", city="الرياض")
    office = Ad(id="office", title="مكتب للإيجار في King Fahd الرياض", city="الرياض", seller=seller)
    shop = Ad(id="shop", title="محل للإيجار في الرياض", city="الرياض", seller=seller)
    sale = Ad(id="sale", title="مكتب تجاري للبيع في الرياض", city="الرياض", seller=seller)

    assert decide(intent, office, seller)[0] is True
    assert decide(intent, shop, seller)[0] is False
    assert decide(intent, sale, seller)[0] is False


def test_commercial_property_heads_do_not_override_engineering_office_or_study_desk():
    engineering = analyze("أحتاج مكتب هندسي لاستخراج رخصة بالرياض")
    desk = analyze("أبي مكتب دراسة صغير")

    assert engineering.subcategory.value == "engineering_offices"
    assert desk.subcategory.value == "office_desks"
