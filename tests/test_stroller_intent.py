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


@pytest.mark.parametrize(
    "title",
    [
        "مفرش عربية يويو",
        "مظلة عربية يويو",
        "مقبض عربة يويو",
        "ستوك يويو مقعد عربة أطفال",
        "مهد عربية يويو",
    ],
)
def test_stroller_search_does_not_count_accessories_as_complete_strollers(title):
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    accessory = Ad(id="accessory-1", title=title, seller=seller)

    assert decide(intent, accessory, seller) == (False, ["accessory_not_device"])


def test_stroller_with_included_accessories_is_still_a_stroller_listing():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    bundle = Ad(id="ad-1", title="عربية أطفال يويو مع مفرش ومظلة", seller=seller)

    assert decide(intent, bundle, seller)[0] is True


def test_babyzen_brand_is_only_set_when_the_query_names_it():
    assert not analyze("عربية أطفال يويو").brand.known
    assert analyze("Babyzen YOYO stroller").brand.value == "Babyzen"


def test_stroller_accessory_search_is_separate_from_complete_stroller_search():
    intent = analyze("مفرش عربية يويو")

    assert intent.category.value == "baby_products"
    assert intent.subcategory.value == "stroller_accessories"
    assert intent.model.value is None


def test_stroller_sellers_are_classified_as_product_suppliers():
    result = read_business("بيع عربيات وعربات أطفال وبيبي زين يويو")

    assert any(item["key"] == "strollers" for item in result["products"])


def test_stroller_accessory_sellers_are_classified_separately():
    result = read_business("بيع فرش ومظلات ومقابض عربيات الأطفال")

    assert any(item["key"] == "stroller_accessories" for item in result["products"])

