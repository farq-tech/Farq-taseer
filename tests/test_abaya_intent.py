import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze


@pytest.mark.parametrize(
    "query",
    [
        "أبي عباءة سوداء مقاس 54 جديدة",
        "أدور عباية راس سوداء",
        "أحتاج عبايات بالرياض",
        "abaya size 54",
    ],
)
def test_abaya_queries_are_classified_as_clothing_ads(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "clothing"
    assert intent.subcategory.value == "abayas"
    assert intent.result_unit.value == "ad"
    assert intent.search_terms


def test_abaya_search_preserves_requested_size_color_and_condition():
    intent = analyze("أبي عباءة سوداء مقاس ٥٤ جديدة")

    assert intent.need and "مقاس ٥٤" in intent.need
    assert any("مقاس ٥٤" in term for term in intent.search_terms)
    assert {item.value for item in intent.attributes} >= {"size:54", "color:black"}
    assert intent.condition.value == "new"
    assert any(any(term in {"مقاس 54", "مقاس54", "size 54"} for term in group) for group in intent.eligibility_groups)
    assert any("black" in group or "سوداء" in group for group in intent.eligibility_groups)


def test_abaya_matching_requires_the_requested_color_and_size_evidence():
    intent = analyze("أبي عباءة سوداء مقاس 54")
    seller = Seller(id="seller-1", name="معلن")
    matching = Ad(
        id="ad-1",
        title="عباية سوداء للبيع",
        description="المقاس: ٥٤، جديدة.",
        seller=seller,
    )
    wrong_size = Ad(
        id="ad-2",
        title="عباية سوداء للبيع",
        description="المقاس: ٥٦، جديدة.",
        seller=seller,
    )
    wrong_color = Ad(
        id="ad-3",
        title="عباية بيضاء للبيع",
        description="المقاس: ٥٤، جديدة.",
        seller=seller,
    )
    missing_size = Ad(id="ad-4", title="عباية سوداء للبيع", description="جديدة.", seller=seller)
    unrelated = Ad(id="ad-5", title="شيلة سوداء مقاس 54", seller=seller)

    assert decide(intent, matching, seller)[0] is True
    assert decide(intent, wrong_size, seller)[0] is False
    assert decide(intent, wrong_color, seller)[0] is False
    assert decide(intent, missing_size, seller)[0] is False
    assert decide(intent, unrelated, seller)[0] is False
    assert decide(intent, wrong_size, seller, relaxed=True)[0] is False
    assert decide(intent, wrong_color, seller, relaxed=True)[0] is False
    assert decide(intent, missing_size, seller, relaxed=True)[0] is False


@pytest.mark.parametrize(
    "query,city",
    [
        ("أحتاج خياطة عبايات وتعديلها بالخبر", "الخبر"),
        ("أحتاج خياط عبايات بالرياض", "الرياض"),
        ("تعديل عباية", None),
        ("tailor abaya", None),
    ],
)
def test_tailoring_abayas_remains_a_local_service_request(query, city):
    intent = analyze(query)

    assert intent.type.value == "service"
    assert intent.category.value == "services"
    assert intent.subcategory.value == "tailoring"
    assert intent.location_city.value == city
