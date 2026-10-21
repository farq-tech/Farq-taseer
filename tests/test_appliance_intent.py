from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze


def test_arabic_coffee_machine_query_keeps_brand_qualifier():
    intent = analyze("أحتاج آلة قهوة ديلونجي بالرياض")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "appliances"
    assert intent.subcategory.value == "coffee_machines"
    assert intent.result_unit == ResultUnit.AD
    assert intent.brand.value == "De'Longhi"
    assert any("ديلونجي" in term for term in intent.search_terms)
    assert any("الرياض" in term for term in intent.search_terms)


def test_arabic_sewing_machine_query_identifies_singer():
    intent = analyze("أبي مكينة خياطة سنجر")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "appliances"
    assert intent.subcategory.value == "sewing_machines"
    assert intent.result_unit == ResultUnit.AD
    assert intent.brand.value == "Singer"
    assert any("سنجر" in term for term in intent.search_terms)


def test_english_espresso_machine_query_is_classified():
    intent = analyze("DeLonghi espresso machine Riyadh")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "appliances"
    assert intent.subcategory.value == "coffee_machines"
    assert intent.brand.value == "De'Longhi"
    assert any("Riyadh" in term for term in intent.search_terms)


def test_machine_repair_remains_a_local_service_request():
    intent = analyze("أحتاج صيانة ماكينة قهوة بالرياض")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert intent.subcategory.value == "appliance_repair"
    assert intent.location_sensitivity.value == "required"
    assert any("ماكينة قهوة" in term for term in intent.search_terms)
    assert not any("غسالات" in term for term in intent.search_terms)


def test_machine_repair_without_city_still_asks_for_location():
    intent = analyze("أحتاج صيانة ماكينة قهوة")

    assert intent.type.value == "service"
    assert intent.location_sensitivity.value == "required"
    assert "location_city" in intent.missing_decision_information


def test_de_longhi_search_rejects_a_different_coffee_machine_brand():
    intent = analyze("أحتاج آلة قهوة ديلونجي بالرياض")
    seller = Seller(id="seller", name="", city="الرياض")
    delonghi = Ad(
        id="delonghi",
        title="ماكينة قهوة ديلونجي",
        city="الرياض",
        listing_state="active",
        seller=seller,
    )
    philips = Ad(
        id="philips",
        title="ماكينة قهوة فيليبس",
        city="الرياض",
        listing_state="active",
        seller=seller,
    )

    assert decide(intent, delonghi, seller)[0] is True
    assert decide(intent, philips, seller)[0] is False


def test_singer_search_rejects_a_different_sewing_machine_brand():
    intent = analyze("أبي مكينة خياطة سنجر")
    seller = Seller(id="seller", name="", city="الرياض")
    singer = Ad(
        id="singer",
        title="ماكينة خياطة سنجر",
        city="الرياض",
        listing_state="active",
        seller=seller,
    )
    brother = Ad(
        id="brother",
        title="ماكينة خياطة براذر",
        city="الرياض",
        listing_state="active",
        seller=seller,
    )

    assert decide(intent, singer, seller)[0] is True
    assert decide(intent, brother, seller)[0] is False


def test_coffee_alone_does_not_become_a_coffee_machine_search():
    intent = analyze("أبي قهوة ديلونجي")

    assert intent.subcategory.value != "coffee_machines"
