import pytest

from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze


@pytest.mark.parametrize(
    "query",
    [
        "أبي قط شيرازي ذكر عمر شهرين",
        "أبي قطة سيامي أنثى",
        "أبي قطط للتبني",
        "Persian male cat age 2 months",
    ],
)
def test_cat_listing_queries_are_understood_in_the_animals_category(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "other"
    assert intent.category.value == "animals"
    assert intent.subcategory.value == "cats"
    assert intent.result_unit == ResultUnit.AD
    assert intent.search_terms


def test_cat_breed_sex_and_age_are_preserved_as_explicit_attributes():
    intent = analyze("أبي قط شيرازي ذكر عمر شهرين")

    assert "قط شيرازي" in intent.need
    assert "ذكر" in intent.need
    assert "شهرين" in intent.need
    assert {item.value for item in intent.attributes} >= {
        "breed:persian",
        "sex:male",
        "age_months:2",
    }
    assert any("شيرازي" in group or "persian" in group for group in intent.eligibility_groups)
    assert any("ذكر" in group or "male" in group for group in intent.eligibility_groups)


def test_cat_match_requires_requested_breed_sex_and_age_evidence():
    intent = analyze("أبي قط شيرازي ذكر عمر شهرين")
    seller = Seller(id="seller-1", name="معلن")
    matching = Ad(
        id="ad-1",
        title="قط شيرازي ذكر للبيع",
        description="العمر شهرين.",
        seller=seller,
    )
    wrong_breed = Ad(id="ad-2", title="قط بريطاني ذكر للبيع", description="العمر شهرين.", seller=seller)
    wrong_sex = Ad(id="ad-3", title="قط شيرازي أنثى للبيع", description="العمر شهرين.", seller=seller)
    wrong_age = Ad(id="ad-4", title="قط شيرازي ذكر للبيع", description="العمر أربعة أشهر.", seller=seller)
    missing_age = Ad(id="ad-5", title="قط شيرازي ذكر للبيع", description="العمر صغير.", seller=seller)
    dog = Ad(id="ad-6", title="كلب شيرازي ذكر للبيع", description="العمر شهرين.", seller=seller)

    assert decide(intent, matching, seller)[0] is True
    assert decide(intent, wrong_breed, seller)[0] is False
    assert decide(intent, wrong_sex, seller)[0] is False
    assert decide(intent, wrong_age, seller)[0] is False
    assert decide(intent, missing_age, seller)[0] is False
    assert decide(intent, dog, seller)[0] is False


def test_cat_food_is_not_misclassified_as_a_live_cat_listing():
    intent = analyze("أبي طعام قطط للبيع")

    assert intent.type.value == "product"
    assert intent.category.value == "animals"
    assert intent.subcategory.value == "cat_food"


def test_veterinary_requests_remain_local_services():
    intent = analyze("أحتاج طبيب بيطري للقطط بالرياض")

    assert intent.type.value == "service"
    assert intent.category.value == "services"
    assert intent.subcategory.value == "veterinary"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert intent.location_city.value == "الرياض"
