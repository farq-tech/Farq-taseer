"""Common Taseer requests retain the service or model they actually name."""

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze


def test_painting_an_apartment_is_a_service_request_not_a_property_search():
    intent = analyze("سعر دهان شقة غرفتين بالرياض")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "painter"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert any("دهان" in term for term in intent.search_terms)


def test_paint_brand_query_does_not_turn_into_a_painter_request():
    intent = analyze("دهانات جوتن للبيع")

    assert intent.type.value != "service"
    assert intent.result_unit.value == "ad"


def test_painting_service_results_reject_paint_supply_ads():
    intent = analyze("دهان شقة بالرياض")
    seller = Seller(id="seller", name="بائع", city="الرياض")
    paint_supply = Ad(
        id="paint-supply",
        title="دهانات جوتن للبيع",
        seller=seller,
        city="الرياض",
    )
    painter = Ad(
        id="painter",
        title="دهان شقق داخلي وخارجي",
        seller=seller,
        city="الرياض",
    )

    assert decide(intent, paint_supply, seller)[0] is False
    assert decide(intent, painter, seller)[0] is True


def test_iphone_generation_and_storage_survive_natural_query_wording():
    intent = analyze("أبي ايفون 15 برو ماكس 256 جيجا بالرياض")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.subcategory.value == "phones"
    assert intent.brand.value == "Apple"
    assert intent.model.value == "iPhone 15 Pro Max"
    assert "storage" not in intent.missing_decision_information
    assert ["15"] in intent.eligibility_groups
    assert any("256" in group for group in intent.eligibility_groups)
    assert any("ايفون 15 برو ماكس" in term for term in intent.search_terms)


def test_iphone_without_a_pro_max_variant_is_still_a_phone_search():
    intent = analyze("ايفون 15 بالرياض")

    assert intent.type.value == "product"
    assert intent.subcategory.value == "phones"
    assert intent.model.value == "iPhone 15"
    assert any("ايفون 15" in term for term in intent.search_terms)


def test_iphone_storage_capacity_is_used_to_reject_a_different_capacity():
    intent = analyze("ايفون 15 برو ماكس 256 جيجا")
    seller = Seller(id="seller", name="متجر الجوالات")
    wrong_capacity = Ad(
        id="wrong-capacity",
        title="ايفون 15 برو ماكس 512GB",
        seller=seller,
    )

    accepted, reasons = decide(intent, wrong_capacity, seller)

    assert accepted is False
    assert "spec_mismatch:storage_gb" in reasons
