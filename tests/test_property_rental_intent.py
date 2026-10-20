from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.text import normalize


def _listing(title: str, description: str, city: str = "الرياض") -> Ad:
    seller = Seller(id="seller-1", name="معلن", city=city)
    return Ad(
        id="listing-1",
        title=title,
        description=description,
        city=city,
        seller=seller,
    )


def test_resthouse_rental_intent_preserves_transaction_family_and_city():
    intent = analyze("استراحة عائلية للإيجار في الرياض")

    assert intent.understood is True
    assert intent.type.value == "property"
    assert intent.category.value == "property"
    assert intent.subcategory.value == "resthouses"
    assert intent.result_unit == ResultUnit.AD
    assert intent.location_sensitivity.value == "required"
    assert "للايجار" in normalize(intent.need)
    assert "عائلية" in intent.need
    assert intent.search_terms[0] == "استراحة عائلية للإيجار الرياض"
    assert any("للإيجار" in group for group in intent.eligibility_groups)


def test_daily_chalet_search_retains_daily_rental_qualifier():
    intent = analyze("أبي شاليه يومي بجدة")

    assert intent.subcategory.value == "chalets"
    assert "يومي" in intent.need
    assert "شاليه يومي جدة" in intent.search_terms[0]
    assert intent.location_city.value == "جده"


def test_residential_house_rental_is_understood_and_keeps_rent_and_city():
    intent = analyze("أبي بيت للإيجار في جدة")

    assert intent.understood is True
    assert intent.type.value == "property"
    assert intent.subcategory.value == "house"
    assert intent.location_city.value == "جده"
    assert "للإيجار" in intent.need
    assert "بيت للإيجار جدة" in intent.search_terms[0]
    assert any("للإيجار" in group for group in intent.eligibility_groups)


def test_house_without_transaction_does_not_invent_a_rent_or_sale_filter():
    intent = analyze("أبي بيت في جدة")

    assert intent.subcategory.value == "house"
    assert intent.missing_decision_information == []
    assert intent.clarification_question is None
    assert not any("للإيجار" in group or "للبيع" in group for group in intent.eligibility_groups)


def test_house_intent_does_not_match_tent():
    intent = analyze("أبي بيت شعر للإيجار في جدة")

    assert intent.subcategory is None or intent.subcategory.value != "house"


def test_weekend_chalet_search_is_classified_as_rental():
    intent = analyze("أبي شاليه بالويكند في جدة")

    assert intent.subcategory.value == "chalets"
    assert "ويكند" in intent.need
    assert any("weekend" in group for group in intent.eligibility_groups)


def test_farm_for_stay_is_classified_and_requests_missing_city():
    intent = analyze("أدور مزرعة للاستراحة للإيجار")

    assert intent.subcategory.value == "farms"
    assert intent.location_sensitivity.value == "required"
    assert intent.missing_decision_information == ["location_city"]
    assert intent.clarification_question == "في أي مدينة؟"
    assert "للايجار" in normalize(intent.search_terms[0])
    assert "أدور" not in intent.search_terms[0]


def test_property_sale_and_rental_queries_reject_the_opposite_transaction():
    sale_intent = analyze("استراحة للبيع بجدة")
    rent_intent = analyze("استراحة للإيجار بجدة")
    rental = _listing("استراحة عائلية للإيجار", "متاحة يوميًا", city="جده")
    sale = _listing("استراحة للبيع", "موقعها مميز", city="جده")

    assert decide(sale_intent, sale, sale.seller)[0] is True
    assert decide(sale_intent, rental, rental.seller)[0] is False
    assert decide(rent_intent, rental, rental.seller)[0] is True
    assert decide(rent_intent, sale, sale.seller)[0] is False


def test_conflicting_sale_and_rental_request_asks_for_clarification():
    intent = analyze("استراحة للبيع أو للإيجار بالرياض")

    assert intent.subcategory.value == "resthouses"
    assert intent.missing_decision_information == ["transaction_type"]
    assert intent.clarification_question == "هل تبحث عن العقار للبيع أم للإيجار؟"


def test_buying_wording_still_searches_seller_ads_for_sale():
    intent = analyze("أبحث عن استراحة للشراء بالرياض")
    sale = _listing("استراحة للبيع", "موقعها مميز")

    assert intent.subcategory.value == "resthouses"
    assert "للشراء" in intent.need
    assert any("للبيع" in group for group in intent.eligibility_groups)
    assert decide(intent, sale, sale.seller)[0] is True
