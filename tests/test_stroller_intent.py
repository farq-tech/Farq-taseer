import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.live_haraj import haraj_queries
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
        "عربية أطفال بيبي زن يويو",
        "يويو بيبي زين عربية أطفال",
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


def test_yoyo_lookalike_is_not_exact_but_can_be_a_labeled_near_match():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    inspired = Ad(id="ad-1", title="عربية أطفال بتصميم مستوحى من يويو", seller=seller)

    assert decide(intent, inspired, seller) == (False, ["model_lookalike"])
    assert decide(intent, inspired, seller, relaxed=True)[0] is True


def test_babyzen_brand_is_only_set_when_the_query_names_it():
    assert not analyze("عربية أطفال يويو").brand.known
    assert analyze("Babyzen YOYO stroller").brand.value == "Babyzen"
    assert analyze("عربية أطفال بيبي زن يويو").brand.value == "Babyzen"


def test_bare_yoyo_toy_query_does_not_become_a_stroller():
    intent = analyze("يويو 3")

    assert intent.subcategory.value != "strollers"
    assert intent.model.value != "YOYO 3"


@pytest.mark.parametrize(
    "query,model",
    [
        ("عربية أطفال يويو 2", "YOYO 2"),
        ("عربة اطفال يويـو ٣", "YOYO 3"),
        ("Babyzen YOYO3 stroller", "YOYO 3"),
    ],
)
def test_explicit_yoyo_generation_is_preserved_in_intent_and_search(query, model):
    intent = analyze(query)

    assert intent.model.value == model
    assert any(model.split()[-1] in term and ("يويو" in term or "YOYO" in term) for term in intent.search_terms)


@pytest.mark.parametrize("query", ["عربية أطفال يويو", "عربية أطفال يويو 2"])
def test_haraj_query_budget_keeps_arabic_and_english_yoyo_searches(query):
    intent = analyze(query)

    # Production searches the first six unique phrases. Keep both English forms
    # inside that budget so Arabic synonyms do not crowd them out.
    searched = haraj_queries(intent.search_terms, None)[:6]

    assert len(searched) == 6
    assert any("Babyzen YOYO" in term for term in searched)
    assert any("YOYO" in term and "stroller" in term for term in searched)
    if intent.model.value == "YOYO 2":
        assert any("YOYO 2" in term and "stroller" in term for term in searched)
        assert all("2" in term for term in searched)


def test_yoyo_generations_do_not_match_each_other_as_exact_results():
    intent = analyze("عربية أطفال يويو 2")
    seller = Seller(id="seller-1", name="معلن")
    yoyo3 = Ad(id="ad-3", title="عربية أطفال يويو 3 أصلية", seller=seller)
    unspecified = Ad(id="ad-unknown", title="عربة أطفال يويو للبيع", seller=seller)

    assert decide(intent, yoyo3, seller) == (False, ["yoyo_variant_mismatch"])
    assert decide(intent, yoyo3, seller, relaxed=True)[0] is True
    assert decide(intent, unspecified, seller)[0] is True


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

