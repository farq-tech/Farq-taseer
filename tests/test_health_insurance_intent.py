import pytest

from farq.contracts import Ad, LocationSensitivity, Seller
from farq.eligibility import decide
from farq.intent import analyze


@pytest.mark.parametrize(
    ("query", "provider_title"),
    [
        ("تأمين صحي", "شركة تأمين صحي"),
        ("تأمين طبي", "مكتب تأمين طبي"),
        ("health insurance", "Health insurance providers"),
    ],
)
def test_health_insurance_routes_to_health_providers_not_car_insurance(
    query, provider_title
):
    intent = analyze(query)
    seller = Seller(id="seller", name="معلن")
    provider = Ad(id="provider", title=provider_title, seller=seller)
    car_insurance = Ad(id="car", title="مكتب تأمين سيارات", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.category.value == "services"
    assert intent.subcategory.value == "health_insurance"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_sensitivity == LocationSensitivity.PREFERRED
    assert decide(intent, provider, seller)[0] is True
    assert decide(intent, car_insurance, seller)[0] is False


def test_health_insurance_preserves_family_coverage_and_location_in_need():
    intent = analyze("تأمين طبي عائلي في الرياض")

    assert intent.subcategory.value == "health_insurance"
    assert "عائلي" in intent.need
    assert "الرياض" in intent.need
    assert any("عائلي" in term for term in intent.search_terms)
