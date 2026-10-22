import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze


@pytest.mark.parametrize(
    "query",
    [
        "سيارة تويوتا يارس 2022",
        "يارس 2022 للبيع بالرياض",
        "Toyota Yaris 2022",
    ],
)
def test_yaris_queries_get_a_specific_toyota_car_intent(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "vehicle"
    assert intent.category.value == "vehicles"
    assert intent.subcategory.value == "cars"
    assert intent.brand.value == "Toyota"
    assert intent.model.value == "Yaris"
    assert intent.year.value == 2022
    assert any("يارس" in term or "Yaris" in term for term in intent.search_terms)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("تويوتا يارس Y Plus 2022", True),
        ("تويوتا يارس 2022", True),
        ("سياره يارس موديل 2022 للبيع", True),
        ("(مطلوب) تويوتا يارس 2022 فل كامل", False),
        ("تويوتا يارس 2022 قطع غيار تشليح", False),
    ],
)
def test_yaris_search_keeps_whole_cars_and_rejects_wanted_or_dismantled_parts(title, expected):
    intent = analyze("سيارة تويوتا يارس 2022")
    seller = Seller(id="seller", name="معلن")
    ad = Ad(id="listing", title=title, seller=seller)

    assert decide(intent, ad, seller)[0] is expected


@pytest.mark.parametrize(
    ("query", "model", "brand", "matching_title", "wrong_title"),
    [
        (
            "أبي سيارة يارس 2020 بالرياض",
            "Yaris",
            "Toyota",
            "سيارة تويوتا يارس 2020",
            "سيارة تويوتا كورولا 2020",
        ),
        (
            "سيارة كامري 2015",
            "Camry",
            "Toyota",
            "سيارة تويوتا كامري 2015",
            "سيارة تويوتا كورولا 2015",
        ),
    ],
)
def test_explicit_model_beats_generic_car_word(
    query, model, brand, matching_title, wrong_title
):
    intent = analyze(query)
    seller = Seller(id="seller", name="معلن")
    matching = Ad(id="matching", title=matching_title, city="الرياض", seller=seller)
    wrong = Ad(id="wrong", title=wrong_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.subcategory.value == "cars"
    assert intent.model.value == model
    assert intent.brand.value == brand
    assert decide(intent, matching, seller)[0] is True
    assert decide(intent, wrong, seller)[0] is False


def test_specific_product_beats_car_model_mentioned_as_compatibility():
    query = "أحتاج كرسي سيارة أطفال كامري"
    intent = analyze(query)
    seller = Seller(id="seller", name="معلن")
    matching = Ad(
        id="seat",
        title="كرسي سيارة أطفال متوافق مع كامري",
        seller=seller,
    )
    wrong = Ad(id="car", title="تويوتا كامري للبيع", seller=seller)

    assert intent.understood is True
    assert intent.subcategory.value == "car_seats"
    assert decide(intent, matching, seller)[0] is True
    assert decide(intent, wrong, seller)[0] is False


def test_double_cab_datsun_is_understood_as_a_pickup():
    intent = analyze("مطلوب ددسن غمارتين")

    assert intent.type.value == "vehicle"
    assert intent.category.value == "vehicles"
    assert intent.subcategory.value == "pickups"
    assert intent.brand.value == "Datsun"
    assert intent.model.value == "Datsun"
    assert "غمارتين" in " ".join(" ".join(group) for group in intent.eligibility_groups)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("ددسن غمارتين نقل عفوش غنم", True),
        ("ددسن غمارتين( 1989 ) مكيف ثلج", True),
        ("ددسن غمارتين للبيع مع كفرات جديدة", True),
        ("كمبيوتر ددسن غمارتين 2015 جي ال", False),
        ("للبيع فيبر ددسن غمارتين 2015", False),
        ("( ( تم البيع ) ) ازياج بدي ددسن غمارتين موديل 98 الى 2016", False),
    ],
)
def test_datsun_pickup_search_does_not_return_parts_or_sold_ads(title, expected):
    intent = analyze("مطلوب ددسن غمارتين")
    seller = Seller(id="seller", name="معلن")
    ad = Ad(id="listing", title=title, seller=seller)

    assert decide(intent, ad, seller)[0] is expected
