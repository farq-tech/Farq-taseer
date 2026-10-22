from datetime import datetime, timezone

import pytest

from farq.config import SearchConfig
from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.live_haraj import haraj_queries
from farq.taxonomy import read_business


@pytest.mark.parametrize(
    "query",
    [
        "عربية اطفال يويو",
        "عربايات اطفال يويو",
        "وش عندكم عربايات اطفال يويو",
        "وش عندكم من عربايات أطفال يويو",
        "عرباية اطفال يويو",
        "عربايه أطفال يويو",
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


@pytest.mark.parametrize(
    "query,discarded,retained",
    [
        ("هل يتوفر عربية أطفال يويو أصلية", ("هل", "يتوفر"), "أصلية"),
        ("تقدر تدورلي عربية يويو في الرياض", ("تقدر", "تدورلي"), "عربية"),
    ],
)
def test_request_framing_does_not_pollute_the_stroller_search(query, discarded, retained):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.subcategory.value == "strollers"
    assert intent.model.value == "YOYO"
    assert retained in intent.need
    assert not any(word in intent.need.split() for word in discarded)


@pytest.mark.parametrize(
    "query",
    [
        "الله يعافيك عربية أطفال يويو",
        "الله يعافيكي عربية أطفال يويو",
        "الله يعافيكم عربية أطفال يويو",
        "يعطيك العافية أبي عربية أطفال يويو",
        "الله يسعدك أبي عربية أطفال يويو",
    ],
)
def test_polite_arabic_framing_does_not_pollute_product_search(query):
    intent = analyze(query)

    assert intent.need == "عربية أطفال يويو"
    for term in intent.search_terms:
        assert not any(word in term for word in ("يعافيك", "يعافيكي", "يعافيكم", "يعطيك", "يسعدك"))


@pytest.mark.parametrize(
    "query", ["عربية اطفال", "عربة أطفال", "عربيات أطفال", "عربايات أطفال", "baby stroller"]
)
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


def test_low_scoring_but_eligible_yoyo_ads_are_not_hidden_by_the_relative_floor():
    from farq.contracts import SearchResult
    from farq.ranking import rank

    intent = analyze("عربية اطفال يويو")
    first_seller = Seller(id="seller-1", name="معلن 1")
    second_seller = Seller(id="seller-2", name="معلن 2")
    first = Ad(id="ad-1", title="عربيه أطفال ماركة يويو yoyo", seller=first_seller)
    second = Ad(id="ad-2", title="عربة اطفال يويو 2", seller=second_seller)

    assert decide(intent, first, first_seller)[0] is True
    assert decide(intent, second, second_seller)[0] is True
    kept = rank(
        intent,
        [
            SearchResult(result_unit=intent.result_unit, ad=first, seller=first_seller, score=1.0),
            SearchResult(result_unit=intent.result_unit, ad=second, seller=second_seller, score=0.2),
        ],
        SearchConfig(),
        datetime.now(timezone.utc),
    )

    assert [item.ad.id for item in kept] == ["ad-1", "ad-2"]


@pytest.mark.parametrize(
    "title,description",
    [
        (
            "عربية أطفال",
            "عربية أطفال ماركة بريتكس ممتازة ومناسبة للمواليد وماماز اند باباز، ويويو من الماركات المعروفة",
        ),
        (
            "عربيات اطفال العدد 5 شركات متنوعه",
            "متوفر عدة أنواع وماركات منها بريتكس وماماز اند باباز ويويو، السعر حسب النوع",
        ),
        (
            "عربية أطفال",
            "عربية أطفال ماركة بريتكس\nخفيفة وعملية\nالنظافة متوسطة\nتعمل بشكل جيد تماماً\nالسعر 100ريال\nماماز اند باباز\nيويو",
        ),
    ],
)
def test_yoyo_mentioned_as_secondary_brand_is_not_an_exact_or_near_match(title, description):
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    listing = Ad(id="mixed-strollers", title=title, description=description, seller=seller)

    assert decide(intent, listing, seller) == (False, ["yoyo_not_primary_listing_identity"])
    assert decide(intent, listing, seller, relaxed=True) == (False, ["yoyo_not_primary_listing_identity"])


def test_generic_stroller_title_can_be_clarified_by_primary_yoyo_description():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    listing = Ad(
        id="described-yoyo",
        title="عربية أطفال",
        description="عربية أطفال يويو أصلية بحالة ممتازة مع ملحقاتها",
        seller=seller,
    )

    assert decide(intent, listing, seller)[0] is True


def test_yoyo_stroller_with_colloquial_arabiya_title_is_eligible():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    listing = Ad(id="ad-arabiya", title="طقم يويو 2 عرباية وباسينت بيج جديد", seller=seller)

    assert decide(intent, listing, seller)[0] is True


@pytest.mark.parametrize("typo", ["يوييو", "ييويو", "يويوو"])
def test_repeated_letter_yoyo_typos_still_find_the_stroller(typo):
    intent = analyze(f"عرباية {typo}")

    assert intent.understood is True
    assert intent.subcategory.value == "strollers"
    assert intent.model.value == "YOYO"
    assert "عرباية يويو" in intent.search_terms


def test_yoyo_expansion_spends_a_search_slot_on_colloquial_arabiya():
    intent = analyze("عربية اطفال يويو")
    searched = haraj_queries(intent.search_terms, None)[: SearchConfig().live_max_queries]

    assert "عرباية أطفال يويو" in searched
    assert "عربايات أطفال يويو" in searched
    assert "Babyzen YOYO" in searched
    assert "يويو عربة أطفال" in searched
    assert len(searched) == 7


def test_plural_arabiyat_listing_title_matches_a_yoyo_stroller_search():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    listing = Ad(id="ad-plural-arabiyat", title="عربايات أطفال YOYO للبيع", seller=seller)

    assert decide(intent, listing, seller)[0] is True


@pytest.mark.parametrize(
    "title",
    [
        "مفرش عربية يويو",
        "مفرش عرباية يويو",
        "مظلة عربية يويو",
        "مقبض عربة يويو",
        "ستوك يويو مقعد عربة أطفال",
        "مهد عربية يويو",
        "شنطة تخزين يويو",
        "حقيبة تخزين لعربية يويو",
        "كرسي سيارة لعربية يويو carseat babyzen",
        "كونكت توأم لعربية يويو2 جديد بالكرتونة",
        "مهاد مواليد عربية بيبي يويو",
        "Babyzen YOYO carseat",
        "Babyzen YOYO twin connector",
        "عجلات لعربية يويو للأطفال",
    ],
)
def test_stroller_search_does_not_count_accessories_as_complete_strollers(title):
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    accessory = Ad(id="accessory-1", title=title, seller=seller)

    assert decide(intent, accessory, seller) == (False, ["accessory_not_device"])
    assert decide(intent, accessory, seller, relaxed=True) == (False, ["accessory_not_device"])


def test_stroller_with_included_accessories_is_still_a_stroller_listing():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    bundle = Ad(id="ad-1", title="عربية أطفال يويو مع مفرش ومظلة", seller=seller)

    assert decide(intent, bundle, seller)[0] is True


def test_stroller_listing_with_spare_wheels_stays_eligible():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    bundle = Ad(id="ad-spare-wheels", title="عربية أطفال يويو مع عجلات احتياطية", seller=seller)

    assert decide(intent, bundle, seller)[0] is True


@pytest.mark.parametrize("title", ["عربية يويو هيكل بس للبيع", "هيكل عربة يويو فقط"])
def test_stroller_search_rejects_component_only_listings_even_as_near_matches(title):
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    listing = Ad(id="component-only", title=title, seller=seller)

    assert decide(intent, listing, seller) == (False, ["stroller_component_only"])
    assert decide(intent, listing, seller, relaxed=True) == (False, ["stroller_component_only"])


def test_stroller_with_a_car_seat_bundle_is_still_a_stroller_listing():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    bundle = Ad(id="ad-bundle", title="عربية أطفال يويو مع كرسي سيارة", seller=seller)

    assert decide(intent, bundle, seller)[0] is True


def test_yoyo_lookalike_is_not_exact_but_can_be_a_labeled_near_match():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    inspired = Ad(id="ad-1", title="عربية أطفال بتصميم مستوحى من يويو", seller=seller)

    assert decide(intent, inspired, seller) == (False, ["model_lookalike"])
    assert decide(intent, inspired, seller, relaxed=True)[0] is True


def test_yoyo_near_match_cannot_drop_requested_model_identity():
    intent = analyze("عربية اطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    generic = Ad(id="ad-generic", title="عربية أطفال للبيع", seller=seller)

    assert decide(intent, generic, seller, relaxed=True)[0] is False


def test_condition_phrase_similar_to_new_does_not_mark_original_yoyo_as_lookalike():
    intent = analyze("عربية أطفال يويو")
    seller = Seller(id="seller-1", name="معلن")
    original = Ad(id="ad-original", title="Babyzen YOYO stroller, similar to new", seller=seller)

    allowed, evidence = decide(intent, original, seller)
    assert allowed is True
    assert "yoyo" in evidence


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
        ("عربية يويو الجيل الثالث", "YOYO 3"),
        ("عربة أطفال YOYO الجيل الثاني", "YOYO 2"),
        ("عربية أطفال يويو إصدار ٣", "YOYO 3"),
    ],
)
def test_explicit_yoyo_generation_is_preserved_in_intent_and_search(query, model):
    intent = analyze(query)

    assert intent.model.value == model
    assert any(model.split()[-1] in term and ("يويو" in term or "YOYO" in term) for term in intent.search_terms)


@pytest.mark.parametrize("query", ["عربية أطفال يويو", "عربية أطفال يويو 2"])
def test_haraj_query_budget_keeps_arabic_and_english_yoyo_searches(query):
    intent = analyze(query)

    # Production searches the first seven unique phrases. Keep both English forms
    # inside that budget so Arabic synonyms do not crowd them out.
    searched = haraj_queries(intent.search_terms, None)[: SearchConfig().live_max_queries]

    assert len(searched) == 7
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


def test_yoyo_generation_mismatch_remains_only_a_relaxed_match():
    intent = analyze("عربية أطفال يويو 2")
    seller = Seller(id="seller-1", name="معلن")
    yoyo3 = Ad(id="ad-3", title="عربية أطفال يويو 3 أصلية", seller=seller)

    assert decide(intent, yoyo3, seller, relaxed=True)[0] is True


@pytest.mark.parametrize("query", ["مفرش عربية يويو", "مفرش عرباية يويو"])
def test_stroller_accessory_search_is_separate_from_complete_stroller_search(query):
    intent = analyze(query)

    assert intent.category.value == "baby_products"
    assert intent.subcategory.value == "stroller_accessories"
    assert intent.model.value is None


@pytest.mark.parametrize("query,title", [("مفرش عربية يويو", "مظلة عربية يويو"), ("مظلة عربة يويو", "مقعد عربة يويو")])
def test_stroller_accessory_search_keeps_the_requested_part(query, title):
    intent = analyze(query)
    seller = Seller(id="seller-1", name="معلن")
    wrong_part = Ad(id="wrong-part", title=title, seller=seller)

    assert intent.subcategory.value == "stroller_accessories"
    assert decide(intent, wrong_part, seller)[0] is False


def test_generic_english_stroller_accessory_search_accepts_matching_listing():
    intent = analyze("stroller accessories")
    seller = Seller(id="seller-1", name="seller")
    listing = Ad(id="accessory", title="Stroller accessories for sale", seller=seller)

    assert intent.subcategory.value == "stroller_accessories"
    assert decide(intent, listing, seller)[0] is True


def test_attached_arabic_yoyo_generation_keeps_model_specific_eligibility():
    intent = analyze("عربية أطفال يويو3")
    seller = Seller(id="seller-1", name="معلن")
    generic = Ad(id="generic", title="عربية أطفال مستعملة", seller=seller)

    assert intent.model.value == "YOYO 3"
    assert decide(intent, generic, seller)[0] is False


def test_stroller_sellers_are_classified_as_product_suppliers():
    result = read_business("بيع عربيات وعربات أطفال وبيبي زين يويو")

    assert any(item["key"] == "strollers" for item in result["products"])


def test_stroller_accessory_sellers_are_classified_separately():
    result = read_business("بيع فرش ومظلات ومقابض عربايات الأطفال")

    assert any(item["key"] == "stroller_accessories" for item in result["products"])
    assert not any(item["key"] == "shades" for item in result["services"])


@pytest.mark.parametrize("description", ["بيع ألعاب يويو", "بيع فرش سيارات"])
def test_ambiguous_yoyo_or_furnishing_words_do_not_classify_stroller_suppliers(description):
    result = read_business(description)

    assert not any(item["key"] in {"strollers", "stroller_accessories"} for item in result["products"])


def test_english_plural_stroller_business_descriptions_are_classified():
    result = read_business("Babyzen YOYO strollers and baby strollers")

    assert any(item["key"] == "strollers" for item in result["products"])
