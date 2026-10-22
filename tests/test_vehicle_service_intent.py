import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze


@pytest.mark.parametrize(
    ("query", "subcategory", "model"),
    [
        ("تأمين كامري", "vehicle_insurance", "Camry"),
        ("تلميع كامري", "car_detailing", "Camry"),
        ("بوليش كامري", "car_detailing", "Camry"),
    ],
)
def test_vehicle_service_queries_keep_the_model_as_a_qualifier(
    query, subcategory, model
):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == subcategory
    assert intent.result_unit.value == "service_provider"
    assert intent.model.value == model
    assert query in intent.need
    assert any("كامري" in term for term in intent.search_terms)


@pytest.mark.parametrize(
    ("query", "provider_title", "subcategory"),
    [
        ("تأمين كامري", "مكتب تأمين سيارات", "vehicle_insurance"),
        ("تلميع كامري", "مركز تلميع سيارات", "car_detailing"),
    ],
)
def test_vehicle_service_intent_accepts_providers_and_rejects_car_sales(
    query, provider_title, subcategory
):
    intent = analyze(query)
    seller = Seller(id="seller", name="معلن")
    provider = Ad(id="provider", title=provider_title, city="الرياض", seller=seller)
    car_for_sale = Ad(
        id="car-for-sale", title="تويوتا كامري للبيع", city="الرياض", seller=seller
    )

    assert intent.subcategory.value == subcategory
    assert decide(intent, provider, seller)[0] is True
    assert decide(intent, car_for_sale, seller)[0] is False
