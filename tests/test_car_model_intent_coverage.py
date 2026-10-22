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
    ("query", "model", "brand", "subcategory", "matching_title", "wrong_title"),
    [
        (
            "أبي سيارة يارس 2020 بالرياض",
            "Yaris",
            "Toyota",
            "cars",
            "سيارة تويوتا يارس 2020",
            "سيارة تويوتا كورولا 2020",
        ),
        (
            "سيارة كامري 2015",
            "Camry",
            "Toyota",
            "cars",
            "سيارة تويوتا كامري 2015",
            "سيارة تويوتا كورولا 2015",
        ),
        (
            "أبي سيارة كورولا 2024 بالرياض",
            "Corolla",
            "Toyota",
            "cars",
            "تويوتا كورولا 2024",
            "هيونداي النترا 2024",
        ),
        (
            "أبي سيارة النترا 2021 بالرياض",
            "Elantra",
            "Hyundai",
            "cars",
            "هيونداي النترا 2021",
            "تويوتا كورولا 2021",
        ),
        (
            "أبي سيارة سوناتا 2019 بالرياض",
            "Sonata",
            "Hyundai",
            "cars",
            "هيونداي سوناتا 2019",
            "هوندا اكورد 2019",
        ),
        (
            "أبي سيارة اكورد 2021 بالرياض",
            "Accord",
            "Honda",
            "cars",
            "هوندا اكورد 2021",
            "هوندا سيفيك 2021",
        ),
        (
            "أبي سيارة سيفيك 2022 بالرياض",
            "Civic",
            "Honda",
            "cars",
            "هوندا سيفيك 2022",
            "هوندا اكورد 2022",
        ),
        (
            "أبي سيارة باترول فتك بالرياض",
            "Patrol",
            "Nissan",
            "cars",
            "نيسان باترول فتك",
            "تويوتا لاندكروزر",
        ),
        (
            "أبي سيارة جمس يوكن 2020 بالرياض",
            "Yukon",
            "GMC",
            "cars",
            "جمس يوكن 2020",
            "جمس سييرا 2020",
        ),
        (
            "أبي سيارة مرسيدس E200 بالرياض",
            "E200",
            "Mercedes-Benz",
            "cars",
            "مرسيدس E200",
            "مرسيدس E300",
        ),
        (
            "أبي سيارة لكزس ES 2023 بالرياض",
            "ES",
            "Lexus",
            "cars",
            "لكزس ES 2023",
            "لكزس LX570",
        ),
        (
            "أبي سيارة لكزس LX570 بالرياض",
            "LX570",
            "Lexus",
            "cars",
            "لكزس LX570",
            "لكزس ES 2023",
        ),
        (
            "أبي سيارة شاص 2021 بالرياض",
            "Land Cruiser 70",
            "Toyota",
            "cars",
            "تويوتا شاص 2021",
            "تويوتا لاندكروزر برادو",
        ),
        (
            "أبي سيارة برادو 2020 بالرياض",
            "Land Cruiser Prado",
            "Toyota",
            "cars",
            "تويوتا برادو 2020",
            "تويوتا لاندكروزر",
        ),
        (
            "أبي سيارة اف جي 2014 بالرياض",
            "FJ Cruiser",
            "Toyota",
            "cars",
            "تويوتا اف جي 2014",
            "تويوتا برادو 2014",
        ),
        (
            "أبي سيارة اكسنت 2018 بالرياض",
            "Accent",
            "Hyundai",
            "cars",
            "هيونداي اكسنت 2018",
            "هيونداي النترا 2018",
        ),
        (
            "أبي سيارة ازيرا 2019 بالرياض",
            "Azera",
            "Hyundai",
            "cars",
            "هيونداي ازيرا 2019",
            "هيونداي سوناتا 2019",
        ),
        (
            "أبي سيارة تاهو 2023 بالرياض",
            "Tahoe",
            "Chevrolet",
            "cars",
            "شفروليه تاهو 2023",
            "جمس يوكن 2023",
        ),
        (
            "أبي بيك اب جي ام سي سييرا 2020 بالرياض",
            "Sierra",
            "GMC",
            "pickups",
            "جمس سييرا 2020",
            "رام 1500 2020",
        ),
        (
            "أبي بيك اب رام 1500 بالرياض",
            "Ram 1500",
            "Ram",
            "pickups",
            "رام 1500",
            "جمس سييرا",
        ),
        (
            "أبي سيارة موستنق 2017 بالرياض",
            "Mustang",
            "Ford",
            "cars",
            "فورد موستنق 2017",
            "دودج تشارجر 2017",
        ),
        (
            "أبي سيارة تشارجر 2019 بالرياض",
            "Charger",
            "Dodge",
            "cars",
            "دودج تشارجر 2019",
            "فورد موستنق 2019",
        ),
        (
            "أبي سيارة كيا K5 2022 بالرياض",
            "K5",
            "Kia",
            "cars",
            "كيا K5 2022",
            "هيونداي سوناتا 2022",
        ),
        (
            "أبي سيارة نيسان التيما 2020 بالرياض",
            "Altima",
            "Nissan",
            "cars",
            "نيسان التيما 2020",
            "نيسان باترول 2020",
        ),
        (
            "أبي سيارة مازدا 6 بالرياض",
            "Mazda 6",
            "Mazda",
            "cars",
            "مازدا 6",
            "مازدا CX-5",
        ),
        (
            "أبي سيارة بي ام 520 بالرياض",
            "520",
            "BMW",
            "cars",
            "بي ام دبليو 520",
            "بي ام دبليو 530",
        ),
        (
            "أبي سيارة اودي A6 بالرياض",
            "A6",
            "Audi",
            "cars",
            "اودي A6",
            "اودي A8",
        ),
        (
            "أبي سيارة بورش كايين بالرياض",
            "Cayenne",
            "Porsche",
            "cars",
            "بورش كايين",
            "بورش ماكان",
        ),
        (
            "أبي سيارة تسلا موديل 3 بالرياض",
            "Model 3",
            "Tesla",
            "cars",
            "تسلا موديل 3",
            "تسلا موديل Y",
        ),
    ],
)
def test_explicit_model_beats_generic_car_word(
    query, model, brand, subcategory, matching_title, wrong_title
):
    intent = analyze(query)
    seller = Seller(id="seller", name="معلن")
    matching = Ad(id="matching", title=matching_title, city="الرياض", seller=seller)
    wrong = Ad(id="wrong", title=wrong_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.subcategory.value == subcategory
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


def test_quarter_hour_is_not_misread_as_a_land_cruiser_trim():
    intent = analyze("أحتاج ربع ساعة")

    assert intent.model.value != "Land Cruiser 70"


@pytest.mark.parametrize(
    "query,ambiguous_model",
    [
        ("English accent teacher", "Accent"),
        ("شاحن جوال Charger", "Charger"),
    ],
)
def test_ambiguous_english_words_are_not_assumed_to_be_car_models(
    query, ambiguous_model
):
    intent = analyze(query)

    assert intent.model.value != ambiguous_model


def test_car_model_in_description_does_not_make_another_model_match():
    intent = analyze("أبي سيارة كورولا 2024 بالرياض")
    seller = Seller(id="seller", name="معلن")
    other_model = Ad(
        id="elantra",
        title="هيونداي النترا 2024",
        description="قد أستبدلها بكورولا لاحقًا",
        city="الرياض",
        seller=seller,
    )

    assert decide(intent, other_model, seller)[0] is False


def test_vehicle_model_must_be_the_leading_vehicle_in_the_listing_title():
    intent = analyze("أبي سيارة كورولا 2024 بالرياض")
    seller = Seller(id="seller", name="معلن")
    title = Ad(
        id="mixed-models",
        title="هيونداي النترا كورولا 2024 للبيع",
        city="الرياض",
        seller=seller,
    )

    assert decide(intent, title, seller)[0] is False


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
