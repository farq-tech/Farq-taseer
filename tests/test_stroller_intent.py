import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.taxonomy import read_business


@pytest.mark.parametrize(
    "query",
    [
        "عربية اطفال يويو",
        "عربية أطفال يويو",
        "عربيات أطفال يويو",
        "عربة أطفال يويو",
        "عربية يويو",
        "عربة يويو",
        "بيبيزن يويو",
        "Babyzen YOYO stroller",
    ],
)
def test_yoyo_stroller_is_understood_as_a_specific_product(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "baby_products"
    assert intent.subcategory.value == "strollers"
    assert intent.brand.value == "Babyzen"
    assert intent.model.value == "YOYO"
    assert intent.result_unit.value == "ad"
    assert any("YOYO" in term or "يويو" in term.lower() for term in intent.search_terms)


@pytest.mark.parametrize("query", ["عربية اطفال", "عربة أطفال", "عربيات أطفال", "baby stroller"])
def test_generic_stroller_search_does_not_assume_the_yoyo_model(query):
    intent = analyze(query)

    assert intent.type.value == "product"
    assert intent.subcategory.value == "strollers"
    assert not intent.model.known
    assert not intent.brand.known
    assert any("عربة" in term or "عربية" in term or "stroller" in term.lower() for term in intent.search_terms)


def test_yoyo_model_does_not_match_an_unbranded_stroller_ad():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    generic = Ad(id="ad-1", title="عربة أطفال مستعملة", seller=seller)
    yoyo = Ad(id="ad-2", title="عربية أطفال يويو للبيع", seller=seller)

    assert decide(intent, generic, seller)[0] is False
    assert decide(intent, yoyo, seller)[0] is True


def test_stroller_sellers_are_classified_as_product_suppliers():
    result = read_business("بيع عربيات وعربات أطفال وبيبي زين يويو")

    assert any(item["key"] == "strollers" for item in result["products"])

